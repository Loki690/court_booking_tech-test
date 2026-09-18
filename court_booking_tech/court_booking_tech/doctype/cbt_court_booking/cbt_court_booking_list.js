// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

frappe.listview_settings["CBT Court Booking"] = {
	add_fields: ["booking_status"],
	get_indicator(doc) {
		const colors = {
			Reserved: "orange",
			Confirmed: "green",
			Extended: "blue",
			Completed: "gray",
			Cancelled: "red",
			Expired: "darkgrey",
			// Section-16: NOT gray. A released no-show is not a quiet archival
			// state like Completed — it is money kept for a court that stood
			// empty, and the row the board's No-shows chip links here to find.
			"No Show": "yellow",
		};
		return [
			__(doc.booking_status),
			colors[doc.booking_status] || "gray",
			`booking_status,=,${doc.booking_status}`,
		];
	},
};
