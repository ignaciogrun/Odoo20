from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain

# Fields that decide who can reach a folder: only its owner or an administrator
# may change them, so an editor cannot widen access on their own.
ACCESS_FIELDS = {
    "access_mode",
    "company_id",
    "editor_ids",
    "inherit_access",
    "owner_id",
    "viewer_ids",
}
# Stored results of a computation. The ORM accepts writes to them, and the access
# rows read them, so writing one directly would be a way around every other check.
COMPUTED_ACCESS_FIELDS = {
    "effective_access_mode",
    "effective_editor_ids",
    "effective_owner_id",
    "effective_viewer_ids",
}
MANAGER_GROUP = "file_manager.group_file_manager_admin"


class FileManagerFolder(models.Model):
    _name = "file.manager.folder"
    _description = "Folder"
    _order = "name, id"
    _parent_store = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    description = fields.Text()
    parent_id = fields.Many2one(
        "file.manager.folder", string="Parent Folder", index=True, ondelete="restrict",
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("file.manager.folder", "parent_id", string="Subfolders")
    document_ids = fields.One2many("file.manager.document", "folder_id", string="Files")
    company_id = fields.Many2one("res.company", string="Company", index=True)
    complete_name = fields.Char(compute="_compute_complete_name", string="Path", recursive=True)
    trashed_at = fields.Datetime(readonly=True, copy=False)
    trashed_by_id = fields.Many2one("res.users", string="Trashed By", readonly=True, copy=False)

    # Access settings as chosen on this folder.
    inherit_access = fields.Boolean(
        string="Same Access as Parent",
        default=lambda self: bool(self.env.context.get("default_parent_id")),
        help="Use the access settings of the parent folder.",
    )
    owner_id = fields.Many2one(
        "res.users", string="Owner", required=True, default=lambda self: self.env.user,
        domain=[("share", "=", False)],
    )
    access_mode = fields.Selection(
        [
            ("edit", "Everyone can edit"),
            ("view", "Everyone can view"),
            ("restricted", "Only selected people"),
        ],
        string="Internal Users",
        required=True,
        default="restricted",
    )
    editor_ids = fields.Many2many(
        "res.users", "file_manager_folder_editor_rel", "folder_id", "user_id",
        string="Can Edit", domain=[("share", "=", False)],
    )
    viewer_ids = fields.Many2many(
        "res.users", "file_manager_folder_viewer_rel", "folder_id", "user_id",
        string="Can View", domain=[("share", "=", False)],
    )

    # Access settings in force, following the chain of inherited parents. The
    # access rows only look at these fields.
    effective_owner_id = fields.Many2one(
        "res.users", compute="_compute_effective_access", store=True, recursive=True, index=True,
    )
    effective_access_mode = fields.Selection(
        [("edit", "Everyone can edit"), ("view", "Everyone can view"), ("restricted", "Restricted")],
        compute="_compute_effective_access", store=True, recursive=True, index=True,
    )
    effective_editor_ids = fields.Many2many(
        "res.users", "file_manager_folder_effective_editor_rel", "folder_id", "user_id",
        compute="_compute_effective_access", store=True, recursive=True,
    )
    effective_viewer_ids = fields.Many2many(
        "res.users", "file_manager_folder_effective_viewer_rel", "folder_id", "user_id",
        compute="_compute_effective_access", store=True, recursive=True,
    )

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for folder in self:
            parent = folder.sudo().parent_id
            folder.complete_name = (
                f"{parent.complete_name} / {folder.name}" if parent else folder.name
            )

    @api.depends(
        "inherit_access",
        "owner_id",
        "access_mode",
        "editor_ids",
        "viewer_ids",
        "parent_id",
        "parent_id.effective_owner_id",
        "parent_id.effective_access_mode",
        "parent_id.effective_editor_ids",
        "parent_id.effective_viewer_ids",
    )
    def _compute_effective_access(self):
        for folder in self:
            parent = folder.parent_id
            if folder.inherit_access and parent:
                folder.effective_owner_id = parent.effective_owner_id
                folder.effective_access_mode = parent.effective_access_mode
                folder.effective_editor_ids = parent.effective_editor_ids
                folder.effective_viewer_ids = parent.effective_viewer_ids
            else:
                folder.effective_owner_id = folder.owner_id
                folder.effective_access_mode = folder.access_mode
                folder.effective_editor_ids = folder.editor_ids
                folder.effective_viewer_ids = folder.viewer_ids

    # ------------------------------------------------------------------
    # Access checks the access rows cannot express
    # ------------------------------------------------------------------

    def _fm_is_manager(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    def _fm_check_can_manage_access(self):
        """Raise unless the current user may change who can reach these folders."""
        if self._fm_is_manager():
            return
        if any(folder.effective_owner_id != self.env.user for folder in self):
            raise AccessError(_("Only the folder owner can change who has access to it."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su and COMPUTED_ACCESS_FIELDS & vals.keys():
                raise AccessError(_("These access settings are calculated automatically."))
            if parent_id := vals.get("parent_id"):
                # The access rows evaluate the new folder, whose access settings
                # may be its own, so the parent has to be checked explicitly.
                self.browse(parent_id).check_access("write")
                vals.setdefault("inherit_access", True)
            else:
                vals["inherit_access"] = False
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and COMPUTED_ACCESS_FIELDS & vals.keys():
            raise AccessError(_("These access settings are calculated automatically."))
        if ACCESS_FIELDS & vals.keys():
            self._fm_check_can_manage_access()
        if parent_id := vals.get("parent_id"):
            self.browse(parent_id).check_access("write")
        return super().write(vals)

    def unlink(self):
        for folder in self.with_context(active_test=False):
            if folder.child_ids - self or folder.document_ids:
                raise UserError(_("Delete the contents of “%s” first.", folder.name))
        return super().unlink()

    # ------------------------------------------------------------------
    # Actions used by the interface
    # ------------------------------------------------------------------

    def _fm_subtree(self):
        """Return these folders and all their descendants, trashed ones included."""
        return self.with_context(active_test=False).search([("id", "child_of", self.ids)])

    def action_fm_move(self, parent_id=False):
        parent_id = parent_id or False
        self.write({"parent_id": parent_id})
        if not parent_id:
            # A root folder has nothing to inherit from.
            self.sudo().write({"inherit_access": False})
        return True

    def action_fm_trash(self):
        """Move folders to the trash together with everything inside them."""
        self.check_access("write")
        now = fields.Datetime.now()
        subtree = self._fm_subtree()
        subtree.check_access("write")
        documents = self.env["file.manager.document"].search([("folder_id", "in", subtree.ids)])
        documents._fm_check_not_locked()
        values = {"active": False, "trashed_at": now, "trashed_by_id": self.env.uid}
        documents.write(values)
        subtree.filtered("active").write(values)
        return True

    def action_fm_restore(self):
        """Restore trashed folders, the parents they need, and what was trashed with them."""
        self.check_access("write")
        all_folders = self.with_context(active_test=False)
        for folder in all_folders:
            if folder.active:
                continue
            trashed_at = folder.trashed_at
            subtree = folder._fm_subtree().filtered(lambda f: f.trashed_at == trashed_at)
            documents = (
                self.env["file.manager.document"]
                .with_context(active_test=False)
                .search([
                    ("folder_id", "in", subtree.ids),
                    ("active", "=", False),
                    ("trashed_at", "=", trashed_at),
                ])
            )
            values = {"active": True, "trashed_at": False, "trashed_by_id": False}
            documents.write(values)
            subtree.write(values)
            folder._fm_restore_parents()
        return True

    def _fm_restore_parents(self):
        """Bring back trashed ancestors, without their other contents."""
        self.ensure_one()
        parent = self.with_context(active_test=False).parent_id
        if parent and not parent.active:
            parent.write({"active": True, "trashed_at": False, "trashed_by_id": False})
            parent._fm_restore_parents()

    def action_fm_delete_forever(self):
        self.check_access("unlink")
        subtree = self._fm_subtree()
        subtree.check_access("unlink")
        documents = (
            self.env["file.manager.document"]
            .with_context(active_test=False)
            .search([("folder_id", "in", subtree.ids)])
        )
        documents.unlink()
        # Children first: a folder cannot go while a subfolder still points to it.
        for folder in subtree.sorted(lambda f: f.parent_path.count("/"), reverse=True):
            folder.unlink()
        return True

    @api.model
    def fm_ensure_path(self, parent_id, names):
        """Find or create a chain of subfolders, for uploading a whole folder.

        :param int parent_id: folder to start from, or False for a new root folder
        :param list[str] names: folder names, from the outermost to the innermost
        :return: the id of the innermost folder
        """
        current = self.browse(parent_id or [])
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            child = self.search([("parent_id", "=", current.id), ("name", "=", name)], limit=1)
            current = child or self.create({"name": name, "parent_id": current.id})
        return current.id

    # ------------------------------------------------------------------
    # Data for the interface
    # ------------------------------------------------------------------

    def _fm_serialize(self):
        writable = self._filtered_access("write")
        is_manager = self._fm_is_manager()
        counts = dict(
            self.env["file.manager.document"]._read_group(
                [("folder_id", "in", self.ids)], ["folder_id"], ["__count"]
            )
        )
        shared_ids = set(
            self.env["file.manager.share"].search([("folder_id", "in", self.ids)]).folder_id.ids
        )
        return [
            {
                "kind": "folder",
                "key": f"f{folder.id}",
                "id": folder.id,
                "name": folder.name,
                "color": folder.color,
                "description": folder.description or "",
                "parent_id": folder.sudo().parent_id.id or False,
                "owner": folder.sudo().effective_owner_id.name or "",
                "access_mode": folder.effective_access_mode,
                "can_write": folder in writable,
                "can_manage": is_manager or folder.effective_owner_id == self.env.user,
                "is_shared": folder.id in shared_ids,
                "document_count": counts.get(folder, 0),
                "active": folder.active,
                "write_date": fields.Datetime.to_string(folder.write_date),
                "trashed_at": fields.Datetime.to_string(folder.trashed_at),
            }
            for folder in self
        ]

    @api.model
    def fm_get_tree(self):
        """Return every folder the user can see, flat; the interface nests them."""
        folders = self.search([])
        counts = dict(
            self.env["file.manager.document"]._read_group(
                [("folder_id", "in", folders.ids)], ["folder_id"], ["__count"]
            )
        )
        visible_ids = set(folders.ids)
        writable = folders._filtered_access("write")
        result = []
        for folder in folders:
            parent_id = folder.sudo().parent_id.id
            result.append({
                "id": folder.id,
                "name": folder.name,
                "color": folder.color,
                # A folder whose parent is hidden from this user shows at the top level.
                "parent_id": parent_id if parent_id in visible_ids else False,
                "access_mode": folder.effective_access_mode,
                "can_write": folder in writable,
                "document_count": counts.get(folder, 0),
            })
        return result

    def _fm_breadcrumbs(self):
        """Return the visible path to this folder, outermost first."""
        self.ensure_one()
        ancestors = self.with_context(active_test=False).search(
            Domain("id", "parent_of", self.id)
        )
        by_depth = ancestors.sorted(lambda f: f.parent_path.count("/"))
        return [{"id": folder.id, "name": folder.name} for folder in by_depth]
