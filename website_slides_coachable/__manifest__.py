{
    "name": "eLearning: Coachable Play Preview",
    "summary": "Show Coachable play animations in quiz questions and video lessons",
    "description": """
Adds a Play Link field to eLearning quiz questions, and accepts Coachable links in
the Video Link field of video content. The animation is cropped to the playing
field so that the name of the play stays hidden. Only the link is stored in Odoo.
""",
    # No series prefix: Odoo adds its own, so the same module installs on 19 and 20.
    "version": "1.0.0",
    "category": "Website/eLearning",
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    "depends": ["website_slides"],
    "data": [
        "views/slide_question_views.xml",
        "views/slide_slide_views.xml",
        "views/website_slides_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "website_slides_coachable/static/src/scss/play_frame.scss",
        ],
        "web.assets_frontend": [
            "website_slides_coachable/static/src/scss/play_frame.scss",
            "website_slides_coachable/static/src/xml/slide_quiz.xml",
        ],
    },
    "installable": True,
    "application": False,
}
