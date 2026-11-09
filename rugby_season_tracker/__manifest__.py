{
    "name": "Season Tracker",
    "summary": "Rugby fixtures, results, lineups and player stats, with an event for every match",
    "description": """
Enter a team's games for the season. Each match creates its own event (kept off
the website until you publish it) and keeps it in step when the match changes.
After the game, record the score, the lineup and each player's minutes, tackles
and tries, plus a link to the match recording.
""",
    "version": "20.0.1.0.0",
    "category": "Marketing/Events",
    "sequence": 50,
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    # website_event: matches become events, and "Show on Website" publishes them.
    "depends": ["website_event", "mail"],
    "data": [
        "security/ir.access.csv",
        "data/rugby_data.xml",
        "views/rugby_team_views.xml",
        "views/rugby_match_player_views.xml",
        "views/rugby_match_views.xml",
        "views/rugby_season_views.xml",
        "views/rugby_menus.xml",
    ],
    "installable": True,
    "application": True,
}
