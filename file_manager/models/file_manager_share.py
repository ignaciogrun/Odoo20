import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain


class FileManagerShare(models.Model):
    _name = "file.manager.share"
    _description = "File Share Link"
    _order = "create_date desc, id desc"

    name = fields.Char(string="Title", required=True)
    token = fields.Char(
        required=True, readonly=True, copy=False, index=True,
        default=lambda self: secrets.token_urlsafe(32),
    )
    url = fields.Char(compute="_compute_url", string="Link")
    document_ids = fields.Many2many(
        "file.manager.document", "file_manager_share_document_rel", "share_id", "document_id",
        string="Files",
    )
    folder_id = fields.Many2one("file.manager.folder", string="Folder", ondelete="cascade")
    expiration_date = fields.Date(string="Expires On")
    is_expired = fields.Boolean(compute="_compute_is_expired", string="Expired")
    view_count = fields.Integer(string="Views", readonly=True, copy=False)
    create_uid = fields.Many2one("res.users", string="Created By", readonly=True, index=True)

    _token_unique = models.Constraint("unique(token)", "This link already exists.")

    def _compute_url(self):
        for share in self:
            share.url = f"{share.get_base_url()}/file_manager/share/{share.token}"

    @api.depends("expiration_date")
    def _compute_is_expired(self):
        today = fields.Date.context_today(self)
        for share in self:
            share.is_expired = bool(share.expiration_date and share.expiration_date < today)

    @api.constrains("document_ids", "folder_id")
    def _check_content(self):
        for share in self:
            if not share.document_ids and not share.folder_id:
                raise UserError(_("Choose at least one file or folder to share."))

    def _fm_check_content_access(self):
        """Publishing needs edit rights, whether by creating a link or by editing one.

        View-only access stays inside Odoo: a public link goes further than the
        folder's owner agreed to when granting it.

        This cannot be a constraint, because constraints run as superuser. The
        links are read through sudo and then checked as the user: reading a
        many2many as the user silently drops the records they cannot see, which
        would let exactly those records through.
        """
        if self.env.su:
            return
        Document = self.env["file.manager.document"]
        for share in self:
            content = share.sudo().with_context(active_test=False)
            Document.browse(content.document_ids.ids).check_access("write")
            self.env["file.manager.folder"].browse(content.folder_id.ids).check_access("write")

    def _fm_check_writable_values(self, vals):
        # A hand-picked token would be guessable; the counter is not the user's to set.
        if not self.env.su and {"token", "view_count"} & vals.keys():
            raise AccessError(_("The link address and its view count cannot be edited."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._fm_check_writable_values(vals)
        shares = super().create(vals_list)
        shares._fm_check_content_access()
        return shares

    def write(self, vals):
        self._fm_check_writable_values(vals)
        result = super().write(vals)
        if {"document_ids", "folder_id"} & vals.keys():
            self._fm_check_content_access()
        return result

    @api.model
    def fm_create_link(self, document_ids=None, folder_id=False, name="", expiration_date=False):
        documents = self.env["file.manager.document"].browse(
            [int(document_id) for document_id in document_ids or []]
        )
        folder = self.env["file.manager.folder"].browse(int(folder_id) if folder_id else [])
        default_name = folder.name or (documents[:1].name if len(documents) == 1 else "")
        share = self.create({
            "name": name or default_name or _("Shared files"),
            "document_ids": [(6, 0, documents.ids)],
            "folder_id": folder.id or False,
            "expiration_date": expiration_date or False,
        })
        return share._fm_serialize()

    @api.model
    def fm_list_links(self, document_ids=None, folder_id=False):
        domain = Domain.FALSE
        if document_ids:
            domain |= Domain("document_ids", "in", [int(d) for d in document_ids])
        if folder_id:
            domain |= Domain("folder_id", "=", int(folder_id))
        return [share._fm_serialize() for share in self.search(domain)]

    def _fm_serialize(self):
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "expiration_date": fields.Date.to_string(self.expiration_date),
            "is_expired": self.is_expired,
            "view_count": self.view_count,
            "file_count": len(self.document_ids),
            "folder": self.folder_id.name or "",
        }

    # ------------------------------------------------------------------
    # Public access. Everything below runs from the public controller, so the
    # token is the only credential and the results are limited to this share.
    # ------------------------------------------------------------------

    @api.model
    def _fm_find_by_token(self, token):
        if not token:
            return self.browse()
        share = self.sudo().search([("token", "=", token)], limit=1)
        if not share or share.is_expired:
            return self.browse()
        return share

    def _fm_public_folders(self):
        """Return the shared folder and its subfolders that are not in the trash."""
        self.ensure_one()
        if not self.folder_id or not self.folder_id.active:
            return self.env["file.manager.folder"].sudo()
        return self.env["file.manager.folder"].sudo().search([("id", "child_of", self.folder_id.id)])

    def _fm_public_documents(self):
        """Return the files this link exposes right now, never trashed ones."""
        self.ensure_one()
        Document = self.env["file.manager.document"].sudo()
        return Document.search(
            Domain("id", "in", self.document_ids.ids)
            | Domain("folder_id", "in", self._fm_public_folders().ids),
            order="name, id",
        )
