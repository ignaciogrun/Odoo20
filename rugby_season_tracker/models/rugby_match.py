import re
from datetime import timedelta
from html import unescape

from odoo import Command, api, fields, models

# The first web address in the recording field: a link's target, or a pasted address.
LINK_TARGET = re.compile(r"""href=["'](https?://[^"']+)["']""")
PASTED_ADDRESS = re.compile(r"""https?://[^\s<>"']+""")

# Changing any of these changes the event's name, time or place.
EVENT_FIELDS = {"opponent", "home_away", "season_id", "kickoff", "duration", "venue_id"}


class RugbyMatch(models.Model):
    _name = "rugby.match"
    _description = "Rugby Match"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "kickoff desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    season_id = fields.Many2one("rugby.season", required=True, index=True, tracking=True, ondelete="restrict")
    team_id = fields.Many2one(related="season_id.team_id", store=True, index=True)
    opponent = fields.Char(required=True, tracking=True)
    home_away = fields.Selection(
        [("home", "Home"), ("away", "Away")], string="Home or Away",
        required=True, default="home", tracking=True,
    )
    kickoff = fields.Datetime(required=True, tracking=True)
    duration = fields.Float(
        "Event Length", default=2.0, help="How long the event lasts on the calendar, in hours.",
    )
    kickoff_end = fields.Datetime("Ends", compute="_compute_kickoff_end", store=True)
    venue_id = fields.Many2one("res.partner", string="Venue", tracking=True)
    state = fields.Selection(
        [("scheduled", "Scheduled"), ("played", "Played"), ("cancelled", "Cancelled")],
        string="Status", required=True, default="scheduled", tracking=True, index=True,
    )
    score_for = fields.Integer("Our Score", tracking=True)
    score_against = fields.Integer("Opponent Score", tracking=True)
    result = fields.Selection(
        [("win", "Win"), ("loss", "Loss"), ("draw", "Draw")],
        compute="_compute_result", store=True, index=True,
    )
    # Worked out when shown, so "Draw" appears in each person's language.
    winner = fields.Char(compute="_compute_winner")
    score_display = fields.Char("Score", compute="_compute_winner")
    recording_link = fields.Html("Match Recording")
    recording_url = fields.Char(compute="_compute_recording_url", help="Used for the Watch button on the website.")
    lineup_ids = fields.One2many("rugby.match.player", "match_id", string="Lineup")
    lineup_count = fields.Integer(compute="_compute_lineup_count")

    # Film review: one row per coded event, feeding the team sheet's stats and
    # the match summary.
    event_ids = fields.One2many("rugby.match.event", "match_id", string="Event Log")
    event_count = fields.Integer("Events", compute="_compute_event_info")
    next_event_half = fields.Selection(
        [("1", "1st Half"), ("2", "2nd Half")], compute="_compute_event_info",
        help="The half of the last logged event, offered on the next new row.",
    )
    unlisted_jerseys = fields.Char(
        compute="_compute_event_info",
        help="Jersey numbers used in the event log that aren't on the team sheet.",
    )
    log_score = fields.Char("Score from Event Log", compute="_compute_summary_html")
    summary_html = fields.Html("Match Summary", compute="_compute_summary_html", sanitize=False)

    event_id = fields.Many2one("event.event", string="Event", readonly=True, copy=False, ondelete="set null")
    # Only for the results table on the team's website page. The match's event
    # (upcoming games, the Event block) is published separately, in Events.
    show_result = fields.Boolean(
        "Show Result on Website", tracking=True,
        help="Lists this match in the results table on the team's Schedule & Results page once it's played. "
             "It doesn't publish the match's event.",
    )

    _check_scores = models.Constraint(
        "CHECK(score_for >= 0 AND score_against >= 0)", "Scores can't be negative.",
    )
    _check_duration = models.Constraint("CHECK(duration > 0)", "The event length must be more than zero.")

    @api.depends("team_id.name", "opponent", "home_away")
    def _compute_name(self):
        # Rugby lists the home side first.
        for match in self:
            sides = [match.team_id.name or "", match.opponent or ""]
            if match.home_away == "away":
                sides.reverse()
            match.name = " vs ".join(sides)

    @api.depends("kickoff", "duration")
    def _compute_kickoff_end(self):
        for match in self:
            match.kickoff_end = match.kickoff and match.kickoff + timedelta(hours=match.duration or 2.0)

    @api.depends("state", "score_for", "score_against")
    def _compute_result(self):
        for match in self:
            if match.state != "played":
                match.result = False
            elif match.score_for > match.score_against:
                match.result = "win"
            elif match.score_for < match.score_against:
                match.result = "loss"
            else:
                match.result = "draw"

    @api.depends("result", "score_for", "score_against", "team_id.name", "opponent")
    def _compute_winner(self):
        winners = {"win": lambda match: match.team_id.name, "loss": lambda match: match.opponent,
                   "draw": lambda match: self.env._("Draw")}
        for match in self:
            played = match.state == "played"
            match.score_display = f"{match.score_for} - {match.score_against}" if played else False
            match.winner = winners[match.result](match) if played and match.result else False

    @api.depends("recording_link")
    def _compute_recording_url(self):
        for match in self:
            html = str(match.recording_link or "")
            found = LINK_TARGET.search(html) or PASTED_ADDRESS.search(html)
            address = found.group(1) if found and found.re is LINK_TARGET else (found and found.group(0))
            # The field holds HTML, where "&" in an address is stored as "&amp;".
            match.recording_url = address and unescape(address)

    @api.depends("lineup_ids")
    def _compute_lineup_count(self):
        for match in self:
            match.lineup_count = len(match.lineup_ids)

    @api.depends("event_ids.half", "event_ids.side", "event_ids.jersey_number", "lineup_ids.jersey_number")
    def _compute_event_info(self):
        for match in self:
            events = match.event_ids
            match.event_count = len(events)
            match.next_event_half = events[-1:].half or "1"
            listed = set(match.lineup_ids.mapped("jersey_number"))
            used = {event.jersey_number for event in events if event.side == "own" and event.jersey_number}
            match.unlisted_jerseys = ", ".join(str(number) for number in sorted(used - listed)) or False

    @api.depends(
        "event_ids.event_type_id", "event_ids.side", "event_ids.half", "event_ids.jersey_number",
        "event_ids.carry_meters", "event_ids.kick_meters", "event_ids.buildup_rucks", "event_ids.buildup_passes",
        "event_ids.lineout_numbers", "event_ids.offence_id", "event_ids.try_source_id",
        "event_ids.try_origin_id", "event_ids.try_location_id", "team_id.name", "opponent",
    )
    def _compute_summary_html(self):
        for match in self:
            if not match.event_ids:
                match.summary_html = match.log_score = False
                continue
            summary = match._get_event_summary()
            match.log_score = "%s - %s" % (summary["points"]["own"], summary["points"]["opp"])
            match.summary_html = self.env["ir.qweb"]._render("rugby_season_tracker.match_summary", {
                **summary,
                # Blank rather than 0 when nothing was logged to work a figure out from.
                "pct": lambda value: "" if value is None else f"{value * 100:.0f}%",
                "num": lambda value: "" if value is None else (f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"),
            })

    def _get_event_summary(self):
        """The coding sheet's Match Summary tab, worked out from the event log."""
        self.ensure_one()
        events = self.event_ids
        sides = ("own", "opp")
        count = {}  # (statistics key, side, half) -> rows
        by_type = {}  # (event type id, side, half) -> rows
        for event in events:
            count[event.event_code, event.side, event.half] = count.get((event.event_code, event.side, event.half), 0) + 1
            key = (event.event_type_id.id, event.side, event.half)
            by_type[key] = by_type.get(key, 0) + 1

        def total(side, *codes):
            return sum(count.get((code, side, half), 0) for code in codes for half in ("1", "2"))

        def starting(side, prefix):
            return sum(rows for (code, row_side, _half), rows in count.items()
                       if row_side == side and code and code.startswith(prefix))

        def rate(top, bottom):
            return top / bottom if bottom else None

        def of(side, *codes):
            return events.filtered(lambda event: event.side == side and event.event_code in codes)

        def average(rows, field):
            return sum(rows.mapped(field)) / len(rows) if rows else None

        def both(label, how, values, with_total=False):
            return {"label": label, "how": how, "own": values["own"], "opp": values["opp"],
                    "both": values["own"] + values["opp"] if with_total else None}

        kick_codes = ("kick", "penalty_kicked", "free_kick_kicked", "own_kick_off_restart")
        _t = self.env._
        rates = [
            (_t("Tackle completion %"), _t("(Tackler + Assist Tackler) / (+ Missed Tackle)"),
             lambda s: rate(total(s, "tackler", "assist_tackler"), total(s, "tackler", "assist_tackler", "missed_tackle"))),
            (_t("Ruck success %"), _t("Own Ruck Won / (Won + Lost)"),
             lambda s: rate(total(s, "own_ruck_won"), total(s, "own_ruck_won", "own_ruck_lost"))),
            (_t("Ruck arrivals per ruck"), _t("Ruck Arrival / (Own Ruck Won + Lost)"),
             lambda s: rate(total(s, "ruck_arrival"), total(s, "own_ruck_won", "own_ruck_lost"))),
            (_t("Maul success %"), _t("Own Maul Won / (Won + Lost)"),
             lambda s: rate(total(s, "own_maul_won"), total(s, "own_maul_won", "own_maul_lost"))),
            (_t("Scrum win % (own feed)"), _t("Own Scrum Won / (Won + Lost)"),
             lambda s: rate(total(s, "own_scrum_won"), total(s, "own_scrum_won", "own_scrum_lost"))),
            (_t("Line-out win % (own throw)"), _t("Won / (Won + Lost + Not Straight)"),
             lambda s: rate(total(s, "own_line_out_won"),
                            total(s, "own_line_out_won", "own_line_out_lost", "own_line_out_lost_not_straight"))),
            (_t("Line-out contested %"), _t("Lineout Contested / Own Line-out"),
             lambda s: rate(total(s, "lineout_contested"), total(s, "own_line_out"))),
            (_t("Conversion %"), _t("Scored / (Scored + Missed)"),
             lambda s: rate(total(s, "conversion_scored"), total(s, "conversion_scored", "conversion_missed"))),
            (_t("Penalty goal %"), _t("Scored / (Scored + Missed)"),
             lambda s: rate(total(s, "penalty_to_goal_scored"), total(s, "penalty_to_goal_scored", "penalty_to_goal_missed"))),
            (_t("Jackle success %"), _t("Jackle / Jackle Attempt"),
             lambda s: rate(total(s, "jackle"), total(s, "jackle_attempt"))),
            (_t("Kick regained %"), _t("Kick Regained / Kick"),
             lambda s: rate(total(s, "kick_regained_by_kicking_team"), total(s, "kick"))),
            (_t("Own restart regained %"), _t("Regained / Own Kick Off/Restart"),
             lambda s: rate(total(s, "own_kick_off_restart_regained"), total(s, "own_kick_off_restart"))),
        ]
        plain = {_t("Ruck arrivals per ruck")}
        key_rates = [{"label": label, "how": how, "percent": label not in plain,
                      "own": formula("own"), "opp": formula("opp")} for label, how, formula in rates]
        key_rates += [
            {"label": _t("Average build-up rucks/mauls per try"), "how": _t("Average on Try Scored rows"), "percent": False,
             "own": average(of("own", "try_scored"), "buildup_rucks"), "opp": average(of("opp", "try_scored"), "buildup_rucks")},
            {"label": _t("Average build-up passes per try"), "how": _t("Average on Try Scored rows"), "percent": False,
             "own": average(of("own", "try_scored"), "buildup_passes"), "opp": average(of("opp", "try_scored"), "buildup_passes")},
        ]

        points = {side: sum(event.event_type_id.points for event in events if event.side == side) for side in sides}

        def passes(side, low, high):
            return len(of(side, "pass").filtered(lambda event: low <= event.jersey_number <= high))

        per_side = lambda formula: {side: formula(side) for side in sides}  # noqa: E731
        penalties = per_side(lambda s: starting(s, "penalty_conceded"))
        free_kicks = per_side(lambda s: starting(s, "free_kick_conceded"))
        totals = [
            both(_t("Tries (including penalty tries)"), _t("Try Scored + Penalty Try"),
                 per_side(lambda s: total(s, "try_scored", "penalty_try"))),
            both(_t("Points scored"), _t("Try 5, penalty try 7, conversion 2, penalty goal 3, drop goal 3"), points),
            both(_t("Scoring margin"), _t("Own points - Opp points"),
                 {"own": points["own"] - points["opp"], "opp": points["opp"] - points["own"]}),
            both(_t("Rucks (won + lost)"), _t("World Rugby total, both teams"),
                 per_side(lambda s: total(s, "own_ruck_won", "own_ruck_lost")), True),
            both(_t("Mauls (won + lost)"), _t("World Rugby total, both teams"),
                 per_side(lambda s: total(s, "own_maul_won", "own_maul_lost")), True),
            both(_t("Total penalties conceded"), _t("Sum of Penalty Conceded rows"), penalties, True),
            both(_t("Total free kicks conceded"), _t("Sum of Free Kick Conceded rows"), free_kicks, True),
            both(_t("Total infringements conceded"), _t("Penalties + free kicks conceded"),
                 per_side(lambda s: penalties[s] + free_kicks[s]), True),
            both(_t("Kicks regained by kicking team"), _t("Kick Regained + Own Restart Regained"),
                 per_side(lambda s: total(s, "kick_regained_by_kicking_team", "own_kick_off_restart_regained"))),
            both(_t("Passes by forwards (1-8)"), _t("Pass rows by jersey number"), per_side(lambda s: passes(s, 1, 8))),
            both(_t("Passes by scrum half (9)"), _t("Pass rows by jersey number"), per_side(lambda s: passes(s, 9, 9))),
            both(_t("Passes by backs (10-15)"), _t("Pass rows by jersey number"), per_side(lambda s: passes(s, 10, 15))),
            both(_t("Carry meters (total)"), _t("Sum of Carry Meters on Ball Carry rows"),
                 per_side(lambda s: sum(of(s, "ball_carry").mapped("carry_meters")))),
            both(_t("Average meters per carry"), _t("Total meters / Ball Carry rows"),
                 per_side(lambda s: average(of(s, "ball_carry"), "carry_meters"))),
            both(_t("Kick meters (total)"), _t("Sum of Kick Meters on kick rows"),
                 per_side(lambda s: sum(of(s, *kick_codes).mapped("kick_meters")))),
            both(_t("Average meters per kick"), _t("Total meters / kick rows"),
                 per_side(lambda s: average(of(s, *kick_codes), "kick_meters"))),
        ]

        # Every event on the list, in list order, under its category.
        counts, heading = [], None
        for event_type in self.env["rugby.event.type"].search([]) | events.event_type_id:
            row = {
                "name": event_type.name,
                "own_1": by_type.get((event_type.id, "own", "1"), 0),
                "own_2": by_type.get((event_type.id, "own", "2"), 0),
                "opp": sum(by_type.get((event_type.id, "opp", half), 0) for half in ("1", "2")),
            }
            row["own"] = row["own_1"] + row["own_2"]
            category = ", ".join(event_type.category_ids.mapped("name"))
            if category != heading:
                heading = category
                counts.append({"heading": category})
            counts.append(row)

        def breakdown(rows, field, kind):
            options = self.env["rugby.event.option"].search([("kind", "=", kind)]) | rows[field]
            return [{"name": option.name, "count": len(rows.filtered(lambda event: event[field] == option))}
                    for option in options]

        own_tries = of("own", "try_scored")
        own_lineouts = of("own", "own_line_out")
        own_offences = events.filtered(lambda event: event.side == "own" and (event.event_code or "").startswith(
            ("penalty_conceded", "free_kick_conceded")))
        numbers = sorted(set(range(3, 9)) | {n for n in own_lineouts.mapped("lineout_numbers") if n})
        return {
            "own_name": self.team_id.name, "opp_name": self.opponent, "points": points,
            "key_rates": key_rates, "totals": totals, "counts": counts,
            "tries_by_source": breakdown(own_tries, "try_source_id", "try_source"),
            "tries_by_origin": breakdown(own_tries, "try_origin_id", "try_origin"),
            "tries_by_location": breakdown(own_tries, "try_location_id", "try_location"),
            "lineouts_by_numbers": [
                {"name": number, "count": len(own_lineouts.filtered(lambda event: event.lineout_numbers == number))}
                for number in numbers],
            "offences": breakdown(own_offences, "offence_id", "offence"),
        }

    # Events -------------------------------------------------------------
    # The module creates, updates and removes each match's event itself (as
    # superuser), so people who manage matches don't also need Events rights.

    def _event_values(self):
        self.ensure_one()
        return {
            "date_begin": self.kickoff,
            "date_end": self.kickoff_end,
            "address_id": self.venue_id.id,
        }

    def _sync_event(self):
        langs = [code for code, _name in self.env["res.lang"].get_installed()]
        # Each event carries its team's tag (for the Event block's tag filter):
        # swap any other team's tag for this match's team, keep other tags.
        team_tags = self.env["rugby.team"].with_context(active_test=False).search([]).event_tag_id
        for match in self.filtered("event_id"):
            event = match.event_id.sudo()
            tags = (event.tag_ids - team_tags) | match.team_id.event_tag_id
            event.write({**match._event_values(), "tag_ids": [Command.set(tags.ids)]})
            # The event name is translatable; a match name is the same in every
            # language, so set them all (one event at a time, as Odoo requires).
            event.update_field_translations("name", dict.fromkeys(langs, match.name))

    def _sync_event_state(self):
        done = self.env.ref("event.event_stage_done", raise_if_not_found=False)
        for match in self.filtered("event_id"):
            event = match.event_id.sudo().with_context(active_test=False)
            values = {"active": match.state != "cancelled"}
            if match.state == "played" and done:
                values["stage_id"] = done.id
            event.write(values)

    @api.model_create_multi
    def create(self, vals_list):
        matches = super().create(vals_list)
        Event = self.env["event.event"].sudo()
        # Kickoff times on the website show in the event's time zone. Odoo takes
        # it from the person creating the match; if they never set one, use the
        # administrator's rather than UTC (which would be hours off).
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        time_zone = self.env.user.tz or (admin and admin.tz) or "UTC"
        for match in matches:
            match.event_id = Event.create({
                "name": match.name, "is_published": False, "date_tz": time_zone, **match._event_values(),
            })
        matches._sync_event()
        matches.filtered(lambda match: match.state != "scheduled")._sync_event_state()
        return matches

    def write(self, vals):
        result = super().write(vals)
        if EVENT_FIELDS & set(vals):
            self._sync_event()
        if "state" in vals:
            self._sync_event_state()
        return result

    def unlink(self):
        events = self.event_id.sudo().with_context(active_test=False)
        result = super().unlink()
        # Keep an event that people registered for; just hide it.
        events.filtered("registration_ids").write({"active": False})
        events.filtered(lambda event: not event.registration_ids).unlink()
        return result

    # Buttons ------------------------------------------------------------

    def action_mark_played(self):
        self.write({"state": "played"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_reschedule(self):
        self.write({"state": "scheduled"})

    def action_open_match(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "rugby.match",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_event_log(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("rugby_season_tracker.action_rugby_match_event")
        action["domain"] = [("match_id", "=", self.id)]
        action["context"] = {"default_match_id": self.id, "default_half": self.next_event_half}
        action["display_name"] = self.env._("Event Log")
        return action

    def action_open_player_stats(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("rugby_season_tracker.action_rugby_player_stats")
        action["domain"] = [("match_id", "=", self.id)]
        action["context"] = {}
        action["views"] = [(False, "list"), (False, "pivot"), (False, "graph")]
        return action

    def action_open_event(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "event.event",
            "res_id": self.event_id.id,
            "view_mode": "form",
            "context": {"active_test": False},
        }
