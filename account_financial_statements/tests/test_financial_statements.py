import io
import json
from datetime import date

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

REPORT_MODEL = "account.financial.statements"


class FinancialCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.misc = cls.company_data["default_journal_misc"]
        Account = cls.env["account.account"]

        def account(code, name, account_type, **values):
            return Account.create({"code": code, "name": name, "account_type": account_type, **values})

        cls.bank = account("T100", "Test Bank", "asset_cash")
        cls.receivable = account("T110", "Test Receivable", "asset_receivable", reconcile=True)
        cls.fixed = account("T150", "Test Equipment", "asset_fixed")
        cls.payable = account("T200", "Test Payable", "liability_payable", reconcile=True)
        cls.capital = account("T300", "Test Capital", "equity")
        cls.income = account("T400", "Test Sales", "income")
        cls.other_income = account("T410", "Test Other Income", "income_other")
        cls.cost = account("T500", "Test Cost of Sales", "expense_direct_cost")
        cls.expense = account("T510", "Test Rent", "expense")
        cls.depreciation = account("T520", "Test Depreciation", "expense_depreciation")
        cls.customer = cls.env["res.partner"].create({"name": "Customer A"})
        cls.vendor = cls.env["res.partner"].create({"name": "Vendor B"})

    @classmethod
    def entry(cls, day, lines, post=True, journal=None):
        """Post an entry. ``lines`` is a list of (account, signed amount[, partner[, due]])."""
        commands = []
        for line in lines:
            account, amount = line[0], line[1]
            values = {
                "account_id": account.id,
                "debit": max(amount, 0.0),
                "credit": max(-amount, 0.0),
                "name": "line",
            }
            if len(line) > 2 and line[2]:
                values["partner_id"] = line[2].id
            if len(line) > 3:
                values["date_maturity"] = line[3]
            commands.append((0, 0, values))
        move = cls.env["account.move"].create({
            "move_type": "entry",
            "date": day,
            "journal_id": (journal or cls.misc).id,
            "line_ids": commands,
        })
        if post:
            move.action_post()
        return move

    @classmethod
    def load_scenario(cls):
        """Hand-worked books. The expected figures in the tests are worked out on paper."""
        entries = [
            ("2023-06-01", cls.bank, 10000, cls.capital),  # owner puts in capital
            ("2023-09-01", cls.bank, 2000, cls.income),  # prior-year sale
            ("2023-10-01", cls.expense, 500, cls.bank),  # prior-year rent
            ("2024-03-15", cls.expense, 200, cls.bank),  # rent
            ("2024-04-01", cls.fixed, 4000, cls.bank),  # buys equipment
            ("2024-05-01", cls.depreciation, 100, cls.fixed),
            ("2024-07-01", cls.bank, 50, cls.other_income),
            ("2025-01-10", cls.bank, 999, cls.income),  # after every report date used
        ]
        for day, debit_account, amount, credit_account in entries:
            cls.entry(day, [(debit_account, amount), (credit_account, -amount)])
        cls.entry("2024-02-01", [(cls.receivable, 3000, cls.customer), (cls.income, -3000)])
        cls.entry("2024-03-01", [(cls.cost, 1000), (cls.payable, -1000, cls.vendor)])
        # Must never count: a draft and a cancelled entry.
        cls.entry("2024-03-01", [(cls.bank, 7777), (cls.income, -7777)], post=False)
        cancelled = cls.entry("2024-03-01", [(cls.bank, 5555), (cls.income, -5555)], post=False)
        cancelled.button_cancel()

    def report(self, name, **options):
        return self.env[REPORT_MODEL].fs_get_report(name, options)

    @staticmethod
    def row(data, row_id, column="current"):
        for row in data["rows"]:
            if row["id"] == row_id:
                return row["values"].get(column)
        raise AssertionError(f"No row {row_id}: {[r['id'] for r in data['rows']]}")

    def account_row(self, data, account, column="current"):
        return self.row(data, f"account-{account.id}", column)

    def assert_checks_pass(self, data):
        self.assertTrue(data["checks"] or data["report"] == "profit_and_loss")
        for check in data["checks"]:
            self.assertTrue(check["ok"], check)

    def sql_balance(self, account_types, date_to, date_from=None, companies=None):
        """A second, separate calculation: plain SQL on the posted journal items."""
        self.env.flush_all()
        query = """
            SELECT COALESCE(SUM(aml.debit - aml.credit), 0)
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_account aa ON aa.id = aml.account_id
             WHERE am.state = 'posted'
               AND aml.company_id = ANY(%s)
               AND aa.account_type = ANY(%s)
               AND aml.date <= %s
        """
        params = [list(companies or [self.env.company.id]), list(account_types), date_to]
        if date_from:
            query += " AND aml.date >= %s"
            params.append(date_from)
        self.env.cr.execute(query, params)
        return float(self.env.cr.fetchone()[0])


class TestBalanceSheet(FinancialCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_scenario()

    def test_hand_worked_figures(self):
        data = self.report("balance_sheet", date_to="2024-06-30")
        self.assertEqual(self.row(data, "bank"), 7300)  # 10000 + 2000 - 500 - 200 - 4000
        self.assertEqual(self.row(data, "receivables"), 3000)
        self.assertEqual(self.row(data, "fixed_assets"), 3900)  # 4000 - 100
        self.assertEqual(self.row(data, "assets"), 14200)
        self.assertEqual(self.row(data, "payables"), 1000)
        self.assertEqual(self.row(data, "liabilities"), 1000)
        self.assertEqual(self.row(data, "retained"), 10000)
        self.assertEqual(self.row(data, "previous_earnings"), 1500)  # 2000 - 500
        self.assertEqual(self.row(data, "current_earnings"), 1700)  # 3000 - 1000 - 200 - 100
        self.assertEqual(self.row(data, "equity"), 13200)
        self.assertEqual(self.row(data, "liabilities_and_equity"), 14200)
        self.assert_checks_pass(data)

    def test_prior_year_results_stay_in_previous_earnings(self):
        data = self.report("balance_sheet", date_to="2023-12-31")
        self.assertEqual(self.row(data, "current_earnings"), 1500)
        self.assertEqual(self.row(data, "previous_earnings"), 0)
        data = self.report("balance_sheet", date_to="2024-01-01")
        self.assertEqual(self.row(data, "current_earnings"), 0)
        self.assertEqual(self.row(data, "previous_earnings"), 1500)
        self.assert_checks_pass(data)

    def test_entry_on_the_report_date_counts(self):
        self.assertEqual(self.row(self.report("balance_sheet", date_to="2024-07-01"), "bank"), 7350)
        self.assertEqual(self.row(self.report("balance_sheet", date_to="2024-06-30"), "bank"), 7300)

    def test_drafts_only_when_asked(self):
        data = self.report("balance_sheet", date_to="2024-06-30", include_draft=True)
        self.assertEqual(self.row(data, "bank"), 7300 + 7777)
        self.assert_checks_pass(data)

    def test_cancelled_never_counts(self):
        self.assertNotIn(
            7300 + 5555,
            [self.row(self.report("balance_sheet", date_to="2024-06-30", include_draft=flag), "bank")
             for flag in (False, True)],
        )

    def test_fiscal_year_not_on_the_calendar(self):
        # Fiscal year July to June: 2024-03-31 belongs to the year that began 2023-07-01.
        self.env.company.write({"fiscalyear_last_month": "6", "fiscalyear_last_day": 30})
        data = self.report("balance_sheet", date_to="2024-03-31")
        # Since 2023-07-01: 2000 - 500 + 3000 - 1000 - 200. Nothing before that date.
        self.assertEqual(self.row(data, "current_earnings"), 3300)
        self.assertEqual(self.row(data, "previous_earnings"), 0)
        self.assert_checks_pass(data)

    def test_comparison_previous_year(self):
        data = self.report("balance_sheet", date_to="2024-06-30", compare="previous_year")
        self.assertEqual(self.row(data, "assets", "previous"), 10000)  # 10000 by 2023-06-30
        self.assertEqual(self.row(data, "assets", "current"), 14200)
        self.assertAlmostEqual(self.row(data, "assets", "change"), 42.0)
        self.assert_checks_pass(data)

    def test_matches_separate_sql_calculation(self):
        day = "2024-06-30"
        data = self.report("balance_sheet", date_to=day)
        self.assertEqual(self.row(data, "bank"), self.sql_balance(["asset_cash"], day))
        self.assertEqual(self.row(data, "payables"), -self.sql_balance(["liability_payable"], day))
        results = ["income", "income_other", "expense", "expense_other", "expense_depreciation", "expense_direct_cost"]
        self.assertEqual(self.row(data, "current_earnings"), -self.sql_balance(results, day, "2024-01-01"))
        self.assertEqual(self.row(data, "previous_earnings"), -self.sql_balance(results, "2023-12-31"))

    def test_zero_accounts_hidden_only_when_asked(self):
        account_id = self.env["account.account"].create({
            "code": "T999", "name": "Unused", "account_type": "asset_cash",
        }).id
        # Accounts with no journal items at all never appear; a zero balance is what's hidden.
        self.entry("2024-01-02", [(self.env["account.account"].browse(account_id), 5), (self.bank, -5)])
        self.entry("2024-01-03", [(self.env["account.account"].browse(account_id), -5), (self.bank, 5)])
        hidden = self.report("balance_sheet", date_to="2024-06-30")
        self.assertNotIn(f"account-{account_id}", [row["id"] for row in hidden["rows"]])
        shown = self.report("balance_sheet", date_to="2024-06-30", hide_zero=False)
        self.assertIn(f"account-{account_id}", [row["id"] for row in shown["rows"]])
        self.assertEqual(self.row(hidden, "bank"), self.row(shown, "bank"))

    def test_equity_unaffected_account_is_included(self):
        unaffected = self.env["account.account"].search([
            ("account_type", "=", "equity_unaffected"),
            ("company_ids", "in", self.env.company.id),
        ], limit=1)
        self.assertTrue(unaffected, "The test company needs an earnings account")
        self.entry("2024-06-01", [(self.bank, 300), (unaffected, -300)])
        data = self.report("balance_sheet", date_to="2024-06-30")
        self.assertEqual(self.row(data, "previous_earnings"), 1800)
        self.assert_checks_pass(data)


class TestProfitAndLoss(FinancialCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_scenario()

    def test_hand_worked_figures(self):
        data = self.report("profit_and_loss", date_from="2024-01-01", date_to="2024-06-30")
        self.assertEqual(self.row(data, "revenue"), 3000)
        self.assertEqual(self.row(data, "cost_of_revenue"), 1000)
        self.assertEqual(self.row(data, "gross_profit"), 2000)
        self.assertEqual(self.row(data, "other_income"), 0)
        self.assertEqual(self.row(data, "expenses"), 200)
        self.assertEqual(self.row(data, "depreciation"), 100)
        self.assertEqual(self.row(data, "net_profit"), 1700)

    def test_period_edges_are_inclusive(self):
        data = self.report("profit_and_loss", date_from="2024-07-01", date_to="2024-07-01")
        self.assertEqual(self.row(data, "other_income"), 50)
        self.assertEqual(self.row(data, "net_profit"), 50)
        data = self.report("profit_and_loss", date_from="2024-07-02", date_to="2024-12-31")
        self.assertEqual(self.row(data, "net_profit"), 0)

    def test_net_profit_equals_balance_sheet_current_earnings(self):
        profit = self.report("profit_and_loss", date_from="2024-01-01", date_to="2024-12-31")
        sheet = self.report("balance_sheet", date_to="2024-12-31")
        self.assertEqual(self.row(profit, "net_profit"), self.row(sheet, "current_earnings"))
        self.assertEqual(self.row(profit, "net_profit"), 1750)

    def test_a_loss_is_negative(self):
        data = self.report("profit_and_loss", date_from="2024-03-01", date_to="2024-05-31")
        self.assertEqual(self.row(data, "net_profit"), -1300)  # -1000 - 200 - 100

    def test_comparison_previous_period_keeps_whole_months(self):
        data = self.report(
            "profit_and_loss", date_from="2024-02-01", date_to="2024-02-29", compare="previous_period",
        )
        self.assertEqual(data["columns"][1]["label"], "2024-01-01 – 2024-01-31")
        self.assertEqual(self.row(data, "revenue", "current"), 3000)
        self.assertEqual(self.row(data, "revenue", "previous"), 0)
        self.assertIsNone(self.row(data, "revenue", "change"))

    def test_comparison_previous_year_handles_leap_day(self):
        data = self.report(
            "profit_and_loss", date_from="2024-02-01", date_to="2024-02-29", compare="previous_year",
        )
        self.assertEqual(data["columns"][1]["label"], "2023-02-01 – 2023-02-28")
        data = self.report(
            "profit_and_loss", date_from="2023-02-01", date_to="2023-02-28", compare="previous_year",
        )
        self.assertEqual(data["columns"][1]["label"], "2022-02-01 – 2022-02-28")

    def test_journal_filter(self):
        other = self.env["account.journal"].create({"name": "Other", "code": "OTHR", "type": "general"})
        self.entry("2024-06-01", [(self.bank, 40), (self.income, -40)], journal=other)
        data = self.report("profit_and_loss", date_from="2024-01-01", date_to="2024-06-30", journal_ids=[other.id])
        self.assertEqual(self.row(data, "revenue"), 40)
        data = self.report("profit_and_loss", date_from="2024-01-01", date_to="2024-06-30")
        self.assertEqual(self.row(data, "revenue"), 3040)

    def test_foreign_currency_uses_company_amounts(self):
        other_currency = self.setup_other_currency("EUR", rates=[("2024-01-01", 2.0)])
        move = self.env["account.move"].create({
            "move_type": "entry", "date": "2024-06-01", "journal_id": self.misc.id,
            "line_ids": [
                (0, 0, {"account_id": self.bank.id, "currency_id": other_currency.id,
                        "amount_currency": 200, "debit": 100, "credit": 0}),
                (0, 0, {"account_id": self.income.id, "currency_id": other_currency.id,
                        "amount_currency": -200, "debit": 0, "credit": 100}),
            ],
        })
        move.action_post()
        data = self.report("profit_and_loss", date_from="2024-06-01", date_to="2024-06-01")
        self.assertEqual(self.row(data, "revenue"), 100)

    def test_real_invoice_and_refund(self):
        invoice = self.init_invoice(
            "out_invoice", partner=self.customer, invoice_date="2024-08-10", post=True, amounts=[1000], taxes=[],
        )
        refund = self.init_invoice(
            "out_refund", partner=self.customer, invoice_date="2024-08-11", post=True, amounts=[250], taxes=[],
        )
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(refund.state, "posted")
        data = self.report("profit_and_loss", date_from="2024-08-01", date_to="2024-08-31")
        self.assertEqual(self.row(data, "revenue"), 750)


class TestTrialBalanceAndLedger(FinancialCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_scenario()

    def test_trial_balance_hand_worked(self):
        data = self.report("trial_balance", date_from="2024-03-01", date_to="2024-03-31")
        values = {row["id"]: row["values"] for row in data["rows"]}
        bank = values[f"account-{self.bank.id}"]
        self.assertEqual(bank, {"opening": 11500, "debit": 0, "credit": 200, "ending": 11300})
        income = values[f"account-{self.income.id}"]
        self.assertEqual(income, {"opening": -3000, "debit": 0, "credit": 0, "ending": -3000})
        expense = values[f"account-{self.expense.id}"]
        self.assertEqual(expense, {"opening": 0, "debit": 200, "credit": 0, "ending": 200})
        self.assertEqual(values["undistributed"]["opening"], -1500)
        self.assertEqual(values["total"]["debit"], 1200)  # 1000 cost + 200 rent
        self.assertEqual(values["total"]["credit"], 1200)
        self.assertEqual(values["total"]["ending"], 0)
        self.assert_checks_pass(data)

    def test_profit_accounts_restart_each_fiscal_year(self):
        data = self.report("trial_balance", date_from="2024-01-01", date_to="2024-12-31")
        values = {row["id"]: row["values"] for row in data["rows"]}
        self.assertEqual(values[f"account-{self.income.id}"]["opening"], 0)
        self.assertEqual(values["undistributed"]["ending"], -1500)
        self.assert_checks_pass(data)

    def test_trial_balance_ending_matches_balance_sheet(self):
        trial = self.report("trial_balance", date_from="2024-01-01", date_to="2024-06-30")
        sheet = self.report("balance_sheet", date_to="2024-06-30")
        self.assertEqual(
            self.account_row(trial, self.bank, "ending"), self.account_row(sheet, self.bank),
        )
        self.assertEqual(
            self.account_row(trial, self.payable, "ending"), -self.account_row(sheet, self.payable),
        )

    def test_general_ledger_running_balance(self):
        options = {"date_from": "2024-01-01", "date_to": "2024-12-31"}
        data = self.report("general_ledger", **options)
        row = next(r for r in data["rows"] if r["id"] == f"account-{self.bank.id}")
        self.assertEqual(row["values"]["opening"], 11500)
        self.assertEqual(row["values"]["balance"], 7350)
        lines = self.env[REPORT_MODEL].fs_get_ledger_lines(options, self.bank.id)
        self.assertEqual(
            [line["values"]["balance"] for line in lines["rows"]], [11300, 7300, 7350],
        )
        self.assertFalse(lines["next_offset"])

    def test_general_ledger_pages_keep_the_running_balance(self):
        model = self.env[REPORT_MODEL]
        parsed = model._fs_options("general_ledger", {"date_from": "2024-01-01", "date_to": "2024-12-31"})
        first = model._fs_ledger_lines(parsed, self.bank.id, 0, 2)
        second = model._fs_ledger_lines(parsed, self.bank.id, 2, 2)
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual([r["values"]["balance"] for r in second["rows"]], [7350])

    def test_ledger_total_matches_sql(self):
        data = self.report("general_ledger", date_from="2024-01-01", date_to="2024-06-30")
        row = next(r for r in data["rows"] if r["id"] == f"account-{self.fixed.id}")
        self.assertEqual(row["values"]["balance"], self.sql_balance(["asset_fixed"], "2024-06-30"))

    def test_start_after_end_refused(self):
        with self.assertRaises(UserError):
            self.report("trial_balance", date_from="2024-02-01", date_to="2024-01-01")


class TestAged(FinancialCommon):
    def reconcile(self, *lines):
        self.env["account.move.line"].browse([line.id for line in lines]).reconcile()

    def receivable_line(self, move):
        return move.line_ids.filtered(lambda line: line.account_id == self.receivable)

    def test_partial_payment_only_counts_from_its_date(self):
        invoice = self.entry("2024-01-15", [
            (self.receivable, 1000, self.customer, "2024-02-15"), (self.income, -1000),
        ])
        payment = self.entry("2024-03-10", [
            (self.bank, 400), (self.receivable, -400, self.customer, "2024-03-10"),
        ])
        self.reconcile(self.receivable_line(invoice), self.receivable_line(payment))

        before = self.report("aged_receivable", date_to="2024-03-01")
        self.assertEqual(self.row(before, "total", "total"), 1000)
        self.assertEqual(self.row(before, "total", "days_30"), 1000)  # 15 days overdue
        self.assert_checks_pass(before)

        after = self.report("aged_receivable", date_to="2024-03-31")
        self.assertEqual(self.row(after, "total", "total"), 600)
        self.assertEqual(self.row(after, "total", "days_60"), 600)  # 45 days overdue
        self.assert_checks_pass(after)

    def test_paid_later_still_open_at_earlier_date(self):
        invoice = self.entry("2024-01-15", [
            (self.receivable, 1000, self.customer, "2024-01-15"), (self.income, -1000),
        ])
        payment = self.entry("2024-06-01", [
            (self.bank, 1000), (self.receivable, -1000, self.customer),
        ])
        self.reconcile(self.receivable_line(invoice), self.receivable_line(payment))
        self.assertTrue(self.receivable_line(invoice).reconciled)
        self.assertEqual(self.row(self.report("aged_receivable", date_to="2024-05-31"), "total", "total"), 1000)
        final = self.report("aged_receivable", date_to="2024-06-01")
        self.assertEqual(self.row(final, "total", "total"), 0)
        self.assert_checks_pass(final)

    def test_bucket_boundaries(self):
        model = self.env[REPORT_MODEL]
        as_of = date(2024, 6, 30)
        for days, bucket in (
            (-5, "not_due"), (0, "not_due"), (1, "days_30"), (30, "days_30"), (31, "days_60"),
            (60, "days_60"), (61, "days_90"), (90, "days_90"), (91, "days_120"), (120, "days_120"),
            (121, "older"), (5000, "older"),
        ):
            due = date.fromordinal(as_of.toordinal() - days)
            self.assertEqual(model._fs_age_bucket(due, as_of), bucket, days)

    def test_unapplied_credit_is_negative(self):
        self.entry("2024-02-01", [(self.bank, 300), (self.receivable, -300, self.customer, "2024-02-01")])
        data = self.report("aged_receivable", date_to="2024-02-28")
        self.assertEqual(self.row(data, "total", "total"), -300)
        self.assert_checks_pass(data)

    def test_payable_shows_what_is_owed_as_positive(self):
        self.entry("2024-03-01", [(self.cost, 1000), (self.payable, -1000, self.vendor, "2024-04-30")])
        data = self.report("aged_payable", date_to="2024-03-31")
        self.assertEqual(self.row(data, f"partner-{self.vendor.id}", "not_due"), 1000)
        self.assertEqual(self.row(data, "total", "total"), 1000)
        self.assert_checks_pass(data)

    def test_no_partner_still_counts(self):
        self.entry("2024-03-01", [(self.receivable, 75), (self.income, -75)])
        data = self.report("aged_receivable", date_to="2024-03-31")
        self.assertEqual(self.row(data, "partner-0", "total"), 75)
        self.assert_checks_pass(data)

    def test_real_invoice_and_payment(self):
        invoice = self.init_invoice(
            "out_invoice", partner=self.customer, invoice_date="2024-08-10", post=True, amounts=[1000], taxes=[],
        )
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create({"amount": 250, "payment_date": "2024-09-01"})._create_payments()
        open_day = self.report("aged_receivable", date_to="2024-08-31")
        self.assertEqual(self.row(open_day, "total", "total"), 1000)
        self.assert_checks_pass(open_day)
        later = self.report("aged_receivable", date_to="2024-12-31")
        self.assert_checks_pass(later)
        self.assertEqual(self.row(later, "total", "total"), -self.sql_balance(["liability_payable"], "2024-12-31") + self.sql_balance(["asset_receivable"], "2024-12-31"))

    def test_lines_add_up_to_the_partner(self):
        self.entry("2024-01-15", [(self.receivable, 100, self.customer, "2024-01-15"), (self.income, -100)])
        self.entry("2024-02-15", [(self.receivable, 50, self.customer, "2024-02-15"), (self.income, -50)])
        data = self.report("aged_receivable", date_to="2024-03-01")
        lines = [r for r in data["rows"] if r["parent_id"] == f"partner-{self.customer.id}"]
        self.assertEqual(sum(line["values"]["total"] for line in lines), 150)
        self.assertEqual(self.row(data, f"partner-{self.customer.id}", "total"), 150)


class TestIsolationAndSafety(FinancialCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_scenario()

    def test_other_company_never_mixes_in(self):
        before = self.report("balance_sheet", date_to="2024-06-30")
        other = self.company_data_2 if hasattr(self, "company_data_2") else self.setup_other_company()
        company = other["company"]
        Account = self.env["account.account"].with_company(company)
        bank = Account.create({"code": "O100", "name": "Other Bank", "account_type": "asset_cash", "company_ids": [(6, 0, company.ids)]})
        income = Account.create({"code": "O400", "name": "Other Sales", "account_type": "income", "company_ids": [(6, 0, company.ids)]})
        move = self.env["account.move"].with_company(company).create({
            "move_type": "entry", "date": "2024-05-01", "journal_id": other["default_journal_misc"].id,
            "line_ids": [
                (0, 0, {"account_id": bank.id, "debit": 123456, "credit": 0}),
                (0, 0, {"account_id": income.id, "debit": 0, "credit": 123456}),
            ],
        })
        move.action_post()
        self.env.user.company_ids |= company
        after = self.env[REPORT_MODEL].with_context(allowed_company_ids=[self.env.company.id, company.id]).fs_get_report(
            "balance_sheet", {"date_to": "2024-06-30"},
        )
        self.assertEqual(self.row(after, "assets"), self.row(before, "assets"))

    def test_user_without_accounting_access_is_refused(self):
        user = new_test_user(self.env, "fs_plain", groups="base.group_user", company_id=self.env.company.id)
        model = self.env[REPORT_MODEL].with_user(user)
        for name in ("balance_sheet", "profit_and_loss", "trial_balance", "general_ledger", "aged_receivable", "aged_payable"):
            with self.assertRaises(AccessError):
                model.fs_get_report(name, {})
        with self.assertRaises(AccessError):
            model.fs_get_filters()
        with self.assertRaises(AccessError):
            model.fs_get_ledger_lines({}, self.bank.id)
        with self.assertRaises(AccessError):
            model.fs_open_journal_items("trial_balance", {}, {})

    def test_read_only_user_can_read(self):
        user = new_test_user(self.env, "fs_ro", groups="base.group_user,account.group_account_readonly", company_id=self.env.company.id)
        data = self.env[REPORT_MODEL].with_user(user).fs_get_report("balance_sheet", {"date_to": "2024-06-30"})
        self.assertEqual(self.row(data, "assets"), 14200)

    def test_invoicing_administrator_can_read(self):
        # What the administrator of a new database has: no "accounting features" group.
        user = new_test_user(self.env, "fs_admin", groups="base.group_user,account.group_account_manager", company_id=self.env.company.id)
        self.assertFalse(user.has_group("account.group_account_readonly"))
        data = self.env[REPORT_MODEL].with_user(user).fs_get_report("balance_sheet", {"date_to": "2024-06-30"})
        self.assertEqual(self.row(data, "assets"), 14200)
        self.assert_checks_pass(data)

    def test_billing_user_is_refused(self):
        user = new_test_user(self.env, "fs_bill", groups="base.group_user,account.group_account_invoice", company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            self.env[REPORT_MODEL].with_user(user).fs_get_report("balance_sheet", {})

    def test_unknown_report_and_bad_values(self):
        model = self.env[REPORT_MODEL]
        with self.assertRaises(UserError):
            model.fs_get_report("nonsense", {})
        with self.assertRaises(UserError):
            model.fs_get_report("balance_sheet", {"date_to": "not-a-date"})
        with self.assertRaises(ValueError):
            model.fs_get_report("balance_sheet", {"journal_ids": ["1; DROP TABLE x"]})

    def test_reports_change_nothing(self):
        self.env.flush_all()
        self.env.cr.execute("SELECT count(*), COALESCE(sum(balance), 0), max(write_date) FROM account_move_line")
        before = self.env.cr.fetchone()
        for name in ("balance_sheet", "profit_and_loss", "trial_balance", "general_ledger", "aged_receivable", "aged_payable"):
            self.env[REPORT_MODEL]._fs_export_data(name, {"date_from": "2024-01-01", "date_to": "2024-06-30"})
        self.env.flush_all()
        self.env.cr.execute("SELECT count(*), COALESCE(sum(balance), 0), max(write_date) FROM account_move_line")
        self.assertEqual(self.env.cr.fetchone(), before)

    def test_drill_lists_the_same_figure(self):
        data = self.report("balance_sheet", date_to="2024-06-30")
        row = next(r for r in data["rows"] if r["id"] == "previous_earnings")
        action = self.env[REPORT_MODEL].fs_open_journal_items(
            "balance_sheet", {"date_to": "2024-06-30"}, row["drills"]["current"],
        )
        lines = self.env["account.move.line"].search(action["domain"])
        self.assertEqual(-sum(lines.mapped("balance")), row["values"]["current"])
        account_row = next(r for r in data["rows"] if r["id"] == f"account-{self.bank.id}")
        action = self.env[REPORT_MODEL].fs_open_journal_items(
            "balance_sheet", {"date_to": "2024-06-30"}, account_row["drills"]["current"],
        )
        lines = self.env["account.move.line"].search(action["domain"])
        self.assertEqual(sum(lines.mapped("balance")), account_row["values"]["current"])

    def test_default_dates_follow_the_fiscal_year(self):
        filters = self.env[REPORT_MODEL].fs_get_filters()
        preset = next(p for p in filters["presets"] if p["id"] == "this_year")
        today = fields.Date.context_today(self.env.user)
        self.assertEqual(preset["date_from"], f"{today.year}-01-01")


@tagged("post_install", "-at_install")
class TestExports(HttpCase, FinancialCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_scenario()
        cls.user = new_test_user(
            cls.env, "fs_export", password="fs_export_pass_1",
            groups="base.group_user,account.group_account_readonly",
            company_id=cls.env.company.id,
        )

    def _url(self, kind, report, **options):
        return f"/account_financial_statements/{kind}/{report}?options={json.dumps(options)}"

    def test_excel_holds_the_same_numbers(self):
        from openpyxl import load_workbook  # noqa: PLC0415

        self.authenticate("fs_export", "fs_export_pass_1")
        response = self.url_open(self._url("xlsx", "balance_sheet", date_to="2024-06-30"))
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(io.BytesIO(response.content)).active
        values = {row[0].value: row[1].value for row in sheet.iter_rows(min_row=5)}
        self.assertEqual(values["Assets"], 14200)
        self.assertEqual(values["Liabilities + Equity"], 14200)

    def test_excel_general_ledger_has_every_line(self):
        from openpyxl import load_workbook  # noqa: PLC0415

        self.authenticate("fs_export", "fs_export_pass_1")
        response = self.url_open(self._url("xlsx", "general_ledger", date_from="2024-01-01", date_to="2024-12-31"))
        sheet = load_workbook(io.BytesIO(response.content)).active
        dates = [row[1].value for row in sheet.iter_rows(min_row=5) if row[1].value]
        self.assertIn("2024-03-15", dates)

    def test_pdf_template_shows_the_figures(self):
        action = self.env.ref("account_financial_statements.action_financial_statement_pdf")
        data = self.env[REPORT_MODEL]._fs_export_data("balance_sheet", {"date_to": "2024-06-30"})
        printable = {
            "title": data["title"], "company": data["company"], "columns": data["columns"],
            "rows": [
                {"label": row["label"], "level": row["level"], "style": row["style"],
                 "cells": [str(row["values"].get(c["key"], "")) for c in data["columns"]]}
                for row in data["rows"]
            ],
        }
        html, _kind = action._render_qweb_html(action.report_name, [], data={"statement": printable, "subtitle": "x"})
        self.assertIn(b"14200", html)

    def test_exports_refuse_outsiders(self):
        new_test_user(self.env, "fs_out", password="fs_out_pass_1", groups="base.group_user", company_id=self.env.company.id)
        self.authenticate("fs_out", "fs_out_pass_1")
        response = self.url_open(self._url("xlsx", "balance_sheet", date_to="2024-06-30"))
        self.assertNotEqual(response.status_code, 200)
        self.authenticate(None, None)
        response = self.url_open(self._url("xlsx", "balance_sheet", date_to="2024-06-30"), allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
