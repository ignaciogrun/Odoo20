from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.website_store_link.hooks import (
    SHOP_URLS, STORE_URL, _top_menu, post_init_hook, uninstall_hook,
)


def menus_of(website):
    return website.env["website.menu"].search([("id", "child_of", _top_menu(website).id)])


class TestStoreLink(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env["website.menu"]
        cls.has_eshop = bool(cls.env["ir.module.module"].search(
            [("name", "=", "website_sale"), ("state", "=", "installed")]
        ))

    def store_menus(self, website):
        return menus_of(website).filtered(lambda menu: menu.url == STORE_URL)

    def test_installed_state(self):
        self.assertFalse(self.Menu.search([("url", "in", SHOP_URLS)]))
        for website in self.env["website"].search([]):
            store = self.store_menus(website)
            self.assertEqual(len(store), 1, website.name)
            self.assertEqual(store.name, "Store")
            self.assertTrue(store.new_window)
            self.assertEqual(store.parent_id, _top_menu(website))

    def test_keeps_the_shop_position(self):
        if not self.has_eshop:
            self.skipTest("eCommerce not installed")
        website = self.env["website"].search([], limit=1)
        store = self.store_menus(website)
        self.assertEqual(store.sequence, 20)  # where eCommerce puts its Shop tab

    def test_running_again_adds_nothing(self):
        before = self.Menu.search_count([("url", "=", STORE_URL)])
        post_init_hook(self.env)
        self.assertEqual(self.Menu.search_count([("url", "=", STORE_URL)]), before)

    def test_new_website_gets_the_store_tab(self):
        website = self.env["website"].create({"name": "Second site"})
        store = self.store_menus(website)
        self.assertEqual(len(store), 1)
        self.assertEqual(store.name, "Store")
        self.assertTrue(store.new_window)

    def test_website_without_shop_gets_one_added_at_the_end(self):
        website = self.env["website"].create({"name": "Third site"})
        self.store_menus(website).unlink()
        last = max(_top_menu(website).child_id.mapped("sequence"))
        post_init_hook(self.env)
        store = self.store_menus(website)
        self.assertEqual(len(store), 1)
        self.assertGreater(store.sequence, last)

    def test_other_tabs_untouched(self):
        website = self.env["website"].search([], limit=1)
        home = menus_of(website).filtered(lambda menu: menu.url == "/")
        self.assertTrue(home)
        post_init_hook(self.env)
        self.assertEqual(home.url, "/")
        self.assertFalse(home.new_window)

    def test_spanish_label(self):
        # Two Shop menus at once, as on a real database: the website's own and the
        # original that new websites copy from.
        self.env["res.lang"]._activate_lang("es_419")
        website = self.env["website"].search([], limit=1)
        template = self.env.ref("website.main_menu")
        self.Menu.search([("url", "=", STORE_URL)]).unlink()
        shops = self.Menu.create([
            {"name": "Shop", "url": "/shop", "website_id": website.id, "parent_id": _top_menu(website).id},
            {"name": "Shop", "url": "/shop", "parent_id": template.id},
        ])
        post_init_hook(self.env)
        for shop in shops:
            self.assertEqual(shop.with_context(lang="en_US").name, "Store")
            self.assertEqual(shop.with_context(lang="es_419").name, "Tienda")
        uninstall_hook(self.env)
        if self.has_eshop:
            for shop in shops:
                self.assertEqual(shop.with_context(lang="en_US").name, "Shop")

    def test_uninstall_puts_the_shop_back(self):
        uninstall_hook(self.env)
        self.assertFalse(self.Menu.search([("url", "=", STORE_URL)]))
        if self.has_eshop:
            for website in self.env["website"].search([]):
                shop = menus_of(website).filtered(lambda menu: menu.url == "/shop")
                self.assertEqual(len(shop), 1)
                self.assertEqual(shop.name, "Shop")
                self.assertFalse(shop.new_window)


@tagged("post_install", "-at_install")
class TestStoreLinkPage(HttpCase):
    def test_header_shows_store_link(self):
        page = self.url_open("/")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn(f'href="{STORE_URL}"', html)
        self.assertNotIn('href="/shop"', html.split("</header>")[0])
        start = html.index(f'href="{STORE_URL}"')
        tag = html[html.rindex("<a", 0, start):html.index("</a>", start)]
        self.assertIn('target="_blank"', tag)
        self.assertIn("Store", tag)
