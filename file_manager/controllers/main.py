import logging
import os
import re
import tempfile
import zipfile

from werkzeug.exceptions import BadRequest, NotFound
from werkzeug.wrappers import Response
from werkzeug.wsgi import wrap_file

from odoo import _, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.http.stream import content_disposition

_logger = logging.getLogger(__name__)

# Only formats a browser cannot run scripts from. SVG and HTML are always
# sent as downloads on the public pages.
PUBLIC_INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PUBLIC_HEADERS = [
    ("X-Robots-Tag", "noindex, nofollow"),
    # The address holds the secret token: do not leak it to other sites.
    ("Referrer-Policy", "no-referrer"),
]


def _safe_part(name):
    """Make a name usable as one path segment inside a zip file."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return name or "unnamed"


def _human_size(size):
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def _folder_paths(folders, root_ids):
    """Map folder ids to their path, keeping the names from the given roots down."""
    by_id = {folder.id: folder for folder in folders}
    paths = {}

    def path_of(folder):
        if folder.id not in paths:
            parent = folder.parent_id
            prefix = path_of(parent) if parent.id in by_id and folder.id not in root_ids else []
            paths[folder.id] = [*prefix, _safe_part(folder.name)]
        return paths[folder.id]

    for folder in folders:
        path_of(folder)
    return paths


def _zip_response(documents, folder_paths, filename, extra_headers=()):
    """Send files as a zip. ``documents`` must already be access-checked."""
    spool = tempfile.TemporaryFile()
    used_names = set()
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for document in documents:
            attachment = document.sudo().attachment_id
            if not attachment:
                continue
            parts = list(folder_paths.get(document.folder_id.id, []))
            stem, extension = os.path.splitext(_safe_part(document.name))
            candidate = "/".join([*parts, f"{stem}{extension}"])
            counter = 1
            while candidate in used_names:
                counter += 1
                candidate = "/".join([*parts, f"{stem} ({counter}){extension}"])
            used_names.add(candidate)
            stream = attachment._to_http_stream()
            if stream.type == "path":
                archive.write(stream.path, candidate)
            elif stream.type == "data":
                archive.writestr(candidate, stream.data)
    size = spool.tell()
    spool.seek(0)
    headers = [
        ("Content-Type", "application/zip"),
        ("Content-Length", str(size)),
        ("Content-Disposition", content_disposition(filename)),
        ("X-Content-Type-Options", "nosniff"),
        *extra_headers,
    ]
    body = wrap_file(request.httprequest.environ, spool)
    return Response(body, headers=headers, direct_passthrough=True)


class FileManagerController(http.Controller):
    # ------------------------------------------------------------------
    # Logged-in routes: everything runs as the user, so access rows apply
    # ------------------------------------------------------------------

    @http.route("/file_manager/upload", type="http", auth="user", methods=["POST"])
    def upload(self, folder_id=None, document_id=None, **kwargs):
        """Add files, or a new version of one file when document_id is given."""
        Document = request.env["file.manager.document"]
        results = []
        for ufile in request.httprequest.files.getlist("ufile"):
            try:
                with request.env.cr.savepoint():
                    if document_id:
                        document = Document.browse(int(document_id)).exists()
                        if not document:
                            raise UserError(_("This file no longer exists."))
                    else:
                        document = Document.create({
                            "name": ufile.filename,
                            "folder_id": int(folder_id) if folder_id else False,
                        })
                    attachment = document._fm_set_file(ufile)
                    if not document_id:
                        # The attachment holds the cleaned-up version of the name.
                        document.name = attachment.name
            except (AccessError, UserError, ValidationError) as error:
                results.append({"filename": ufile.filename, "error": str(error)})
            except Exception:
                _logger.exception("Could not upload %s", ufile.filename)
                results.append({"filename": ufile.filename, "error": _("The upload failed.")})
            else:
                results.append({"filename": ufile.filename, "id": document.id})
        return request.make_json_response(results)

    @http.route("/file_manager/download", type="http", auth="user", methods=["GET"])
    def download(self, document_ids="", folder_ids="", **kwargs):
        """Download files and folders as one zip file."""
        try:
            document_list = [int(i) for i in document_ids.split(",") if i]
            folder_list = [int(i) for i in folder_ids.split(",") if i]
        except ValueError as error:
            raise BadRequest() from error
        Document = request.env["file.manager.document"]
        Folder = request.env["file.manager.folder"]
        documents = Document.browse(document_list).exists()
        documents.check_access("read")
        roots = Folder.browse(folder_list).exists()
        roots.check_access("read")
        # search() applies the access rows: subfolders this user cannot see stay out.
        folders = Folder.search([("id", "child_of", roots.ids)]) if roots else Folder
        folders |= roots
        paths = _folder_paths(folders, set(roots.ids))
        documents |= Document.search([("folder_id", "in", folders.ids)])
        name = f"{_safe_part(roots.name)}.zip" if len(roots) == 1 and not document_list else "files.zip"
        return _zip_response(documents, paths, name)

    # ------------------------------------------------------------------
    # Public routes: the token in the address is the only credential. Sudo
    # is used for reading, always limited to what this one link exposes.
    # ------------------------------------------------------------------

    def _find_share(self, token):
        return request.env["file.manager.share"]._fm_find_by_token(token)

    def _unavailable(self):
        response = request.render("file_manager.share_unavailable", headers=PUBLIC_HEADERS)
        response.status_code = 404
        return response

    @http.route("/file_manager/share/<string:token>", type="http", auth="public", methods=["GET"], sitemap=False)
    def share_page(self, token, **kwargs):
        share = self._find_share(token)
        if not share:
            return self._unavailable()
        share.view_count += 1
        folders = share._fm_public_folders()
        paths = _folder_paths(folders, set(share.folder_id.ids))
        rows = []
        for document in share._fm_public_documents():
            extension = os.path.splitext(document.name)[1].lstrip(".")[:4]
            rows.append({
                "id": document.id,
                "name": document.name,
                "path": " / ".join(paths.get(document.folder_id.id, [])),
                "size": _human_size(document.file_size) if document.doc_type == "file" else "",
                "url": document.url if document.doc_type == "url" else False,
                "is_image": document.attachment_id.mimetype in PUBLIC_INLINE_IMAGES,
                "extension": extension or ("url" if document.doc_type == "url" else "file"),
            })
        return request.render(
            "file_manager.share_page",
            {
                "share": share,
                "rows": rows,
                "has_files": any(not row["url"] for row in rows),
            },
            headers=PUBLIC_HEADERS,
        )

    @http.route(
        "/file_manager/share/<string:token>/file/<int:document_id>",
        type="http", auth="public", methods=["GET"], sitemap=False,
    )
    def share_file(self, token, document_id, inline=None, **kwargs):
        share = self._find_share(token)
        if not share:
            raise NotFound()
        document = share._fm_public_documents().filtered(lambda d: d.id == document_id)
        attachment = document.attachment_id
        if not attachment:
            raise NotFound()
        # Decided from the attachment, the type the response is actually sent with.
        as_attachment = not (inline and attachment.mimetype in PUBLIC_INLINE_IMAGES)
        response = attachment._to_http_stream().get_response(as_attachment=as_attachment)
        for key, value in PUBLIC_HEADERS:
            response.headers[key] = value
        return response

    @http.route(
        "/file_manager/share/<string:token>/download",
        type="http", auth="public", methods=["GET"], sitemap=False,
    )
    def share_download(self, token, **kwargs):
        share = self._find_share(token)
        if not share:
            raise NotFound()
        folders = share._fm_public_folders()
        paths = _folder_paths(folders, set(share.folder_id.ids))
        return _zip_response(
            share._fm_public_documents(), paths,
            f"{_safe_part(share.name)}.zip", extra_headers=PUBLIC_HEADERS,
        )
