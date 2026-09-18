# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Own-profile portal API (section-8).

The guard is STRUCTURAL: neither endpoint accepts a target user, so "Pia
writes Noel's profile" is unexpressible — every call operates on
frappe.session.user only. Writes use ignore_permissions AFTER that guard
(customers hold no DocPerms on the profile — leak vector 4 discipline).
"""

import frappe
from frappe import _
from frappe.utils import flt

from court_booking_tech.customer import ensure_customer_profile

PROFILE_FIELDS = ("phone", "home_latitude", "home_longitude", "address_text")


def _require_logged_in():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in."), frappe.PermissionError)


@frappe.whitelist(methods=["GET"])
def get_my_profile() -> dict:
	_require_logged_in()
	doc = ensure_customer_profile()
	return {field: doc.get(field) for field in PROFILE_FIELDS}


@frappe.whitelist(methods=["POST"])
def update_my_profile(
	phone: str | None = None,
	home_latitude=None,
	home_longitude=None,
	address_text: str | None = None,
) -> dict:
	_require_logged_in()

	# The pin is set/moved as a pair — half a coordinate is meaningless.
	has_lat = home_latitude not in (None, "")
	has_lng = home_longitude not in (None, "")
	if has_lat != has_lng:
		frappe.throw(_("Latitude and longitude must be set together."))
	if has_lat:
		lat, lng = flt(home_latitude), flt(home_longitude)
		if not (-90 <= lat <= 90):
			frappe.throw(_("Latitude must be between -90 and 90."))
		if not (-180 <= lng <= 180):
			frappe.throw(_("Longitude must be between -180 and 180."))

	doc = ensure_customer_profile()
	if phone is not None:
		doc.phone = phone.strip()
	if address_text is not None:
		doc.address_text = address_text.strip()
	if has_lat:
		doc.home_latitude = lat
		doc.home_longitude = lng
	doc.save(ignore_permissions=True)
	return {field: doc.get(field) for field in PROFILE_FIELDS}
