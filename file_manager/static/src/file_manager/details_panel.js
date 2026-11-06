import { Component, proxy, t, useEffect, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { folderAccessLabel, formatDate, formatSize } from "./file_manager_utils";

/** Right-hand panel: information about the selected file or folder. */
export class DetailsPanel extends Component {
    static template = "file_manager.DetailsPanel";
    props = useProps({
        selection: t.array(),
        allTags: t.array(),
        thumbnailUrl: t.function(),
        onClose: t.function(),
        onChanged: t.function(),
        onTagsChanged: t.function(),
        onUploadVersion: t.function(),
        onPreview: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.formatDate = formatDate;
        this.formatSize = formatSize;
        this.accessLabel = folderAccessLabel;
        this.state = proxy({ versions: [], newTag: "", description: "" });
        this.loadedKey = null;
        useEffect(() => {
            const item = this.props.selection.length === 1 ? this.props.selection[0] : null;
            // The dates only have a precision of one second, which a quick
            // upload or restore can stay within.
            const key = item
                ? `${item.key}:${item.write_date}:${item.checksum}:${item.version_count}`
                : null;
            if (key !== this.loadedKey) {
                this.loadedKey = key;
                this.loadItem(item);
            }
        });
    }

    async loadItem(item) {
        this.state.description = item?.description || "";
        this.state.versions = [];
        if (item?.kind === "document" && item.doc_type === "file") {
            const details = await this.orm.call("file.manager.document", "fm_get_details", [[item.id]]);
            this.state.versions = details.versions;
        }
    }

    tagsNotOnItem(item) {
        const usedIds = new Set(item.tags.map((tag) => tag.id));
        return this.props.allTags.filter((tag) => !usedIds.has(tag.id));
    }

    onTagKeydown(ev, item) {
        if (ev.key === "Enter") {
            ev.stopPropagation();
            this.addTag(item);
        }
    }

    async addTag(item) {
        const name = this.state.newTag.trim();
        if (!name) {
            return;
        }
        let tag = this.props.allTags.find((tag) => tag.name.toLowerCase() === name.toLowerCase());
        if (!tag) {
            const [id] = await this.orm.create("file.manager.tag", [{ name }]);
            tag = { id };
            await this.props.onTagsChanged();
        }
        await this.orm.write("file.manager.document", [item.id], { tag_ids: [[4, tag.id]] });
        this.state.newTag = "";
        await this.props.onChanged();
    }

    async removeTag(item, tag) {
        await this.orm.write("file.manager.document", [item.id], { tag_ids: [[3, tag.id]] });
        await this.props.onChanged();
    }

    async saveDescription(item) {
        if (this.state.description !== item.description) {
            await this.orm.write("file.manager.document", [item.id], {
                description: this.state.description,
            });
            await this.props.onChanged();
        }
    }

    async restoreVersion(item, version) {
        await this.orm.call("file.manager.document", "action_fm_restore_version", [[item.id], version.id]);
        await this.props.onChanged();
    }

    versionUrl(version) {
        return `/web/content/${version.attachment_id}?download=true`;
    }

    typeLabel(item) {
        return item.doc_type === "url" ? _t("Link") : item.mimetype || _t("File");
    }
}
