import json
import re

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSocialTaggingContact(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Jane Doe"})

    def test_not_answered_by_default(self):
        self.assertFalse(self.partner.social_tagging)
        self.assertFalse(self.partner.social_tagging_date)

    def test_handle_is_tidied(self):
        for typed, saved in [
            ("jane.doe", "@jane.doe"),
            ("  @jane_doe ", "@jane_doe"),
            ("@@jane", "@jane"),
            ("https://www.instagram.com/jane.doe/", "@jane.doe"),
            ("instagram.com/jane.doe?igsh=abc", "@jane.doe"),
            ("https://www.tiktok.com/@jane.doe", "@jane.doe"),
        ]:
            with self.subTest(typed=typed):
                self.partner.write({"social_tagging": "yes", "social_handle": typed})
                self.assertEqual(self.partner.social_handle, saved)

    def test_yes_needs_a_valid_handle(self):
        with self.assertRaises(ValidationError):
            self.partner.write({"social_tagging": "yes"})
        for bad in ("jane doe", "jane-doe!", "a" * 31):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.partner.write({"social_tagging": "yes", "social_handle": bad})

    def test_refusing_forgets_the_handle(self):
        self.partner.write({"social_tagging": "yes", "social_handle": "jane.doe"})
        self.partner.social_tagging = "no"
        self.assertFalse(self.partner.social_handle, "a handle that can't be used isn't kept")
        self.partner.write({"social_tagging": "no", "social_handle": "jane.doe"})
        self.assertFalse(self.partner.social_handle)

    def test_answer_date_follows_changes_only(self):
        self.partner.write({"social_tagging": "yes", "social_handle": "jane.doe"})
        self.assertTrue(self.partner.social_tagging_date)
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET social_tagging_date = '2020-01-01' WHERE id = %s", [self.partner.id])
        self.partner.invalidate_recordset()
        self.partner.write({"social_tagging": "yes", "social_handle": "jane.doe2"})
        self.assertEqual(str(self.partner.social_tagging_date), "2020-01-01 00:00:00", "same answer: date kept")
        self.partner.social_tagging = "no"
        self.assertNotEqual(str(self.partner.social_tagging_date), "2020-01-01 00:00:00")

    def test_several_contacts_at_once(self):
        other = self.env["res.partner"].create({"name": "John Roe", "social_tagging": "no"})
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET social_tagging_date = '2020-01-01' WHERE id = %s", [other.id])
        (self.partner | other).invalidate_recordset()
        (self.partner | other).write({"social_tagging": "no"})
        self.assertTrue(self.partner.social_tagging_date)
        self.assertEqual(str(other.social_tagging_date), "2020-01-01 00:00:00")

    def test_answer_is_kept_in_the_history(self):
        self.partner.write({"social_tagging": "yes", "social_handle": "jane.doe"})
        self.env.flush_all()
        self.env.cr.precommit.run()  # history is written just before the database commits
        self.partner.invalidate_recordset(["message_ids"])
        bodies = " ".join(str(message.body) for message in self.partner.message_ids)
        self.assertIn("Can Tag", bodies)
        self.assertIn("(Social Media Tagging)", bodies)
        self.assertNotIn("jane.doe", bodies, "the handle itself isn't copied into the history")

    def test_not_copied(self):
        self.partner.write({"social_tagging": "yes", "social_handle": "jane.doe"})
        copy = self.partner.copy()
        self.assertFalse(copy.social_tagging or copy.social_handle)

    def test_search_filters(self):
        yes = self.env["res.partner"].create({"name": "Yes Person", "social_tagging": "yes", "social_handle": "yes.person"})
        no = self.env["res.partner"].create({"name": "No Person", "social_tagging": "no"})
        Partner = self.env["res.partner"]
        self.assertIn(yes, Partner.search([("social_tagging", "=", "yes")]))
        self.assertIn(no, Partner.search([("social_tagging", "=", "no")]))
        self.assertIn(self.partner, Partner.search([("social_tagging", "=", False), ("is_company", "=", False)]))


@tagged("post_install", "-at_install")
class TestSocialTaggingPortal(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="player_social", groups="base.group_portal",
                                 name="Portal Player", email="player.social@example.com")
        cls.partner = cls.user.partner_id
        cls.partner.write({"street": "1 Main St", "city": "Orlando", "zip": "32801", "country_id": cls.env.ref("base.us").id,
                           "state_id": cls.env.ref("base.state_us_10").id, "phone": "+1 407 555 0100"})

    def submit(self, **values):
        data = {
            "csrf_token": self.csrf_token(), "partner_id": self.partner.id, "name": self.partner.name,
            "email": self.partner.email, "phone": self.partner.phone, "street": self.partner.street,
            "city": self.partner.city, "zip": self.partner.zip, "country_id": self.partner.country_id.id,
            "state_id": self.partner.state_id.id, "address_type": "billing", "use_delivery_as_billing": "true",
            "required_fields": "name,email", "callback": "/my", **values,
        }
        response = self.url_open("/my/address/submit", data=data)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.text)

    def test_account_page_asks_the_question(self):
        self.authenticate("player_social", "player_social")
        html = self.url_open("/my/account").text
        self.assertIn('name="social_tagging"', html)
        self.assertIn('name="social_handle"', html)
        self.assertIn("Can we tag you", self.url_open("/my").text, "a reminder until it's answered")

    def test_saying_yes_saves_the_handle(self):
        self.authenticate("player_social", "player_social")
        result = self.submit(social_tagging="yes", social_handle="  @Jane.Doe ")
        self.assertEqual(result.get("redirectUrl"), "/my", result)
        self.assertEqual((self.partner.social_tagging, self.partner.social_handle), ("yes", "@Jane.Doe"))
        html = self.url_open("/my/account").text
        self.assertRegex(html, r'id="o_social_tagging_yes"[^>]*checked')
        self.assertIn('value="Jane.Doe"', html)
        self.assertNotIn("Can we tag you", self.url_open("/my").text, "no reminder once answered")

    def test_yes_without_a_handle_is_refused(self):
        self.authenticate("player_social", "player_social")
        result = self.submit(social_tagging="yes", social_handle="")
        self.assertIn("social_handle", result["invalid_fields"])
        self.assertTrue(any("handle" in message for message in result["messages"]))
        result = self.submit(social_tagging="yes", social_handle="jane doe!")
        self.assertIn("social_handle", result["invalid_fields"])
        self.assertFalse(self.partner.social_tagging, "nothing saved")

    def test_saying_no(self):
        self.partner.write({"social_tagging": "yes", "social_handle": "jane"})
        self.authenticate("player_social", "player_social")
        result = self.submit(social_tagging="no", social_handle="jane")
        self.assertIn("redirectUrl", result)
        self.assertEqual((self.partner.social_tagging, self.partner.social_handle), ("no", False))

    def test_other_addresses_ignore_the_question(self):
        delivery = self.env["res.partner"].create({
            "name": "Delivery", "parent_id": self.partner.id, "type": "delivery", "street": "2 Side St",
            "city": "Orlando", "zip": "32801", "country_id": self.env.ref("base.us").id,
        })
        self.authenticate("player_social", "player_social")
        response = self.url_open("/my/address/submit", data={
            "csrf_token": self.csrf_token(), "partner_id": delivery.id, "name": "Delivery", "street": "2 Side St",
            "city": "Orlando", "zip": "32801", "country_id": self.env.ref("base.us").id,
            "state_id": self.env.ref("base.state_us_10").id, "address_type": "delivery",
            "social_tagging": "yes", "social_handle": "someone.else",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(delivery.social_tagging)
        self.assertFalse(self.partner.social_tagging)
