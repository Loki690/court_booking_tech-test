"""
Court Booking Tech — Ads on the marketplace home page (Backlog B52)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_ads

PLATFORM SCOPE. `CBT Ad` carries no `company` link on purpose: `/find-court` is
the platform's own front door, so the placement is the platform's to sell and a
tenant cannot create one. That is also why this module asserts the GUEST path —
the doctype grants no Guest DocPerm and `geo.get_home_ads` is the only door onto
it.

THE CLOCK IS INJECTED. An ad is a DATED thing, so a test that read the real date
would pass in March and fail in April. Every row here pins `clock.now_dt`.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.geo import get_home_ads

CLOCK = "court_booking_tech.clock.now_dt"
TODAY = datetime(2029, 3, 15, 10, 0)

PLATFORM_ADMIN = "cbt.admin@example.com"
PUBLIC_IMAGE = "/files/cbt-ad-test.png"


class AdTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def _ad(self, **overrides):
		values = {
			"doctype": "CBT Ad",
			"title": "Rally Sports Gear",
			"image": PUBLIC_IMAGE,
			"link_url": "https://example.com/rally",
			"placement": "Top",
			"starts_on": "2029-03-01",
			"ends_on": "2029-03-31",
			"is_active": 1,
			"sort_order": 0,
		}
		values.update(overrides)
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop, doc.name)
		return doc

	def _drop(self, name):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Ad", name, force=True, ignore_permissions=True, ignore_missing=True
		)

	def _live(self):
		with patch(CLOCK, return_value=TODAY):
			return get_home_ads()


class TestHomeAdWindow(AdTestCase):
	def test_an_ad_live_today_is_returned_with_only_its_public_fields(self):
		ad = self._ad()
		rows = self._live()
		mine = [row for row in rows if row["name"] == ad.name]
		self.assertEqual(len(mine), 1)
		self.assertEqual(
			set(mine[0].keys()), {"name", "title", "image", "link_url"}
		)
		self.assertEqual(mine[0]["title"], "Rally Sports Gear")

	def test_an_ad_that_ended_yesterday_is_gone(self):
		ad = self._ad(starts_on="2029-03-01", ends_on="2029-03-14")
		self.assertNotIn(ad.name, [row["name"] for row in self._live()])

	def test_an_ad_that_starts_tomorrow_has_not_begun(self):
		ad = self._ad(starts_on="2029-03-16", ends_on="2029-03-31")
		self.assertNotIn(ad.name, [row["name"] for row in self._live()])

	def test_both_ends_of_the_window_are_inclusive(self):
		"""'The 1st to the 7th' means the 7th is still running."""
		opens = self._ad(starts_on="2029-03-15", ends_on="2029-03-15")
		self.assertIn(opens.name, [row["name"] for row in self._live()])

	def test_an_inactive_ad_never_renders(self):
		ad = self._ad(is_active=0)
		self.assertNotIn(ad.name, [row["name"] for row in self._live()])

	def test_sort_order_decides_which_ads_come_first(self):
		second = self._ad(title="Second", sort_order=20)
		first = self._ad(title="First", sort_order=10)
		names = [row["name"] for row in self._live()]
		self.assertLess(names.index(first.name), names.index(second.name))


class TestAdValidation(AdTestCase):
	def test_a_private_banner_is_refused_because_a_visitor_cannot_see_it(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._ad(image="/private/files/secret.png")
		self.assertIn("public file", str(caught.exception))

	def test_a_javascript_link_is_refused(self):
		# The same validator the company's public links use: this href is
		# rendered on a page served to guests.
		with self.assertRaises(frappe.ValidationError):
			self._ad(link_url="javascript:alert(1)")

	def test_a_bare_host_with_no_scheme_is_refused(self):
		# "example.com" would resolve RELATIVE to /find-court.
		with self.assertRaises(frappe.ValidationError):
			self._ad(link_url="example.com/rally")

	def test_an_end_before_the_start_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._ad(starts_on="2029-03-20", ends_on="2029-03-10")
		self.assertIn("end date", str(caught.exception))


class TestAdScope(AdTestCase):
	def test_a_guest_may_read_the_live_ads(self):
		"""The marketplace home is served to people with no account."""
		ad = self._ad()
		frappe.set_user("Guest")
		with patch(CLOCK, return_value=TODAY):
			rows = get_home_ads()
		self.assertIn(ad.name, [row["name"] for row in rows])

	def test_a_company_seat_cannot_create_an_ad(self):
		"""Platform scope: the front door is not a tenant's to sell."""
		frappe.set_user("admin.ayala@example.com")
		doc = frappe.get_doc(
			{
				"doctype": "CBT Ad",
				"title": "Tenant placement",
				"image": PUBLIC_IMAGE,
				"link_url": "https://example.com/tenant",
				"placement": "Top",
				"starts_on": "2029-03-01",
				"ends_on": "2029-03-31",
			}
		)
		with self.assertRaises(frappe.PermissionError):
			doc.insert()
