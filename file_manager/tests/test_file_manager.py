import io
import zipfile
from datetime import timedelta

from werkzeug.datastructures import FileStorage

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


def make_upload(name="note.txt", content=b"hello", content_type="text/plain"):
    return FileStorage(stream=io.BytesIO(content), filename=name, content_type=content_type)


class FileManagerCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, "fm_owner", groups="base.group_user")
        cls.editor = new_test_user(cls.env, "fm_editor", groups="base.group_user")
        cls.viewer = new_test_user(cls.env, "fm_viewer", groups="base.group_user")
        cls.outsider = new_test_user(cls.env, "fm_outsider", groups="base.group_user")
        cls.admin = new_test_user(
            cls.env, "fm_admin", groups="base.group_user,file_manager.group_file_manager_admin",
        )
        cls.Folder = cls.env["file.manager.folder"]
        cls.Document = cls.env["file.manager.document"]
        cls.Share = cls.env["file.manager.share"]
        cls.team = cls.Folder.with_user(cls.owner).create({
            "name": "Team",
            "access_mode": "restricted",
            "editor_ids": [(6, 0, cls.editor.ids)],
            "viewer_ids": [(6, 0, cls.viewer.ids)],
        })
        cls.sub = cls.Folder.with_user(cls.owner).create({"name": "Sub", "parent_id": cls.team.id})

    def _doc(self, user=None, folder=None, name="note.txt", content=b"hello"):
        document = self.Document.with_user(user or self.owner).create({
            "name": name,
            "folder_id": (folder or self.team).id,
        })
        document._fm_set_file(make_upload(name, content))
        return document


class TestFolderAccess(FileManagerCommon):
    def test_restricted_folder_visibility(self):
        self.assertTrue(self.team.with_user(self.viewer).has_access("read"))
        self.assertFalse(self.team.with_user(self.viewer).has_access("write"))
        self.assertTrue(self.team.with_user(self.editor).has_access("write"))
        self.assertFalse(self.team.with_user(self.outsider).has_access("read"))
        self.assertTrue(self.team.with_user(self.admin).has_access("write"))

    def test_child_follows_parent_changes(self):
        self.assertTrue(self.sub.inherit_access)
        self.assertFalse(self.sub.with_user(self.outsider).has_access("read"))
        self.team.with_user(self.owner).write({"viewer_ids": [(4, self.outsider.id)]})
        self.assertTrue(self.sub.with_user(self.outsider).has_access("read"))
        self.team.with_user(self.owner).write({"access_mode": "edit", "viewer_ids": [(5, 0, 0)]})
        self.assertTrue(self.sub.with_user(self.outsider).has_access("write"))

    def test_deep_inheritance(self):
        deep = self.Folder.with_user(self.owner).create({"name": "Deep", "parent_id": self.sub.id})
        self.assertFalse(deep.with_user(self.outsider).has_access("read"))
        self.team.with_user(self.owner).write({"editor_ids": [(4, self.outsider.id)]})
        self.assertTrue(deep.with_user(self.outsider).has_access("write"))

    def test_editor_cannot_change_access(self):
        with self.assertRaises(AccessError):
            self.team.with_user(self.editor).write({"access_mode": "edit"})
        with self.assertRaises(AccessError):
            self.team.with_user(self.editor).write({"editor_ids": [(4, self.outsider.id)]})
        with self.assertRaises(AccessError):
            self.sub.with_user(self.editor).write({"inherit_access": False})

    def test_computed_access_cannot_be_written(self):
        for values in (
            {"effective_access_mode": "edit"},
            {"effective_owner_id": self.editor.id},
            {"effective_editor_ids": [(4, self.outsider.id)]},
            {"effective_viewer_ids": [(4, self.outsider.id)]},
        ):
            with self.assertRaises(AccessError):
                self.team.with_user(self.editor).write(values)
            with self.assertRaises(AccessError):
                self.team.with_user(self.owner).write(values)
            with self.assertRaises(AccessError):
                self.Folder.with_user(self.outsider).create({"name": "x", **values})

    def test_viewer_cannot_create_inside(self):
        with self.assertRaises(AccessError):
            self.Folder.with_user(self.viewer).create({"name": "No", "parent_id": self.team.id})
        with self.assertRaises(AccessError):
            self.Folder.with_user(self.viewer).create({
                "name": "No", "parent_id": self.team.id, "inherit_access": False,
                "owner_id": self.viewer.id,
            })

    def test_cannot_move_folder_into_unwritable_parent(self):
        mine = self.Folder.with_user(self.outsider).create({"name": "Mine"})
        with self.assertRaises(AccessError):
            mine.action_fm_move(self.team.id)

    def test_cycle_refused(self):
        with self.assertRaises((UserError, ValidationError)):
            self.team.with_user(self.owner).action_fm_move(self.sub.id)

    def test_new_root_folder_is_private(self):
        mine = self.Folder.with_user(self.editor).create({"name": "Private"})
        self.assertFalse(mine.with_user(self.outsider).has_access("read"))
        self.assertFalse(mine.inherit_access)

    def test_other_company_folder_hidden(self):
        other_company = self.env["res.company"].create({"name": "Other"})
        folder = self.Folder.with_user(self.owner).create({"name": "Open", "access_mode": "edit"})
        folder.sudo().company_id = other_company
        document = self.Document.sudo().create({
            "name": "x", "folder_id": folder.id, "owner_id": self.owner.id,
        })
        self.assertFalse(folder.with_user(self.viewer).has_access("read"))
        self.assertFalse(document.with_user(self.viewer).has_access("read"))

    def test_tree_hides_invisible_folders(self):
        ids = {f["id"] for f in self.Folder.with_user(self.outsider).fm_get_tree()}
        self.assertNotIn(self.team.id, ids)
        ids = {f["id"] for f in self.Folder.with_user(self.viewer).fm_get_tree()}
        self.assertLessEqual({self.team.id, self.sub.id}, ids)

    def test_ensure_path(self):
        Folder = self.Folder.with_user(self.editor)
        first = Folder.fm_ensure_path(self.team.id, ["A", "B"])
        self.assertEqual(Folder.fm_ensure_path(self.team.id, ["A", "B"]), first)
        self.assertEqual(Folder.browse(first).parent_id.parent_id, self.team)


class TestDocumentAccess(FileManagerCommon):
    def test_read_follows_folder(self):
        document = self._doc()
        self.assertTrue(document.with_user(self.viewer).has_access("read"))
        self.assertFalse(document.with_user(self.viewer).has_access("write"))
        self.assertTrue(document.with_user(self.editor).has_access("write"))
        self.assertFalse(document.with_user(self.outsider).has_access("read"))

    def test_search_hides_other_files(self):
        document = self._doc()
        found = self.Document.with_user(self.outsider).search([("id", "=", document.id)])
        self.assertFalse(found)

    def test_viewer_cannot_upload(self):
        with self.assertRaises(AccessError):
            self.Document.with_user(self.viewer).create({"name": "x", "folder_id": self.team.id})

    def test_attachment_follows_document(self):
        document = self._doc()
        attachment = document.sudo().attachment_id
        self.assertEqual(bytes(attachment.with_user(self.viewer).raw), b"hello")
        with self.assertRaises(AccessError):
            attachment.with_user(self.outsider).raw  # noqa: B018
        with self.assertRaises(AccessError):
            attachment.with_user(self.viewer).write({"name": "x"})

    def test_private_file(self):
        document = self.Document.with_user(self.editor).create({"name": "mine"})
        self.assertTrue(document.with_user(self.editor).has_access("write"))
        self.assertFalse(document.with_user(self.owner).has_access("read"))
        self.assertTrue(document.with_user(self.admin).has_access("read"))

    def test_cannot_move_into_unwritable_folder(self):
        document = self.Document.with_user(self.outsider).create({"name": "mine"})
        with self.assertRaises(AccessError):
            document.action_fm_move(self.team.id)

    def test_viewer_cannot_move_or_trash(self):
        document = self._doc().with_user(self.viewer)
        with self.assertRaises(AccessError):
            document.action_fm_move(False)
        with self.assertRaises(AccessError):
            document.action_fm_trash()

    def test_move_to_personal_takes_ownership(self):
        document = self._doc(self.owner).with_user(self.editor)
        document.action_fm_move(False)
        self.assertEqual(document.owner_id, self.editor)
        self.assertFalse(document.with_user(self.owner).has_access("read"))

    def test_cannot_steal_attachment(self):
        secret = self._doc(name="secret")
        mine = self.Document.with_user(self.outsider).create({"name": "mine"})
        with self.assertRaises((ValidationError, AccessError)):
            mine.write({"attachment_id": secret.sudo().attachment_id.id})
        with self.assertRaises((ValidationError, AccessError)):
            self.Document.with_user(self.outsider).create({
                "name": "steal", "attachment_id": secret.sudo().attachment_id.id,
            })

    def test_unsafe_link_refused(self):
        with self.assertRaises(ValidationError):
            self.Document.with_user(self.owner).create({
                "name": "x", "doc_type": "url", "url": "javascript:alert(1)",
            })
        link = self.Document.with_user(self.owner).create({
            "name": "x", "doc_type": "url", "url": "https://example.com/a",
        })
        with self.assertRaises(ValidationError):
            link.write({"url": "data:text/html,x"})

    def test_lock(self):
        document = self._doc().with_user(self.editor)
        document.action_fm_toggle_lock()
        locked_for_owner = document.with_user(self.owner)
        with self.assertRaises(UserError):
            locked_for_owner.write({"name": "other"})
        with self.assertRaises(UserError):
            locked_for_owner.action_fm_trash()
        with self.assertRaises(UserError):
            locked_for_owner.action_fm_toggle_lock()
        with self.assertRaises(UserError):
            locked_for_owner.unlink()
        document.with_user(self.admin).action_fm_toggle_lock()
        locked_for_owner.write({"name": "other"})

    def test_file_facts_cannot_be_written(self):
        document = self._doc().with_user(self.editor)
        for values in ({"mimetype": "image/png"}, {"file_size": 1}, {"checksum": "x"}):
            with self.assertRaises(AccessError):
                document.write(values)

    def test_cannot_lock_for_someone_else(self):
        document = self._doc().with_user(self.editor)
        with self.assertRaises(AccessError):
            document.write({"locked_by_id": self.owner.id})

    def test_favorite_is_personal_and_needs_only_read(self):
        document = self._doc()
        document.with_user(self.viewer).action_fm_toggle_favorite()
        self.assertTrue(document.with_user(self.viewer).is_favorite)
        self.assertFalse(document.with_user(self.editor).is_favorite)
        with self.assertRaises(AccessError):
            document.with_user(self.outsider).action_fm_toggle_favorite()
        document.with_user(self.viewer).action_fm_toggle_favorite()
        self.assertFalse(document.with_user(self.viewer).is_favorite)

    def test_read_only_favorites_field_is_not_writable(self):
        document = self._doc()
        with self.assertRaises(AccessError):
            document.with_user(self.viewer).write({"favorite_user_ids": [(4, self.viewer.id)]})


class TestVersionsAndTrash(FileManagerCommon):
    def test_versions_and_restore(self):
        document = self._doc(content=b"first")
        first = document.sudo().attachment_id
        document._fm_set_file(make_upload("note.txt", b"second"))
        self.assertEqual(document.file_size, 6)
        self.assertEqual(len(document.version_ids), 2)
        old = document.version_ids.filtered(lambda v: v.attachment_id == first)
        document.action_fm_restore_version(old.id)
        self.assertEqual(bytes(document.sudo().attachment_id.raw), b"first")
        self.assertEqual(document.file_size, 5)

    def test_foreign_version_refused(self):
        document = self._doc()
        other = self._doc()
        with self.assertRaises(UserError):
            document.action_fm_restore_version(other.version_ids.id)

    def test_viewer_cannot_duplicate_into_folder(self):
        document = self._doc().with_user(self.viewer)
        with self.assertRaises(AccessError):
            document.action_fm_duplicate()

    def test_viewer_cannot_add_version(self):
        document = self._doc().with_user(self.viewer)
        with self.assertRaises(AccessError):
            document._fm_set_file(make_upload())

    def test_rename_renames_file(self):
        document = self._doc()
        document.write({"name": "renamed.txt"})
        self.assertEqual(document.sudo().attachment_id.name, "renamed.txt")

    def test_duplicate_copies_file(self):
        document = self._doc(content=b"abc")
        copy = self.Document.with_user(self.editor).browse(document.action_fm_duplicate())
        self.assertNotEqual(copy.sudo().attachment_id, document.sudo().attachment_id)
        self.assertEqual(bytes(copy.sudo().attachment_id.raw), b"abc")
        self.assertEqual(copy.sudo().attachment_id.res_id, copy.id)

    def test_trash_and_restore_document(self):
        document = self._doc()
        document.action_fm_trash()
        self.assertFalse(document.active)
        self.assertTrue(document.trashed_at)
        document.action_fm_restore()
        self.assertTrue(document.active)
        self.assertFalse(document.trashed_at)

    def test_trash_folder_takes_contents(self):
        document = self._doc(folder=self.sub)
        self.team.with_user(self.owner).action_fm_trash()
        self.assertFalse(self.team.active)
        self.assertFalse(self.sub.active)
        self.assertFalse(document.active)
        self.team.with_user(self.owner).action_fm_restore()
        self.assertTrue(self.team.active)
        self.assertTrue(self.sub.active)
        self.assertTrue(document.active)

    def test_folder_restore_keeps_items_trashed_earlier(self):
        earlier = self._doc(folder=self.sub)
        earlier.action_fm_trash()
        earlier.write({"trashed_at": fields.Datetime.now() - timedelta(days=2)})
        self.team.with_user(self.owner).action_fm_trash()
        self.team.with_user(self.owner).action_fm_restore()
        self.assertFalse(earlier.active)

    def test_restoring_file_brings_back_folder(self):
        document = self._doc(folder=self.sub)
        self.team.with_user(self.owner).action_fm_trash()
        document.with_user(self.owner).action_fm_restore()
        self.assertTrue(document.active)
        self.assertTrue(self.sub.active)
        self.assertTrue(self.team.active)

    def test_viewer_reads_trashed_but_cannot_restore(self):
        document = self._doc()
        document.action_fm_trash()
        as_viewer = document.with_user(self.viewer)
        self.assertTrue(as_viewer.has_access("read"))
        with self.assertRaises(AccessError):
            as_viewer.action_fm_restore()

    def test_delete_forever_removes_attachments(self):
        document = self._doc()
        attachment = document.sudo().attachment_id
        document.action_fm_delete_forever()
        self.assertFalse(attachment.exists())

    def test_delete_folder_forever(self):
        document = self._doc(folder=self.sub)
        attachment = document.sudo().attachment_id
        team = self.team.with_user(self.owner)
        team.action_fm_trash()
        team.action_fm_delete_forever()
        self.assertFalse(self.team.exists())
        self.assertFalse(self.sub.exists())
        self.assertFalse(document.exists())
        self.assertFalse(attachment.exists())

    def test_viewer_cannot_delete_folder_forever(self):
        with self.assertRaises(AccessError):
            self.team.with_user(self.viewer).action_fm_delete_forever()

    def test_cron_purges_only_old_items(self):
        old = self._doc(name="old")
        recent = self._doc(name="recent")
        (old | recent).action_fm_trash()
        old.write({"trashed_at": fields.Datetime.now() - timedelta(days=31)})
        self.Document._cron_purge_trash()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


class TestExplorer(FileManagerCommon):
    def _names(self, user, **kwargs):
        data = self.Document.with_user(user).fm_get_explorer_data(**kwargs)
        return {document["name"] for document in data["documents"]}

    def test_folder_section_and_search(self):
        self._doc(name="top.txt")
        self._doc(name="inner.txt", folder=self.sub)
        kwargs = {"section": "folder", "folder_id": self.team.id}
        self.assertEqual(self._names(self.viewer, **kwargs), {"top.txt"})
        self.assertEqual(self._names(self.viewer, search="inner", **kwargs), {"inner.txt"})

    def test_folder_section_refuses_outsider(self):
        with self.assertRaises(AccessError):
            self.Document.with_user(self.outsider).fm_get_explorer_data(
                section="folder", folder_id=self.team.id,
            )

    def test_all_section_only_lists_accessible(self):
        self._doc(name="team.txt")
        self.assertNotIn("team.txt", self._names(self.outsider, section="all"))
        self.assertIn("team.txt", self._names(self.viewer, section="all"))

    def test_trash_section(self):
        plain = self._doc(name="plain.txt")
        nested = self._doc(name="nested.txt", folder=self.sub)
        plain.action_fm_trash()
        self.sub.with_user(self.owner).action_fm_trash()
        data = self.Document.with_user(self.owner).fm_get_explorer_data(section="trash")
        names = {document["name"] for document in data["documents"]}
        self.assertIn("plain.txt", names)
        self.assertNotIn(nested.name, names)
        self.assertEqual({f["id"] for f in data["folders"]}, set(self.sub.ids))
        self.assertNotIn("plain.txt", self._names(self.owner, section="all"))

    def test_favorites_section(self):
        document = self._doc(name="fav.txt")
        document.with_user(self.viewer).action_fm_toggle_favorite()
        self.assertEqual(self._names(self.viewer, section="favorites"), {"fav.txt"})
        self.assertFalse(self._names(self.editor, section="favorites"))

    def test_bad_arguments(self):
        Document = self.Document.with_user(self.viewer)
        with self.assertRaises(UserError):
            Document.fm_get_explorer_data(section="nonsense")
        # An unknown sort falls back to the default instead of reaching SQL.
        Document.fm_get_explorer_data(sort="id; DROP TABLE res_users")
        self.assertLessEqual(len(Document.fm_get_explorer_data(limit=100000)["documents"]), 200)

    def test_tag_filter(self):
        tag = self.env["file.manager.tag"].create({"name": "Invoice"})
        document = self._doc(name="tagged.txt")
        self._doc(name="plain.txt")
        document.write({"tag_ids": [(4, tag.id)]})
        self.assertEqual(
            self._names(self.viewer, section="folder", folder_id=self.team.id, tag_ids=[tag.id]),
            {"tagged.txt"},
        )
        self.assertEqual(
            self._names(self.viewer, section="folder", folder_id=self.team.id, search="invoice"),
            {"tagged.txt"},
        )


class TestShareModel(FileManagerCommon):
    def test_cannot_share_what_you_cannot_read(self):
        document = self._doc()
        Share = self.Share.with_user(self.outsider)
        with self.assertRaises(AccessError):
            Share.fm_create_link(document_ids=document.ids)
        with self.assertRaises(AccessError):
            Share.fm_create_link(folder_id=self.team.id)

    def test_cannot_edit_own_link_to_reach_other_files(self):
        document = self._doc()
        mine = self.Document.with_user(self.outsider).create({"name": "mine"})
        share = self.Share.with_user(self.outsider).create({
            "name": "x", "document_ids": [(6, 0, mine.ids)],
        })
        with self.assertRaises(AccessError):
            share.write({"document_ids": [(4, document.id)]})
        with self.assertRaises(AccessError):
            share.write({"folder_id": self.team.id})

    def test_links_are_private_to_creator(self):
        document = self._doc()
        share = self.Share.with_user(self.editor).browse(
            self.Share.with_user(self.editor).fm_create_link(document_ids=document.ids)["id"]
        )
        self.assertFalse(share.with_user(self.owner).has_access("read"))
        self.assertTrue(share.with_user(self.admin).has_access("read"))

    def test_view_only_user_cannot_publish(self):
        document = self._doc()
        with self.assertRaises(AccessError):
            self.Share.with_user(self.viewer).fm_create_link(document_ids=document.ids)
        with self.assertRaises(AccessError):
            self.Share.with_user(self.viewer).fm_create_link(folder_id=self.team.id)

    def test_token_and_counter_cannot_be_written(self):
        document = self._doc()
        Share = self.Share.with_user(self.editor)
        share = Share.browse(Share.fm_create_link(document_ids=document.ids)["id"])
        with self.assertRaises(AccessError):
            share.write({"token": "easy"})
        with self.assertRaises(AccessError):
            share.write({"view_count": 99})
        with self.assertRaises(AccessError):
            Share.create({"name": "x", "token": "easy", "document_ids": [(6, 0, document.ids)]})

    def test_empty_link_refused(self):
        with self.assertRaises(UserError):
            self.Share.with_user(self.editor).fm_create_link()

    def test_token_is_long_and_unique(self):
        document = self._doc()
        first = self.Share.fm_create_link(document_ids=document.ids)
        second = self.Share.fm_create_link(document_ids=document.ids)
        self.assertNotEqual(first["url"], second["url"])
        self.assertGreaterEqual(len(first["url"].rsplit("/", 1)[1]), 40)


@tagged("post_install", "-at_install")
class TestRoutes(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, "fm_http", password="fm_http_pass_1", groups="base.group_user")
        cls.other = new_test_user(cls.env, "fm_http2", password="fm_http_pass_2", groups="base.group_user")
        cls.folder = cls.env["file.manager.folder"].with_user(cls.user).create({"name": "Root"})
        cls.child = cls.env["file.manager.folder"].with_user(cls.user).create({
            "name": "Child", "parent_id": cls.folder.id,
        })
        Document = cls.env["file.manager.document"].with_user(cls.user)
        cls.document = Document.create({"name": "a.txt", "folder_id": cls.folder.id})
        cls.document._fm_set_file(make_upload("a.txt", b"AAA"))
        cls.nested = Document.create({"name": "b.txt", "folder_id": cls.child.id})
        cls.nested._fm_set_file(make_upload("b.txt", b"BBB"))
        cls.unrelated = Document.create({"name": "secret.txt"})
        cls.unrelated._fm_set_file(make_upload("secret.txt", b"SECRET"))

    def test_upload_requires_login(self):
        response = self.url_open(
            "/file_manager/upload", data={}, files={"ufile": ("up.txt", b"x", "text/plain")},
            allow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 200)

    def test_zip_download_scoped_to_access(self):
        self.authenticate("fm_http2", "fm_http_pass_2")
        response = self.url_open(f"/file_manager/download?folder_ids={self.folder.id}")
        self.assertNotEqual(response.status_code, 200)
        self.authenticate("fm_http", "fm_http_pass_1")
        response = self.url_open(f"/file_manager/download?folder_ids={self.folder.id}")
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(archive.namelist()), {"Root/a.txt", "Root/Child/b.txt"})
        self.assertEqual(archive.read("Root/Child/b.txt"), b"BBB")

    def _share(self, **values):
        return self.env["file.manager.share"].with_user(self.user).create({"name": "Link", **values})

    def test_public_page_lists_only_shared(self):
        share = self._share(document_ids=[(6, 0, self.document.ids)])
        response = self.url_open(f"/file_manager/share/{share.token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("a.txt", response.text)
        self.assertNotIn("b.txt", response.text)
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(share.view_count, 1)

    def test_public_file_download(self):
        share = self._share(document_ids=[(6, 0, self.document.ids)])
        base = f"/file_manager/share/{share.token}/file"
        response = self.url_open(f"{base}/{self.document.id}")
        self.assertEqual(response.content, b"AAA")
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_token_only_reaches_its_files(self):
        share = self._share(document_ids=[(6, 0, self.document.ids)])
        base = f"/file_manager/share/{share.token}/file"
        self.assertEqual(self.url_open(f"{base}/{self.unrelated.id}").status_code, 404)
        self.assertEqual(self.url_open(f"{base}/{self.nested.id}").status_code, 404)

    def test_bad_token(self):
        self.assertEqual(self.url_open("/file_manager/share/not-a-token").status_code, 404)
        self.assertEqual(
            self.url_open(f"/file_manager/share/not-a-token/file/{self.document.id}").status_code, 404,
        )
        self.assertEqual(self.url_open("/file_manager/share/not-a-token/download").status_code, 404)

    def test_expired_link(self):
        yesterday = fields.Date.today() - timedelta(days=1)
        share = self._share(document_ids=[(6, 0, self.document.ids)], expiration_date=yesterday)
        self.assertEqual(self.url_open(f"/file_manager/share/{share.token}").status_code, 404)
        response = self.url_open(f"/file_manager/share/{share.token}/file/{self.document.id}")
        self.assertEqual(response.status_code, 404)

    def test_trashed_file_leaves_the_link(self):
        share = self._share(document_ids=[(6, 0, self.document.ids)])
        self.document.action_fm_trash()
        response = self.url_open(f"/file_manager/share/{share.token}/file/{self.document.id}")
        self.assertEqual(response.status_code, 404)

    def test_folder_link_includes_subfolders_and_zip(self):
        share = self._share(folder_id=self.folder.id)
        page = self.url_open(f"/file_manager/share/{share.token}")
        self.assertIn("a.txt", page.text)
        self.assertIn("b.txt", page.text)
        self.assertNotIn("secret.txt", page.text)
        response = self.url_open(f"/file_manager/share/{share.token}/download")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(archive.namelist()), {"Root/a.txt", "Root/Child/b.txt"})

    def test_public_html_never_inline_even_with_a_faked_type(self):
        fake = self.env["file.manager.document"].with_user(self.user).create({
            "name": "x.html", "folder_id": self.folder.id,
        })
        fake._fm_set_file(make_upload("x.html", b"<script>alert(1)</script>", "text/html"))
        fake.sudo().write({"mimetype": "image/png"})
        share = self._share(document_ids=[(6, 0, fake.ids)])
        response = self.url_open(f"/file_manager/share/{share.token}/file/{fake.id}?inline=1")
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_public_svg_never_inline(self):
        svg = self.env["file.manager.document"].with_user(self.user).create({
            "name": "x.svg", "folder_id": self.folder.id,
        })
        svg._fm_set_file(make_upload("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"))
        share = self._share(document_ids=[(6, 0, svg.ids)])
        response = self.url_open(f"/file_manager/share/{share.token}/file/{svg.id}?inline=1")
        self.assertIn("attachment", response.headers["Content-Disposition"])
