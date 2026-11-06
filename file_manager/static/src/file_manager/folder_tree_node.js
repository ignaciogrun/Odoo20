import { Component, t, useProps } from "@odoo/owl";

/** One folder of the sidebar tree, drawing its open subfolders recursively. */
export class FolderTreeNode extends Component {
    static template = "file_manager.FolderTreeNode";
    props = useProps({
        folder: t.object(),
        depth: t.number(),
        childrenOf: t.function(),
        isExpanded: t.function(),
        isActive: t.function(),
        isDropTarget: t.function(),
        onOpen: t.function(),
        onToggle: t.function(),
        onDragOver: t.function(),
        onDragLeave: t.function(),
        onDrop: t.function(),
    });
}
FolderTreeNode.components = { FolderTreeNode };
