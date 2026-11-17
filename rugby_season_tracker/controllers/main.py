from odoo import fields, http
from odoo.http import request


def sitemap_teams(env, rule, qs):
    for team in env["rugby.team"].sudo().search([("is_published", "=", True)]):
        if not qs or qs.lower() in team.website_url.lower():
            yield {"loc": team.website_url}


class RugbyTeamPages(http.Controller):

    @http.route("/schedule/<string:slug>", type="http", auth="public", website=True,
                sitemap=sitemap_teams, readonly=True)
    def team_page(self, slug, season=None, **kwargs):
        team = request.env["rugby.team"].sudo().search([("website_slug", "=", slug)], limit=1)
        # Unpublished teams are visible to staff only, so they can check the page first.
        if not team or not (team.is_published or request.env.user._is_internal()):
            raise request.not_found()

        # Results only: played matches switched to "Show Result on Website".
        # Upcoming games are the events' job (Event block filtered by team tag).
        played = request.env["rugby.match"].sudo().search([
            ("team_id", "=", team.id), ("state", "=", "played"), ("show_result", "=", True),
        ])

        # Seasons that have results, newest first; show the current one unless
        # the visitor picked another.
        seasons = played.season_id.sorted(lambda s: (s.date_start or fields.Date.today(), s.id), reverse=True)
        today = fields.Date.context_today(team)
        selected = seasons.filtered(lambda s: str(s.id) == str(season))[:1] or seasons.filtered(
            lambda s: s.date_start and s.date_start <= today and (not s.date_end or today <= s.date_end)
        )[:1] or seasons[:1]
        results = played.filtered(lambda match: match.season_id == selected).sorted("kickoff", reverse=True)

        return request.render("rugby_season_tracker.team_page", {
            "team": team,
            "main_object": team,
            "results": results,
            "seasons": seasons,
            "selected_season": selected,
            "summary": {
                "wins": len(results.filtered(lambda m: m.result == "win")),
                "losses": len(results.filtered(lambda m: m.result == "loss")),
                "draws": len(results.filtered(lambda m: m.result == "draw")),
                "points_for": sum(results.mapped("score_for")),
                "points_against": sum(results.mapped("score_against")),
            },
        })
