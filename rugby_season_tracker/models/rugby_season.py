from odoo import api, fields, models


class RugbySeason(models.Model):
    _name = "rugby.season"
    _description = "Rugby Season"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"
    # Seasons are shown as "Team Season", so find them by either part.
    _rec_names_search = ["name", "team_id.name"]

    name = fields.Char(required=True, tracking=True, help="For example: Fall 2026")
    team_id = fields.Many2one("rugby.team", required=True, tracking=True, index=True)
    date_start = fields.Date("Start Date", tracking=True)
    date_end = fields.Date("End Date", tracking=True)
    active = fields.Boolean(default=True)
    match_ids = fields.One2many("rugby.match", "season_id", string="Matches")

    match_count = fields.Integer(compute="_compute_totals")
    matches_played = fields.Integer("Played", compute="_compute_totals")
    wins = fields.Integer(compute="_compute_totals")
    losses = fields.Integer(compute="_compute_totals")
    draws = fields.Integer(compute="_compute_totals")
    record = fields.Char("Record (W-L-D)", compute="_compute_totals")
    points_for = fields.Integer(compute="_compute_totals")
    points_against = fields.Integer(compute="_compute_totals")
    points_difference = fields.Integer(compute="_compute_totals")
    tries = fields.Integer(compute="_compute_totals", help="Tries scored, from the lineups")

    _check_dates = models.Constraint(
        "CHECK(date_end IS NULL OR date_start IS NULL OR date_end >= date_start)",
        "The season can't end before it starts.",
    )

    @api.depends("name", "team_id.name")
    def _compute_display_name(self):
        for season in self:
            season.display_name = " ".join(filter(None, [season.team_id.name, season.name]))

    @api.depends(
        "match_ids.state", "match_ids.result", "match_ids.score_for",
        "match_ids.score_against", "match_ids.lineup_ids.tries",
    )
    def _compute_totals(self):
        for season in self:
            played = season.match_ids.filtered(lambda match: match.state == "played")
            season.match_count = len(season.match_ids)
            season.matches_played = len(played)
            season.wins = len(played.filtered(lambda match: match.result == "win"))
            season.losses = len(played.filtered(lambda match: match.result == "loss"))
            season.draws = len(played.filtered(lambda match: match.result == "draw"))
            season.record = f"{season.wins}-{season.losses}-{season.draws}"
            season.points_for = sum(played.mapped("score_for"))
            season.points_against = sum(played.mapped("score_against"))
            season.points_difference = season.points_for - season.points_against
            season.tries = sum(played.lineup_ids.mapped("tries"))

    def write(self, vals):
        result = super().write(vals)
        if "team_id" in vals:
            self.match_ids._sync_event()  # match names include the team name
        return result

    def action_view_matches(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("rugby_season_tracker.action_rugby_match")
        action["domain"] = [("season_id", "=", self.id)]
        action["context"] = {"default_season_id": self.id}
        return action
