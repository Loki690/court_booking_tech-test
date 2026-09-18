# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""/find-court hands the platform's Bypass GPS Request switch to the page."""

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.www import find_court

SETTINGS = "CBT Platform Settings"
FIELD = "bypass_gps_request"


class TestFindCourtLocationSwitch(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_the_switch_is_a_checkbox_that_starts_off(self):
		field = frappe.get_meta(SETTINGS).get_field(FIELD)
		self.assertIsNotNone(field)
		self.assertEqual((field.fieldtype, field.default, field.label), ("Check", "0", "Bypass GPS Request"))

	def test_the_page_context_follows_the_switch(self):
		for value, expected in ((1, True), (0, False), (1, True)):
			frappe.db.set_single_value(SETTINGS, FIELD, value)
			self.assertIs(find_court.get_context(frappe._dict()).bypass_gps_request, expected)

	def test_a_site_that_never_saved_the_switch_still_asks(self):
		frappe.db.delete("Singles", {"doctype": SETTINGS, "field": FIELD})
		self.assertIs(find_court.get_context(frappe._dict()).bypass_gps_request, False)
