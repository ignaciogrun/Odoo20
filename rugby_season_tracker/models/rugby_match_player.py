from odoo import Command, api, fields, models

POSITIONS = [
    ("loosehead_prop", "Loosehead Prop"),
    ("hooker", "Hooker"),
    ("tighthead_prop", "Tighthead Prop"),
    ("lock", "Lock"),
    ("blindside_flanker", "Blindside Flanker"),
    ("openside_flanker", "Openside Flanker"),
    ("number_8", "Number 8"),
    ("scrum_half", "Scrum-half"),
    ("fly_half", "Fly-half"),
    ("left_wing", "Left Wing"),
    ("inside_center", "Inside Center"),
    ("outside_center", "Outside Center"),
    ("right_wing", "Right Wing"),
    ("fullback", "Fullback"),
    ("replacement", "Replacement"),
]
# Standard rugby union numbering: 1-15 start, 16 and up are replacements.
NUMBER_POSITIONS = {
    1: "loosehead_prop", 2: "hooker", 3: "tighthead_prop", 4: "lock", 5: "lock",
    6: "blindside_flanker", 7: "openside_flanker", 8: "number_8", 9: "scrum_half",
    10: "fly_half", 11: "left_wing", 12: "inside_center", 13: "outside_center",
    14: "right_wing", 15: "fullback",
}
STARTERS = 15
# Events whose Kick Meters count towards a player's kick meters.
KICK_CODES = ("kick", "penalty_kicked", "free_kick_kicked", "own_kick_off_restart")


class RugbyMatchPlayer(models.Model):
    _name = "rugby.match.player"
    _description = "Match Lineup and Player Stats"
    _order = "match_id, jersey_number, id"
    _rec_name = "player_id"

    match_id = fields.Many2one("rugby.match", required=True, index=True, ondelete="cascade")
    jersey_number = fields.Integer("Jersey", required=True)
    player_id = fields.Many2one(
        "res.partner", string="Player", required=True, index=True, domain=[("is_company", "=", False)],
    )
    position = fields.Selection(POSITIONS, compute="_compute_position", store=True, readonly=False)
    starter = fields.Boolean(compute="_compute_starter", store=True)
    minutes_played = fields.Integer("Minutes", aggregator="sum")

    # Stats from the match's event log (our own events with this jersey number).
    event_ids = fields.One2many("rugby.match.event", "lineup_id", string="Events")
    # Tackles and tries were typed by hand before the event log existed. A match
    # with no event log keeps what was typed; once a log exists it is the source.
    tackles = fields.Integer(
        "Tackles", compute="_compute_event_stats", store=True, readonly=False, aggregator="sum",
        help="Tackler events. Typed by hand only on matches without an event log.",
    )
    tries = fields.Integer(
        "Tries", compute="_compute_event_stats", store=True, readonly=False, aggregator="sum",
        help="Try Scored events. Typed by hand only on matches without an event log.",
    )
    carries = fields.Integer("Ball Carries", compute="_compute_event_stats", store=True, aggregator="sum")
    carry_meters = fields.Float("Carry Meters", compute="_compute_event_stats", store=True, aggregator="sum")
    meters_per_carry = fields.Float("Meters / Carry", compute="_compute_event_stats", store=True, aggregator="avg")
    passes = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    offloads = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    ruck_arrivals = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    assist_tackles = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    missed_tackles = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    tackle_rate = fields.Float(
        "Tackle %", compute="_compute_event_stats", store=True, aggregator="avg",
        help="(Tackler + Assist Tackler) / (Tackler + Assist Tackler + Missed Tackle)",
    )
    jackles = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    jackle_attempts = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    kicks = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    kick_meters = fields.Float(
        compute="_compute_event_stats", store=True, aggregator="sum",
        help="Meters on Kick, Penalty Kicked, Free Kick Kicked and Own Kick Off/Restart events.",
    )
    meters_per_kick = fields.Float("Meters / Kick", compute="_compute_event_stats", store=True, aggregator="avg")
    knock_ons = fields.Integer("Knock-ons", compute="_compute_event_stats", store=True, aggregator="sum")
    forward_passes = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    penalties_conceded = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    yellow_cards = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    red_cards = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    conversions = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    penalty_goals = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    drop_goals = fields.Integer(compute="_compute_event_stats", store=True, aggregator="sum")
    points = fields.Integer(
        compute="_compute_event_stats", store=True, aggregator="sum",
        help="Try 5, conversion 2, penalty goal 3, drop goal 3.",
    )
    # Always 1, so adding it up per player counts the matches they appeared in.
    appearances = fields.Integer(compute="_compute_appearances", store=True, aggregator="sum")

    # Copied from the match so stats can be filtered and grouped by them.
    season_id = fields.Many2one(related="match_id.season_id", store=True, index=True)
    team_id = fields.Many2one(related="match_id.team_id", store=True, index=True)
    kickoff = fields.Datetime(related="match_id.kickoff", store=True)
    match_state = fields.Selection(related="match_id.state", store=True, string="Match Status")
    result = fields.Selection(related="match_id.result", store=True)

    _player_once = models.Constraint(
        "UNIQUE(match_id, player_id)", "A player can only be in a match's lineup once.",
    )
    _jersey_once = models.Constraint(
        "UNIQUE(match_id, jersey_number)", "Each jersey number can only be used once per match.",
    )
    _check_numbers = models.Constraint(
        "CHECK(jersey_number > 0 AND minutes_played >= 0 AND tackles >= 0 AND tries >= 0)",
        "Jersey numbers must be above zero, and minutes, tackles and tries can't be negative.",
    )

    @api.depends("jersey_number")
    def _compute_position(self):
        for line in self:
            line.position = NUMBER_POSITIONS.get(line.jersey_number, "replacement")

    @api.depends("match_id")
    def _compute_appearances(self):
        self.appearances = 1

    @api.depends("event_ids.event_type_id", "event_ids.carry_meters", "event_ids.kick_meters", "match_id.event_ids")
    def _compute_event_stats(self):
        for line in self:
            codes = [event.event_code or "" for event in line.event_ids]
            count = codes.count
            tackles, assists, missed = count("tackler"), count("assist_tackler"), count("missed_tackle")
            carries, tries = count("ball_carry"), count("try_scored")
            kick_events = line.event_ids.filtered(lambda event: event.event_code in KICK_CODES)
            if line.match_id.event_ids:
                line.tackles, line.tries = tackles, tries
            else:
                # No event log on this match: keep the numbers typed by hand.
                line.tackles, line.tries = line.tackles, line.tries
            line.carries = carries
            line.carry_meters = sum(line.event_ids.filtered(lambda event: event.event_code == "ball_carry").mapped("carry_meters"))
            line.meters_per_carry = line.carry_meters / carries if carries else 0
            line.passes, line.offloads = count("pass"), count("offload")
            line.ruck_arrivals = count("ruck_arrival")
            line.assist_tackles, line.missed_tackles = assists, missed
            attempts = tackles + assists + missed
            line.tackle_rate = (tackles + assists) / attempts if attempts else 0
            line.jackles, line.jackle_attempts = count("jackle"), count("jackle_attempt")
            line.kicks = count("kick")
            line.kick_meters = sum(kick_events.mapped("kick_meters"))
            line.meters_per_kick = line.kick_meters / len(kick_events) if kick_events else 0
            line.knock_ons, line.forward_passes = count("knockon"), count("forward_pass")
            line.penalties_conceded = sum(code.startswith("penalty_conceded") for code in codes)
            line.yellow_cards, line.red_cards = count("yellow_card"), count("red_card")
            line.conversions = count("conversion_scored")
            line.penalty_goals, line.drop_goals = count("penalty_to_goal_scored"), count("drop_goal_scored")
            line.points = 5 * tries + 2 * line.conversions + 3 * line.penalty_goals + 3 * line.drop_goals

    @api.depends("jersey_number")
    def _compute_starter(self):
        for line in self:
            line.starter = 0 < line.jersey_number <= STARTERS

    def _tag_players(self):
        tag = self.env.ref("rugby_season_tracker.partner_category_player", raise_if_not_found=False)
        if tag:
            untagged = self.player_id.filtered(lambda partner: tag not in partner.category_id)
            untagged.sudo().write({"category_id": [Command.link(tag.id)]})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._tag_players()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if "player_id" in vals:
            self._tag_players()
        return result
