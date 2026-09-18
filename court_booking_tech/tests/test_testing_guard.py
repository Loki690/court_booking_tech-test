# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""The test-only levers refuse on a site without allow_tests (testing._guard)."""

from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech import testing


class TestTestingGuard(FrappeTestCase):
	def tearDown(self):
		frappe.clear_messages()
		frappe.db.rollback()

	def test_a_lever_refuses_without_allow_tests_and_works_again_after(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			with self.assertRaisesRegex(frappe.PermissionError, "allow_tests"):
				testing.peek_oauth_state(uuid4().hex)
			with self.assertRaisesRegex(frappe.PermissionError, "allow_tests"):
				testing.set_test_clock_offset(0)
		self.assertEqual(testing.peek_oauth_state(uuid4().hex), {"redirect_to": None})
