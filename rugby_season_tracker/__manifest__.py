{
    "name": "Season Tracker",
    "summary": "Rugby fixtures, results, team sheets, a coded event log and player stats",
    "description": """
Enter a team's games for the season. Each match creates its own event (kept off
the website until you publish it) and keeps it in step when the match changes.
After the game, record the score, the team sheet and a link to the match
recording. The Event Log codes the match video one event per row (World Rugby
Game Analysis definitions) or imports the film-review Excel sheet; player stats
and the match summary are worked out from it. Each team can have its own public
Schedule & Results page listing its upcoming matches and the season's results.
""",
    "version": "20.0.1.2.0",
    "category": "Marketing/Events",
    "sequence": 50,
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    # website_event: matches become events, and "Show on Website" publishes them.
    "depends": ["website_event", "mail"],
    "data": [
        "security/ir.access.csv",
        "data/rugby_data.xml",
        "data/rugby_event_data.xml",
        "views/rugby_event_views.xml",
        "views/rugby_team_views.xml",
        "views/rugby_match_player_views.xml",
        "views/rugby_match_views.xml",
        "views/rugby_season_views.xml",
        "views/rugby_menus.xml",
        "views/rugby_website_templates.xml",
    ],
    "installable": True,
    "application": True,
}
