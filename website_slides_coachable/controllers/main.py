from odoo import http
from odoo.http import request

from odoo.addons.website_slides.controllers.main import WebsiteSlides


class WebsiteSlidesCoachable(WebsiteSlides):
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
