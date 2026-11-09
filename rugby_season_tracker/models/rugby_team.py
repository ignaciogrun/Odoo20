from odoo import api, fields, models
from odoo.tools.translate import html_translate

# Changing any of these changes the team's website menu item.
MENU_FIELDS = {"name", "website_slug", "is_published", "website_published", "website_menu_parent_id", "active"}


class RugbyTeam(models.Model):
    _name = "rugby.team"
    _description = "Rugby Team"
    _inherit = ["website.published.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    season_ids = fields.One2many("rugby.season", "team_id", string="Seasons")

    # Website ------------------------------------------------------------
    website_slug = fields.Char(
        "Web Address", compute="_compute_website_slug", store=True, readonly=False, copy=False,
        help="The end of the page's address, for example mens-a for /schedule/mens-a. "
             "It's filled in from the team name once and then kept, so shared links keep working.",
    )
    # A block area on the team's page (like a product page's description): text,
    # photos or an Event block filtered by the team's tag. Edited on the website.
    website_description = fields.Html(
        "Page Content", translate=html_translate, sanitize_overridable=True, sanitize_attributes=False,
        sanitize_form=False,
        help="Shown above the results table. Edit it on the website, where you can drop in blocks "
             "such as an Event block filtered by this team's tag.",
    )
    website_menu_parent_id = fields.Many2one(
        "website.menu", string="Show in Menu Under", domain=[("parent_id", "!=", False)],
        help="The website menu this team's page appears under, for example Schedule & Results.",
    )
    website_menu_id = fields.Many2one(
        "website.menu", string="Website Menu Item", readonly=True, copy=False, ondelete="set null",
    )
    event_tag_id = fields.Many2one(
        "event.tag", string="Event Tag", readonly=True, copy=False, ondelete="set null",
        help="Added to this team's match events, so an Event block can show just this team's games.",
    )

    _name_unique = models.Constraint("UNIQUE(name)", "A team with this name already exists.")
    _slug_unique = models.Constraint("UNIQUE(website_slug)", "Another team already uses this web address.")

    @api.depends("name")
    def _compute_website_slug(self):
        for team in self:
            if not team.website_slug and team.name:
                team.website_slug = self.env["ir.http"]._slugify(team.name)

    @api.depends("website_slug")
    def _compute_website_url(self):
        for team in self:
            team.website_url = f"/schedule/{team.website_slug}" if team.website_slug else "#"

    def _sync_website_menu(self):
        """Keep each team's menu item matching its name, address and publish state.

        Done as superuser, so people who manage teams don't also need website
        editing rights. The label is saved in English only: other languages show
        it until someone translates it in the website editor, and a later rename
        doesn't overwrite that translation.
        """
        Menu = self.env["website.menu"].sudo().with_context(lang="en_US")
        for team in self:
            menu = team.website_menu_id.sudo().with_context(lang="en_US")
            parent = team.website_menu_parent_id.sudo()
            if not (parent and team.is_published and team.active and team.website_slug):
                menu.unlink()
                continue
            values = {"name": team.name, "url": team.website_url, "parent_id": parent.id,
                      "website_id": parent.website_id.id}
            if menu:
                menu.write(values)
            else:
                last = max(parent.child_id.mapped("sequence"), default=0)
                menu = Menu.create({**values, "sequence": last + 1})
                super(RugbyTeam, team).write({"website_menu_id": menu.id})

    def _sync_event_tag(self):
        """Give each team an event tag named after it, in the "Teams" category."""
        category = self.env.ref("rugby_season_tracker.event_tag_category_teams")
        Tag = self.env["event.tag"].sudo().with_context(lang="en_US")
        for team in self:
            tag = team.event_tag_id.sudo().with_context(lang="en_US")
            if tag:
                tag.name = team.name
            else:
                tag = Tag.create({"name": team.name, "category_id": category.id})
                super(RugbyTeam, team).write({"event_tag_id": tag.id})

    @api.model
    def _setup_event_tags(self):
        """Run on install and upgrade: tag every team, then every match event."""
        teams = self.with_context(active_test=False).search([])
        teams._sync_event_tag()
        self.env["rugby.match"].with_context(active_test=False).search([("team_id", "in", teams.ids)])._sync_event()

    @api.model_create_multi
    def create(self, vals_list):
        teams = super().create(vals_list)
        teams._sync_event_tag()
        teams._sync_website_menu()
        return teams

    def write(self, vals):
        result = super().write(vals)
        if "name" in vals:
            self._sync_event_tag()
            # Match names (and their events) include the team name.
            self.env["rugby.match"].search([("team_id", "in", self.ids)])._sync_event()
        if MENU_FIELDS & set(vals):
            self._sync_website_menu()
        return result

    def unlink(self):
        menus = self.website_menu_id.sudo()
        result = super().unlink()
        menus.unlink()
        return result
