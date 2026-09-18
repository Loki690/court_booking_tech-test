# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/my-profile — phone, address, and the Leaflet home pin that powers
nearest-first ordering (PLAN §7). Auto-creates the profile on first visit
(PLAN §11.4). Saves go through api/profile.py (own-profile structural guard).

Filename gotcha: the ROUTE comes from my-profile.html (hyphen), but this
controller must be a valid Python module name (underscore) — frappe's own
update-password.html + update_password.py pair is the convention. A
hyphenated .py silently never loads: no context, no guest redirect, and the
template 500s on undefined keys.
"""

import frappe

from court_booking_tech.customer import ensure_customer_profile
from court_booking_tech.membership import get_customer_memberships

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/my-profile"
		raise frappe.Redirect

	profile = ensure_customer_profile()
	# Same pin convention as CBT Branch (geo.py): 0/None = no pin. Frappe
	# stores numeric fields as 0 when unset, so truthiness IS the check.
	has_pin = bool(profile.home_latitude and profile.home_longitude)
	context.has_pin = has_pin
	context.profile = {
		"phone": profile.phone or "",
		"home_latitude": profile.home_latitude if has_pin else None,
		"home_longitude": profile.home_longitude if has_pin else None,
		"address_text": profile.address_text or "",
	}
	context.user_email = frappe.session.user
	context.full_name = frappe.db.get_value("User", frappe.session.user, "full_name")
	# Section-11: the customer's own memberships across every facility. Read
	# here in the page controller rather than via a whitelisted endpoint —
	# customers hold NO DocPerm on CBT Membership (leak vector 4), and the
	# session user is the only id this can ever be called with.
	context.memberships = get_customer_memberships(frappe.session.user)
	return context
