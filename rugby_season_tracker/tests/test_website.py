import re
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestTeamPages(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        website = cls.env.ref("base.default_website")
        cls.parent_menu = cls.env["website.menu"].create({
            "name": "Schedule & Results", "url": "#", "parent_id": website.menu_id.id, "website_id": website.id,
        })
        cls.team = cls.env["rugby.team"].create({
            "name": "Test Side A", "is_published": True, "website_menu_parent_id": cls.parent_menu.id,
        })
        other_team = cls.env["rugby.team"].create({"name": "Test Side W", "is_published": True})
        today = fields.Date.today()
        cls.current = cls.env["rugby.season"].create({
            "name": "Fall Now", "team_id": cls.team.id,
            "date_start": today - timedelta(days=60), "date_end": today + timedelta(days=60),
        })
        cls.old = cls.env["rugby.season"].create({
            "name": "Spring Before", "team_id": cls.team.id,
            "date_start": today - timedelta(days=300), "date_end": today - timedelta(days=200),
        })
        women_season = cls.env["rugby.season"].create({"name": "Fall Now", "team_id": other_team.id})
        now = fields.Datetime.now()

        def match(opponent, days, season=cls.current, published=True, **values):
            record = cls.env["rugby.match"].create({
                "season_id": season.id, "opponent": opponent, "kickoff": now + timedelta(days=days), **values,
            })
            record.show_result = published
            return record

        cls.upcoming = match("Ocala Upcoming", 5)
        match("Secret Unpublished", 6, published=False)
        match("Rained Out", 7).action_cancel()
        cls.win = match("Tampa Played", -10, score_for=29, score_against=17,
                        recording_link='<p><a href="https://www.youtube.com/watch?v=abc">Full match</a></p>')
        cls.away = match("Jacksonville Away", -20, home_away="away", score_for=12, score_against=24)
        hidden = match("Hidden Unpublished", -30, published=False, score_for=50, score_against=0)
        cls.old_match = match("Old Rivals", -250, season=cls.old, score_for=10, score_against=10)
        women = match("Women Opponent", -5, season=women_season, score_for=5, score_against=0)
        (cls.win | cls.away | hidden | cls.old_match | women).action_mark_played()
        player = cls.env["res.partner"].create({"name": "Private Player Name"})
        cls.env["rugby.match.player"].create({"match_id": cls.win.id, "jersey_number": 9, "player_id": player.id})

    def page(self, url="/schedule/test-side-a"):
        response = self.url_open(url)
        return response.status_code, re.sub(r"\s+", " ", response.text)

    def test_the_page_lists_only_results_switched_on(self):
        status, html = self.page()
        self.assertEqual(status, 200)
        for shown in ("Test Side A vs Tampa Played", "Jacksonville Away vs Test Side A"):
            self.assertIn(shown, html)
        for hidden in ("Ocala Upcoming", "Secret Unpublished", "Hidden Unpublished", "Rained Out",
                       "Women Opponent", "Old Rivals", "Private Player Name"):
            self.assertNotIn(hidden, html)
        self.assertNotIn("o_rugby_upcoming", html, "upcoming games come from the Event block now")

    def test_results_switch_is_separate_from_the_event(self):
        self.win.event_id.is_published = False
        _status, html = self.page()
        self.assertIn("Tampa Played", html, "an unpublished event doesn't hide the result")
        self.win.show_result = False
        self.assertNotIn("Tampa Played", self.page()[1])

    def test_staff_can_drop_blocks_on_each_team_page(self):
        self.authenticate("admin", "admin")
        _status, html = self.page()
        area = re.search(r'<div[^>]*class="[^"]*o_rugby_team_blocks[^"]*"[^>]*>', html).group(0)
        for attribute in ('data-oe-model="rugby.team"', 'data-oe-field="website_description"', "oe_structure"):
            self.assertIn(attribute, area)

    def test_blocks_are_saved_per_team(self):
        self.team.website_description = '<section class="s_text_block"><p>Thanks to our sponsors</p></section>'
        self.assertIn("Thanks to our sponsors", self.page()[1])
        women = self.env["rugby.team"].search([("name", "=", "Test Side W")])
        self.assertNotIn("Thanks to our sponsors", self.page(women.website_url)[1])

    def test_scores_read_home_side_first(self):
        _status, html = self.page()
        self.assertIn("29 – 17", html)  # home win
        self.assertIn("24 – 12", html)  # away loss: Jacksonville (home) 24, us 12
        self.assertIn("Record</strong> 1-1-0", html)

    def test_watch_button_links_to_the_recording(self):
        _status, html = self.page()
        self.assertIn('href="https://www.youtube.com/watch?v=abc"', html)

    def test_season_links_switch_the_results(self):
        _status, html = self.page()
        self.assertIn(f"/schedule/test-side-a?season={self.old.id}", html)
        status, html = self.page(f"/schedule/test-side-a?season={self.old.id}")
        self.assertEqual(status, 200)
        self.assertIn("Old Rivals", html)
        self.assertNotIn("Tampa Played", html)

    def test_an_unpublished_team_is_hidden_from_visitors(self):
        self.team.is_published = False
        self.assertEqual(self.page()[0], 404)
        self.authenticate("admin", "admin")
        self.assertEqual(self.page()[0], 200, "staff can check the page before publishing")

    def test_unknown_address_is_not_found(self):
        self.assertEqual(self.page("/schedule/no-such-team")[0], 404)

    def test_menu_item_follows_the_team(self):
        menu = self.team.website_menu_id
        self.assertEqual((menu.name, menu.url, menu.parent_id), ("Test Side A", "/schedule/test-side-a", self.parent_menu))
        self.team.name = "Test Side A XV"
        self.assertEqual(menu.name, "Test Side A XV")
        self.assertEqual(menu.url, "/schedule/test-side-a", "the address stays the same after a rename")
        self.team.is_published = False
        self.assertFalse(menu.exists())
        self.team.is_published = True
        self.assertEqual(self.team.website_menu_id.parent_id, self.parent_menu)
        self.team.action_archive()
        self.assertFalse(self.team.website_menu_id)
        self.team.action_unarchive()
        self.team.website_menu_parent_id = False
        self.assertFalse(self.team.website_menu_id)

    def test_web_address(self):
        self.assertEqual(self.team.website_slug, "test-side-a")
        self.assertEqual(self.team.website_url, "/schedule/test-side-a")
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["rugby.team"].create({"name": "Test Side A copy", "website_slug": "test-side-a"})
            self.env.flush_all()

    def test_sitemap_lists_published_teams(self):
        from odoo.addons.rugby_season_tracker.controllers.main import sitemap_teams
        locations = [entry["loc"] for entry in sitemap_teams(self.env, None, "")]
        self.assertIn("/schedule/test-side-a", locations)
        self.team.is_published = False
        self.assertNotIn("/schedule/test-side-a", [entry["loc"] for entry in sitemap_teams(self.env, None, "")])
