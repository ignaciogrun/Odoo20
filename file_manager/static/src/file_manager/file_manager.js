import { Component, onMounted, onWillStart, onWillUnmount, proxy, signal, useProps } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { FileModel } from "@web/core/file_viewer/file_model";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { checkFileSize } from "@web/core/utils/files";
import { useService } from "@web/core/utils/hooks";
import { debounce } from "@web/core/utils/timing";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

import { DetailsPanel } from "./details_panel";
import { MoveDialog, ShareDialog, TextPromptDialog } from "./file_manager_dialogs";
import {
    filesFromDrop,
    filesFromInput,
    formatDate,
    formatSize,
    INTERNAL_DRAG_TYPE,
    SECTIONS,
    SORT_OPTIONS,
    uploadFile,
} from "./file_manager_utils";
import { FolderTreeNode } from "./folder_tree_node";

const PAGE_SIZE = 80;
const UPLOAD_CONCURRENCY = 3;
const DOCUMENT_MODEL = "file.manager.document";
const FOLDER_MODEL = "file.manager.folder";
const VIEW_MODE_KEY = "file_manager.view_mode";
const THUMBNAIL_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"];

function readPreference(key, fallback) {
    try {
        return browser.localStorage.getItem(key) || fallback;
    } catch {
        return fallback;
    }
}

function writePreference(key, value) {
    try {
        browser.localStorage.setItem(key, value);
    } catch {
        // Storage can be blocked; the choice just isn't remembered.
    }
}

export class FileManager extends Component {
    static template = "file_manager.FileManager";
    static components = { DetailsPanel, Dropdown, DropdownItem, FolderTreeNode };
    props = useProps(standardActionServiceProps);

    fileInputRef = signal.ref();
    folderInputRef = signal.ref();
    versionInputRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.ui = useService("ui");
        this.fileViewer = useFileViewer();
        this.sections = SECTIONS;
        this.sortOptions = SORT_OPTIONS;
        this.formatSize = formatSize;
        this.formatDate = formatDate;

        const initialFolderId = this.props.resId || this.props.state?.resId || false;
        this.state = proxy({
            section: initialFolderId ? "folder" : "all",
            folderId: initialFolderId,
            currentFolder: null,
            breadcrumbs: [],
            folders: [],
            documents: [],
            total: 0,
            tree: [],
            tags: [],
            activeTagIds: [],
            expanded: {},
            search: "",
            sort: "date_desc",
            viewMode: readPreference(VIEW_MODE_KEY, "grid"),
            selected: {},
            loading: true,
            uploads: [],
            detailsOpen: !this.isSmallScreen(),
            sidebarOpen: false,
            fileDragActive: false,
            dropFolderId: false,
            contextMenu: null,
        });
        this.anchorKey = null;
        this.loadToken = 0;
        this.versionTarget = null;
        this.fileDragDepth = 0;
        this.reloadSoon = debounce(() => this.reload(), 300);
        this.searchSoon = debounce(() => this.loadItems(), 250);

        onWillStart(async () => {
            await Promise.all([this.loadTree(), this.loadTags()]);
            await this.loadItems().catch(() => this.openSection("all"));
        });
        const closeMenu = () => {
            this.state.contextMenu = null;
        };
        onMounted(() => browser.addEventListener("click", closeMenu));
        onWillUnmount(() => browser.removeEventListener("click", closeMenu));
    }

    /** Keeps clicks on the toolbars from reaching the area that clears the selection. */
    noop() {}

    isSmallScreen() {
        return browser.matchMedia?.("(max-width: 767.98px)").matches;
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    async loadItems({ append = false } = {}) {
        const token = ++this.loadToken;
        this.state.loading = !append;
        const data = await this.orm.call(DOCUMENT_MODEL, "fm_get_explorer_data", [], {
            section: this.state.section,
            folder_id: this.state.folderId,
            search: this.state.search,
            tag_ids: this.state.activeTagIds,
            sort: this.state.sort,
            limit: PAGE_SIZE,
            offset: append ? this.state.documents.length : 0,
        });
        if (token !== this.loadToken) {
            return; // A newer request is on its way: this answer is out of date.
        }
        this.state.documents = append ? [...this.state.documents, ...data.documents] : data.documents;
        if (!append) {
            this.state.folders = data.folders;
        }
        this.state.total = data.total;
        this.state.breadcrumbs = data.breadcrumbs;
        this.state.currentFolder = data.current_folder;
        this.state.loading = false;
        this.pruneSelection();
    }

    async loadTree() {
        this.state.tree = await this.orm.call(FOLDER_MODEL, "fm_get_tree", []);
    }

    async loadTags() {
        this.state.tags = await this.orm.searchRead("file.manager.tag", [], ["name", "color"]);
    }

    async reload() {
        await Promise.all([this.loadItems(), this.loadTree()]);
    }

    pruneSelection() {
        const keys = new Set(this.allItems().map((item) => item.key));
        for (const key of Object.keys(this.state.selected)) {
            if (!keys.has(key)) {
                delete this.state.selected[key];
            }
        }
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    openSection(section) {
        this.state.section = section;
        this.state.folderId = false;
        this.state.search = "";
        this.state.sidebarOpen = false;
        this.clearSelection();
        this.props.updateActionState?.({ resId: false });
        return this.loadItems();
    }

    openFolder(folder) {
        this.state.section = "folder";
        this.state.folderId = folder.id;
        this.state.search = "";
        this.state.sidebarOpen = false;
        this.state.expanded[folder.id] = true;
        this.clearSelection();
        this.props.updateActionState?.({ resId: folder.id });
        return this.loadItems();
    }

    toggleExpanded(folderId) {
        this.state.expanded[folderId] = !this.state.expanded[folderId];
    }

    onSearchInput() {
        this.searchSoon();
    }

    setSort(sort) {
        this.state.sort = sort;
        return this.loadItems();
    }

    setViewMode(mode) {
        this.state.viewMode = mode;
        writePreference(VIEW_MODE_KEY, mode);
    }

    toggleTagFilter(tag) {
        const tags = this.state.activeTagIds;
        this.state.activeTagIds = tags.includes(tag.id)
            ? tags.filter((id) => id !== tag.id)
            : [...tags, tag.id];
        return this.loadItems();
    }

    childrenOf = (parentId) => this.state.tree.filter((folder) => folder.parent_id === parentId);
    isExpanded = (folderId) => !!this.state.expanded[folderId];
    isActiveFolder = (folderId) => this.state.section === "folder" && this.state.folderId === folderId;
    isDropTarget = (folderId) => this.state.dropFolderId === folderId;
    onTreeOpen = (folder) => this.openFolder(folder);
    onTreeToggle = (folderId) => this.toggleExpanded(folderId);
    onFolderDragOver = (ev, folder) => this.handleFolderDragOver(ev, folder);
    onFolderDragLeave = (ev, folder) => this.handleFolderDragLeave(ev, folder);
    onFolderDrop = (ev, folder) => this.handleFolderDrop(ev, folder);

    title() {
        if (this.state.section === "folder") {
            return this.state.currentFolder?.name || "";
        }
        return this.sections.find((section) => section.id === this.state.section).label;
    }

    isTrash() {
        return this.state.section === "trash";
    }

    /** Where new files go, and whether the user may add them there. */
    canAddHere() {
        if (this.isTrash()) {
            return false;
        }
        return this.state.section !== "folder" || !!this.state.currentFolder?.can_write;
    }

    uploadTargetLabel() {
        return this.state.section === "folder" ? this.state.currentFolder?.name : _t("My Files");
    }

    // ------------------------------------------------------------------
    // Selection
    // ------------------------------------------------------------------

    allItems() {
        return [...this.state.folders, ...this.state.documents];
    }

    selectedItems() {
        return this.allItems().filter((item) => this.state.selected[item.key]);
    }

    isSelected(item) {
        return !!this.state.selected[item.key];
    }

    clearSelection() {
        this.state.selected = {};
        this.anchorKey = null;
    }

    selectOnly(item) {
        this.state.selected = { [item.key]: true };
        this.anchorKey = item.key;
    }

    toggleSelected(item) {
        if (this.state.selected[item.key]) {
            delete this.state.selected[item.key];
        } else {
            this.state.selected[item.key] = true;
        }
        this.anchorKey = item.key;
    }

    selectRange(item) {
        const items = this.allItems();
        const from = items.findIndex((other) => other.key === this.anchorKey);
        const to = items.findIndex((other) => other.key === item.key);
        if (from < 0) {
            this.selectOnly(item);
            return;
        }
        const selected = {};
        for (const other of items.slice(Math.min(from, to), Math.max(from, to) + 1)) {
            selected[other.key] = true;
        }
        this.state.selected = selected;
    }

    onItemClick(ev, item) {
        ev.stopPropagation();
        this.state.contextMenu = null;
        if (ev.shiftKey) {
            this.selectRange(item);
        } else if (ev.metaKey || ev.ctrlKey) {
            this.toggleSelected(item);
        } else if (this.isSmallScreen()) {
            // Phones have no double click: a tap opens, the checkbox selects.
            this.openItem(item);
        } else {
            this.selectOnly(item);
        }
    }

    onCheckboxClick(ev, item) {
        ev.stopPropagation();
        this.toggleSelected(item);
    }

    onItemContextMenu(ev, item) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!this.isSelected(item)) {
            this.selectOnly(item);
        }
        const rootRect = ev.currentTarget.closest(".o_file_manager").getBoundingClientRect();
        this.state.contextMenu = {
            x: Math.min(ev.clientX - rootRect.left, rootRect.width - 230),
            y: Math.min(ev.clientY - rootRect.top, rootRect.height - 340),
        };
    }

    onKeydown(ev) {
        if (ev.target.closest("input, textarea, select, .modal, .o-dropdown--menu")) {
            return;
        }
        const selection = this.selectedItems();
        if (ev.key === "Escape") {
            this.clearSelection();
            this.state.contextMenu = null;
        } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "a") {
            ev.preventDefault();
            this.state.selected = Object.fromEntries(this.allItems().map((item) => [item.key, true]));
        } else if (!selection.length) {
            return;
        } else if (ev.key === "Enter" && selection.length === 1) {
            this.openItem(selection[0]);
        } else if (ev.key === "F2" && selection.length === 1 && selection[0].can_write && !this.isTrash()) {
            this.rename(selection[0]);
        } else if (ev.key === "Delete" || (ev.key === "Backspace" && ev.metaKey)) {
            if (!this.isTrash() && selection.every((item) => item.can_write)) {
                this.moveToTrash(selection);
            }
        }
    }

    // ------------------------------------------------------------------
    // Showing items
    // ------------------------------------------------------------------

    thumbnailUrl = (item, size = 400) => {
        if (item.kind !== "document" || !item.attachment_id || !THUMBNAIL_TYPES.includes(item.mimetype)) {
            return false;
        }
        return `/web/image/${item.attachment_id}/${size}x${size}?unique=${item.checksum}`;
    };

    makeFile(document) {
        return Object.assign(new FileModel(), {
            id: document.attachment_id,
            name: document.name,
            mimetype: document.mimetype,
            checksum: document.checksum,
        });
    }

    openItem(item) {
        if (this.isTrash() && item.kind === "folder") {
            return;
        }
        if (item.kind === "folder") {
            this.openFolder(item);
        } else {
            this.previewDocument(item);
        }
    }

    previewDocument = (document) => {
        if (document.doc_type === "url") {
            browser.open(document.url, "_blank", "noopener,noreferrer");
            return;
        }
        if (!document.attachment_id) {
            return;
        }
        const files = this.state.documents
            .filter((other) => other.attachment_id)
            .map((other) => ({ other, file: this.makeFile(other) }));
        const current = files.find(({ other }) => other.id === document.id).file;
        if (current.isViewable) {
            this.fileViewer.open(current, files.map(({ file }) => file));
        } else {
            this.download([document]);
        }
    };

    // ------------------------------------------------------------------
    // Actions on the selection
    // ------------------------------------------------------------------

    /** The actions offered for the current selection, shared by toolbar and menu. */
    availableActions() {
        const selection = this.selectedItems();
        if (!selection.length) {
            return [];
        }
        const single = selection.length === 1 ? selection[0] : null;
        const documents = selection.filter((item) => item.kind === "document");
        const onlyDocuments = documents.length === selection.length;
        const canWrite = selection.every((item) => item.can_write);
        if (this.isTrash()) {
            if (!canWrite) {
                return [];
            }
            return [
                { id: "restore", label: _t("Restore"), icon: "history", run: () => this.restore(selection) },
                {
                    id: "delete",
                    label: _t("Delete forever"),
                    icon: "delete",
                    danger: true,
                    run: () => this.confirmDeleteForever(selection),
                },
            ];
        }
        const actions = [];
        if (single) {
            actions.push({
                id: "open",
                label: single.kind === "folder" ? _t("Open") : _t("Preview"),
                icon: single.kind === "folder" ? "folder_open" : "visibility",
                run: () => this.openItem(single),
            });
        }
        if (selection.some((item) => item.kind === "folder" || item.attachment_id)) {
            actions.push({
                id: "download",
                label: _t("Download"),
                icon: "download",
                run: () => this.download(selection),
            });
        }
        if (canWrite && (onlyDocuments || single)) {
            actions.push({ id: "share", label: _t("Share"), icon: "share", run: () => this.share(selection) });
        }
        if (canWrite) {
            actions.push({ id: "move", label: _t("Move"), icon: "drive_file_move", run: () => this.move(selection) });
        }
        if (single && canWrite) {
            actions.push({ id: "rename", label: _t("Rename"), icon: "edit", run: () => this.rename(single) });
        }
        if (onlyDocuments) {
            const allFavorite = documents.every((document) => document.is_favorite);
            actions.push({
                id: "favorite",
                label: allFavorite ? _t("Remove from favorites") : _t("Add to favorites"),
                icon: allFavorite ? "star_f" : "star",
                run: () => this.toggleFavorite(documents.filter((d) => d.is_favorite === allFavorite)),
            });
            if (canWrite) {
                actions.push({
                    id: "duplicate",
                    label: _t("Duplicate"),
                    icon: "content_copy",
                    run: () => this.duplicate(documents),
                });
            }
        }
        if (single?.kind === "document" && canWrite) {
            actions.push({
                id: "lock",
                label: single.locked_by ? _t("Unlock") : _t("Lock"),
                icon: single.locked_by ? "lock_open" : "lock",
                run: () => this.toggleLock(single),
            });
        }
        if (single?.kind === "document") {
            actions.push({
                id: "activity",
                label: _t("Comments and activities"),
                icon: "open_in_new",
                run: () => this.openFullForm(single),
            });
        }
        if (single?.kind === "folder" && single.can_manage) {
            actions.push({
                id: "settings",
                label: _t("Access and settings"),
                icon: "settings",
                run: () => this.editFolder(single),
            });
        }
        if (canWrite) {
            actions.push({
                id: "trash",
                label: _t("Move to trash"),
                icon: "delete",
                danger: true,
                run: () => this.moveToTrash(selection),
            });
        }
        return actions;
    }

    /** Actions with an icon button on the toolbar; the rest go under "More". */
    toolbarActions() {
        const primary = ["download", "share", "move", "rename", "restore", "delete", "trash"];
        return this.availableActions().filter((action) => primary.includes(action.id));
    }

    moreActions() {
        const primary = ["download", "share", "move", "rename", "restore", "delete", "trash"];
        return this.availableActions().filter((action) => !primary.includes(action.id));
    }

    runMenuAction(action) {
        this.state.contextMenu = null;
        action.run();
    }

    splitSelection(items) {
        return {
            documentIds: items.filter((item) => item.kind === "document").map((item) => item.id),
            folderIds: items.filter((item) => item.kind === "folder").map((item) => item.id),
        };
    }

    download(items) {
        const single = items.length === 1 ? items[0] : null;
        let url;
        if (single?.kind === "document") {
            if (single.doc_type === "url") {
                browser.open(single.url, "_blank", "noopener,noreferrer");
                return;
            }
            url = `/web/content/${single.attachment_id}?download=true`;
        } else {
            const { documentIds, folderIds } = this.splitSelection(items);
            url = `/file_manager/download?document_ids=${documentIds.join(",")}&folder_ids=${folderIds.join(",")}`;
        }
        const link = document.createElement("a");
        link.href = url;
        link.download = "";
        document.body.append(link);
        link.click();
        link.remove();
    }

    share(items) {
        const { documentIds, folderIds } = this.splitSelection(items);
        this.dialog.add(ShareDialog, {
            documentIds,
            folderId: folderIds[0] || false,
            defaultName: items.length === 1 ? items[0].name : "",
            onChange: () => this.loadItems(),
        });
    }

    /** Folders that cannot receive these items: the moved folders and what they contain. */
    excludedMoveTargets(folderIds) {
        const excluded = new Set(folderIds);
        let grew = true;
        while (grew) {
            grew = false;
            for (const folder of this.state.tree) {
                if (excluded.has(folder.parent_id) && !excluded.has(folder.id)) {
                    excluded.add(folder.id);
                    grew = true;
                }
            }
        }
        return [...excluded];
    }

    move(items) {
        const { folderIds } = this.splitSelection(items);
        this.dialog.add(MoveDialog, {
            tree: this.state.tree,
            excludedIds: this.excludedMoveTargets(folderIds),
            rootLabel: folderIds.length ? _t("Top level") : _t("My Files (private)"),
            onConfirm: (targetId) => this.moveItems(items, targetId),
        });
    }

    async moveItems(items, targetId) {
        const { documentIds, folderIds } = this.splitSelection(items);
        if (documentIds.length) {
            await this.orm.call(DOCUMENT_MODEL, "action_fm_move", [documentIds, targetId]);
        }
        if (folderIds.length) {
            await this.orm.call(FOLDER_MODEL, "action_fm_move", [folderIds, targetId]);
        }
        this.clearSelection();
        await this.reload();
    }

    rename(item) {
        const model = item.kind === "folder" ? FOLDER_MODEL : DOCUMENT_MODEL;
        this.dialog.add(TextPromptDialog, {
            title: _t("Rename"),
            label: _t("New name"),
            value: item.name,
            onConfirm: async ({ value }) => {
                await this.orm.write(model, [item.id], { name: value });
                await this.reload();
            },
        });
    }

    async toggleFavorite(documents) {
        await this.orm.call(DOCUMENT_MODEL, "action_fm_toggle_favorite", [documents.map((d) => d.id)]);
        await this.loadItems();
    }

    async toggleLock(document) {
        await this.orm.call(DOCUMENT_MODEL, "action_fm_toggle_lock", [[document.id]]);
        await this.loadItems();
    }

    async duplicate(documents) {
        await this.orm.call(DOCUMENT_MODEL, "action_fm_duplicate", [documents.map((d) => d.id)]);
        await this.reload();
    }

    openFullForm(document) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: DOCUMENT_MODEL,
            res_id: document.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    editFolder(folder) {
        this.dialog.add(FormViewDialog, {
            resModel: FOLDER_MODEL,
            resId: folder.id,
            title: _t("Folder settings"),
            onRecordSaved: () => this.reload(),
        });
    }

    async moveToTrash(items) {
        const { documentIds, folderIds } = this.splitSelection(items);
        if (documentIds.length) {
            await this.orm.call(DOCUMENT_MODEL, "action_fm_trash", [documentIds]);
        }
        if (folderIds.length) {
            await this.orm.call(FOLDER_MODEL, "action_fm_trash", [folderIds]);
        }
        this.clearSelection();
        const leavingFolder = folderIds.includes(this.state.folderId);
        await (leavingFolder ? this.openSection("all") : this.reload());
        await this.loadTree();
        const close = this.notification.add(
            items.length === 1
                ? _t("“%s” moved to the trash.", items[0].name)
                : _t("%s items moved to the trash.", items.length),
            {
                type: "success",
                buttons: [
                    {
                        name: _t("Undo"),
                        onClick: async () => {
                            close();
                            await this.restore(items);
                        },
                    },
                ],
            }
        );
    }

    async restore(items) {
        const { documentIds, folderIds } = this.splitSelection(items);
        if (folderIds.length) {
            await this.orm.call(FOLDER_MODEL, "action_fm_restore", [folderIds]);
        }
        if (documentIds.length) {
            await this.orm.call(DOCUMENT_MODEL, "action_fm_restore", [documentIds]);
        }
        this.clearSelection();
        await this.reload();
    }

    confirmDeleteForever(items) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete forever?"),
            body:
                items.length === 1
                    ? _t("“%s” and everything in it will be permanently deleted. This cannot be undone.", items[0].name)
                    : _t("%s items and everything in them will be permanently deleted. This cannot be undone.", items.length),
            confirmLabel: _t("Delete forever"),
            confirmClass: "btn-danger",
            cancelLabel: _t("Cancel"),
            cancel: () => {},
            confirm: async () => {
                const { documentIds, folderIds } = this.splitSelection(items);
                if (documentIds.length) {
                    await this.orm.call(DOCUMENT_MODEL, "action_fm_delete_forever", [documentIds]);
                }
                if (folderIds.length) {
                    await this.orm.call(FOLDER_MODEL, "action_fm_delete_forever", [folderIds]);
                }
                this.clearSelection();
                await this.reload();
            },
        });
    }

    // ------------------------------------------------------------------
    // Creating
    // ------------------------------------------------------------------

    newFolder() {
        const parentId = this.state.section === "folder" ? this.state.folderId : false;
        this.dialog.add(TextPromptDialog, {
            title: _t("New folder"),
            label: _t("Folder name"),
            placeholder: _t("e.g. Contracts"),
            confirmLabel: _t("Create"),
            choiceLabel: parentId ? undefined : _t("Who can use it?"),
            choices: parentId
                ? undefined
                : [
                      { id: "restricted", label: _t("Only me (add people later)") },
                      { id: "edit", label: _t("Everyone can edit") },
                      { id: "view", label: _t("Everyone can view, only I can edit") },
                  ],
            onConfirm: async ({ value, choice }) => {
                const values = { name: value, parent_id: parentId };
                if (!parentId) {
                    values.access_mode = choice;
                }
                await this.orm.create(FOLDER_MODEL, [values]);
                if (parentId) {
                    this.state.expanded[parentId] = true;
                }
                await this.reload();
            },
        });
    }

    newLink() {
        const folderId = this.state.section === "folder" ? this.state.folderId : false;
        this.dialog.add(TextPromptDialog, {
            title: _t("Add a link"),
            label: _t("Name"),
            placeholder: _t("e.g. Company handbook"),
            secondLabel: _t("Web address"),
            secondPlaceholder: "https://",
            confirmLabel: _t("Add"),
            onConfirm: async ({ value, second }) => {
                await this.orm.create(DOCUMENT_MODEL, [
                    { name: value, doc_type: "url", url: second, folder_id: folderId },
                ]);
                await this.reload();
            },
        });
    }

    // ------------------------------------------------------------------
    // Uploading
    // ------------------------------------------------------------------

    pickFiles() {
        this.fileInputRef().click();
    }

    pickFolder() {
        this.folderInputRef().click();
    }

    onFilesPicked(ev) {
        const entries = [...ev.target.files].map((file) => ({ file, path: [] }));
        ev.target.value = "";
        return this.uploadEntries(entries, this.defaultUploadFolder());
    }

    onFolderPicked(ev) {
        const entries = filesFromInput(ev.target.files);
        ev.target.value = "";
        return this.uploadEntries(entries, this.defaultUploadFolder());
    }

    defaultUploadFolder() {
        return this.state.section === "folder" ? this.state.folderId : false;
    }

    onUploadVersion = (document) => {
        this.versionTarget = document;
        this.versionInputRef().click();
    };

    async onVersionPicked(ev) {
        const [file] = ev.target.files;
        ev.target.value = "";
        if (!file || !this.versionTarget || !checkFileSize(file.size, this.notification)) {
            return;
        }
        const upload = this.addUpload(file);
        try {
            await uploadFile(file, {
                documentId: this.versionTarget.id,
                onProgress: (progress) => (upload.progress = progress),
            });
            this.finishUpload(upload);
        } catch (error) {
            this.failUpload(upload, error);
        }
        await this.loadItems();
    }

    addUpload(file) {
        const upload = proxy({
            id: `${file.name}-${Math.random()}`,
            name: file.name,
            progress: 0,
            error: false,
        });
        this.state.uploads.push(upload);
        return upload;
    }

    finishUpload(upload) {
        this.state.uploads = this.state.uploads.filter((other) => other.id !== upload.id);
    }

    failUpload(upload, error) {
        upload.error = error.message || _t("The upload failed.");
    }

    dismissUpload(upload) {
        this.finishUpload(upload);
    }

    /**
     * Upload files, creating the subfolders of dropped folders first.
     *
     * @param {{file: File, path: string[]}[]} entries
     * @param {number|false} baseFolderId
     */
    async uploadEntries(entries, baseFolderId) {
        const accepted = entries.filter(({ file }) => checkFileSize(file.size, this.notification));
        if (!accepted.length) {
            return;
        }
        // One at a time, so that two files of the same folder don't create it twice.
        const folderIds = new Map();
        for (const { path } of accepted) {
            const key = path.join("/");
            if (path.length && !folderIds.has(key)) {
                folderIds.set(
                    key,
                    await this.orm.call(FOLDER_MODEL, "fm_ensure_path", [baseFolderId, path])
                );
            }
        }
        if (folderIds.size) {
            await this.loadTree();
        }
        const queue = accepted.map(({ file, path }) => ({
            file,
            folderId: path.length ? folderIds.get(path.join("/")) : baseFolderId,
            upload: this.addUpload(file),
        }));
        const worker = async () => {
            for (let job = queue.shift(); job; job = queue.shift()) {
                const { file, folderId, upload } = job;
                try {
                    await uploadFile(file, {
                        folderId,
                        onProgress: (progress) => (upload.progress = progress),
                    });
                    this.finishUpload(upload);
                    this.reloadSoon();
                } catch (error) {
                    this.failUpload(upload, error);
                }
            }
        };
        await Promise.all(Array.from({ length: UPLOAD_CONCURRENCY }, worker));
        await this.reload();
    }

    // ------------------------------------------------------------------
    // Drag and drop
    // ------------------------------------------------------------------

    onItemDragStart(ev, item) {
        if (!this.isSelected(item)) {
            this.selectOnly(item);
        }
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData(INTERNAL_DRAG_TYPE, "1");
        ev.dataTransfer.setData("text/plain", item.name);
    }

    isFileDrag(ev) {
        return ev.dataTransfer?.types.includes("Files");
    }

    isInternalDrag(ev) {
        return ev.dataTransfer?.types.includes(INTERNAL_DRAG_TYPE);
    }

    onMainDragEnter(ev) {
        if (this.isFileDrag(ev) && this.canAddHere()) {
            this.fileDragDepth++;
            this.state.fileDragActive = true;
        }
    }

    onMainDragLeave(ev) {
        if (this.isFileDrag(ev) && this.state.fileDragActive) {
            this.fileDragDepth = Math.max(0, this.fileDragDepth - 1);
            if (!this.fileDragDepth) {
                this.state.fileDragActive = false;
            }
        }
    }

    onMainDragOver(ev) {
        if (this.isFileDrag(ev) && this.canAddHere()) {
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "copy";
        }
    }

    async onMainDrop(ev) {
        if (!this.isFileDrag(ev)) {
            return;
        }
        ev.preventDefault();
        this.fileDragDepth = 0;
        this.state.fileDragActive = false;
        if (this.canAddHere()) {
            await this.uploadEntries(await filesFromDrop(ev.dataTransfer), this.defaultUploadFolder());
        }
    }

    canDropOnFolder(folder) {
        return folder.can_write && !this.isTrash();
    }

    handleFolderDragOver(ev, folder) {
        const valid = this.isFileDrag(ev) || (this.isInternalDrag(ev) && !this.isDraggingInto(folder));
        if (valid && this.canDropOnFolder(folder)) {
            ev.preventDefault();
            ev.stopPropagation();
            ev.dataTransfer.dropEffect = this.isFileDrag(ev) ? "copy" : "move";
            this.state.dropFolderId = folder.id;
        }
    }

    handleFolderDragLeave(ev, folder) {
        if (!ev.currentTarget.contains(ev.relatedTarget) && this.state.dropFolderId === folder.id) {
            this.state.dropFolderId = false;
        }
    }

    /** A folder cannot be dropped on itself or on anything inside it. */
    isDraggingInto(folder) {
        const { folderIds } = this.splitSelection(this.selectedItems());
        return this.excludedMoveTargets(folderIds).includes(folder.id);
    }

    async handleFolderDrop(ev, folder) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.dropFolderId = false;
        this.fileDragDepth = 0;
        this.state.fileDragActive = false;
        if (!this.canDropOnFolder(folder)) {
            return;
        }
        if (this.isFileDrag(ev)) {
            await this.uploadEntries(await filesFromDrop(ev.dataTransfer), folder.id);
        } else if (this.isInternalDrag(ev) && !this.isDraggingInto(folder)) {
            await this.moveItems(this.selectedItems(), folder.id);
        }
    }

    onFolderCardDragOver(ev, item) {
        if (item.kind === "folder") {
            this.handleFolderDragOver(ev, item);
        }
    }

    onFolderCardDragLeave(ev, item) {
        if (item.kind === "folder") {
            this.handleFolderDragLeave(ev, item);
        }
    }

    onFolderCardDrop(ev, item) {
        if (item.kind === "folder") {
            this.handleFolderDrop(ev, item);
        }
    }

    onDetailsChanged = () => this.loadItems();
    onTagsChanged = () => this.loadTags();
    onDetailsClose = () => {
        this.state.detailsOpen = false;
    };
}

registry.category("actions").add("file_manager.main", FileManager);
