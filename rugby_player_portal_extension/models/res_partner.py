import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Handles on Instagram, TikTok and X: letters, numbers, periods and underscores, at most 30.
HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# A pasted profile link: keep only the part after the site's address.
PROFILE_LINK = re.compile(r"^(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}/@?([^/?#]+)", re.IGNORECASE)
SOCIAL_FIELDS = {"social_tagging", "social_handle"}


def normalize_handle(value):
    """'  https://instagram.com/jane.doe/ ' or 'jane.doe' -> '@jane.doe'. Empty stays empty."""
    value = (value or "").strip()
    if not value:
        return False
    found = PROFILE_LINK.match(value)
    if found:
        value = found.group(1)
    return "@" + value.lstrip("@").strip()


class ResPartner(models.Model):
    _inherit = "res.partner"

    social_tagging = fields.Selection(
        # Staff-facing labels; the portal asks the question in its own words.
        [("yes", "Can Tag"), ("no", "Don't Tag")],
        string="Social Media Tagging", tracking=True, copy=False, index=True,
        help="The person's own answer to \"Can we tag you on social media?\". "
             "Empty means they haven't answered yet: don't tag them.",
    )
    social_handle = fields.Char(
        "Social Media Handle", copy=False,
        help="The account to tag, for example @jane.doe. Only kept while tagging is allowed.",
    )
    social_tagging_date = fields.Datetime(
        "Tagging Answered On", readonly=True, copy=False,
        help="When the tagging answer was last given or changed.",
    )

    @api.constrains("social_tagging", "social_handle")
    def _check_social_handle(self):
        for partner in self:
            if partner.social_tagging != "yes":
                continue
            if not partner.social_handle:
                raise ValidationError(self.env._(
                    "%s agreed to be tagged on social media: add their handle, for example @jane.doe.",
                    partner.display_name,
                ))
            if not HANDLE.match(partner.social_handle.lstrip("@")):
                raise ValidationError(self.env._(
                    "\"%s\" isn't a valid handle. Use letters, numbers, periods and underscores only "
                    "(30 at most), for example @jane.doe.", partner.social_handle,
                ))

    def _prepare_social_values(self, vals):
        """Tidy the handle, forget it when tagging is refused, and date the answer."""
        vals = dict(vals)
        if "social_handle" in vals:
            vals["social_handle"] = normalize_handle(vals["social_handle"])
        if vals.get("social_tagging") in ("no", False) and "social_tagging" in vals:
            # Refused (or reset): the handle isn't needed, so it isn't kept.
            vals["social_handle"] = False
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._prepare_social_values(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("social_tagging"):
                vals["social_tagging_date"] = fields.Datetime.now()
        return super().create(vals_list)

    def write(self, vals):
        if not SOCIAL_FIELDS & set(vals):
            return super().write(vals)
        vals = self._prepare_social_values(vals)
        if "social_tagging" in vals:
            changed = self.filtered(lambda partner: partner.social_tagging != vals["social_tagging"])
            same = self - changed
            result = True
            if changed:
                result = super(ResPartner, changed).write({**vals, "social_tagging_date": fields.Datetime.now()})
            if same:
                result = super(ResPartner, same).write(vals) and result
            return result
        return super().write(vals)

    def _get_frontend_writable_fields(self):
        return super()._get_frontend_writable_fields() | SOCIAL_FIELDS
