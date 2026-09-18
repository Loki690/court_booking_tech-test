// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// The list view derives Active / Future / Expired from the DATES at render
// time. There is deliberately no stored is_active field: it would be correct
// on the day it was written and wrong the day after end_date, and keeping it
// honest would need a nightly job to fix data that was never uncertain.

frappe.listview_settings["CBT Membership"] = {
	add_fields: ["start_date", "end_date", "tier", "discount_percent"],

	get_indicator(doc) {
		const today = frappe.datetime.get_today();
		if (doc.start_date > today) {
			return [__("Future"), "blue", "start_date,>,Today"];
		}
		if (doc.end_date && doc.end_date < today) {
			return [__("Expired"), "gray", "end_date,<,Today"];
		}
		return [__("Active"), "green", "start_date,<=,Today"];
	},
};
