{
    "name": "Website: Store Link",
    "summary": "Replace the website's Shop tab with a Store tab that opens the club's InkSoft store",
    "description": """
Turns the Shop tab of every website into a Store tab that opens
https://stores.inksoft.com/orlandorugby/shop/ in a new browser tab. A website
without a Shop tab gets a Store tab added at the end of its menu. Uninstalling
the module puts the Shop tab back.
""",
    "version": "20.0.1.0.0",
    "category": "Website/Website",
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    # Only "website": the club sells through InkSoft, so Odoo's own eCommerce is optional.
    "depends": ["website"],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
