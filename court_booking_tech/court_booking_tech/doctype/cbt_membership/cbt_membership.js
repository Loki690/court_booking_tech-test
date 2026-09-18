// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

// Backlog B38: without this the mandatory Customer field falls through to
// frappe's stock user_query, which B38's permission_query_conditions narrows to
// own-company SEATS — leaving the field unfillable. See docs/sections/section-13.md.

frappe.ui.form.on("CBT Membership", {
	setup(frm) {
		frm.set_query("customer", () => ({
			query: "court_booking_tech.api.board.customer_query",
		}));
	},
});
