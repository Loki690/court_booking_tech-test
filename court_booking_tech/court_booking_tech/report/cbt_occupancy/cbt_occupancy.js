// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["CBT Occupancy"] = {
	onload(report) {
		// Tenants can only ever pick their own company — fill it for them
		// (server still forces tenant scope; this only saves the click).
		// Platform scope gets null and keeps the fail-closed manual choice.
		// Explicit "GET": frappe.xcall defaults to POST (S10 lesson).
		frappe
			.xcall("court_booking_tech.api.company_users.get_my_company", {}, "GET")
			.then((company) => {
				if (company && !report.get_filter_value("company")) {
					report.set_filter_value("company", company);
				}
			})
			.catch(() => {}); // fail-closed = no default; stay silent
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
		// A utilisation number is only useful if the eye can rank days at a
		// glance; three bands beat reading two decimals per row.
		if (data && column.fieldname === "occupancy_percent") {
			const pct = flt(data.occupancy_percent);
			const color =
				pct >= 70 ? "var(--green-600)" : pct >= 35 ? "var(--orange-600)" : "var(--gray-600)";
			value = `<span style="color:${color}; font-weight:600">${value}</span>`;
		}
		return value;
	},
};
