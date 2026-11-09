{
    "name": "Warranties",
    "summary": "Track warranties, receipts and claim links, sorted by how soon they expire",
    "description": """
Keep every warranty in one place: what it covers, when it starts and ends, where
it was bought, who holds it, a photo of the receipt and the link to file a claim.
The board sorts warranties into In Progress, Expiring in 30 Days and Expired, and
moves them along by itself as the days pass.
""",
    "version": "20.0.1.0.0",
    "category": "Productivity",
    "sequence": 45,
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    # "mail" adds the history panel (chatter) that records date changes and column moves.
    "depends": ["mail"],
    "data": [
        "security/ir.access.csv",
        "data/warranty_cron.xml",
        "views/warranty_views.xml",
        "views/warranty_menus.xml",
    ],
    "installable": True,
    "application": True,
}
