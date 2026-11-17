import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

HALVES = [("1", "1st Half"), ("2", "2nd Half")]
SIDES = [("own", "Own"), ("opp", "Opp")]
QUALITIES = [("positive", "Positive"), ("neutral", "Neutral"), ("negative", "Negative")]
# Which extra columns an event asks for (the coloured columns of the coding sheet).
DETAILS = [
    ("carry", "Carry meters"),
    ("kick", "Kick meters"),
    ("try", "Try source, origin, location and build-up"),
    ("offence", "Offence detail"),
    ("lineout", "Lineout numbers"),
]
OPTION_KINDS = [
    ("try_source", "Try Source"),
    ("try_origin", "Try Origin"),
    ("try_location", "Try Location"),
    ("offence", "Offence Detail"),
]
# Video time as typed on the coding sheet: minutes:seconds, e.g. 3:04 or 54:12.
VIDEO_TIME = re.compile(r"^\s*(\d{1,3}):([0-5]\d)\s*$")


class RugbyEventCategory(models.Model):
    _name = "rugby.event.category"
    _description = "Match Event Category"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer()

    _name_unique = models.Constraint("UNIQUE(name)", "This category already exists.")


class RugbyEventType(models.Model):
    _name = "rugby.event.type"
    _description = "Match Event Type"
    _order = "sequence, name"

    name = fields.Char("Event", required=True)
    category_ids = fields.Many2many("rugby.event.category", string="Category")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    definition = fields.Text(
        "Coding Rule", help="When to log this event (paraphrased from the World Rugby Game Analysis definitions).",
    )
    detail = fields.Selection(
        DETAILS, string="Extra Detail", help="The extra columns filled in on rows of this event.",
    )
    points = fields.Integer(help="Points this event adds to the score worked out from the event log.")
    # The statistics look events up by this key, not by name, so an event can be
    # renamed without breaking player stats or the match summary.
    code = fields.Char(
        "Statistics Key", copy=False, readonly=True,
        help="Set on the built-in events. Player stats and the match summary use it, "
             "so renaming the event keeps the numbers working.",
    )

    _name_unique = models.Constraint("UNIQUE(name)", "An event with this name already exists.")
    _code_unique = models.Constraint("UNIQUE(code)", "Another event already uses this statistics key.")


class RugbyEventOption(models.Model):
    _name = "rugby.event.option"
    _description = "Match Event Detail Option"
    _order = "kind, sequence, name"

    name = fields.Char(required=True)
    kind = fields.Selection(OPTION_KINDS, string="List", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint("UNIQUE(kind, name)", "This option is already on the list.")


class RugbyMatchEvent(models.Model):
    _name = "rugby.match.event"
    _description = "Match Event Log"
    _order = "match_id, sequence, id"
    _rec_name = "event_type_id"

    match_id = fields.Many2one("rugby.match", required=True, index=True, ondelete="cascade")
    sequence = fields.Integer("#", help="Row number: the order the events were logged in.")
    half = fields.Selection(HALVES, required=True, default="1")
    video_time = fields.Char("Video Time", help="Time on the match video as minutes:seconds, for example 3:04 or 54:12.")
    video_seconds = fields.Integer("Secs", compute="_compute_video_seconds", store=True)
    video_url = fields.Char("Video Link", compute="_compute_video_url")
    side = fields.Selection(
        SIDES, string="Team", required=True, default="own",
        help="Own = our team. Opp = the opposition: an 'Own Scrum' logged as Opp is the opposition's own scrum.",
    )
    jersey_number = fields.Integer("Player #")
    lineup_id = fields.Many2one(
        "rugby.match.player", string="Team Sheet Line", compute="_compute_lineup_id", store=True, index=True,
    )
    player_id = fields.Many2one(related="lineup_id.player_id", store=True, string="Player")
    event_type_id = fields.Many2one("rugby.event.type", string="Event", required=True, index=True, ondelete="restrict")
    event_code = fields.Char(related="event_type_id.code", store=True, index=True)
    category_ids = fields.Many2many(related="event_type_id.category_ids", string="Category")
    detail = fields.Selection(related="event_type_id.detail")
    quality = fields.Selection(QUALITIES, help="Optional film-review judgement, e.g. a carry over the gain line = Positive.")
    offence_id = fields.Many2one("rugby.event.option", string="Offence Detail", domain=[("kind", "=", "offence")])
    try_source_id = fields.Many2one("rugby.event.option", string="Try Source", domain=[("kind", "=", "try_source")])
    try_origin_id = fields.Many2one("rugby.event.option", string="Try Origin", domain=[("kind", "=", "try_origin")])
    try_location_id = fields.Many2one("rugby.event.option", string="Try Location", domain=[("kind", "=", "try_location")])
    buildup_rucks = fields.Integer("Build-up Rucks/Mauls")
    buildup_passes = fields.Integer("Build-up Passes")
    lineout_numbers = fields.Integer("Lineout Numbers", help="How many players took part in the lineout.")
    carry_meters = fields.Float("Carry Meters", help="Metres gained on the carry; negative if the carrier went backwards.")
    kick_meters = fields.Float("Kick Meters", help="Distance the kick travelled.")
    notes = fields.Char()

    # Copied from the match so the log can be filtered and grouped across matches.
    season_id = fields.Many2one(related="match_id.season_id", store=True, index=True)
    team_id = fields.Many2one(related="match_id.team_id", store=True, index=True, string="Club Team")
    kickoff = fields.Datetime(related="match_id.kickoff", store=True)

    _check_numbers = models.Constraint(
        "CHECK(jersey_number >= 0 AND buildup_rucks >= 0 AND buildup_passes >= 0 "
        "AND lineout_numbers >= 0 AND kick_meters >= 0)",
        "Player numbers, build-up counts, lineout numbers and kick meters can't be negative.",
    )

    @api.constrains("video_time")
    def _check_video_time(self):
        for event in self:
            if event.video_time and not VIDEO_TIME.match(event.video_time):
                raise ValidationError(self.env._(
                    "Video time \"%s\" isn't minutes:seconds. Type it like 3:04 or 54:12.", event.video_time,
                ))

    @api.depends("video_time")
    def _compute_video_seconds(self):
        for event in self:
            found = VIDEO_TIME.match(event.video_time or "")
            event.video_seconds = int(found.group(1)) * 60 + int(found.group(2)) if found else 0

    @api.depends("video_time", "video_seconds", "match_id.recording_url")
    def _compute_video_url(self):
        # Opens the match recording at this moment, as Veo and YouTube links allow.
        for event in self:
            base = event.match_id.recording_url
            if base and event.video_time:
                joiner = "&" if "?" in base else "?"
                event.video_url = f"{base}{joiner}t={event.video_seconds}s"
            else:
                event.video_url = False

    @api.depends("side", "jersey_number", "match_id.lineup_ids.jersey_number")
    def _compute_lineup_id(self):
        # Our own players are found by the jersey number on the match's team sheet.
        for event in self:
            line = event.match_id.lineup_ids.filtered(
                lambda line: line.jersey_number == event.jersey_number
            ) if event.side == "own" and event.jersey_number else self.env["rugby.match.player"]
            event.lineup_id = line[:1]

    @api.model_create_multi
    def create(self, vals_list):
        # New rows go to the end of their match's log.
        last = {}
        for vals in vals_list:
            if not vals.get("sequence") and vals.get("match_id"):
                match_id = vals["match_id"]
                if match_id not in last:
                    newest = self.search([("match_id", "=", match_id)], order="sequence desc", limit=1)
                    last[match_id] = newest.sequence
                last[match_id] += 1
                vals["sequence"] = last[match_id]
        return super().create(vals_list)
