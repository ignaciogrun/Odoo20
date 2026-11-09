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
    tackles = fields.Integer("Tackles", aggregator="sum")
    tries = fields.Integer("Tries", aggregator="sum")
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
