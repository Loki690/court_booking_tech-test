// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

frappe.listview_settings["CBT Payment Proof"] = {
	get_indicator(doc) {
		const colors = { Pending: "orange", Accepted: "green", Rejected: "red" };
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
