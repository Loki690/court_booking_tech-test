// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

frappe.listview_settings["CBT Platform Statement"] = {
	get_indicator(doc) {
		const colors = { Issued: "orange", Paid: "green", Cancelled: "red" };
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
