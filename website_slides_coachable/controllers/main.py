from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

from odoo.addons.website_slides.controllers.main import WebsiteSlides

from ..models.coachable import is_coachable_url


class WebsiteSlidesCoachable(WebsiteSlides):
    @http.route(
        "/website_slides_coachable/player/<int:slide_id>",
        type="http", auth="public", website=True, methods=["GET"], sitemap=False,
    )
    def coachable_player(self, slide_id, **kwargs):
        """Page holding the cropped animation of a video lesson, shown inside a frame."""
        fetch_result = self._fetch_slide(slide_id)
        slide = fetch_result.get("slide")
        if not slide or slide.slide_category != "video" or not is_coachable_url(slide.video_url):
            raise NotFound()
        channel = slide.channel_id
        # The same rule the lesson pages use to decide who may see the content.
        if not (slide.is_preview or channel.is_member or channel.can_publish):
            raise NotFound()
        return request.render("website_slides_coachable.player_page", {
            "play_url": slide.video_url.strip(),
        })

    def _get_slide_quiz_data(self, slide):
        values = super()._get_slide_quiz_data(slide)
        links = {question.id: question.coachable_url for question in slide.question_ids}
        for question in values["slide_questions"]:
            question["coachable_url"] = links.get(question["id"]) or False
        return values

    @http.route()
    def slide_quiz_question_add_or_update(
        self, slide_id, question, sequence, answer_ids, existing_question_id=None,
    ):
        # Editing a question on the website deletes it and creates a new one from the
        # values of that editor, which has no Play Link field. Without this, the
        # link would be lost every time someone fixed a typo there.
        if existing_question_id:
            existing = request.env["slide.question"].search([
                ("slide_id", "=", int(slide_id)),
                ("id", "=", int(existing_question_id)),
            ])
            if existing.coachable_url:
                request.update_context(default_coachable_url=existing.coachable_url)
        return super().slide_quiz_question_add_or_update(
            slide_id, question, sequence, answer_ids, existing_question_id=existing_question_id,
        )
