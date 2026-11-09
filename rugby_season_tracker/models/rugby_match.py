import re
from datetime import timedelta

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
            match.recording_url = found.group(1) if found and found.re is LINK_TARGET else (found and found.group(0))

    @api.depends("lineup_ids")
    def _compute_lineup_count(self):
        for match in self:
            match.lineup_count = len(match.lineup_ids)

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

    def action_open_event(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "event.event",
            "res_id": self.event_id.id,
            "view_mode": "form",
            "context": {"active_test": False},
        }
