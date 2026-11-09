import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { formatCurrency } from "@web/core/currency";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const MODEL = "account.financial.statements";
const COMPARE_OPTIONS = [
    { id: "none", label: _t("No comparison") },
    { id: "previous_period", label: _t("Previous period") },
    { id: "previous_year", label: _t("Same period last year") },
];

/** One screen for every report: the server decides the columns and rows. */
export class FinancialStatement extends Component {
    static template = "account_financial_statements.FinancialStatement";
    static components = { Dropdown, DropdownItem };
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.reportName = this.props.action.params.report;
        this.compareOptions = COMPARE_OPTIONS;
        this.state = proxy({
            loading: true,
            data: null,
            filters: { presets: [], journals: [] },
            options: {
                date_from: false,
                date_to: false,
                compare: "none",
                include_draft: false,
                journal_ids: [],
                hide_zero: true,
            },
            preset: this.isAsOfReport() ? "today" : "this_year",
            expanded: {},
            ledgerLines: {},
            loadingLines: {},
        });
        this.loadToken = 0;
        onWillStart(async () => {
            this.state.filters = await this.orm.call(MODEL, "fs_get_filters", []);
            this.applyPreset(this.state.preset, false);
            await this.load();
            this.expandSections();
        });
    }

    isAsOfReport() {
        return ["balance_sheet", "aged_receivable", "aged_payable"].includes(this.reportName);
    }

    isComparable() {
        return ["balance_sheet", "profit_and_loss"].includes(this.reportName);
    }

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------

    presets() {
        const presets = [...this.state.filters.presets];
        if (this.isAsOfReport()) {
            presets.unshift({
                id: "today",
                label: _t("Today"),
                date_from: this.state.filters.today,
                date_to: this.state.filters.today,
            });
        }
        return presets;
    }

    applyPreset(presetId, reload = true) {
        const preset = this.presets().find((item) => item.id === presetId);
        this.state.preset = presetId;
        if (preset) {
            this.state.options.date_from = preset.date_from;
            this.state.options.date_to = preset.date_to;
        }
        if (reload) {
            return this.load();
        }
    }

    onPresetChange(ev) {
        return this.applyPreset(ev.target.value);
    }

    onDateChange(field, ev) {
        if (!ev.target.value) {
            return;
        }
        this.state.options[field] = ev.target.value;
        this.state.preset = "custom";
        return this.load();
    }

    onCompareChange(ev) {
        this.state.options.compare = ev.target.value;
        return this.load();
    }

    toggleOption(field) {
        this.state.options[field] = !this.state.options[field];
        return this.load();
    }

    toggleJournal(journal) {
        const ids = this.state.options.journal_ids;
        this.state.options.journal_ids = ids.includes(journal.id)
            ? ids.filter((id) => id !== journal.id)
            : [...ids, journal.id];
        return this.load();
    }

    journalLabel() {
        const count = this.state.options.journal_ids.length;
        return count ? _t("%s journals", count) : _t("All journals");
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    optionsForServer() {
        const { date_from, date_to, compare, include_draft, journal_ids, hide_zero } = this.state.options;
        return { date_from, date_to, compare, include_draft, journal_ids: [...journal_ids], hide_zero };
    }

    async load() {
        const token = ++this.loadToken;
        this.state.loading = true;
        try {
            const data = await this.orm.call(MODEL, "fs_get_report", [this.reportName, this.optionsForServer()]);
            if (token === this.loadToken) {
                this.state.data = data;
                // Lines of the general ledger depend on the filters, so they load again on opening.
                this.state.ledgerLines = {};
            }
        } finally {
            if (token === this.loadToken) {
                this.state.loading = false;
            }
        }
    }

    /** Statements open with their sections showing; long lists start closed. */
    expandSections() {
        if (this.isComparable()) {
            for (const row of this.state.data.rows) {
                if (row.style !== "account") {
                    this.state.expanded[row.id] = true;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Rows
    // ------------------------------------------------------------------

    /** Rows to draw, skipping any whose parent is closed, with ledger lines added in. */
    visibleRows() {
        const data = this.state.data;
        if (!data) {
            return [];
        }
        const parents = new Map(data.rows.map((row) => [row.id, row.parent_id]));
        const isOpen = (row) => {
            let parent = row.parent_id;
            while (parent) {
                if (!this.state.expanded[parent]) {
                    return false;
                }
                parent = parents.get(parent);
            }
            return true;
        };
        const rows = [];
        for (const row of data.rows) {
            if (!isOpen(row)) {
                continue;
            }
            rows.push(row);
            const ledger = this.state.ledgerLines[row.id];
            if (ledger && this.state.expanded[row.id]) {
                rows.push(...ledger.rows);
                if (ledger.next_offset) {
                    rows.push({ id: `${row.id}-more`, style: "more", level: 1, values: {}, drills: {}, label: "", owner: row });
                }
            }
        }
        return rows;
    }

    hasChildren(row) {
        return !!row.expandable || this.state.data.rows.some((other) => other.parent_id === row.id);
    }

    async toggleRow(row) {
        if (!this.hasChildren(row)) {
            return;
        }
        this.state.expanded[row.id] = !this.state.expanded[row.id];
        if (row.account_id && this.state.expanded[row.id] && !this.state.ledgerLines[row.id]) {
            await this.loadLedgerLines(row);
        }
    }

    async loadLedgerLines(row) {
        const current = this.state.ledgerLines[row.id];
        this.state.loadingLines[row.id] = true;
        try {
            const page = await this.orm.call(MODEL, "fs_get_ledger_lines", [
                this.optionsForServer(),
                row.account_id,
                current?.next_offset || 0,
            ]);
            this.state.ledgerLines[row.id] = {
                rows: [...(current?.rows || []), ...page.rows],
                next_offset: page.next_offset,
            };
        } finally {
            this.state.loadingLines[row.id] = false;
        }
    }

    formatCell(row, column) {
        const value = row.values[column.key];
        if (value === undefined || value === null || value === "") {
            return "";
        }
        if (column.type === "text") {
            return value;
        }
        if (column.type === "percent") {
            return `${value.toFixed(1)}%`;
        }
        return formatCurrency(value, this.state.data.currency_id);
    }

    isNegative(row, column) {
        return column.type !== "text" && row.values[column.key] < 0;
    }

    canOpen(row, column) {
        return !!row.drills?.[column.key] && !!row.values[column.key];
    }

    async openItems(row, column) {
        const action = await this.orm.call(MODEL, "fs_open_journal_items", [
            this.reportName,
            this.optionsForServer(),
            row.drills[column.key],
        ]);
        return this.action.doAction(action);
    }

    openEntry(row) {
        if (!row.move_id) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "account.move",
            res_id: row.move_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    failedChecks() {
        return (this.state.data?.checks || []).filter((check) => !check.ok);
    }

    passedChecks() {
        return (this.state.data?.checks || []).filter((check) => check.ok);
    }

    // ------------------------------------------------------------------
    // Export
    // ------------------------------------------------------------------

    exportUrl(kind) {
        const options = encodeURIComponent(JSON.stringify(this.optionsForServer()));
        return `/account_financial_statements/${kind}/${this.reportName}?options=${options}`;
    }

    exportFile(kind) {
        browser.open(this.exportUrl(kind), "_blank");
    }
}

registry.category("actions").add("account_financial_statements.report", FinancialStatement);
