{
    "name": "JTO Home Menu",
    "summary": "App launcher home screen with an icon for every app, responsive on phones",
    "version": "20.0.1.0.0",
    "category": "Hidden/Tools",
    "author": "machanic16",
    "license": "LGPL-3",
    "depends": ["web"],
    "data": [
        "data/home_menu_action.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "jto_home_menu/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
