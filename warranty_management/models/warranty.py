from datetime import timedelta

from odoo import api, fields, models

# A warranty moves to "Expiring in 30 Days" when this many days or fewer are left.
EXPIRING_DAYS = 30


class Warranty(models.Model):
    _name = "warranty.warranty"
    _description = "Warranty"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiration_date, id"

    name = fields.Char("Title", required=True, tracking=True)
    active = fields.Boolean(default=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    expiration_date = fields.Date(required=True, tracking=True)
    receipt_image = fields.Image("Receipt", max_width=1920, max_height=1920)
    # Small copy for the cards, so the board doesn't load every full-size receipt.
    receipt_image_128 = fields.Image(
        "Receipt Thumbnail", related="receipt_image", max_width=128, max_height=128, store=True,
    )
    place_of_purchase = fields.Char("Place of Purchase", tracking=True)
    holder_id = fields.Many2one("res.partner", string="Warranty Holder", tracking=True, index=True)
    claim_link = fields.Html("Claim Link")
    # Stored so the board can group by it. It depends on today's date, which no
    # field change announces, so a scheduled job moves cards as the days pass.
    state = fields.Selection(
        [
            ("in_progress", "In Progress"),
            ("expiring", "Expiring in 30 Days"),
            ("expired", "Expired"),
        ],
        string="Status",
        compute="_compute_state",
        store=True,
        index=True,
        tracking=True,
        group_expand=True,  # always show all three columns, even empty ones
    )
    days_left = fields.Integer(compute="_compute_days_left")

    _check_dates = models.Constraint(
        "CHECK(expiration_date >= start_date)",
        "The expiration date can't be before the start date.",
    )

    @api.model
    def _state_on(self, expiration_date, today):
        if expiration_date < today:
            return "expired"
        if expiration_date <= today + timedelta(days=EXPIRING_DAYS):
            return "expiring"
        return "in_progress"

    @api.depends("expiration_date")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for warranty in self:
            warranty.state = (
                self._state_on(warranty.expiration_date, today) if warranty.expiration_date else "in_progress"
            )

    @api.depends("expiration_date")
    def _compute_days_left(self):
        today = fields.Date.context_today(self)
        for warranty in self:
            warranty.days_left = (warranty.expiration_date - today).days if warranty.expiration_date else 0

    @api.model
    def _cron_update_state(self):
        """Move the warranties whose column is out of date because days have passed."""
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=EXPIRING_DAYS)
        outdated = self.with_context(active_test=False).search([
            "|",
            "&", ("state", "!=", "expired"), ("expiration_date", "<", today),
            "&", ("state", "=", "in_progress"), ("expiration_date", "<=", soon),
        ])
        if outdated:
            # Edits record history on their own; a recalculation doesn't, so ask
            # for it. The panel then shows e.g. "In Progress -> Expiring in 30 Days".
            outdated._track_prepare(["state"])
            self.env.add_to_compute(self._fields["state"], outdated)
            outdated.flush_recordset(["state"])
        return outdated
