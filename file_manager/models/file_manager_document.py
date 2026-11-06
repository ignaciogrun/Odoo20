import unicodedata
from datetime import timedelta
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain

from .file_manager_folder import MANAGER_GROUP

DEFAULT_TRASH_DAYS = 30
RECENT_DAYS = 30
MAX_PAGE_SIZE = 200
SORT_ORDERS = {
    "date_desc": "write_date desc, id desc",
    "date_asc": "write_date asc, id asc",
    "name_asc": "name asc, id asc",
    "name_desc": "name desc, id desc",
    "size_desc": "file_size desc, id desc",
    "size_asc": "file_size asc, id asc",
}
SECTIONS = {"all", "mine", "favorites", "recent", "shared", "trash", "folder"}
# Facts about the stored file. The ORM accepts writes to these, so they have to be
# refused, or a file could pose as another type.
COMPUTED_FILE_FIELDS = {"checksum", "company_id", "file_size", "mimetype"}


class FileManagerDocument(models.Model):
    _name = "file.manager.document"
    _description = "File"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "write_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    description = fields.Text()
    doc_type = fields.Selection(
        [("file", "File"), ("url", "Link")], string="Type", required=True, default="file",
    )
    url = fields.Char(string="Link")
    folder_id = fields.Many2one(
        "file.manager.folder", string="Folder", index=True, ondelete="restrict", tracking=True,
    )
    company_id = fields.Many2one(
        related="folder_id.company_id", store=True, index=True, string="Company",
    )
    owner_id = fields.Many2one(
        "res.users", string="Owner", required=True, index=True, tracking=True,
        default=lambda self: self.env.user, domain=[("share", "=", False)],
    )
    tag_ids = fields.Many2many("file.manager.tag", string="Tags")
    # Set only by the upload code; _check_attachment rejects any attachment that
    # does not belong to this record, so nobody can point it at another file.
    attachment_id = fields.Many2one("ir.attachment", string="File", ondelete="set null", copy=False)
    version_ids = fields.One2many("file.manager.document.version", "document_id", string="Versions")
    mimetype = fields.Char(compute="_compute_file_info", store=True, string="File Type")
    file_size = fields.Integer(compute="_compute_file_info", store=True, string="Size")
    checksum = fields.Char(compute="_compute_file_info", store=True)
    favorite_user_ids = fields.Many2many(
        "res.users", "file_manager_document_favorite_rel", "document_id", "user_id",
        string="Favorite Of", copy=False,
    )
    is_favorite = fields.Boolean(compute="_compute_is_favorite", string="Favorite")
    locked_by_id = fields.Many2one("res.users", string="Locked By", copy=False, tracking=True)
    share_ids = fields.Many2many(
        "file.manager.share", "file_manager_share_document_rel", "document_id", "share_id",
        string="Share Links", copy=False,
    )
    trashed_at = fields.Datetime(readonly=True, copy=False)
    trashed_by_id = fields.Many2one("res.users", string="Trashed By", readonly=True, copy=False)

    @api.depends("attachment_id", "doc_type")
    def _compute_file_info(self):
        for document in self:
            # Reading through sudo is safe: _check_attachment guarantees that the
            # attachment belongs to this document.
            attachment = document.sudo().attachment_id
            document.mimetype = attachment.mimetype or False
            document.file_size = attachment.file_size or 0
            document.checksum = attachment.checksum or False

    @api.depends_context("uid")
    @api.depends("favorite_user_ids")
    def _compute_is_favorite(self):
        for document in self:
            document.is_favorite = self.env.user in document.sudo().favorite_user_ids

    @api.constrains("attachment_id")
    def _check_attachment(self):
        for document in self.sudo():
            attachment = document.attachment_id
            if attachment and (
                attachment.res_model != self._name or attachment.res_id != document.id
            ):
                raise ValidationError(_("This file does not belong to the record."))

    @api.constrains("url", "doc_type")
    def _check_url(self):
        for document in self:
            if document.doc_type != "url":
                continue
            parsed = urlparse(document.url or "")
            # Refuse javascript: and similar links, which would run in the viewer's session.
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValidationError(_("A link must start with http:// or https://."))

    # ------------------------------------------------------------------
    # Access checks the access rows cannot express
    # ------------------------------------------------------------------

    def _fm_is_manager(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    def _fm_check_not_locked(self):
        if self._fm_is_manager():
            return
        for document in self:
            if document.locked_by_id and document.locked_by_id != self.env.user:
                raise UserError(_(
                    "“%(name)s” is locked by %(user)s.",
                    name=document.name, user=document.locked_by_id.name,
                ))

    def _fm_check_writable_values(self, vals):
        if self.env.su:
            return
        if COMPUTED_FILE_FIELDS & vals.keys():
            raise AccessError(_("These details come from the file itself."))
        locked_by = vals.get("locked_by_id")
        if locked_by and locked_by != self.env.uid and not self._fm_is_manager():
            raise AccessError(_("You can only lock a file for yourself."))

    def _fm_check_target_folder(self, folder_id):
        if folder_id:
            self.env["file.manager.folder"].browse(folder_id).check_access("write")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._fm_check_writable_values(vals)
            self._fm_check_target_folder(vals.get("folder_id"))
        return super().create(vals_list)

    def write(self, vals):
        self._fm_check_writable_values(vals)
        if not self.env.su:
            self._fm_check_not_locked()
        if "folder_id" in vals:
            self._fm_check_target_folder(vals["folder_id"])
        result = super().write(vals)
        if "name" in vals:
            self.sudo().attachment_id.write({"name": vals["name"]})
        return result

    def unlink(self):
        self.check_access("unlink")
        self._fm_check_not_locked()
        # Covers the current file, older versions and files attached in the chatter.
        self.env["ir.attachment"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "in", self.ids),
        ]).unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def _fm_set_file(self, ufile, filename=None):
        """Store an uploaded file as the new current version of this document."""
        self.ensure_one()
        self.check_access("write")
        self._fm_check_not_locked()
        # macOS sends names in decomposed form, which breaks search and display.
        name = unicodedata.normalize("NFC", filename or ufile.filename or self.name)
        attachment = self.env["ir.attachment"]._upload_file(ufile, {
            "name": name,
            "mimetype": getattr(ufile, "content_type", None),
            "res_model": self._name,
            "res_id": self.id,
        })
        self.env["file.manager.document.version"].create({
            "document_id": self.id,
            "attachment_id": attachment.id,
        })
        self.write({"attachment_id": attachment.id})
        return attachment

    def action_fm_restore_version(self, version_id):
        self.ensure_one()
        self.check_access("write")
        version = self.version_ids.filtered(lambda v: v.id == version_id)
        if not version:
            raise UserError(_("This version does not belong to the file."))
        self.write({"attachment_id": version.sudo().attachment_id.id})
        return True

    def action_fm_duplicate(self):
        new_ids = []
        for document in self:
            document.check_access("read")
            copy = document.copy({"name": _("%s (copy)", document.name), "owner_id": self.env.uid})
            attachment = document.sudo().attachment_id
            if attachment:
                new_attachment = attachment.copy({"res_id": copy.id, "name": copy.name})
                self.env["file.manager.document.version"].create({
                    "document_id": copy.id,
                    "attachment_id": new_attachment.id,
                })
                copy.write({"attachment_id": new_attachment.id})
            new_ids.append(copy.id)
        return new_ids

    # ------------------------------------------------------------------
    # Actions used by the interface
    # ------------------------------------------------------------------

    def action_fm_toggle_favorite(self):
        # Favorites are personal, so viewing rights are enough. Sudo limited to
        # adding or removing the current user on records they can read.
        self.check_access("read")
        for document in self:
            operation = (4 if self.env.user not in document.sudo().favorite_user_ids else 3)
            document.sudo().write({"favorite_user_ids": [(operation, self.env.uid)]})
        return True

    def action_fm_toggle_lock(self):
        self.ensure_one()
        self.check_access("write")
        if self.locked_by_id:
            self._fm_check_not_locked()
            self.write({"locked_by_id": False})
        else:
            self.write({"locked_by_id": self.env.uid})
        return True

    def action_fm_move(self, folder_id=False):
        folder_id = folder_id or False
        values = {"folder_id": folder_id}
        if not folder_id:
            # A file outside any folder is private to its owner.
            values["owner_id"] = self.env.uid
        self.write(values)
        return True

    def action_fm_trash(self):
        self.check_access("write")
        self.write({
            "active": False,
            "trashed_at": fields.Datetime.now(),
            "trashed_by_id": self.env.uid,
        })
        return True

    def action_fm_restore(self):
        self.check_access("write")
        for document in self.with_context(active_test=False):
            folder = document.folder_id
            if folder and not folder.active:
                folder.write({"active": True, "trashed_at": False, "trashed_by_id": False})
                folder._fm_restore_parents()
            document.write({"active": True, "trashed_at": False, "trashed_by_id": False})
        return True

    def action_fm_delete_forever(self):
        self.unlink()
        return True

    @api.model
    def _cron_purge_trash(self):
        days = self.env["ir.config_parameter"].sudo().get_int(
            "file_manager.trash_days", DEFAULT_TRASH_DAYS,
        )
        limit = fields.Datetime.now() - timedelta(days=days)
        all_records = self.sudo().with_context(active_test=False)
        all_records.search([("active", "=", False), ("trashed_at", "<", limit)]).unlink()
        folders = self.env["file.manager.folder"].sudo().with_context(active_test=False)
        for folder in folders.search(
            [("active", "=", False), ("trashed_at", "<", limit)]
        ).sorted(lambda f: f.parent_path.count("/"), reverse=True):
            if folder.exists() and not folder.child_ids and not folder.document_ids:
                folder.unlink()

    # ------------------------------------------------------------------
    # Data for the interface
    # ------------------------------------------------------------------

    def _fm_serialize(self):
        writable = self._filtered_access("write")
        result = []
        for document in self:
            # Sudo for display names only: a viewer may not be allowed to read the
            # owner's user record or a hidden parent folder.
            sudo_document = document.sudo()
            result.append({
                "kind": "document",
                "key": f"d{document.id}",
                "id": document.id,
                "name": document.name,
                "description": document.description or "",
                "doc_type": document.doc_type,
                "url": document.url or "",
                "attachment_id": document.attachment_id.id or False,
                "mimetype": document.mimetype or "",
                "file_size": document.file_size,
                "checksum": document.checksum or "",
                "folder_id": document.folder_id.id or False,
                "folder_name": sudo_document.folder_id.name or "",
                "owner": sudo_document.owner_id.name or "",
                "owner_id": document.owner_id.id,
                "tags": [
                    {"id": tag.id, "name": tag.name, "color": tag.color}
                    for tag in document.tag_ids
                ],
                "is_favorite": document.is_favorite,
                "locked_by": sudo_document.locked_by_id.name or "",
                "can_write": document in writable,
                "active": document.active,
                "version_count": len(document.version_ids),
                "is_shared": bool(document.share_ids.ids),
                "create_date": fields.Datetime.to_string(document.create_date),
                "write_date": fields.Datetime.to_string(document.write_date),
                "trashed_at": fields.Datetime.to_string(document.trashed_at),
            })
        return result

    @api.model
    def fm_get_explorer_data(
        self, section="all", folder_id=False, search="", tag_ids=None,
        sort="date_desc", limit=80, offset=0,
    ):
        """Return what the file manager shows for one section or folder.

        All arguments come from the browser. The search runs as the current user,
        so the access rows still apply; ``section`` and ``sort`` are whitelisted and
        ``search`` and ``tag_ids`` are only ever used as values.
        """
        if section not in SECTIONS:
            raise UserError(_("Unknown section."))
        order = SORT_ORDERS.get(sort, SORT_ORDERS["date_desc"])
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))
        search = (search or "").strip()
        Folder = self.env["file.manager.folder"]
        current_folder = Folder

        document_domain = Domain.TRUE
        folder_domain = Domain.FALSE
        records = self
        folders = Folder
        if section == "folder":
            current_folder = Folder.with_context(active_test=False).browse(int(folder_id)).exists()
            if not current_folder:
                raise UserError(_("This folder no longer exists."))
            current_folder.check_access("read")
            if search or tag_ids:
                document_domain = Domain("folder_id", "child_of", current_folder.id)
            else:
                document_domain = Domain("folder_id", "=", current_folder.id)
            folder_domain = Domain("parent_id", "=", current_folder.id)
        elif section == "mine":
            document_domain = Domain("owner_id", "=", self.env.uid)
            folder_domain = Domain("parent_id", "=", False) & Domain(
                "effective_owner_id", "=", self.env.uid,
            )
        elif section == "favorites":
            document_domain = Domain("favorite_user_ids", "in", [self.env.uid])
        elif section == "recent":
            document_domain = Domain(
                "write_date", ">=", fields.Datetime.now() - timedelta(days=RECENT_DAYS),
            )
            order = SORT_ORDERS["date_desc"]
        elif section == "shared":
            shares = self.env["file.manager.share"].search([])
            document_domain = Domain("id", "in", shares.document_ids.ids)
            folder_domain = Domain("id", "in", shares.folder_id.ids)
        elif section == "trash":
            records = self.with_context(active_test=False)
            folders = Folder.with_context(active_test=False)
            # Files inside a trashed folder are restored with it, so only the
            # topmost trashed item is listed.
            document_domain = Domain("active", "=", False) & (
                Domain("folder_id", "=", False) | Domain("folder_id.active", "=", True)
            )
            folder_domain = Domain("active", "=", False) & (
                Domain("parent_id", "=", False) | Domain("parent_id.active", "=", True)
            )

        if search:
            document_domain &= (
                Domain("name", "ilike", search)
                | Domain("description", "ilike", search)
                | Domain("tag_ids.name", "ilike", search)
            )
            folder_domain &= Domain("name", "ilike", search)
        if tag_ids:
            clean_tag_ids = [int(tag_id) for tag_id in tag_ids]
            document_domain &= Domain("tag_ids", "in", clean_tag_ids)
            folder_domain = Domain.FALSE

        total = records.search_count(document_domain)
        documents = records.search(document_domain, order=order, limit=limit, offset=offset)
        listed_folders = folders or Folder
        listed_folders = listed_folders.search(folder_domain) if offset == 0 else Folder
        return {
            "documents": documents._fm_serialize(),
            "folders": listed_folders._fm_serialize(),
            "total": total,
            "breadcrumbs": current_folder._fm_breadcrumbs() if current_folder else [],
            "current_folder": current_folder._fm_serialize()[0] if current_folder else False,
        }

    def fm_get_details(self):
        """Return the extra information of the details panel."""
        self.ensure_one()
        self.check_access("read")
        versions = self.version_ids.sorted("id", reverse=True)
        current = self.sudo().attachment_id
        return {
            "versions": [
                {
                    "id": version.id,
                    "name": version.sudo().attachment_id.name,
                    "attachment_id": version.sudo().attachment_id.id,
                    "file_size": version.sudo().attachment_id.file_size,
                    "author": version.sudo().create_uid.name,
                    "date": fields.Datetime.to_string(version.create_date),
                    "is_current": version.sudo().attachment_id == current,
                }
                for version in versions
            ],
        }


class FileManagerDocumentVersion(models.Model):
    _name = "file.manager.document.version"
    _description = "File Version"
    _order = "id desc"

    document_id = fields.Many2one(
        "file.manager.document", required=True, index=True, ondelete="cascade",
    )
    attachment_id = fields.Many2one("ir.attachment", required=True, ondelete="cascade")

    @api.constrains("attachment_id", "document_id")
    def _check_attachment(self):
        for version in self.sudo():
            attachment = version.attachment_id
            if (
                attachment.res_model != "file.manager.document"
                or attachment.res_id != version.document_id.id
            ):
                raise AccessError(_("This file does not belong to the record."))
