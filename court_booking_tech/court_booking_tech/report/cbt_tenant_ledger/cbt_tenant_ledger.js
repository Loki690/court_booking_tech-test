// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
/* eslint-disable */

// Backlog B29: the journal view of ONE tenant's month — the rows their
// accountant posts (docs/collection_flow_v1.md). "Books" picks whose ledger:
// the tenant's own (Part 2), or the platform's with that tenant as the party
// (Part 1) — the second is platform-only and the server refuses it otherwise.
frappe.query_reports["CBT Tenant Ledger"] = {
	onload(report) {
		frappe
			.xcall("court_booking_tech.api.company_users.get_my_company", {}, "GET")
			.then((company) => {
				if (company && !report.get_filter_value("company")) {
					report.set_filter_value("company", company);
				}
			})
			.catch(() => {});
	},
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "CBT Company",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "books",
			label: __("Books"),
			fieldtype: "Select",
			options: [
				{ value: "Tenant", label: __("Tenant's books") },
				{ value: "Platform", label: __("Platform's books (this tenant as party)") },
			],
			default: "Tenant",
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.is_total_row) {
			return `<span style="font-weight:600">${value}</span>`;
		}
		if (data && data.is_reversal && ["debit", "credit"].includes(column.fieldname)) {
			return `<span style="color: var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
