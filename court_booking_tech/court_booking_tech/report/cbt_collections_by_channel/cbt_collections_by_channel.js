// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
/* eslint-disable */

// Backlog B29: the reconciliation split — what came in through each channel,
// per day the money was CONFIRMED (not the service date: a GCash statement is
// reconciled by the day the transfer landed).
frappe.query_reports["CBT Collections by Channel"] = {
	onload(report) {
		// Same convenience as CBT Company Revenue: a tenant seat gets its own
		// company pre-filled; platform scope keeps the fail-closed manual pick.
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
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.is_total_row) {
			return `<span style="font-weight:600">${value}</span>`;
		}
		return value;
	},
};
