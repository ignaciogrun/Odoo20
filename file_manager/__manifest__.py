{
    "name": "Files",
    "summary": "File manager with folders, tags, previews, sharing links, versions and trash",
    "description": """
A file manager for Odoo Community: organize files in a folder tree, upload by
drag and drop, preview them in the browser, tag and search them, keep older
versions, control who can see each folder and share files through public links.
""",
    "version": "20.0.1.0.0",
    "category": "Productivity/Documents",
    "sequence": 40,
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    "depends": ["web", "mail"],
    "data": [
        "security/file_manager_security.xml",
        "security/ir.access.csv",
        "data/file_manager_data.xml",
        "views/file_manager_folder_views.xml",
        "views/file_manager_document_views.xml",
        "views/file_manager_tag_views.xml",
        "views/file_manager_share_views.xml",
        "views/file_manager_share_templates.xml",
        "views/file_manager_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "file_manager/static/src/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
