import { _t } from "@web/core/l10n/translation";

export const INTERNAL_DRAG_TYPE = "application/x-file-manager-items";

export const SECTIONS = [
    { id: "all", label: _t("All Files"), icon: "home" },
    { id: "mine", label: _t("My Files"), icon: "person" },
    { id: "favorites", label: _t("Favorites"), icon: "star" },
    { id: "recent", label: _t("Recent"), icon: "schedule" },
    { id: "shared", label: _t("Shared by Me"), icon: "share" },
    { id: "trash", label: _t("Trash"), icon: "delete" },
];

export const SORT_OPTIONS = [
    { id: "date_desc", label: _t("Newest first") },
    { id: "date_asc", label: _t("Oldest first") },
    { id: "name_asc", label: _t("Name, A to Z") },
    { id: "name_desc", label: _t("Name, Z to A") },
    { id: "size_desc", label: _t("Largest first") },
    { id: "size_asc", label: _t("Smallest first") },
];

export function formatSize(size) {
    if (!size) {
        return "";
    }
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = size;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit++;
    }
    return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

export function formatDate(serverDate) {
    if (!serverDate) {
        return "";
    }
    const date = new Date(`${serverDate.replace(" ", "T")}Z`);
    return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function folderAccessLabel(mode) {
    return {
        edit: _t("Everyone can edit"),
        view: _t("Everyone can view"),
        restricted: _t("Selected people only"),
    }[mode];
}

/** Read all the entries of a dropped directory; browsers return them in batches. */
function readAllEntries(reader) {
    return new Promise((resolve, reject) => {
        const entries = [];
        const readBatch = () => {
            reader.readEntries((batch) => {
                if (!batch.length) {
                    resolve(entries);
                    return;
                }
                entries.push(...batch);
                readBatch();
            }, reject);
        };
        readBatch();
    });
}

async function walkEntry(entry, path, result) {
    if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        result.push({ file, path });
    } else if (entry.isDirectory) {
        const children = await readAllEntries(entry.createReader());
        await Promise.all(children.map((child) => walkEntry(child, [...path, entry.name], result)));
    }
}

/**
 * Turn a drop event into a list of files with their folder path, so that
 * dropped folders keep their structure. The entries must be collected before
 * the first await: the browser empties the DataTransfer when the handler yields.
 *
 * @returns {Promise<{file: File, path: string[]}[]>}
 */
export async function filesFromDrop(dataTransfer) {
    const items = [...(dataTransfer.items || [])];
    const entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
    if (!entries.length) {
        return [...(dataTransfer.files || [])].map((file) => ({ file, path: [] }));
    }
    const result = [];
    await Promise.all(entries.map((entry) => walkEntry(entry, [], result)));
    return result;
}

/** Turn the files of a folder picker into files with their folder path. */
export function filesFromInput(fileList) {
    return [...fileList].map((file) => {
        const parts = (file.webkitRelativePath || "").split("/").filter(Boolean);
        return { file, path: parts.slice(0, -1) };
    });
}

/** Upload one file, reporting progress. Resolves with the result of the server. */
export function uploadFile(file, { folderId, documentId, onProgress }) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const data = new FormData();
        data.append("csrf_token", odoo.csrf_token);
        data.append("ufile", file);
        if (folderId) {
            data.append("folder_id", folderId);
        }
        if (documentId) {
            data.append("document_id", documentId);
        }
        xhr.open("POST", "/file_manager/upload");
        xhr.upload.addEventListener("progress", (ev) => {
            if (ev.lengthComputable) {
                onProgress?.(ev.loaded / ev.total);
            }
        });
        xhr.addEventListener("load", () => {
            let result;
            try {
                [result] = JSON.parse(xhr.responseText);
            } catch {
                result = { error: _t("The server refused this file.") };
            }
            if (xhr.status !== 200 || !result || result.error) {
                reject(new Error(result?.error || _t("The upload failed.")));
            } else {
                resolve(result);
            }
        });
        xhr.addEventListener("error", () => reject(new Error(_t("The connection was lost."))));
        xhr.send(data);
    });
}
