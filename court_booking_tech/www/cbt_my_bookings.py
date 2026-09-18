# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/my-bookings — cross-company booking history (PLAN §7). Route file is
cbt-my-bookings.html and the public URL is mapped in hooks.website_route_rules
(see www/cbt_book.py for why).
"""

import frappe

from court_booking_tech.api.portal import portal_base_context, portal_login_redirect

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		portal_login_redirect("/my-bookings")
	portal_base_context(context)
	context.page_heading = frappe._("My bookings")
	return context
