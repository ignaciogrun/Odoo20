from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .coachable import COACHABLE_URL, coachable_frame


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
            question.coachable_preview = coachable_frame(question.coachable_url, small=True)

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
