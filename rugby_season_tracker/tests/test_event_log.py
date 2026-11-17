import io
from datetime import datetime

import openpyxl
from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import BinaryBytes, mute_logger


@tagged("post_install", "-at_install")
class TestEventLog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env["rugby.team"].create({"name": "Log Test XV"})
        cls.season = cls.env["rugby.season"].create({"name": "Fall 2026", "team_id": cls.team.id})
        cls.match = cls.env["rugby.match"].create({
            "season_id": cls.season.id, "opponent": "Brevard", "kickoff": datetime(2026, 10, 3, 17, 0),
            "recording_link": '<p><a href="https://app.veo.co/matches/abc/?highlight=1">Video</a></p>',
        })
        players = cls.env["res.partner"].create([{"name": f"Log Player {number}"} for number in (8, 9, 10, 15)])
        cls.lines = cls.env["rugby.match.player"].create([
            {"match_id": cls.match.id, "jersey_number": number, "player_id": player.id}
            for number, player in zip((8, 9, 10, 15), players)
        ])
        cls.line = {line.jersey_number: line for line in cls.lines}

    def kind(self, code):
        return self.env.ref(f"rugby_season_tracker.event_type_{code}")

    def log(self, code, jersey=0, **values):
        return self.env["rugby.match.event"].create({
            "match_id": self.match.id, "event_type_id": self.kind(code).id, "jersey_number": jersey, **values,
        })

    # Lists ----------------------------------------------------------------

    def test_the_coding_sheet_lists_are_installed(self):
        types = self.env["rugby.event.type"].search([])
        self.assertEqual(len(types.filtered("code")), 73)
        self.assertEqual(self.kind("try_scored").category_ids.name, "Scoring")
        self.assertEqual(self.kind("ball_carry").detail, "carry")
        self.assertEqual(self.kind("penalty_conceded_scrum").detail, "offence")
        self.assertEqual(self.kind("penalty_try").points, 7)
        options = self.env["rugby.event.option"].search([])
        self.assertEqual({kind: len(options.filtered(lambda o: o.kind == kind))
                          for kind in ("try_source", "try_origin", "try_location", "offence")},
                         {"try_source": 6, "try_origin": 4, "try_location": 5, "offence": 23})

    # Rows -----------------------------------------------------------------

    def test_rows_number_themselves_and_link_to_the_video(self):
        first = self.log("pass", 9, video_time="1:44")
        second = self.log("ball_carry", 8, video_time="54:12", half="2")
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(second.video_seconds, 54 * 60 + 12)
        self.assertEqual(second.video_url, "https://app.veo.co/matches/abc/?highlight=1&t=3252s")
        self.assertEqual(self.match.next_event_half, "2")
        with self.assertRaises(ValidationError):
            self.log("pass", 9, video_time="3.04")

    def test_video_address_with_several_parameters(self):
        self.match.recording_link = '<p><a href="https://app.veo.co/matches/abc/?highlight=1&amp;scroll=MT">Video</a></p>'
        self.assertEqual(self.match.recording_url, "https://app.veo.co/matches/abc/?highlight=1&scroll=MT")
        self.assertEqual(self.log("pass", 9, video_time="2:01").video_url,
                         "https://app.veo.co/matches/abc/?highlight=1&scroll=MT&t=121s")

    def test_own_events_find_their_player_by_jersey(self):
        own = self.log("tackler", 8)
        theirs = self.log("tackler", 8, side="opp")
        unknown = self.log("tackler", 21)
        self.assertEqual(own.player_id, self.line[8].player_id)
        self.assertFalse(theirs.lineup_id, "the opposition's number 8 isn't our number 8")
        self.assertFalse(unknown.lineup_id)
        self.assertEqual(self.match.unlisted_jerseys, "21")
        # Adding number 21 to the team sheet picks the event up.
        late = self.env["res.partner"].create({"name": "Late Addition"})
        line = self.env["rugby.match.player"].create({"match_id": self.match.id, "jersey_number": 21, "player_id": late.id})
        self.assertEqual(unknown.lineup_id, line)
        self.assertEqual(line.tackles, 1)
        self.assertFalse(self.match.unlisted_jerseys)

    def test_negative_carries_are_allowed_but_not_negative_kicks(self):
        self.assertEqual(self.log("ball_carry", 8, carry_meters=-3).carry_meters, -3)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.log("kick", 10, kick_meters=-5)
            self.env.flush_all()

    # Team sheet -----------------------------------------------------------

    def test_team_sheet_stats_come_from_the_log(self):
        for meters in (10, 4, -2):
            self.log("ball_carry", 8, carry_meters=meters)
        for code, times in (("pass", 3), ("offload", 1), ("ruck_arrival", 2), ("tackler", 5), ("assist_tackler", 1),
                            ("missed_tackle", 2), ("jackle", 1), ("jackle_attempt", 2), ("knockon", 1),
                            ("penalty_conceded_ruck_maul", 1), ("penalty_conceded_offside", 1),
                            ("free_kick_conceded_scrum", 1), ("yellow_card", 1), ("try_scored", 1)):
            for _repeat in range(times):
                self.log(code, 8)
        self.log("tackler", 8, side="opp")  # the opposition's tackle is not ours
        line = self.line[8]
        self.assertEqual((line.carries, line.carry_meters, line.meters_per_carry), (3, 12, 4))
        self.assertEqual((line.passes, line.offloads, line.ruck_arrivals), (3, 1, 2))
        self.assertEqual((line.tackles, line.assist_tackles, line.missed_tackles), (5, 1, 2))
        self.assertAlmostEqual(line.tackle_rate, 6 / 8)
        self.assertEqual((line.jackles, line.jackle_attempts, line.knock_ons), (1, 2, 1))
        self.assertEqual(line.penalties_conceded, 2, "free kicks are not penalties")
        self.assertEqual((line.yellow_cards, line.tries, line.points), (1, 1, 5))

    def test_kicker_points_and_kick_meters(self):
        self.log("kick", 15, kick_meters=40)
        self.log("kick", 15, kick_meters=20)
        self.log("penalty_kicked", 15, kick_meters=30)
        self.log("own_kick_off_restart", 15, kick_meters=30)
        for code in ("conversion_scored", "conversion_scored", "penalty_to_goal_scored", "drop_goal_scored", "try_scored"):
            self.log(code, 15)
        line = self.line[15]
        self.assertEqual((line.kicks, line.kick_meters, line.meters_per_kick), (2, 120, 30))
        self.assertEqual((line.conversions, line.penalty_goals, line.drop_goals), (2, 1, 1))
        self.assertEqual(line.points, 5 + 2 * 2 + 3 + 3)

    def test_typed_stats_are_kept_until_a_log_exists(self):
        line = self.line[10]
        line.write({"tackles": 7, "tries": 2, "minutes_played": 80})
        self.assertEqual((line.tackles, line.tries), (7, 2))
        self.log("tackler", 9)
        self.assertEqual((line.tackles, line.tries), (0, 0), "with a log, the log is the source")
        self.assertEqual(line.minutes_played, 80, "minutes stay typed")
        self.assertEqual(self.line[9].tackles, 1)
        self.assertEqual(self.season.tries, 0)

    def test_editing_and_deleting_rows_updates_the_stats(self):
        event = self.log("tackler", 9)
        self.assertEqual(self.line[9].tackles, 1)
        event.jersey_number = 10
        self.assertEqual((self.line[9].tackles, self.line[10].tackles), (0, 1))
        event.event_type_id = self.kind("missed_tackle")
        self.assertEqual((self.line[10].tackles, self.line[10].missed_tackles), (0, 1))
        self.log("tackler", 10)
        event.unlink()
        self.assertEqual((self.line[10].tackles, self.line[10].missed_tackles), (1, 0))

    # Match summary ----------------------------------------------------------

    def test_match_summary(self):
        option = lambda kind, name: self.env["rugby.event.option"].search([("kind", "=", kind), ("name", "=", name)])  # noqa: E731
        for code, times in (("tackler", 5), ("assist_tackler", 1), ("missed_tackle", 2), ("own_ruck_won", 3),
                            ("own_ruck_lost", 1), ("ruck_arrival", 6), ("own_line_out_won", 2), ("own_line_out_lost", 1),
                            ("own_line_out_lost_not_straight", 1), ("conversion_scored", 1), ("conversion_missed", 1),
                            ("penalty_try", 1), ("penalty_conceded_scrum", 2), ("free_kick_conceded_scrum", 1)):
            for _repeat in range(times):
                self.log(code)
        self.log("own_ruck_won", side="opp")
        self.log("try_scored", 8, half="2", buildup_rucks=4, buildup_passes=7,
                 try_source_id=option("try_source", "Lineout").id, try_location_id=option("try_location", "Centre").id)
        self.log("try_scored", 15, buildup_rucks=0, buildup_passes=1, try_source_id=option("try_source", "Kick").id)
        self.log("try_scored", 4, side="opp")
        self.log("own_line_out", lineout_numbers=5)
        self.log("own_line_out", lineout_numbers=5)
        self.log("penalty_conceded_ruck_maul", 8, offence_id=option("offence", "Not Releasing").id)
        for jersey in (2, 9, 9, 12, 16):
            self.log("pass", jersey)
        summary = self.match._get_event_summary()
        rates = {row["label"]: row for row in summary["key_rates"]}
        totals = {row["label"]: row for row in summary["totals"]}
        self.assertAlmostEqual(rates["Tackle completion %"]["own"], 6 / 8)
        self.assertIsNone(rates["Tackle completion %"]["opp"], "nothing logged, so no rate rather than 0%")
        self.assertAlmostEqual(rates["Ruck success %"]["own"], 3 / 4)
        self.assertAlmostEqual(rates["Ruck arrivals per ruck"]["own"], 6 / 4)
        self.assertAlmostEqual(rates["Line-out win % (own throw)"]["own"], 2 / 4)
        self.assertAlmostEqual(rates["Conversion %"]["own"], 0.5)
        self.assertEqual(rates["Average build-up rucks/mauls per try"]["own"], 2)
        self.assertEqual(rates["Average build-up passes per try"]["own"], 4)
        self.assertEqual((totals["Tries (including penalty tries)"]["own"], totals["Tries (including penalty tries)"]["opp"]), (3, 1))
        self.assertEqual((summary["points"]["own"], summary["points"]["opp"]), (5 + 5 + 7 + 2, 5))
        self.assertEqual(totals["Scoring margin"]["opp"], -14)
        self.assertEqual(totals["Rucks (won + lost)"]["both"], 5)
        self.assertEqual(totals["Total penalties conceded"]["own"], 3)
        self.assertEqual(totals["Total infringements conceded"]["own"], 4)
        self.assertEqual([totals[f"Passes by {who}"]["own"] for who in ("forwards (1-8)", "scrum half (9)", "backs (10-15)")],
                         [1, 2, 1], "bench numbers are not counted")
        counts = {row["name"]: row for row in summary["counts"] if "name" in row}
        self.assertEqual((counts["Try Scored"]["own_1"], counts["Try Scored"]["own_2"], counts["Try Scored"]["own"],
                          counts["Try Scored"]["opp"]), (1, 1, 2, 1))
        self.assertEqual({row["name"]: row["count"] for row in summary["tries_by_source"] if row["count"]},
                         {"Lineout": 1, "Kick": 1})
        self.assertEqual({row["name"]: row["count"] for row in summary["lineouts_by_numbers"] if row["count"]}, {5: 2})
        self.assertEqual({row["name"]: row["count"] for row in summary["offences"] if row["count"]}, {"Not Releasing": 1})
        self.assertEqual(self.match.log_score, "19 - 5")
        self.assertIn("Tackle completion %", self.match.summary_html)
        self.assertIn("75%", self.match.summary_html)
        for shown in ("Own tries by source", "Own lineouts by numbers", "Not Releasing", "Own offences conceded by detail"):
            self.assertIn(shown, self.match.summary_html)

    def test_a_match_without_a_log_has_no_summary(self):
        self.assertFalse(self.match.summary_html)
        self.assertFalse(self.match.log_score)

    # Import -----------------------------------------------------------------

    def workbook(self, rows, players=()):
        book = openpyxl.Workbook()
        guide = book.active
        guide.title = "Guide"
        guide.append(["Video URL (no &t= part)", "https://app.veo.co/matches/xyz/?highlight=2"])
        log = book.create_sheet("Event Log")
        log.append(["Event Log"])
        log.append([])
        log.append([])
        log.append(["#", "Half", "Video Time (m:ss)", "Team", "Player #", "Event", "Category", "Quality", "Offence Detail",
                    "Try Source", "Try Origin", "Try Location", "Build-up Rucks/Mauls", "Build-up Passes",
                    "Lineout Numbers", "Carry Meters", "Kick Meters", "Notes"])
        for row in rows:
            log.append(row)
        stats = book.create_sheet("Player Stats")
        stats.append(["Player Stats (Own team)"])
        stats.append([])
        stats.append([])
        stats.append(["#", "Player name", "Ball Carry"])
        for jersey, name in players:
            stats.append([jersey, name, 0])
        stream = io.BytesIO()
        book.save(stream)
        return BinaryBytes(stream.getvalue())

    def run_import(self, rows, players=(), **options):
        wizard = self.env["rugby.event.import"].create({
            "match_id": self.match.id, "file": self.workbook(rows, players), "filename": "coded.xlsx", **options,
        })
        return wizard.action_import()

    def test_import_the_coding_sheet(self):
        self.match.recording_link = False
        self.log("pass", 9)  # replaced by the import
        self.run_import([
            [1, "1H", "1:44", "Own", 13.0, "Ball Carry", "Attack", None, None, None, None, None, None, None, None, 18.0],
            [2, "1H", "2:00", None, None, "Own Ruck Won", "Breakdown"],
            [3, "2H", "54:12", "Own", 8.0, "Try Scored", "Scoring", "Positive", None, "Kick", "Opp 22", "Centre", 1.0, 3.0],
            [4, "2H", "61:05", "Opp", 3.0, "Penalty Conceded - Scrum", "Sanctions", None, "Collapsing"],
            [5, "2H", "62:00", "Own", 10.0, "Kick", "Kicking", None, None, None, None, None, None, None, None, None, 42.0, "to touch"],
            [6, "2H", "63:00", "Own", 9.0, "Box Kick Chase", "Kicking"],
            [None, None, None, None, None, None],
        ], players=[(8, "Log Player 8"), (13, "Sam Newcomer"), (14, None)])
        events = self.match.event_ids
        self.assertEqual(len(events), 6)
        self.assertEqual(events.mapped("sequence"), [1, 2, 3, 4, 5, 6])
        carry, ruck, try_row, penalty, kick, custom = events
        self.assertEqual((carry.half, carry.video_time, carry.side, carry.jersey_number, carry.carry_meters),
                         ("1", "1:44", "own", 13, 18))
        self.assertEqual((ruck.side, ruck.jersey_number), ("own", 0), "a blank Team means our own team")
        self.assertEqual((try_row.quality, try_row.try_source_id.name, try_row.try_origin_id.name,
                          try_row.try_location_id.name, try_row.buildup_rucks, try_row.buildup_passes),
                         ("positive", "Kick", "Opp 22", "Centre", 1, 3))
        self.assertEqual((penalty.side, penalty.offence_id.name), ("opp", "Collapsing"))
        self.assertEqual((kick.kick_meters, kick.notes), (42, "to touch"))
        self.assertEqual((custom.event_type_id.name, custom.category_ids.name), ("Box Kick Chase", "Kicking"),
                         "an event added on the sheet's Lists tab is added to Odoo's list")
        # Team sheet: 13 is new, 8 was already there, 14 has no name.
        self.assertEqual(sorted(self.match.lineup_ids.mapped("jersey_number")), [8, 9, 10, 13, 15])
        newcomer = self.match.lineup_ids.filtered(lambda line: line.jersey_number == 13)
        self.assertEqual((newcomer.player_id.name, newcomer.carries, newcomer.carry_meters), ("Sam Newcomer", 1, 18))
        self.assertEqual(self.line[8].tries, 1)
        self.assertEqual(self.match.recording_url, "https://app.veo.co/matches/xyz/?highlight=2")
        self.assertEqual(try_row.video_url, "https://app.veo.co/matches/xyz/?highlight=2&t=3252s")

    def test_import_can_add_to_the_log(self):
        self.log("pass", 9)
        self.run_import([[1, "1H", "0:30", "Own", 9.0, "Pass", "Attack"]], replace=False, fill_lineup=False)
        self.assertEqual(self.match.event_ids.mapped("sequence"), [1, 2])
        self.assertEqual(self.line[9].passes, 2)

    def test_import_refuses_bad_rows_and_changes_nothing(self):
        self.log("pass", 9)
        with self.assertRaises(UserError) as error:
            self.run_import([
                [1, "1H", "0:30", "Own", 9.0, "Pass", "Attack"],
                [2, "3H", "0:40", "Own", 9.0, "Pass", "Attack"],
                [3, "1H", "0.50", "Own", 9.0, "Pass", "Attack"],
            ])
        self.assertIn("Row 6", str(error.exception))
        self.assertIn("Row 7", str(error.exception))
        self.assertEqual(len(self.match.event_ids), 1)

    def test_import_needs_the_event_log_tab(self):
        book = openpyxl.Workbook()
        stream = io.BytesIO()
        book.save(stream)
        wizard = self.env["rugby.event.import"].create({"match_id": self.match.id, "file": BinaryBytes(stream.getvalue())})
        with self.assertRaises(UserError):
            wizard.action_import()
        wizard.file = BinaryBytes(b"not a spreadsheet")
        with self.assertRaises(UserError):
            wizard.action_import()
