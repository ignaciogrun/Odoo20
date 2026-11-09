from datetime import datetime, timedelta

from psycopg2 import IntegrityError

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestSeasonTracker(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env["rugby.team"].create({"name": "Orlando Test XV"})
        cls.season = cls.env["rugby.season"].create({"name": "Fall 2026", "team_id": cls.team.id})
        cls.venue = cls.env["res.partner"].create({"name": "Barnett Park", "city": "Orlando"})
        cls.kickoff = datetime(2026, 9, 12, 17, 0)
        Partner = cls.env["res.partner"]
        cls.players = Partner.create([{"name": f"Player {number}"} for number in range(1, 24)])

    def make_match(self, **values):
        return self.env["rugby.match"].create({
            "season_id": self.season.id, "opponent": "Tampa Bay", "kickoff": self.kickoff,
            "venue_id": self.venue.id, **values,
        })

    # Events ---------------------------------------------------------------

    def test_a_new_match_creates_its_event(self):
        match = self.make_match()
        event = match.event_id
        self.assertTrue(event)
        self.assertEqual(match.name, "Orlando Test XV vs Tampa Bay")
        self.assertEqual(event.name, match.name)
        self.assertEqual(event.date_begin, self.kickoff)
        self.assertEqual(event.date_end, self.kickoff + timedelta(hours=2))
        self.assertEqual(event.address_id, self.venue)
        self.assertFalse(event.is_published, "events stay off the website until published")

    def test_fixtures_typed_on_the_season_get_events(self):
        self.season.write({"match_ids": [
            Command.create({"opponent": "Ocala", "kickoff": self.kickoff}),
            Command.create({"opponent": "Lakeland", "home_away": "away", "kickoff": self.kickoff + timedelta(days=7)}),
        ]})
        matches = self.season.match_ids
        self.assertEqual(matches.mapped("event_id.name"),
                         ["Lakeland vs Orlando Test XV", "Orlando Test XV vs Ocala"])
        action = matches[0].action_open_match()
        self.assertEqual((action["res_model"], action["res_id"], action["target"]), ("rugby.match", matches[0].id, "current"))

    def test_away_matches_list_the_home_side_first(self):
        self.assertEqual(self.make_match(home_away="away").name, "Tampa Bay vs Orlando Test XV")

    def test_editing_the_match_updates_its_event(self):
        match = self.make_match()
        other_venue = self.env["res.partner"].create({"name": "Showalter Field"})
        later = self.kickoff + timedelta(days=7)
        match.write({"opponent": "Miami", "home_away": "away", "kickoff": later,
                     "duration": 3, "venue_id": other_venue.id})
        self.assertEqual(match.event_id.name, "Miami vs Orlando Test XV")
        self.assertEqual(match.event_id.date_begin, later)
        self.assertEqual(match.event_id.date_end, later + timedelta(hours=3))
        self.assertEqual(match.event_id.address_id, other_venue)

    def test_renaming_the_team_renames_the_events(self):
        match = self.make_match()
        self.team.name = "Orlando Griffins"
        self.assertEqual(match.name, "Orlando Griffins vs Tampa Bay")
        self.assertEqual(match.event_id.name, "Orlando Griffins vs Tampa Bay")

    def test_event_name_matches_in_english_and_spanish(self):
        self.env["res.lang"]._activate_lang("es_419")
        match = self.make_match()
        match.with_context(lang="es_419").opponent = "Jacksonville"
        event = match.event_id
        self.assertEqual(event.with_context(lang="en_US").name, "Orlando Test XV vs Jacksonville")
        self.assertEqual(event.with_context(lang="es_419").name, "Orlando Test XV vs Jacksonville")

    def test_show_on_website_publishes_the_event(self):
        match = self.make_match()
        match.event_published = True
        self.assertTrue(match.event_id.is_published)
        match.event_published = False
        self.assertFalse(match.event_id.is_published)

    def test_cancelling_hides_the_event_and_rescheduling_brings_it_back(self):
        match = self.make_match()
        event = match.event_id
        match.action_cancel()
        self.assertFalse(event.active)
        match.action_reschedule()
        self.assertTrue(event.active)

    def test_a_played_match_ends_its_event(self):
        match = self.make_match()
        match.action_mark_played()
        self.assertEqual(match.event_id.stage_id, self.env.ref("event.event_stage_done"))

    def test_deleting_a_match_deletes_its_event(self):
        match = self.make_match()
        event = match.event_id
        match.unlink()
        self.assertFalse(event.exists())

    def test_an_event_people_registered_for_is_kept_but_hidden(self):
        match = self.make_match()
        event = match.event_id
        self.env["event.registration"].create({"event_id": event.id, "name": "Fan", "email": "fan@example.com"})
        match.unlink()
        self.assertTrue(event.exists())
        self.assertFalse(event.active)

    def test_a_coach_without_events_access_can_manage_matches(self):
        coach = new_test_user(self.env, login="coach", groups="base.group_user")
        self.assertFalse(coach.has_group("event.group_event_user"))
        match = self.make_match().with_user(coach)
        match = match.create({"season_id": self.season.id, "opponent": "Sarasota", "kickoff": self.kickoff})
        match.write({"opponent": "Naples"})
        match.event_published = True
        match.action_mark_played()
        event = match.sudo().event_id
        self.assertEqual(event.name, "Orlando Test XV vs Naples")
        self.assertTrue(event.is_published)
        match.unlink()
        self.assertFalse(event.exists())

    # Results --------------------------------------------------------------

    def test_result_and_winner(self):
        cases = [((24, 17), "win", "Orlando Test XV"), ((10, 24), "loss", "Tampa Bay"), ((15, 15), "draw", "Draw")]
        for (ours, theirs), result, winner in cases:
            with self.subTest(score=(ours, theirs)):
                match = self.make_match(score_for=ours, score_against=theirs)
                self.assertFalse(match.result, "no result until the match is played")
                match.action_mark_played()
                self.assertEqual(match.result, result)
                self.assertEqual(match.winner, winner)
                self.assertEqual(match.score_display, f"{ours} - {theirs}")

    def test_negative_scores_are_refused(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.make_match(score_for=-5)
            self.env.flush_all()

    def test_season_totals(self):
        played = [(24, 17), (10, 24), (15, 15), (31, 0)]
        for ours, theirs in played:
            self.make_match(score_for=ours, score_against=theirs).action_mark_played()
        self.make_match()  # still to play
        self.make_match(score_for=50, score_against=0).action_cancel()
        season = self.season
        self.assertEqual(season.match_count, 6)
        self.assertEqual(season.matches_played, 4)
        self.assertEqual(season.record, "2-1-1")
        self.assertEqual((season.points_for, season.points_against, season.points_difference), (80, 56, 24))
        self.assertEqual(season.display_name, "Orlando Test XV Fall 2026")

    def test_seasons_can_be_found_by_team_name(self):
        found = self.env["rugby.season"].name_search("Orlando Test XV")
        self.assertIn(self.season.id, [season_id for season_id, _name in found])

    # Lineups and stats -----------------------------------------------------

    def test_jersey_number_sets_position_and_starter(self):
        match = self.make_match()
        lines = self.env["rugby.match.player"].create([
            {"match_id": match.id, "jersey_number": number, "player_id": self.players[number - 1].id}
            for number in (1, 9, 15, 16, 23)
        ])
        self.assertEqual(lines.mapped("position"),
                         ["loosehead_prop", "scrum_half", "fullback", "replacement", "replacement"])
        self.assertEqual(lines.mapped("starter"), [True, True, True, False, False])
        lines[0].position = "hooker"  # can still be changed by hand
        self.assertEqual(lines[0].position, "hooker")

    def test_lineup_rules(self):
        match = self.make_match()
        Line = self.env["rugby.match.player"]
        Line.create({"match_id": match.id, "jersey_number": 1, "player_id": self.players[0].id})
        bad_lines = {
            "same player twice": {"jersey_number": 2, "player_id": self.players[0].id},
            "same jersey twice": {"jersey_number": 1, "player_id": self.players[1].id},
            "negative tackles": {"jersey_number": 3, "player_id": self.players[2].id, "tackles": -1},
            "jersey zero": {"jersey_number": 0, "player_id": self.players[3].id},
        }
        for label, values in bad_lines.items():
            with self.subTest(label), self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
                Line.create({"match_id": match.id, **values})
                self.env.flush_all()

    def test_players_are_tagged(self):
        match = self.make_match()
        tag = self.env.ref("rugby_season_tracker.partner_category_player")
        self.env["rugby.match.player"].create({"match_id": match.id, "jersey_number": 10, "player_id": self.players[9].id})
        self.assertIn(tag, self.players[9].category_id)

    def test_player_stats_add_up_across_matches(self):
        first, second, upcoming = self.make_match(), self.make_match(), self.make_match()
        Line = self.env["rugby.match.player"]
        player = self.players[10]
        Line.create([
            {"match_id": first.id, "jersey_number": 11, "player_id": player.id, "minutes_played": 80, "tackles": 6, "tries": 2},
            {"match_id": second.id, "jersey_number": 14, "player_id": player.id, "minutes_played": 55, "tackles": 3, "tries": 1},
            {"match_id": upcoming.id, "jersey_number": 11, "player_id": player.id},
        ])
        (first | second).action_mark_played()
        groups = Line.formatted_read_group(
            [("match_state", "=", "played"), ("player_id", "=", player.id)],
            ["player_id"], ["appearances:sum", "minutes_played:sum", "tackles:sum", "tries:sum"],
        )
        self.assertEqual(
            (groups[0]["appearances:sum"], groups[0]["minutes_played:sum"], groups[0]["tackles:sum"], groups[0]["tries:sum"]),
            (2, 135, 9, 3),
        )
        self.assertEqual(self.season.tries, 3)
