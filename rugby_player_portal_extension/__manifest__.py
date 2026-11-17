{
    "name": "Player Portal Extension",
    "summary": "Providing enhanced portal features for rugby players",
    "description": """
Adds a "Can we tag you on social media?" question to the portal's Account
Details page. Answering Yes shows a box for the person's handle (for example
@jane.doe). The answer and the handle are saved on the person's contact, so
staff can filter contacts by who can and can't be tagged, and copy the handle
when posting.
""",
    "version": "20.0.1.0.0",
    "category": "Website/Website",
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    # "portal" for the Account Details page; "mail" so the answer is kept in the
    # contact's history (who answered, and when).
    "depends": ["portal", "mail"],
    "data": [
        "views/res_partner_views.xml",
        "views/portal_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "rugby_player_portal_extension/static/src/scss/portal_social_tagging.scss",
        ],
    },
    "installable": True,
    "application": False,
}
