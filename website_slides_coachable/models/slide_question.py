import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Only Coachable's shared plays can be embedded: this address ends up as the source
# of a frame on the quiz page, so it must not be able to point anywhere else.
COACHABLE_URL = re.compile(r"https://(www\.)?coachableplays\.com/shared/[A-Za-z0-9_-]+/?")


class SlideQuestion(models.Model):
    _inherit = "slide.question"

    coachable_url = fields.Char(
        string="Play Link",
        help="Address of a shared play on Coachable. It appears between the question and "
             "its answers, cropped so that the name of the play stays hidden.",
    )
    coachable_preview = fields.Html(
        string="Play Preview", compute="_compute_coachable_preview", sanitize=False,
    )

    @api.depends("coachable_url")
    def _compute_coachable_preview(self):
        for question in self:
            url = (question.coachable_url or "").strip()
            question.coachable_preview = (
                Markup(
                    '<div class="o_wslides_coachable_frame o_wslides_coachable_frame_small">'
                    '<iframe src="{}" title="Play animation" tabindex="-1" scrolling="no" '
                    'loading="lazy" referrerpolicy="no-referrer" '
                    'sandbox="allow-scripts allow-same-origin"></iframe></div>'
                ).format(url)
                if COACHABLE_URL.fullmatch(url) else False
            )

    @api.constrains("coachable_url")
    def _check_coachable_url(self):
        for question in self:
            if question.coachable_url and not COACHABLE_URL.fullmatch(question.coachable_url):
                raise ValidationError(_(
                    "The play link must look like https://coachableplays.com/shared/... "
                    "Copy it from the Share option of the play in Coachable."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("coachable_url"):
                vals["coachable_url"] = vals["coachable_url"].strip()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("coachable_url"):
            vals = dict(vals, coachable_url=vals["coachable_url"].strip())
        return super().write(vals)
