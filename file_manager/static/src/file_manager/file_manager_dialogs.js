import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

/** Ask for one text value: a name for a new folder, a new name, a link. */
export class TextPromptDialog extends Component {
    static template = "file_manager.TextPromptDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        title: t.string(),
        label: t.string(),
        value: t.string().optional(""),
        placeholder: t.string().optional(""),
        secondLabel: t.string().optional(),
        secondPlaceholder: t.string().optional(""),
        choices: t.array().optional(),
        choiceLabel: t.string().optional(),
        confirmLabel: t.string().optional(_t("Save")),
        onConfirm: t.function(),
    });

    setup() {
        this.state = proxy({
            value: this.props.value,
            second: "",
            choice: this.props.choices?.[0]?.id ?? false,
            busy: false,
        });
    }

    canConfirm() {
        const hasSecond = !this.props.secondLabel || this.state.second.trim();
        return this.state.value.trim() && hasSecond && !this.state.busy;
    }

    async confirm(ev) {
        ev?.preventDefault();
        if (!this.canConfirm()) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.onConfirm({
                value: this.state.value.trim(),
                second: this.state.second.trim(),
                choice: this.state.choice,
            });
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

/** Choose a destination folder. */
export class MoveDialog extends Component {
    static template = "file_manager.MoveDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        tree: t.array(),
        excludedIds: t.array().optional(() => []),
        allowRoot: t.boolean().optional(true),
        rootLabel: t.string().optional(_t("My Files (private)")),
        onConfirm: t.function(),
    });

    setup() {
        this.state = proxy({ target: this.props.allowRoot ? "root" : false, busy: false });
        this.rows = this.buildRows();
    }

    /** Flatten the tree into rows with a depth, leaving out what cannot be a target. */
    buildRows() {
        const byParent = {};
        for (const folder of this.props.tree) {
            (byParent[folder.parent_id || 0] ||= []).push(folder);
        }
        const rows = [];
        const visit = (parentId, depth) => {
            for (const folder of byParent[parentId] || []) {
                if (this.props.excludedIds.includes(folder.id)) {
                    continue;
                }
                rows.push({ id: folder.id, name: folder.name, depth, disabled: !folder.can_write });
                visit(folder.id, depth + 1);
            }
        };
        visit(0, 0);
        return rows;
    }

    async confirm() {
        if (this.state.target === false) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.onConfirm(this.state.target === "root" ? false : this.state.target);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}

/** Create, copy and revoke public links. */
export class ShareDialog extends Component {
    static template = "file_manager.ShareDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        documentIds: t.array().optional(() => []),
        folderId: t.or([t.number(), t.boolean()]).optional(false),
        defaultName: t.string().optional(""),
        onChange: t.function().optional(() => () => {}),
    });

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = proxy({
            links: [],
            name: this.props.defaultName,
            expiration: "",
            busy: false,
        });
        onWillStart(() => this.loadLinks());
    }

    async loadLinks() {
        this.state.links = await this.orm.call("file.manager.share", "fm_list_links", [], {
            document_ids: this.props.documentIds,
            folder_id: this.props.folderId,
        });
    }

    async createLink() {
        this.state.busy = true;
        try {
            const link = await this.orm.call("file.manager.share", "fm_create_link", [], {
                document_ids: this.props.documentIds,
                folder_id: this.props.folderId,
                name: this.state.name,
                expiration_date: this.state.expiration || false,
            });
            await this.loadLinks();
            this.props.onChange();
            await this.copy(link);
        } finally {
            this.state.busy = false;
        }
    }

    async copy(link) {
        try {
            await browser.navigator.clipboard.writeText(link.url);
            this.notification.add(_t("Link copied."), { type: "success" });
        } catch {
            // Clipboard access is refused on plain http; the link stays selectable.
            this.notification.add(_t("Copy the link from the box."), { type: "info" });
        }
    }

    async revoke(link) {
        await this.orm.unlink("file.manager.share", [link.id]);
        await this.loadLinks();
        this.props.onChange();
    }
}
