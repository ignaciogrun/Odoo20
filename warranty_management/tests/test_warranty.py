import io
from datetime import date, timedelta

from psycopg2 import IntegrityError
from PIL import Image

from odoo import fields
from odoo.tests import TransactionCase, freeze_time, tagged
from odoo.tools import BinaryBytes, mute_logger


def png(width=800, height=1200):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, "PNG")
    return BinaryBytes(buffer.getvalue())


@tagged("post_install", "-at_install")
class TestWarranty(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Warranty = cls.env["warranty.warranty"]
        cls.admin = cls.env.ref("base.user_admin")
        cls.cron = cls.env.ref("warranty_management.ir_cron_update_state")
        cls.holder = cls.env["res.partner"].create({"name": "Warranty Holder"})

    def make(self, expiration_date, **values):
        warranty = self.Warranty.create({
            "name": f"Item {expiration_date}",
            "start_date": expiration_date - timedelta(days=400),
            "expiration_date": expiration_date,
            **values,
        })
        # Like the end of a save: the column is worked out and the history written.
        self.env.flush_all()
        self.env.cr.precommit.run()
        return warranty

    def run_job(self):
        # What the scheduled job runs, as the user it runs as.
        return self.Warranty.with_user(self.cron.user_id)._cron_update_state()

    def test_columns_follow_the_expiration_date(self):
        today = fields.Date.context_today(self.Warranty)
        expected = {
            400: "in_progress",
            31: "in_progress",   # 31 days left: not yet expiring
            30: "expiring",      # 30 days left: expiring
            1: "expiring",
            0: "expiring",       # last day of coverage: still covered
            -1: "expired",
            -300: "expired",
        }
        for days_left, state in expected.items():
            with self.subTest(days_left=days_left):
                warranty = self.make(today + timedelta(days=days_left))
                self.assertEqual(warranty.state, state)
                self.assertEqual(warranty.days_left, days_left)

    def test_editing_the_expiration_date_moves_the_card(self):
        today = fields.Date.context_today(self.Warranty)
        warranty = self.make(today + timedelta(days=200))
        warranty.expiration_date = today + timedelta(days=10)
        self.assertEqual(warranty.state, "expiring")
        warranty.expiration_date = today - timedelta(days=1)
        self.assertEqual(warranty.state, "expired")
        warranty.expiration_date = today + timedelta(days=365)  # warranty extended
        self.assertEqual(warranty.state, "in_progress")

    def test_expiration_before_start_is_refused(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.Warranty.create({
                "name": "Backwards",
                "start_date": date(2026, 5, 2),
                "expiration_date": date(2026, 5, 1),
            })
            self.env.flush_all()

    def test_all_three_columns_always_show(self):
        self.Warranty.with_context(active_test=False).search([]).unlink()
        self.make(fields.Date.context_today(self.Warranty) + timedelta(days=100))
        # The board asks for empty columns with this context key.
        groups = self.Warranty.with_context(read_group_expand=True).formatted_read_group(
            [], ["state"], ["__count"],
        )
        self.assertEqual(
            [(group["state"], group["__count"]) for group in groups],
            [("in_progress", 1), ("expiring", 0), ("expired", 0)],
        )

    def test_the_job_runs_hourly_as_the_administrator(self):
        self.assertEqual(self.cron.user_id, self.admin)
        self.assertEqual((self.cron.interval_number, self.cron.interval_type), (1, "hours"))
        self.assertEqual(self.cron.code.strip(), "model._cron_update_state()")

    def test_the_job_moves_cards_as_days_pass(self):
        self.admin.tz = "UTC"
        with freeze_time("2026-03-01 12:00:00"):
            warranty = self.make(date(2026, 4, 1))  # 31 days left
            archived = self.make(date(2026, 4, 1), active=False)
            steady = self.make(date(2027, 4, 1))
        self.assertEqual(warranty.state, "in_progress")
        with freeze_time("2026-03-02 12:00:00"):  # 30 days left
            self.assertEqual(self.run_job(), warranty | archived)
        self.assertEqual(warranty.state, "expiring")
        self.assertEqual(archived.state, "expiring")
        with freeze_time("2026-04-02 12:00:00"):
            self.run_job()
        self.assertEqual(warranty.state, "expired")
        self.assertEqual(steady.state, "in_progress")
        with freeze_time("2026-04-02 13:00:00"):  # nothing left to move an hour later
            self.assertFalse(self.run_job())

    def test_the_job_uses_the_administrators_time_zone(self):
        # 10 PM in Florida on June 9 is already June 10 in UTC: a warranty that
        # ends June 9 is still covered for two more hours.
        self.admin.tz = "America/New_York"
        with freeze_time("2026-05-01 12:00:00"):
            warranty = self.make(date(2026, 6, 9))
        with freeze_time("2026-06-10 02:00:00"):
            self.run_job()
        self.assertEqual(warranty.state, "expiring")
        with freeze_time("2026-06-10 05:00:00"):  # 1 AM in Florida on June 10
            self.run_job()
        self.assertEqual(warranty.state, "expired")

    def test_moves_are_recorded_in_the_history(self):
        self.admin.tz = "UTC"
        with freeze_time("2026-03-01 12:00:00"):
            warranty = self.make(date(2026, 4, 1))
        with freeze_time("2026-03-02 12:00:00"):
            self.run_job()
            self.env.flush_all()
            self.env.cr.precommit.run()  # history is written just before the database commits
        warranty.invalidate_recordset(["message_ids"])
        bodies = " ".join(str(message.body) for message in warranty.message_ids)
        self.assertIn("Expiring in 30 Days", bodies)
        self.assertIn("(Status)", bodies)

    def test_receipt_and_details(self):
        warranty = self.make(
            date(2027, 1, 1),
            holder_id=self.holder.id,
            place_of_purchase="Best Buy",
            receipt_image=png(),
            claim_link='<p><a href="https://example.com/claims">File a claim</a></p>',
        )
        self.assertEqual(warranty.holder_id, self.holder)
        self.assertEqual(warranty._fields["holder_id"].comodel_name, "res.partner")
        thumbnail = Image.open(io.BytesIO(warranty.receipt_image_128.content))
        self.assertLessEqual(max(thumbnail.size), 128)
        self.assertIn('href="https://example.com/claims"', str(warranty.claim_link))
