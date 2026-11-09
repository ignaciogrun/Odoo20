import io
import json

import xlsxwriter

from werkzeug.exceptions import BadRequest

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition
from odoo.tools.misc import formatLang


class FinancialStatementsController(http.Controller):
    def _load(self, report, options):
        try:
            parsed = json.loads(options or "{}")
        except ValueError as error:
            raise BadRequest() from error
        if not isinstance(parsed, dict):
            raise BadRequest()
        # Runs as the logged-in user: the engine checks their accounting access.
        return request.env["account.financial.statements"]._fs_export_data(report, parsed)

    def _subtitle(self, data):
        options = data["options"]
        if data["as_of"]:
            subtitle = f"As of {options['date_to']}"
        else:
            subtitle = f"{options['date_from']} to {options['date_to']}"
        if options["include_draft"]:
            subtitle += " (including draft entries)"
        return subtitle

    @http.route(
        "/account_financial_statements/xlsx/<string:report>",
        type="http", auth="user", methods=["GET"],
    )
    def export_xlsx(self, report, options=None, **kwargs):
        data = self._load(report, options)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet(data["title"][:31])
        currency = request.env.company.currency_id
        decimals = "0" * currency.decimal_places
        money = f"#,##0.{decimals};-#,##0.{decimals}" if decimals else "#,##0;-#,##0"
        bold = {"bold": True}
        formats = {
            "title": workbook.add_format({"bold": True, "font_size": 14}),
            "header": workbook.add_format({"bold": True, "bottom": 1, "align": "right"}),
            "header_left": workbook.add_format({"bold": True, "bottom": 1}),
        }

        def cell_format(row, kind, indent=0):
            strong = row["style"] in ("grand", "section")
            key = (strong, kind, indent, row["style"] == "grand")
            if key not in formats:
                options = dict(bold) if strong else {}
                if row["style"] == "grand":
                    options["top"] = 1
                if kind == "money":
                    options["num_format"] = money
                elif kind == "percent":
                    options["num_format"] = '0.0"%"'
                elif kind == "label":
                    options["indent"] = indent
                formats[key] = workbook.add_format(options)
            return formats[key]

        sheet.write(0, 0, f"{data['company']}: {data['title']}", formats["title"])
        sheet.write(1, 0, self._subtitle(data))
        sheet.write(3, 0, "Name", formats["header_left"])
        for index, column in enumerate(data["columns"], start=1):
            sheet.write(3, index, column["label"], formats["header"] if column["type"] != "text" else formats["header_left"])
        line = 4
        for row in data["rows"]:
            sheet.write(line, 0, row["label"], cell_format(row, "label", row["level"]))
            for index, column in enumerate(data["columns"], start=1):
                value = row["values"].get(column["key"])
                if value is None or value == "":
                    continue
                if column["type"] == "text":
                    sheet.write_string(line, index, str(value), cell_format(row, "text"))
                else:
                    sheet.write_number(line, index, value, cell_format(row, column["type"]))
            line += 1
        sheet.set_column(0, 0, 44)
        sheet.set_column(1, len(data["columns"]), 16)
        sheet.freeze_panes(4, 1)
        workbook.close()
        filename = f"{data['title']} {data['options']['date_to']}.xlsx"
        return request.make_response(output.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(filename)),
        ])

    @http.route(
        "/account_financial_statements/pdf/<string:report>",
        type="http", auth="user", methods=["GET"],
    )
    def export_pdf(self, report, options=None, **kwargs):
        data = self._load(report, options)
        pdf, _content_type = request.env["ir.actions.report"].sudo().with_context(
            active_test=True,
        )._render_qweb_pdf(
            "account_financial_statements.action_financial_statement_pdf",
            res_ids=None,
            data={"statement": self._printable(data), "subtitle": self._subtitle(data)},
        )
        filename = f"{data['title']} {data['options']['date_to']}.pdf"
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", content_disposition(filename)),
        ])

    def _printable(self, data):
        """Format the numbers now: the print template only places them on the page."""
        env = request.env
        currency = env.company.currency_id
        rows = []
        for row in data["rows"]:
            cells = []
            for column in data["columns"]:
                value = row["values"].get(column["key"])
                if value is None or value == "":
                    cells.append("")
                elif column["type"] == "text":
                    cells.append(str(value))
                elif column["type"] == "percent":
                    cells.append(f"{value:.1f}%")
                else:
                    cells.append(formatLang(env, value, currency_obj=currency))
            rows.append({
                "label": row["label"],
                "level": row["level"],
                "style": row["style"],
                "cells": cells,
            })
        return {
            "title": data["title"],
            "company": data["company"],
            "columns": data["columns"],
            "rows": rows,
        }
