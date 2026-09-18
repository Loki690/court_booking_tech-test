// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["CBT Company Revenue"] = {
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
			// Tenant users are forced onto their own company server-side
			// regardless of this value; it is only meaningful for platform
			// scope, which must name one (fail-closed).
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
};
