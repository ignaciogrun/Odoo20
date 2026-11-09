from odoo import fields, models


class RugbyTeam(models.Model):
    _name = "rugby.team"
    _description = "Rugby Team"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    season_ids = fields.One2many("rugby.season", "team_id", string="Seasons")

    _name_unique = models.Constraint("UNIQUE(name)", "A team with this name already exists.")

    def write(self, vals):
        result = super().write(vals)
        if "name" in vals:
            # Match names (and their events) include the team name.
            self.env["rugby.match"].search([("team_id", "in", self.ids)])._sync_event()
        return result
