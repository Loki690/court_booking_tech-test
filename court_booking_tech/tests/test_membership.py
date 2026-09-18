"""
Court Booking Tech — Memberships & discounts (section-11)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_membership

The money assertions are PESO-PINNED against hand-computed expectations (house
rule: never "assert > 0" on a money path). The central invariant proved here is
that the four discount seams — portal quote, portal reserve, desk quick-book and
open-play add_players — cannot disagree, because a quote that differs from the
booking it produces is a customer-facing money bug.

Clock is monkeypatched wherever a date window matters — never wall-clock.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech import membership
from court_booking_tech.api import bookings as bookings_api
from court_booking_tech.api import open_play, portal
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"

MIA = "cust.mia@example.com"  # AYALA VIP 20% (2025-01-01 → 2030-12-31)
MILO = "cust.milo@example.com"  # QCSM Standard 10%
PIA = "cust.pia@example.com"  # no membership anywhere
STELLA = "staff.ayala@example.com"

# June 2027 is untouched by every seed and every other test module (seeds use
# 2027-01/02, test_portal 2027-03) — these fixtures own their month.
BOOK_DATE = "2027-06-04"  # Friday
OPEN_PLAY_DATE = "2027-06-18"  # Friday
T0 = datetime(2027, 6, 4, 8, 0)

BGC_1 = "AYALA-bgc-court-1"  # ₱400/hr
MAKATI_A = "AYALA-makati-court-a"  # ₱300/hr
MAKATI_B = "AYALA-makati-court-b"  # ₱300/hr
QCSM_1 = "QCSM-timog-court-1"  # ₱350/hr

CLOCK = "court_booking_tech.clock.now_dt"


class MembershipTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
			invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
			if invoice:
				frappe.delete_doc(
					"CBT Booking Invoice", invoice, force=True,
					ignore_permissions=True, ignore_missing=True,
				)

		self.addCleanup(_do)


class TestActiveWindow(MembershipTestCase):
	"""'Active' is DERIVED from the dates on every read — there is no stored
	flag that could go stale (the reason a Check field was rejected)."""

	def test_seeded_member_is_active_inside_window(self):
		with patch(CLOCK, return_value=T0):
			self.assertEqual(membership.get_member_discount(AYALA, MIA), 20.0)

	def test_membership_is_inactive_before_it_starts(self):
		with patch(CLOCK, return_value=datetime(2024, 6, 1, 9, 0)):
			# Mia's AYALA VIP starts 2025-01-01.
			self.assertEqual(membership.get_member_discount(AYALA, MIA), 0.0)

	def test_membership_is_inactive_after_it_ends(self):
		with patch(CLOCK, return_value=datetime(2031, 6, 1, 9, 0)):
			self.assertEqual(membership.get_member_discount(AYALA, MIA), 0.0)

	def test_expired_membership_grants_nothing(self):
		# Mia ALSO holds a QCSM membership, expired 2024-06-30.
		with patch(CLOCK, return_value=T0):
			self.assertEqual(membership.get_member_discount(QCSM, MIA), 0.0)

	def test_empty_end_date_is_a_lifetime_membership(self):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Membership",
				"company": AYALA,
				"customer": PIA,
				"tier": "Standard",
				"discount_percent": 5,
				"start_date": "2025-01-01",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "CBT Membership", doc.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)
		with patch(CLOCK, return_value=datetime(2099, 1, 1, 9, 0)):
			self.assertEqual(membership.get_member_discount(AYALA, PIA), 5.0)

	def test_non_member_has_no_discount(self):
		with patch(CLOCK, return_value=T0):
			self.assertEqual(membership.get_member_discount(AYALA, PIA), 0.0)
			self.assertFalse(membership.has_active_membership(AYALA, PIA))


class TestCompanyScoping(MembershipTestCase):
	def test_membership_does_not_bleed_across_companies(self):
		"""The marketplace premise: a VIP at one facility is a walk-in at the
		next. Mia is VIP at AYALA and holds only an EXPIRED QCSM row."""
		with patch(CLOCK, return_value=T0):
			self.assertEqual(membership.get_member_discount(AYALA, MIA), 20.0)
			self.assertEqual(membership.get_member_discount(QCSM, MIA), 0.0)
			self.assertEqual(membership.get_member_discount(QCSM, MILO), 10.0)
			self.assertEqual(membership.get_member_discount(AYALA, MILO), 0.0)

	def test_customer_memberships_list_spans_companies_with_live_state(self):
		with patch(CLOCK, return_value=T0):
			rows = membership.get_customer_memberships(MIA)
		states = {row["company_name"]: row["state"] for row in rows}
		self.assertEqual(states.get("Ayala Courts"), "Active")
		self.assertEqual(states.get("QC Smash"), "Expired")


class TestMembershipValidation(MembershipTestCase):
	def test_overlapping_active_membership_is_rejected(self):
		"""Two active memberships would make 'the' discount ambiguous."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Membership",
					"company": AYALA,
					"customer": MIA,  # already VIP 2025-01-01 → 2030-12-31
					"tier": "Standard",
					"discount_percent": 5,
					"start_date": "2026-01-01",
					"end_date": "2026-12-31",
				}
			).insert(ignore_permissions=True)

	def test_non_overlapping_window_is_allowed(self):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Membership",
				"company": AYALA,
				"customer": MIA,
				"tier": "Standard",
				"discount_percent": 5,
				"start_date": "2031-01-01",
				"end_date": "2031-12-31",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "CBT Membership", doc.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)
		self.assertTrue(doc.name.startswith("MEM-AYALA-"))

	def test_end_before_start_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Membership",
					"company": AYALA,
					"customer": PIA,
					"start_date": "2027-06-30",
					"end_date": "2027-06-01",
				}
			).insert(ignore_permissions=True)

	def test_discount_over_100_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Membership",
					"company": AYALA,
					"customer": PIA,
					"discount_percent": 120,
					"start_date": "2027-06-01",
				}
			).insert(ignore_permissions=True)

	def test_staff_account_cannot_hold_a_membership(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Membership",
					"company": AYALA,
					"customer": STELLA,  # CBT Company Staff, not a customer
					"start_date": "2027-06-01",
				}
			).insert(ignore_permissions=True)

	def test_null_company_is_rejected(self):
		# Leak vector 2 — a blank company link is visible to every tenant.
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Membership",
					"customer": PIA,
					"start_date": "2027-06-01",
				}
			).insert(ignore_permissions=True)

	def test_per_company_naming_series(self):
		self.assertTrue(
			frappe.db.exists("CBT Membership", {"company": AYALA, "customer": MIA})
		)
		name = frappe.db.get_value(
			"CBT Membership", {"company": AYALA, "customer": MIA}, "name"
		)
		self.assertTrue(name.startswith("MEM-AYALA-"), name)


class TestBookingDiscountFlowThrough(MembershipTestCase):
	"""₱400/hr BGC court, VIP 20% → ₱320. The invoice must show subtotal 400,
	discount 80, total 320 — and subtotal − discount == total EXACTLY."""

	def test_portal_reserve_applies_membership_discount_to_invoice(self):
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "09:00:00", 1)
		self._cleanup_booking(result["booking"])
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		booking = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertEqual(float(booking.discount_percent), 20.0)
		self.assertEqual(booking.total_amount, 320.0)

		invoice = frappe.get_doc("CBT Booking Invoice", booking.billing_doc)
		self.assertEqual(invoice.subtotal, 400.0)
		self.assertEqual(invoice.discount_amount, 80.0)
		self.assertEqual(invoice.total_amount, 320.0)
		self.assertEqual(
			invoice.subtotal - invoice.discount_amount, invoice.total_amount
		)

	def test_quote_and_reserve_agree_to_the_centavo(self):
		"""The parity that makes the checkout screen trustworthy. Both sides use
		the SAME single-rounding formula — rounding the subtotal first and the
		discount second can differ by a centavo."""
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			quote = portal.get_quote(BGC_1, 2)
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "13:00:00", 2)
		self._cleanup_booking(result["booking"])
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertEqual(quote["subtotal"], 800.0)
		self.assertEqual(quote["discount_percent"], 20.0)
		self.assertEqual(quote["total_amount"], 640.0)
		self.assertEqual(result["total_amount"], quote["total_amount"])

	def test_quote_vat_breakdown_sums_to_the_discounted_total(self):
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			quote = portal.get_quote(BGC_1, 1)
		# VAT is INCLUSIVE of the discounted total (AYALA is a VAT company).
		self.assertEqual(quote["total_amount"], 320.0)
		self.assertEqual(
			round(quote["vatable_amount"] + quote["vat_amount"], 2),
			quote["total_amount"],
		)

	def test_guest_and_non_member_quotes_are_undiscounted(self):
		frappe.set_user("Guest")
		with patch(CLOCK, return_value=T0):
			guest = portal.get_quote(BGC_1, 2)
		self.assertEqual(guest["total_amount"], 800.0)
		self.assertEqual(guest["discount_percent"], 0.0)

		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			pia = portal.get_quote(BGC_1, 2)
		self.assertEqual(pia["total_amount"], 800.0)

	def test_cross_company_booking_gets_no_foreign_discount(self):
		"""Mia is VIP at AYALA — at QCSM she pays list price (₱350)."""
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(QCSM_1, BOOK_DATE, "09:00:00", 1)
		self._cleanup_booking(result["booking"])
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		booking = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertEqual(float(booking.discount_percent), 0.0)
		self.assertEqual(booking.total_amount, 350.0)


class TestDeskDiscountPrecedence(MembershipTestCase):
	"""Explicit staff intent beats the membership auto-fill, and survives a
	later re-save (the controller must never re-derive the discount)."""

	def test_omitted_discount_auto_fills_from_membership(self):
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_A,
				booking_date=BOOK_DATE,
				start_time="09:00:00",
				customer=MIA,
				payment_method="Cash",
			)
		self._cleanup_booking(result["name"])
		booking = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(float(booking.discount_percent), 20.0)
		self.assertEqual(booking.total_amount, 240.0)  # ₱300 − 20%

	def test_explicit_zero_overrides_the_membership(self):
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_A,
				booking_date=BOOK_DATE,
				start_time="10:00:00",
				customer=MIA,
				payment_method="Cash",
				discount_percent=0,
			)
		self._cleanup_booking(result["name"])
		booking = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(float(booking.discount_percent), 0.0)
		self.assertEqual(booking.total_amount, 300.0)

	def test_explicit_higher_discount_overrides_the_membership(self):
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_A,
				booking_date=BOOK_DATE,
				start_time="11:00:00",
				customer=MIA,
				payment_method="Cash",
				discount_percent=50,
			)
		self._cleanup_booking(result["name"])
		booking = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(float(booking.discount_percent), 50.0)
		self.assertEqual(booking.total_amount, 150.0)

	def test_staff_override_survives_revalidation(self):
		"""A re-save must not silently restore the membership rate — the stored
		value is the agreement with the customer."""
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_B,
				booking_date=BOOK_DATE,
				start_time="09:00:00",
				customer=MIA,
				payment_method="Cash",
				discount_percent=0,
			)
		self._cleanup_booking(result["name"])
		booking = frappe.get_doc("CBT Court Booking", result["name"])
		booking.notes = "touched"
		with patch(CLOCK, return_value=T0):
			booking.save()
		booking.reload()
		self.assertEqual(float(booking.discount_percent), 0.0)
		self.assertEqual(booking.total_amount, 300.0)

	def test_non_member_desk_booking_is_undiscounted(self):
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_B,
				booking_date=BOOK_DATE,
				start_time="10:00:00",
				customer=PIA,
				payment_method="Cash",
			)
		self._cleanup_booking(result["name"])
		booking = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(float(booking.discount_percent), 0.0)
		self.assertEqual(booking.total_amount, 300.0)


class TestOpenPlayMemberDiscount(MembershipTestCase):
	"""Open play charges the SESSION's member rate (not the member's booking
	discount): it is a flat per-head product the organiser prices per session."""

	def _session(self, fee=200, member_discount=15):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "AYALA-makati",
				"title": "Members Night",
				"session_date": OPEN_PLAY_DATE,
				"start_time": "09:00:00",
				"end_time": "12:00:00",
				"court_type": "Badminton",
				"rotation_mode": "Timed",
				"rotation_minutes": 15,
				"entry_fee": fee,
				"member_discount_percent": member_discount,
				"courts": [{"court": MAKATI_A}],
			}
		)
		doc.insert(ignore_permissions=True)

		def _cleanup():
			frappe.set_user("Administrator")
			session = frappe.get_doc("CBT Open Play Session", doc.name)
			for row in session.participants or []:
				if row.billing_doc:
					frappe.delete_doc(
						"CBT Booking Invoice", row.billing_doc, force=True,
						ignore_permissions=True, ignore_missing=True,
					)
			for block in frappe.get_all(
				"CBT Slot Block", filters={"open_play_session": doc.name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Slot Block", block, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Open Play Session", doc.name, force=True,
				ignore_permissions=True, ignore_missing=True,
			)

		self.addCleanup(_cleanup)
		open_play.open_session(doc.name)
		return doc.name

	def test_member_pays_the_session_member_rate(self):
		session = self._session()
		open_play.add_players(
			session,
			frappe.as_json(
				[
					{"customer": MIA, "payment_method": "Cash"},
					{"customer": PIA, "payment_method": "Cash"},
				]
			),
		)
		doc = frappe.get_doc("CBT Open Play Session", session)
		by_customer = {row.customer: row for row in doc.participants}

		member = by_customer[MIA]
		self.assertEqual(float(member.discount_percent), 15.0)
		member_invoice = frappe.get_doc("CBT Booking Invoice", member.billing_doc)
		self.assertEqual(member_invoice.subtotal, 200.0)
		self.assertEqual(member_invoice.discount_amount, 30.0)
		self.assertEqual(member_invoice.total_amount, 170.0)  # ₱200 − 15%

		guest = by_customer[PIA]
		self.assertEqual(float(guest.discount_percent), 0.0)
		guest_invoice = frappe.get_doc("CBT Booking Invoice", guest.billing_doc)
		self.assertEqual(guest_invoice.total_amount, 200.0)

		# Session revenue is the sum of what people actually owe.
		self.assertEqual(doc.total_revenue, 370.0)

	def test_free_entry_is_never_discounted(self):
		"""Free means ₱0 — discounting zero is noise on the statement."""
		session = self._session()
		open_play.add_players(
			session, frappe.as_json([{"customer": MIA, "payment_method": "Free"}])
		)
		doc = frappe.get_doc("CBT Open Play Session", session)
		row = doc.participants[0]
		self.assertEqual(float(row.fee), 0.0)
		self.assertEqual(float(row.discount_percent), 0.0)

	def test_session_without_member_rate_charges_members_full_price(self):
		session = self._session(member_discount=0)
		open_play.add_players(
			session, frappe.as_json([{"customer": MIA, "payment_method": "Cash"}])
		)
		doc = frappe.get_doc("CBT Open Play Session", session)
		row = doc.participants[0]
		self.assertEqual(float(row.discount_percent), 0.0)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "total_amount"),
			200.0,
		)

	def test_membership_at_another_company_does_not_discount_this_session(self):
		"""Milo is a QCSM member; this is an AYALA session."""
		session = self._session()
		open_play.add_players(
			session, frappe.as_json([{"customer": MILO, "payment_method": "Cash"}])
		)
		doc = frappe.get_doc("CBT Open Play Session", session)
		self.assertEqual(float(doc.participants[0].discount_percent), 0.0)


class TestMembershipLookupApi(MembershipTestCase):
	"""The board's quick-book hint. Staff path — goes through the tenancy choke
	point, so it cannot read another tenant's memberships."""

	def test_staff_reads_own_company_membership(self):
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			result = membership.get_member_discount_for(AYALA, MIA)
		self.assertTrue(result["has_membership"])
		self.assertEqual(result["discount_percent"], 20.0)
		self.assertEqual(result["tier"], "VIP")

	def test_staff_cannot_read_another_companys_membership(self):
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError, membership.get_member_discount_for, QCSM, MILO
		)

	def test_non_member_returns_a_clean_zero(self):
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			result = membership.get_member_discount_for(AYALA, PIA)
		self.assertFalse(result["has_membership"])
		self.assertEqual(result["discount_percent"], 0.0)
