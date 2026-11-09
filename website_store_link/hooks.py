"""Swap the website's Shop tab for a Store tab pointing to the InkSoft store.

Website menus are copied for each website when they are created, so changing the
original Shop menu record would not reach the existing websites: every copy is
updated here instead, together with the original that future websites copy from.
"""

STORE_URL = "https://stores.inksoft.com/orlandorugby/shop/"
SHOP_URLS = ["/shop", "/shop/"]
STORE_LABEL = "Store"
SHOP_LABEL = "Shop"
SPANISH_LABELS = {STORE_LABEL: "Tienda", SHOP_LABEL: "Tienda"}


def _set_label(menus, label):
    if not menus:
        return
    menus.with_context(lang="en_US").write({"name": label})
    spanish = [code for code, _name in menus.env["res.lang"].get_installed() if code.startswith("es")]
    if spanish:
        # Odoo saves translations one record at a time.
        for menu in menus:
            menu.update_field_translations("name", dict.fromkeys(spanish, SPANISH_LABELS[label]))


def _store_menu_values(parent, website):
    last = max(parent.child_id.mapped("sequence"), default=0)
    return {
        "name": STORE_LABEL,
        "url": STORE_URL,
        "new_window": True,
        "parent_id": parent.id,
        "website_id": website.id if website else False,
        "sequence": last + 1,
    }


def _top_menu(website):
    # Looked up rather than read from website.menu_id, which is cached and is not
    # refreshed when a new website's menus are created in the same transaction.
    return website.env["website.menu"].search(
        [("website_id", "=", website.id), ("parent_id", "=", False)], order="id", limit=1,
    )


def post_init_hook(env):
    Menu = env["website.menu"]
    shop_menus = Menu.search([("url", "in", SHOP_URLS)])
    shop_menus.write({"url": STORE_URL, "new_window": True})
    _set_label(shop_menus, STORE_LABEL)

    # Websites that had no Shop tab still get a Store tab, at the end of their menu.
    new_menus = Menu
    for website in env["website"].search([]):
        top = _top_menu(website)
        if top and not Menu.search_count([("id", "child_of", top.id), ("url", "=", STORE_URL)]):
            new_menus |= Menu.create(_store_menu_values(top, website))
    template = env.ref("website.main_menu", raise_if_not_found=False)
    if template and not Menu.search_count([("parent_id", "=", template.id), ("url", "=", STORE_URL)]):
        new_menus |= Menu.create(_store_menu_values(template, False))
    _set_label(new_menus, STORE_LABEL)


def uninstall_hook(env):
    store_menus = env["website.menu"].search([("url", "=", STORE_URL)])
    eshop = env["ir.module.module"].search([("name", "=", "website_sale"), ("state", "=", "installed")])
    if eshop:
        store_menus.write({"url": SHOP_URLS[0], "new_window": False})
        _set_label(store_menus, SHOP_LABEL)
    else:
        # Without Odoo's eCommerce there is no Shop page to go back to.
        store_menus.unlink()
