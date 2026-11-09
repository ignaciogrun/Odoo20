{
    "name": "Financial Statements",
    "summary": "Balance Sheet, Profit and Loss, General Ledger, Trial Balance and Aged Partner reports",
    "description": """
Financial reports for Odoo Community, built on posted journal items, with date
filters, comparison columns, drill-down to the journal items, and PDF and Excel
export. This module only reads accounting data: it never creates or changes
journal entries.
""",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Ignacio Grunwaldt",
    "license": "LGPL-3",
    "depends": ["account"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "reports/financial_statement_report.xml",
        "views/financial_statements_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "account_financial_statements/static/src/financial_statements/*",
        ],
    },
    "installable": True,
    "application": False,
}
