# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/find-court — the marketplace home (PLAN §7): every company's branches in one
list, nearest-first. Guests browse freely; logging in only changes where the
distance origin comes from.

Filename gotcha (S8 as-built 8): the ROUTE comes from find-court.html
(hyphen), the controller must be a legal module name (underscore).
"""

import random

import frappe

from court_booking_tech.api.portal import portal_base_context
from court_booking_tech.geo import get_home_ads

no_cache = 1


def get_context(context):
	portal_base_context(context)
	context.page_heading = frappe._("Find a court")
	# A logged-in customer's saved pin is applied SERVER-side by
	# geo.get_branches (S8 as-built 7) — the page must not ask the browser for
	# geolocation in that case, or a "deny" would silently drop the ordering
	# the customer deliberately configured.
	context.has_saved_pin = bool(
		not context.is_guest
		and frappe.db.get_value(
			"CBT Customer Profile",
			{"user": frappe.session.user},
			"home_latitude",
		)
	)
	# Bypass GPS Request: the page never asks the browser for a location.
	context.bypass_gps_request = bool(
		frappe.db.get_single_value("CBT Platform Settings", "bypass_gps_request", cache=False)
	)
	# Backlog B52: ONE banner per load, chosen from whatever is live today, so a
	# second advertiser can never push the search results off a phone screen.
	# Rendered server-side — the ad is part of the page, not a fetch that might
	# arrive after the customer has already started reading.
	ads = get_home_ads()
	context.home_ad = random.choice(ads) if ads else None
	return context
