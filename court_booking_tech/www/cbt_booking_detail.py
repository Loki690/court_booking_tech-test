# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/my-bookings/<name> — one booking: countdowns, proof upload, the exact
verification deadline, billing statement, self-cancel (PLAN §7, §5a).

Reached through hooks.website_route_rules ("/my-bookings/<name>" ->
"cbt-booking-detail"); werkzeug puts the captured <name> into form_dict.
"""

import frappe

from court_booking_tech.api.portal import portal_base_context, portal_login_redirect

no_cache = 1


def get_context(context):
	name = frappe.form_dict.get("name")
	if frappe.session.user == "Guest":
		portal_login_redirect(f"/my-bookings/{name or ''}")
	portal_base_context(context)

	# Ownership is verified HERE (not only in the API) so a stranger gets a
	# clean 403 page instead of an empty shell that fails on its first fetch.
	from court_booking_tech.api.portal import _own_booking

	doc = _own_booking(name)
	context.booking_name = doc.name
	context.page_heading = frappe._("Booking {0}").format(doc.name)
	# B53: the facility's cash policy, on the page a customer opens when they
	# want to cancel. None at a Refund company — nothing new renders there.
	from court_booking_tech import billing

	context.refund_note = (
		billing.no_cash_refund_sentence(doc.company)
		if billing.refund_policy(doc.company) == billing.RESCHEDULE_ONLY
		else None
	)
	return context
