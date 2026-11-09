from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import date_utils

# A new database's administrator only has the second group, not the first.
READ_GROUPS = ("account.group_account_readonly", "account.group_account_manager")
REPORTS = {
    "balance_sheet", "profit_and_loss", "trial_balance", "general_ledger",
    "aged_receivable", "aged_payable",
}
AS_OF_REPORTS = {"balance_sheet", "aged_receivable", "aged_payable"}
COMPARABLE_REPORTS = {"balance_sheet", "profit_and_loss"}
PROFIT_AND_LOSS_TYPES = (
    "income", "income_other", "expense", "expense_other",
    "expense_depreciation", "expense_direct_cost",
)
GL_ORDER = "date, move_name, id"
GL_PAGE_SIZE = 500
AGE_BUCKETS = [
    ("not_due", None, 0),
    ("days_30", 1, 30),
    ("days_60", 31, 60),
    ("days_90", 61, 90),
    ("days_120", 91, 120),
    ("older", 121, None),
]


class AccountFinancialStatements(models.AbstractModel):
    """Calculates the reports. It only ever reads: nothing here writes to any model.

    Every figure comes from a search or a read group as the current user, so Odoo's
    record rules apply on top of the explicit group check.
    """

    _name = "account.financial.statements"
    _description = "Financial Statements"

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def _fs_check_access(self):
        if not self.env.su and not any(self.env.user.has_group(group) for group in READ_GROUPS):
            raise AccessError(_("You need accounting access to see financial reports."))

    def _fs_companies(self):
        """The current company and its selected branches. Other companies never mix in,
        because their amounts may be in another currency."""
        return self.env.company._accessible_branches()

    @api.model
    def _fs_parse_date(self, value, label):
        try:
            return fields.Date.to_date(value)
        except (TypeError, ValueError) as error:
            raise UserError(_("%s is not a valid date.", label)) from error

    @api.model
    def _fs_options(self, report, options=None):
        """Turn what the browser sent into safe values, filling in the defaults."""
        self._fs_check_access()
        if report not in REPORTS:
            raise UserError(_("Unknown report."))
        options = options or {}
        presets = self._fs_presets()
        default = presets["this_year"]
        date_to = self._fs_parse_date(options.get("date_to"), _("The end date")) or (
            fields.Date.context_today(self) if report in AS_OF_REPORTS else default["date_to"]
        )
        date_from = self._fs_parse_date(options.get("date_from"), _("The start date")) or default["date_from"]
        if report in AS_OF_REPORTS:
            date_from = None
        elif date_from > date_to:
            raise UserError(_("The start date must be before the end date."))
        compare = options.get("compare") if report in COMPARABLE_REPORTS else "none"
        if compare not in ("previous_period", "previous_year"):
            compare = "none"
        journal_ids = [int(journal_id) for journal_id in options.get("journal_ids") or []]
        if journal_ids:
            journal_ids = self.env["account.journal"].search([
                ("id", "in", journal_ids), ("company_id", "in", self._fs_companies().ids),
            ]).ids
        return {
            "report": report,
            "date_from": date_from,
            "date_to": date_to,
            "compare": compare,
            "include_draft": bool(options.get("include_draft")),
            "journal_ids": journal_ids,
            "hide_zero": options.get("hide_zero", True) is not False,
        }

    @api.model
    def _fs_presets(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        fiscal_year = company.compute_fiscalyear_dates(today)
        last_fiscal_year = company.compute_fiscalyear_dates(fiscal_year["date_from"] - timedelta(days=1))
        month_start = today.replace(day=1)
        last_month_end = month_start - timedelta(days=1)
        quarter = date_utils.get_quarter(today)
        last_quarter = date_utils.get_quarter(quarter[0] - timedelta(days=1))
        return {
            "this_month": {
                "date_from": month_start, "date_to": date_utils.end_of(today, "month"),
                "label": _("This month"),
            },
            "this_quarter": {"date_from": quarter[0], "date_to": quarter[1], "label": _("This quarter")},
            "this_year": {**fiscal_year, "label": _("This fiscal year")},
            "last_month": {
                "date_from": last_month_end.replace(day=1), "date_to": last_month_end,
                "label": _("Last month"),
            },
            "last_quarter": {"date_from": last_quarter[0], "date_to": last_quarter[1], "label": _("Last quarter")},
            "last_year": {**last_fiscal_year, "label": _("Last fiscal year")},
        }

    @api.model
    def _fs_previous_range(self, date_from, date_to, compare):
        """Return the period to compare with, keeping whole months whole."""
        if compare == "previous_year":
            return (
                date_from - relativedelta(years=1) if date_from else None,
                self._fs_shift_end(date_to, relativedelta(years=1)),
            )
        if date_from is None:
            return None, self._fs_shift_end(date_to, relativedelta(months=1))
        whole_months = date_from.day == 1 and date_to == date_utils.end_of(date_to, "month")
        if whole_months:
            months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
            return date_from - relativedelta(months=months), date_from - timedelta(days=1)
        return date_from - (date_to - date_from) - timedelta(days=1), date_from - timedelta(days=1)

    @api.model
    def _fs_shift_end(self, day, delta):
        # A period ending on the last day of a month compares with the last day of the
        # other month: Feb 29 goes to Feb 28, and Feb 28 2023 goes to Feb 29 2024.
        shifted = day - delta
        return date_utils.end_of(shifted, "month") if day == date_utils.end_of(day, "month") else shifted

    # ------------------------------------------------------------------
    # Reading journal items
    # ------------------------------------------------------------------

    def _fs_base_domain(self, options):
        states = ["posted", "draft"] if options["include_draft"] else ["posted"]
        domain = Domain("company_id", "in", self._fs_companies().ids)
        domain &= Domain("parent_state", "in", states)
        domain &= Domain("account_id", "!=", False)
        domain &= Domain("account_id.account_type", "!=", "off_balance")
        if options["journal_ids"]:
            domain &= Domain("journal_id", "in", options["journal_ids"])
        return domain

    def _fs_date_domain(self, date_from, date_to):
        domain = Domain.TRUE
        if date_from:
            domain &= Domain("date", ">=", date_from)
        if date_to:
            domain &= Domain("date", "<=", date_to)
        return domain

    def _fs_totals(self, domain):
        """Return {account_id: {balance, debit, credit}} for the given journal items."""
        groups = self.env["account.move.line"]._read_group(
            domain, ["account_id"], ["balance:sum", "debit:sum", "credit:sum"],
        )
        return {
            account.id: {"balance": balance, "debit": debit, "credit": credit}
            for account, balance, debit, credit in groups
        }

    def _fs_accounts(self, account_ids):
        accounts = self.env["account.account"].with_company(self.env.company).browse(account_ids)
        return {
            account.id: {
                "id": account.id,
                "code": account.code or "",
                "name": account.name,
                "type": account.account_type,
                "carries_forward": account.include_initial_balance,
            }
            for account in accounts
        }

    def _fs_opening_ranges(self, date_from):
        """Return where the opening balance of a period starts, for each kind of account.

        Balance sheet accounts carry everything forward. Profit and loss accounts restart
        each fiscal year, and what they earned before moves to undistributed profits.
        """
        return self.env.company.compute_fiscalyear_dates(date_from)["date_from"]

    def _fs_round(self, amount):
        return self.env.company.currency_id.round(amount or 0.0)

    def _fs_is_zero(self, amount):
        return self.env.company.currency_id.is_zero(amount or 0.0)

    # ------------------------------------------------------------------
    # Balance sheet and profit and loss
    # ------------------------------------------------------------------

    def _fs_columns(self, options):
        """The amount columns of a statement: the period, then the comparison, if any."""
        report = options["report"]
        as_of = report == "balance_sheet"
        columns = [{
            "key": "current",
            "date_from": options["date_from"],
            "date_to": options["date_to"],
            "label": self._fs_period_label(options["date_from"], options["date_to"], as_of),
        }]
        if options["compare"] != "none":
            date_from, date_to = self._fs_previous_range(
                options["date_from"], options["date_to"], options["compare"],
            )
            columns.append({
                "key": "previous",
                "date_from": date_from,
                "date_to": date_to,
                "label": self._fs_period_label(date_from, date_to, as_of),
            })
        return columns

    def _fs_period_label(self, date_from, date_to, as_of=False):
        if as_of or not date_from:
            return fields.Date.to_string(date_to)
        return f"{fields.Date.to_string(date_from)} – {fields.Date.to_string(date_to)}"

    def _fs_balance_sheet_data(self, options, column):
        """Return balances at a date, and the two earnings figures that have no account.

        Income and expense accounts are never closed into equity automatically, so their
        balances are reported in equity, split at the start of the fiscal year.
        """
        date_to = column["date_to"]
        fiscal_year_start = self.env.company.compute_fiscalyear_dates(date_to)["date_from"]
        base = self._fs_base_domain(options)
        statement_types = Domain("account_id.account_type", "not in", PROFIT_AND_LOSS_TYPES)
        unaffected = Domain("account_id.account_type", "=", "equity_unaffected")
        profit_and_loss = Domain("account_id.account_type", "in", PROFIT_AND_LOSS_TYPES)

        balances = self._fs_totals(
            base & statement_types & ~unaffected & self._fs_date_domain(None, date_to)
        )
        unaffected_total = sum(
            total["balance"]
            for total in self._fs_totals(base & unaffected & self._fs_date_domain(None, date_to)).values()
        )
        earlier_results = sum(
            total["balance"]
            for total in self._fs_totals(
                base & profit_and_loss & self._fs_date_domain(None, fiscal_year_start - timedelta(days=1))
            ).values()
        )
        current_results = sum(
            total["balance"]
            for total in self._fs_totals(
                base & profit_and_loss & self._fs_date_domain(fiscal_year_start, date_to)
            ).values()
        )
        return {
            "balances": {account_id: total["balance"] for account_id, total in balances.items()},
            "special": {
                "current_earnings": current_results,
                "previous_earnings": earlier_results + unaffected_total,
            },
            "drill_ranges": {
                "current_earnings": {
                    "types": list(PROFIT_AND_LOSS_TYPES),
                    "date_from": fiscal_year_start,
                    "date_to": date_to,
                },
                "previous_earnings": {
                    "types": [*PROFIT_AND_LOSS_TYPES, "equity_unaffected"],
                    "date_from": None,
                    "date_to": fiscal_year_start - timedelta(days=1),
                    "extra_unaffected_to": date_to,
                },
            },
        }

    def _fs_profit_and_loss_data(self, options, column):
        domain = (
            self._fs_base_domain(options)
            & Domain("account_id.account_type", "in", PROFIT_AND_LOSS_TYPES)
            & self._fs_date_domain(column["date_from"], column["date_to"])
        )
        return {
            "balances": {account_id: total["balance"] for account_id, total in self._fs_totals(domain).items()},
            "special": {},
            "drill_ranges": {},
        }

    def _fs_balance_sheet_tree(self):
        return [
            {"id": "assets", "label": _("Assets"), "sign": 1, "grand": True, "children": [
                {"id": "current_assets", "label": _("Current Assets"), "children": [
                    {"id": "bank", "label": _("Bank and Cash"), "types": ["asset_cash"]},
                    {"id": "receivables", "label": _("Receivables"), "types": ["asset_receivable"]},
                    {"id": "other_current", "label": _("Other Current Assets"), "types": ["asset_current"]},
                    {"id": "prepayments", "label": _("Prepayments"), "types": ["asset_prepayments"]},
                ]},
                {"id": "fixed_assets", "label": _("Fixed Assets"), "types": ["asset_fixed"]},
                {"id": "non_current_assets", "label": _("Non-current Assets"), "types": ["asset_non_current"]},
            ]},
            {"id": "liabilities", "label": _("Liabilities"), "sign": -1, "grand": True, "children": [
                {"id": "current_liabilities", "label": _("Current Liabilities"), "children": [
                    {"id": "credit_cards", "label": _("Credit Cards"), "types": ["liability_credit_card"]},
                    {"id": "other_current_liabilities", "label": _("Other Current Liabilities"), "types": ["liability_current"]},
                    {"id": "payables", "label": _("Payables"), "types": ["liability_payable"]},
                ]},
                {"id": "non_current_liabilities", "label": _("Non-current Liabilities"), "types": ["liability_non_current"]},
            ]},
            {"id": "equity", "label": _("Equity"), "sign": -1, "grand": True, "children": [
                {"id": "unallocated", "label": _("Unallocated Earnings"), "children": [
                    {"id": "current_earnings", "label": _("Current Year Earnings"), "special": "current_earnings"},
                    {"id": "previous_earnings", "label": _("Previous Years Earnings"), "special": "previous_earnings"},
                ]},
                {"id": "retained", "label": _("Capital and Retained Earnings"), "types": ["equity"]},
            ]},
            {
                "id": "liabilities_and_equity", "label": _("Liabilities + Equity"),
                "grand": True, "sum": ["liabilities", "equity"],
            },
        ]

    def _fs_profit_and_loss_tree(self):
        return [
            {"id": "revenue", "label": _("Revenue"), "sign": -1, "types": ["income"]},
            {"id": "cost_of_revenue", "label": _("Cost of Revenue"), "sign": 1, "types": ["expense_direct_cost"]},
            {
                "id": "gross_profit", "label": _("Gross Profit"), "grand": True,
                "sum": ["revenue"], "subtract": ["cost_of_revenue"],
            },
            {"id": "other_income", "label": _("Other Income"), "sign": -1, "types": ["income_other"]},
            {"id": "expenses", "label": _("Expenses"), "sign": 1, "types": ["expense"]},
            {"id": "depreciation", "label": _("Depreciation"), "sign": 1, "types": ["expense_depreciation"]},
            {"id": "other_expenses", "label": _("Other Expenses"), "sign": 1, "types": ["expense_other"]},
            {
                "id": "net_profit", "label": _("Net Profit"), "grand": True,
                "sum": ["gross_profit", "other_income"],
                "subtract": ["expenses", "depreciation", "other_expenses"],
            },
        ]

    def _fs_statement_rows(self, tree, columns, data, options):
        keys = [column["key"] for column in columns]
        account_ids = {account_id for key in keys for account_id in data[key]["balances"]}
        accounts = self._fs_accounts(list(account_ids))
        by_type = {}
        for account in sorted(accounts.values(), key=lambda a: (a["code"], a["id"])):
            by_type.setdefault(account["type"], []).append(account)

        rows = []
        results = {}

        def drill_for(column, account_ids_=None, types=None):
            is_as_of = options["report"] == "balance_sheet"
            return {
                "account_ids": account_ids_ or [],
                "types": types or [],
                "date_from": fields.Date.to_string(None if is_as_of else column["date_from"]),
                "date_to": fields.Date.to_string(column["date_to"]),
            }

        def walk(node, level, parent_id, sign):
            sign = node.get("sign", sign)
            row = {
                "id": node["id"],
                "parent_id": parent_id,
                "level": level,
                "label": node["label"],
                "style": "grand" if node.get("grand") else ("section" if "children" in node or "types" in node else "line"),
                "values": {},
                "drills": {},
            }
            rows.append(row)
            if "sum" in node:
                row["style"] = "grand"
                for key in keys:
                    value = sum(results[item][key] for item in node["sum"])
                    value -= sum(results[item][key] for item in node.get("subtract", []))
                    row["values"][key] = value
                results[node["id"]] = dict(row["values"])
                return results[node["id"]]
            if "special" in node:
                for column in columns:
                    key = column["key"]
                    value = sign * data[key]["special"][node["special"]]
                    row["values"][key] = value
                    drill = data[key]["drill_ranges"][node["special"]]
                    row["drills"][key] = {
                        "types": drill["types"],
                        "account_ids": [],
                        "date_from": fields.Date.to_string(drill["date_from"]),
                        "date_to": fields.Date.to_string(drill.get("extra_unaffected_to") or drill["date_to"]),
                        "profit_until": fields.Date.to_string(drill["date_to"]) if drill.get("extra_unaffected_to") else False,
                    }
                results[node["id"]] = dict(row["values"])
                return results[node["id"]]
            totals = dict.fromkeys(keys, 0.0)
            if "children" in node:
                for child in node["children"]:
                    child_values = walk(child, level + 1, node["id"], sign)
                    for key in keys:
                        totals[key] += child_values[key]
            else:
                children = []
                for account_type in node["types"]:
                    children.extend(by_type.get(account_type, []))
                for account in children:
                    values = {
                        key: sign * data[key]["balances"].get(account["id"], 0.0) for key in keys
                    }
                    for key in keys:
                        totals[key] += values[key]
                    if options["hide_zero"] and all(self._fs_is_zero(value) for value in values.values()):
                        continue
                    rows.append({
                        "id": f"account-{account['id']}",
                        "parent_id": node["id"],
                        "level": level + 1,
                        "label": f"{account['code']} {account['name']}".strip(),
                        "style": "account",
                        "values": values,
                        "drills": {
                            column["key"]: drill_for(column, account_ids_=[account["id"]])
                            for column in columns
                        },
                    })
            row["values"] = totals
            results[node["id"]] = totals
            return totals

        for node in tree:
            walk(node, 0, False, 1)
        return rows, results

    def _fs_statement(self, options):
        report = options["report"]
        columns = self._fs_columns(options)
        builder = self._fs_balance_sheet_data if report == "balance_sheet" else self._fs_profit_and_loss_data
        data = {column["key"]: builder(options, column) for column in columns}
        tree = self._fs_balance_sheet_tree() if report == "balance_sheet" else self._fs_profit_and_loss_tree()
        rows, results = self._fs_statement_rows(tree, columns, data, options)
        for row in rows:
            for key in list(row["values"]):
                row["values"][key] = self._fs_round(row["values"][key])
        out_columns = [
            {"key": column["key"], "label": column["label"], "type": "money"} for column in columns
        ]
        if len(columns) == 2:
            out_columns.append({"key": "change", "label": _("Change %"), "type": "percent"})
            for row in rows:
                if row["values"]:
                    previous = row["values"]["previous"]
                    current = row["values"]["current"]
                    row["values"]["change"] = (
                        (current - previous) / abs(previous) * 100 if not self._fs_is_zero(previous) else None
                    )
        checks = []
        if report == "balance_sheet":
            for column in columns:
                difference = results["assets"][column["key"]] - results["liabilities_and_equity"][column["key"]]
                checks.append({
                    "ok": self._fs_is_zero(difference),
                    "label": _("Assets equal liabilities plus equity at %s.", column["label"]),
                    "difference": self._fs_round(difference),
                })
        return {"columns": out_columns, "rows": rows, "checks": checks}

    # ------------------------------------------------------------------
    # Trial balance
    # ------------------------------------------------------------------

    def _fs_opening_balances(self, options):
        """Return ({account_id: opening balance}, undistributed profits) at the period start."""
        date_from = options["date_from"]
        fiscal_year_start = self._fs_opening_ranges(date_from)
        base = self._fs_base_domain(options)
        profit_and_loss = Domain("account_id.account_type", "in", PROFIT_AND_LOSS_TYPES)
        before = self._fs_date_domain(None, date_from - timedelta(days=1))
        carried = self._fs_totals(base & ~profit_and_loss & before)
        restarted = self._fs_totals(
            base & profit_and_loss & self._fs_date_domain(fiscal_year_start, date_from - timedelta(days=1))
        )
        earlier = self._fs_totals(
            base & profit_and_loss & self._fs_date_domain(None, fiscal_year_start - timedelta(days=1))
        )
        opening = {account_id: total["balance"] for account_id, total in carried.items()}
        opening.update({account_id: total["balance"] for account_id, total in restarted.items()})
        return opening, sum(total["balance"] for total in earlier.values())

    def _fs_ledger_summary(self, options):
        opening, undistributed = self._fs_opening_balances(options)
        movement = self._fs_totals(
            self._fs_base_domain(options) & self._fs_date_domain(options["date_from"], options["date_to"])
        )
        accounts = self._fs_accounts(list(set(opening) | set(movement)))
        rows = []
        totals = {"opening": undistributed, "debit": 0.0, "credit": 0.0, "ending": undistributed}
        for account in sorted(accounts.values(), key=lambda a: (a["code"], a["id"])):
            start = opening.get(account["id"], 0.0)
            moved = movement.get(account["id"], {"debit": 0.0, "credit": 0.0, "balance": 0.0})
            ending = start + moved["balance"]
            totals["opening"] += start
            totals["debit"] += moved["debit"]
            totals["credit"] += moved["credit"]
            totals["ending"] += ending
            if (
                options["hide_zero"]
                and all(self._fs_is_zero(value) for value in (start, moved["debit"], moved["credit"], ending))
            ):
                continue
            rows.append({
                "account": account,
                "opening": start,
                "debit": moved["debit"],
                "credit": moved["credit"],
                "ending": ending,
            })
        return rows, undistributed, totals

    def _fs_trial_balance(self, options):
        rows, undistributed, totals = self._fs_ledger_summary(options)
        out = []
        drill = {
            "date_from": fields.Date.to_string(options["date_from"]),
            "date_to": fields.Date.to_string(options["date_to"]),
            "types": [],
        }
        for item in rows:
            account = item["account"]
            out.append({
                "id": f"account-{account['id']}",
                "parent_id": False,
                "level": 0,
                "label": f"{account['code']} {account['name']}".strip(),
                "style": "account",
                "values": {key: self._fs_round(item[key]) for key in ("opening", "debit", "credit", "ending")},
                "drills": {
                    key: {**drill, "account_ids": [account["id"]]} for key in ("debit", "credit", "ending")
                },
            })
        if not self._fs_is_zero(undistributed):
            out.append({
                "id": "undistributed",
                "parent_id": False,
                "level": 0,
                "label": _("Undistributed Profits/Losses"),
                "style": "line",
                "values": {
                    "opening": self._fs_round(undistributed), "debit": 0.0, "credit": 0.0,
                    "ending": self._fs_round(undistributed),
                },
                "drills": {},
            })
        out.append({
            "id": "total",
            "parent_id": False,
            "level": 0,
            "label": _("Total"),
            "style": "grand",
            "values": {key: self._fs_round(value) for key, value in totals.items()},
            "drills": {},
        })
        return {
            "columns": [
                {"key": "opening", "label": _("Opening Balance"), "type": "money"},
                {"key": "debit", "label": _("Debit"), "type": "money"},
                {"key": "credit", "label": _("Credit"), "type": "money"},
                {"key": "ending", "label": _("Ending Balance"), "type": "money"},
            ],
            "rows": out,
            "checks": [
                {
                    "ok": self._fs_is_zero(totals["ending"]),
                    "label": _("Ending balances add up to zero."),
                    "difference": self._fs_round(totals["ending"]),
                },
                {
                    "ok": self._fs_is_zero(totals["debit"] - totals["credit"]),
                    "label": _("Total debits equal total credits."),
                    "difference": self._fs_round(totals["debit"] - totals["credit"]),
                },
            ],
        }

    # ------------------------------------------------------------------
    # General ledger
    # ------------------------------------------------------------------

    def _fs_general_ledger(self, options):
        rows, undistributed, totals = self._fs_ledger_summary(options)
        drill = {
            "date_from": fields.Date.to_string(options["date_from"]),
            "date_to": fields.Date.to_string(options["date_to"]),
            "types": [],
        }
        out = []
        for item in rows:
            account = item["account"]
            has_lines = not (self._fs_is_zero(item["debit"]) and self._fs_is_zero(item["credit"]))
            out.append({
                "id": f"account-{account['id']}",
                "parent_id": False,
                "level": 0,
                "label": f"{account['code']} {account['name']}".strip(),
                "style": "section",
                "account_id": account["id"],
                "expandable": has_lines,
                "values": {
                    "debit": self._fs_round(item["debit"]),
                    "credit": self._fs_round(item["credit"]),
                    "opening": self._fs_round(item["opening"]),
                    "balance": self._fs_round(item["ending"]),
                },
                "drills": {key: {**drill, "account_ids": [account["id"]]} for key in ("debit", "credit", "balance")},
            })
        if not self._fs_is_zero(undistributed):
            out.append({
                "id": "undistributed",
                "parent_id": False,
                "level": 0,
                "label": _("Undistributed Profits/Losses"),
                "style": "line",
                "values": {
                    "debit": 0.0, "credit": 0.0,
                    "opening": self._fs_round(undistributed),
                    "balance": self._fs_round(undistributed),
                },
                "drills": {},
            })
        out.append({
            "id": "total",
            "parent_id": False,
            "level": 0,
            "label": _("Total"),
            "style": "grand",
            "values": {
                "debit": self._fs_round(totals["debit"]),
                "credit": self._fs_round(totals["credit"]),
                "opening": self._fs_round(totals["opening"]),
                "balance": self._fs_round(totals["ending"]),
            },
            "drills": {},
        })
        return {
            "columns": [
                {"key": "date", "label": _("Date"), "type": "text"},
                {"key": "reference", "label": _("Entry"), "type": "text"},
                {"key": "partner", "label": _("Partner"), "type": "text"},
                {"key": "opening", "label": _("Opening"), "type": "money"},
                {"key": "debit", "label": _("Debit"), "type": "money"},
                {"key": "credit", "label": _("Credit"), "type": "money"},
                {"key": "balance", "label": _("Balance"), "type": "money"},
            ],
            "rows": out,
            "checks": [{
                "ok": self._fs_is_zero(totals["debit"] - totals["credit"]),
                "label": _("Total debits equal total credits."),
                "difference": self._fs_round(totals["debit"] - totals["credit"]),
            }],
        }

    def _fs_ledger_lines(self, options, account_id, offset=0, limit=GL_PAGE_SIZE):
        """Return one page of an account's journal items, with the running balance."""
        opening, _undistributed = self._fs_opening_balances(options)
        running = opening.get(account_id, 0.0)
        domain = (
            self._fs_base_domain(options)
            & Domain("account_id", "=", account_id)
            & self._fs_date_domain(options["date_from"], options["date_to"])
        )
        Line = self.env["account.move.line"]
        # The running balance needs everything before this page too.
        total = Line.search_count(domain)
        end = total if limit is None else offset + limit
        lines = Line.search_fetch(
            domain, ["date", "move_name", "name", "ref", "partner_id", "debit", "credit", "balance", "move_id"],
            order=GL_ORDER, limit=end,
        )
        rows = []
        for index, line in enumerate(lines):
            running += line.balance
            if index < offset:
                continue
            rows.append({
                "id": f"line-{line.id}",
                "parent_id": f"account-{account_id}",
                "level": 1,
                "label": line.name or line.ref or "",
                "style": "item",
                "move_id": line.move_id.id,
                "values": {
                    "date": fields.Date.to_string(line.date),
                    "reference": line.move_name or "",
                    "partner": line.sudo().partner_id.display_name or "",
                    "opening": None,
                    "debit": self._fs_round(line.debit),
                    "credit": self._fs_round(line.credit),
                    "balance": self._fs_round(running),
                },
                "drills": {},
            })
        return {"rows": rows, "total": total, "next_offset": end if end < total else False}

    # ------------------------------------------------------------------
    # Aged receivable and payable
    # ------------------------------------------------------------------

    def _fs_open_items(self, options, account_type, as_of):
        """Return the items still open at a date, which may be in the past.

        The stored residual on a journal item is its amount today, so it can't be used: a
        payment made after the date must not count. A payment counts only if both sides of
        the reconciliation were already dated on or before that date.
        """
        Line = self.env["account.move.line"]
        lines = Line.search_fetch(
            self._fs_base_domain(options)
            & Domain("account_id.account_type", "=", account_type)
            & Domain("date", "<=", as_of),
            ["balance", "date", "date_maturity", "partner_id", "move_name", "name", "ref", "move_id"],
            order="date, id",
        )
        Partial = self.env["account.partial.reconcile"]
        settled = dict.fromkeys(lines.ids, 0.0)
        for side, sign in (("debit_move_id", 1), ("credit_move_id", -1)):
            for line, amount in Partial._read_group(
                [(side, "in", lines.ids), ("max_date", "<=", as_of)], [side], ["amount:sum"],
            ):
                settled[line.id] += sign * amount
        return [
            (line, line.balance - settled[line.id])
            for line in lines
            if not self._fs_is_zero(line.balance - settled[line.id])
        ]

    @api.model
    def _fs_age_bucket(self, due_date, as_of):
        days_overdue = (as_of - due_date).days
        for key, start, end in AGE_BUCKETS:
            if (start is None or days_overdue >= start) and (end is None or days_overdue <= end):
                return key
        return AGE_BUCKETS[0][0]

    def _fs_aged(self, options):
        report = options["report"]
        account_type = "asset_receivable" if report == "aged_receivable" else "liability_payable"
        sign = 1 if report == "aged_receivable" else -1
        as_of = options["date_to"]
        keys = [key for key, _start, _end in AGE_BUCKETS] + ["total"]
        partners = {}
        grand = dict.fromkeys(keys, 0.0)
        for line, residual in self._fs_open_items(options, account_type, as_of):
            amount = sign * residual
            partner = line.sudo().partner_id
            entry = partners.setdefault(partner.id or 0, {
                "name": partner.display_name or _("No Partner"),
                "values": dict.fromkeys(keys, 0.0),
                "lines": [],
            })
            bucket = self._fs_age_bucket(line.date_maturity or line.date, as_of)
            entry["values"][bucket] += amount
            entry["values"]["total"] += amount
            grand[bucket] += amount
            grand["total"] += amount
            entry["lines"].append({
                "id": f"line-{line.id}",
                "parent_id": f"partner-{partner.id or 0}",
                "level": 1,
                "label": line.move_name or line.name or "",
                "style": "item",
                "move_id": line.move_id.id,
                "due": fields.Date.to_string(line.date_maturity or line.date),
                "values": {
                    key: self._fs_round(amount) if key in (bucket, "total") else 0.0 for key in keys
                },
                "drills": {},
            })
        rows = []
        for partner_id, entry in sorted(partners.items(), key=lambda item: item[1]["name"].lower()):
            rows.append({
                "id": f"partner-{partner_id}",
                "parent_id": False,
                "level": 0,
                "label": entry["name"],
                "style": "section",
                "expandable": True,
                "values": {key: self._fs_round(value) for key, value in entry["values"].items()},
                "drills": {},
            })
            rows.extend(entry["lines"])
        rows.append({
            "id": "total",
            "parent_id": False,
            "level": 0,
            "label": _("Total"),
            "style": "grand",
            "values": {key: self._fs_round(value) for key, value in grand.items()},
            "drills": {},
        })
        ledger = sign * sum(
            total["balance"]
            for total in self._fs_totals(
                self._fs_base_domain(options)
                & Domain("account_id.account_type", "=", account_type)
                & Domain("date", "<=", as_of)
            ).values()
        )
        difference = grand["total"] - ledger
        labels = {
            "not_due": _("Not Due"), "days_30": _("1–30"), "days_60": _("31–60"),
            "days_90": _("61–90"), "days_120": _("91–120"), "older": _("Older"), "total": _("Total"),
        }
        return {
            "columns": [{"key": key, "label": labels[key], "type": "money"} for key in keys],
            "rows": rows,
            "checks": [{
                "ok": self._fs_is_zero(difference),
                "label": _("Open items add up to the ledger balance of these accounts."),
                "difference": self._fs_round(difference),
            }],
        }

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def _fs_title(self, report):
        return {
            "balance_sheet": _("Balance Sheet"),
            "profit_and_loss": _("Profit and Loss"),
            "trial_balance": _("Trial Balance"),
            "general_ledger": _("General Ledger"),
            "aged_receivable": _("Aged Receivable"),
            "aged_payable": _("Aged Payable"),
        }[report]

    @api.model
    def fs_get_report(self, report, options=None):
        parsed = self._fs_options(report, options)
        if report in ("balance_sheet", "profit_and_loss"):
            result = self._fs_statement(parsed)
        elif report == "trial_balance":
            result = self._fs_trial_balance(parsed)
        elif report == "general_ledger":
            result = self._fs_general_ledger(parsed)
        else:
            result = self._fs_aged(parsed)
        company = self.env.company
        result.update({
            "report": report,
            "title": self._fs_title(report),
            "company": company.display_name,
            "currency_id": company.currency_id.id,
            "as_of": report in AS_OF_REPORTS,
            "comparable": report in COMPARABLE_REPORTS,
            "options": {
                "date_from": fields.Date.to_string(parsed["date_from"]),
                "date_to": fields.Date.to_string(parsed["date_to"]),
                "compare": parsed["compare"],
                "include_draft": parsed["include_draft"],
                "journal_ids": parsed["journal_ids"],
                "hide_zero": parsed["hide_zero"],
            },
        })
        return result

    @api.model
    def fs_get_filters(self):
        """What the filter bar offers: date presets and the journals of this company."""
        self._fs_check_access()
        presets = self._fs_presets()
        journals = self.env["account.journal"].search_read(
            [("company_id", "in", self._fs_companies().ids)], ["name"], order="sequence, name",
        )
        return {
            "presets": [
                {
                    "id": key,
                    "label": preset["label"],
                    "date_from": fields.Date.to_string(preset["date_from"]),
                    "date_to": fields.Date.to_string(preset["date_to"]),
                }
                for key, preset in presets.items()
            ],
            "journals": journals,
            "today": fields.Date.to_string(fields.Date.context_today(self)),
        }

    @api.model
    def fs_get_ledger_lines(self, options, account_id, offset=0):
        parsed = self._fs_options("general_ledger", options)
        return self._fs_ledger_lines(parsed, int(account_id), max(0, int(offset)))

    @api.model
    def fs_open_journal_items(self, report, options, drill):
        """Return the action listing the journal items behind a figure."""
        parsed = self._fs_options(report, options)
        date_from = self._fs_parse_date(drill.get("date_from"), _("The start date"))
        date_to = self._fs_parse_date(drill.get("date_to"), _("The end date"))
        domain = self._fs_base_domain(parsed)
        account_ids = [int(account_id) for account_id in drill.get("account_ids") or []]
        types = [str(account_type) for account_type in drill.get("types") or []]
        if account_ids:
            domain &= Domain("account_id", "in", account_ids)
        profit_until = self._fs_parse_date(drill.get("profit_until"), _("The date"))
        if profit_until:
            # Previous years' earnings: results up to the year start, plus any balance
            # booked directly on the earnings account up to the report date.
            domain &= (
                Domain("account_id.account_type", "=", "equity_unaffected")
                | (Domain("account_id.account_type", "in", types) & Domain("date", "<=", profit_until))
            )
        elif types:
            domain &= Domain("account_id.account_type", "in", types)
        domain &= self._fs_date_domain(date_from, date_to)
        return {
            "type": "ir.actions.act_window",
            "name": _("Journal Items"),
            "res_model": "account.move.line",
            "views": [[False, "list"], [False, "form"]],
            "domain": list(domain),
            "context": {"create": False, "edit": False, "delete": False},
            "target": "current",
        }

    def _fs_export_data(self, report, options):
        """Return the report with every line expanded, for PDF and Excel."""
        result = self.fs_get_report(report, options)
        if report == "general_ledger":
            parsed = self._fs_options(report, options)
            rows = []
            for row in result["rows"]:
                rows.append(row)
                if row.get("account_id"):
                    rows.extend(self._fs_ledger_lines(parsed, row["account_id"], 0, None)["rows"])
            result["rows"] = rows
        return result
