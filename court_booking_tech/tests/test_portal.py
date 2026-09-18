"""
Court Booking Tech — Portal API (section-9)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_portal

The customer-facing surface: the reserve endpoint's own gates (it deliberately
does NOT go through require_company_access), self-cancel's "unpaid Reserved
only" rule, per-user rate limits, the guarded proof/billing artefacts, the
deep-link resolver and the server-computed quote. Clock is monkeypatched
throughout — never wall-clock.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, set_request

from court_booking_tech import throttle
from court_booking_tech.api import portal
from court_booking_tech.api.proofs import create_proof, reject_proofs
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, seed_all
from court_booking_tech.verification import office_hours_summary

AYALA = "ayala-courts"
QCSM = "qc-smash"

PIA = "cust.pia@example.com"
NOEL = "cust.noel@example.com"
# Section-11 seeds: VIP at AYALA (20%, to 2030-12-31) and a LONG-EXPIRED
# Standard at QCSM — which is what makes the company-scoping row below real.
MIA = "cust.mia@example.com"
STELLA = "staff.ayala@example.com"  # AYALA staff
SAMUEL = "staff.qcsm@example.com"  # QCSM staff

# A Friday well clear of every seeded fixture date (2027-01-15 / 2027-02-05).
TEST_DATE = "2027-03-05"
T0 = datetime(2027, 3, 5, 8, 0)

COURT_A = "AYALA-makati-court-a"
COURT_B = "AYALA-makati-court-b"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"


def _proof_bytes() -> bytes:
	from court_booking_tech.seeds.seed_test_data import _proof_sample_bytes

	return _proof_sample_bytes()


class PortalTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		throttle.clear_all_buckets()

	# --- helpers ---------------------------------------------------------

	def _reserve(self, court=COURT_A, start_time="10:00:00", slots=1, user=PIA):
		frappe.set_user(user)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(court, TEST_DATE, start_time, slots)
		self._cleanup_booking(result["booking"])
		return result

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
			invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
			for proof in frappe.get_all(
				"CBT Payment Proof", filters={"booking": name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Payment Proof", proof, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
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


class TestPortalReserve(PortalTestCase):
	def test_reserve_creates_customer_owned_fund_transfer_hold(self):
		result = self._reserve()
		doc = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertEqual(doc.customer, PIA)
		self.assertEqual(doc.booking_status, "Reserved")
		# Portal payments are Fund Transfer only — Cash would self-confirm.
		self.assertEqual(doc.payment_method, "Fund Transfer")
		self.assertEqual(doc.company, AYALA)
		self.assertTrue(doc.reservation_expires_at)
		# Money is server-derived from the court, never from the client.
		self.assertEqual(doc.hourly_rate, 300)
		self.assertEqual(doc.total_amount, 300)

	def test_reserve_auto_creates_profile_and_invoice(self):
		result = self._reserve()
		doc = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertTrue(doc.billing_doc)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status"),
			"Unpaid",
		)
		self.assertTrue(frappe.db.exists("CBT Customer Profile", {"user": PIA}))

	def test_guest_cannot_reserve(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			portal.reserve_booking(COURT_A, TEST_DATE, "10:00:00", 1)

	def test_reserve_rejects_suspended_company(self):
		"""The customer gate is separate from require_company_access but MUST
		enforce the same suspension rule (PLAN §5)."""
		frappe.db.set_value("CBT Company", AYALA, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "status", "Active"
		)
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0), self.assertRaises(frappe.ValidationError):
			portal.reserve_booking(COURT_A, TEST_DATE, "11:00:00", 1)

	def test_reserve_rejects_past_slot(self):
		"""flags.customer_created arms the past-start reject (S4 as-built 6):
		staff may back-record walk-ins, customers may not."""
		frappe.set_user(PIA)
		later = datetime(2027, 3, 5, 15, 0)
		with patch(CLOCK, return_value=later), self.assertRaises(frappe.ValidationError):
			portal.reserve_booking(COURT_A, TEST_DATE, "10:00:00", 1)

	def test_reserve_rejects_inactive_court(self):
		frappe.db.set_value("CBT Court", COURT_B, "is_active", 0)
		self.addCleanup(frappe.db.set_value, "CBT Court", COURT_B, "is_active", 1)
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0), self.assertRaises(frappe.ValidationError):
			portal.reserve_booking(COURT_B, TEST_DATE, "10:00:00", 1)

	def test_reserve_loses_cleanly_on_taken_slot(self):
		self._reserve(court=COURT_A, start_time="13:00:00")
		frappe.set_user(NOEL)
		with patch(CLOCK, return_value=T0), self.assertRaises(frappe.ValidationError) as ctx:
			portal.reserve_booking(COURT_A, TEST_DATE, "13:00:00", 1)
		self.assertIn("already taken", str(ctx.exception).lower())

	def test_multi_slot_reserve_spans_consecutive_slots(self):
		result = self._reserve(start_time="14:00:00", slots=2)
		doc = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertEqual(doc.number_of_slots, 2)
		self.assertEqual(doc.duration_hours, 2.0)
		self.assertEqual(doc.total_amount, 600)


class TestPortalRateLimits(PortalTestCase):
	"""The limiter only engages inside an HTTP request (core rate_limit
	parity) — these tests opt in with set_request, exactly like
	tests/test_signup.py does for the D9 signup limiter."""

	def _set_limit(self, fieldname, value):
		frappe.db.set_single_value("CBT Platform Settings", fieldname, value)
		self.addCleanup(frappe.clear_document_cache, "CBT Platform Settings", "CBT Platform Settings")
		self.addCleanup(
			frappe.db.set_single_value, "CBT Platform Settings", fieldname, 10
		)
		frappe.clear_document_cache("CBT Platform Settings", "CBT Platform Settings")

	def test_reserve_rate_limit_fires_at_limit_plus_one(self):
		self._set_limit("portal_booking_limit_per_hour", 2)
		set_request(method="POST", path="/api/method/portal.reserve_booking")
		self._reserve(start_time="09:00:00")
		self._reserve(start_time="10:00:00")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0), self.assertRaises(
			frappe.RateLimitExceededError
		):
			portal.reserve_booking(COURT_A, TEST_DATE, "11:00:00", 1)

	def test_rate_limit_bucket_resets_next_hour(self):
		self._set_limit("portal_booking_limit_per_hour", 1)
		set_request(method="POST", path="/api/method/portal.reserve_booking")
		self._reserve(start_time="09:00:00")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0), self.assertRaises(
			frappe.RateLimitExceededError
		):
			portal.reserve_booking(COURT_A, TEST_DATE, "11:00:00", 1)
		# Same user, next wall-clock hour -> a different bucket key.
		next_hour = T0 + timedelta(hours=1)
		with patch(CLOCK, return_value=next_hour):
			result = portal.reserve_booking(COURT_A, TEST_DATE, "12:00:00", 1)
		self._cleanup_booking(result["booking"])
		self.assertTrue(result["booking"])

	def test_customer_proof_upload_is_throttled(self):
		self._set_limit("proof_upload_limit_per_hour", 1)
		first = self._reserve(start_time="09:00:00")
		second = self._reserve(start_time="10:00:00")
		set_request(method="POST", path="/api/method/proofs.upload_proof")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			create_proof(first["booking"], "p.jpg", _proof_bytes())
			with self.assertRaises(frappe.RateLimitExceededError):
				create_proof(second["booking"], "p.jpg", _proof_bytes())

	def test_staff_proof_upload_is_not_throttled(self):
		"""The desk's "customer sent it via Messenger" path is a STAFF
		workflow — throttling it would punish the branch (as-built decision)."""
		self._set_limit("proof_upload_limit_per_hour", 1)
		first = self._reserve(start_time="09:00:00")
		second = self._reserve(start_time="10:00:00")
		set_request(method="POST", path="/api/method/proofs.upload_proof")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			create_proof(first["booking"], "p.jpg", _proof_bytes())
			create_proof(second["booking"], "p.jpg", _proof_bytes())
		self.assertEqual(
			frappe.db.count("CBT Payment Proof", {"booking": second["booking"]}), 1
		)


class TestPortalSelfCancel(PortalTestCase):
	def test_customer_cancels_own_unpaid_reservation(self):
		result = self._reserve()
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			portal.cancel_my_booking(result["booking"])
		doc = frappe.get_doc("CBT Court Booking", result["booking"])
		self.assertEqual(doc.booking_status, "Cancelled")
		# The invoice follows through the normal on_update seam (S6).
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status"),
			"Cancelled",
		)

	def test_cancel_blocked_once_a_proof_claims_payment(self):
		result = self._reserve()
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			create_proof(result["booking"], "p.jpg", _proof_bytes())
			with self.assertRaises(frappe.ValidationError):
				portal.cancel_my_booking(result["booking"])

	def test_cancel_blocked_on_confirmed_booking(self):
		result = self._reserve()
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with patch(CLOCK, return_value=T0):
			from court_booking_tech.api.bookings import confirm_booking

			confirm_booking(result["booking"])
			frappe.set_user(PIA)
			with self.assertRaises(frappe.ValidationError):
				portal.cancel_my_booking(result["booking"])

	def test_cancel_of_another_customers_booking_is_refused(self):
		result = self._reserve(user=PIA)
		frappe.set_user(NOEL)
		with patch(CLOCK, return_value=T0), self.assertRaises(frappe.PermissionError):
			portal.cancel_my_booking(result["booking"])


class TestPortalReads(PortalTestCase):
	def test_my_bookings_returns_only_own_rows(self):
		mine = self._reserve(user=PIA, start_time="09:00:00")
		theirs = self._reserve(user=NOEL, start_time="10:00:00")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			data = portal.get_my_bookings()
		names = {row["booking"] for row in data["bookings"]}
		self.assertIn(mine["booking"], names)
		self.assertNotIn(theirs["booking"], names)

	def test_my_bookings_card_names_the_company(self):
		"""Cross-company history is the product's premise (PLAN §1) — the
		company must be on every card."""
		result = self._reserve(user=PIA)
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			data = portal.get_my_bookings()
		card = next(
			row for row in data["bookings"] if row["booking"] == result["booking"]
		)
		self.assertEqual(card["company_name"], "Ayala Courts")
		self.assertEqual(card["branch_name"], "Makati Arena")
		self.assertTrue(card["is_open"])

	def test_an_unmoved_bookings_card_carries_no_move_keys(self):
		"""Section-19 (B5) control. The move keys are added only when the booking
		really is half of a move, and that absence is DELIBERATE — a whitelisted
		method serialises None as `null` with the key present (unlike
		/api/resource, which drops nulls, S13 as-built 15), so key absence here is
		something _booking_card does on purpose and a test has to hold it."""
		result = self._reserve(user=PIA, start_time="11:00:00")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			data = portal.get_my_bookings()
		card = next(
			row for row in data["bookings"] if row["booking"] == result["booking"]
		)
		self.assertNotIn("rescheduled_to", card)
		self.assertNotIn("rescheduled_from", card)
		self.assertNotIn("moved_to", card)
		self.assertNotIn("moved_from", card)

	def test_detail_of_another_customers_booking_is_refused(self):
		result = self._reserve(user=PIA)
		frappe.set_user(NOEL)
		with self.assertRaises(frappe.PermissionError):
			portal.get_my_booking_detail(result["booking"])

	def test_detail_shows_deadline_and_office_hours_after_upload(self):
		result = self._reserve()
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			create_proof(result["booking"], "p.jpg", _proof_bytes())
			detail = portal.get_my_booking_detail(result["booking"])
		self.assertTrue(detail["has_pending_proof"])
		self.assertTrue(detail["verification_deadline_at"])
		self.assertEqual(len(detail["proofs"]), 1)
		# Never the raw private-file URL (S5 as-built 8).
		self.assertNotIn("/private/files", detail["proofs"][0]["file_url"])
		self.assertIn("get_proof_file", detail["proofs"][0]["file_url"])
		self.assertIn("Mon", detail["office_hours_summary"])
		self.assertFalse(detail["can_cancel"])

	def test_detail_reports_regrace_after_recoverable_rejection(self):
		result = self._reserve()
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			create_proof(result["booking"], "p.jpg", _proof_bytes())
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			reject_proofs(result["booking"], "Unreadable")
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			detail = portal.get_my_booking_detail(result["booking"])
		self.assertTrue(detail["in_regrace"])
		self.assertEqual(detail["last_rejection_reason"], "Unreadable")
		self.assertTrue(detail["can_upload_proof"])


class TestPortalMovedStory(PortalTestCase):
	"""Section-19 (Backlog B5) — the portal half.

	Reschedule is staff-only, so the customer never sees it happen. What they DO
	see, unaided, is a booking they did not cancel sitting next to one they did
	not make. These rows pin the two payload keys that let the pages tell the
	story instead.
	"""

	def _moved_pair(self):
		"""An account customer's hold, moved by staff. Returns (old, new)."""
		from court_booking_tech.api.bookings import reschedule_booking

		original = self._reserve(user=PIA, start_time="12:00:00")["booking"]
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			result = reschedule_booking(original, start_time="16:00:00")
		self._cleanup_booking(result["name"])
		frappe.set_user(PIA)
		return original, result["name"]

	def _card(self, cards, booking):
		return next(row for row in cards if row["booking"] == booking)

	def test_the_cancelled_half_points_at_its_replacement(self):
		old, new = self._moved_pair()
		with patch(CLOCK, return_value=T0):
			cards = portal.get_my_bookings()["bookings"]

		cancelled = self._card(cards, old)
		self.assertEqual(cancelled["booking_status"], "Cancelled")
		self.assertEqual(cancelled["rescheduled_to"], new)
		# ...and WHEN it moved to, resolved in-payload (the counterpart is always
		# this same customer's booking, because reschedule copies `customer`).
		self.assertEqual(cancelled["moved_to"]["start_time"], "16:00:00")
		self.assertEqual(cancelled["moved_to"]["date"], TEST_DATE)

		# The replacement carries the back-link on the SAME payload, so the two
		# halves can never be resolvable in only one direction.
		self.assertEqual(self._card(cards, new)["rescheduled_from"], old)
		self.assertEqual(self._card(cards, new)["moved_from"]["start_time"], "12:00:00")

	def test_the_replacement_detail_says_where_it_came_from(self):
		old, new = self._moved_pair()
		with patch(CLOCK, return_value=T0):
			detail = portal.get_my_booking_detail(new)
		self.assertEqual(detail["rescheduled_from"], old)
		self.assertEqual(detail["moved_from"]["start_time"], "12:00:00")
		self.assertEqual(detail["moved_from"]["date"], TEST_DATE)
		self.assertNotIn("moved_to", detail)

	def test_the_cancelled_halfs_detail_links_forward(self):
		old, new = self._moved_pair()
		with patch(CLOCK, return_value=T0):
			detail = portal.get_my_booking_detail(old)
		self.assertEqual(detail["rescheduled_to"], new)
		self.assertEqual(detail["moved_to"]["start_time"], "16:00:00")
		self.assertNotIn("moved_from", detail)


class TestPortalQuote(PortalTestCase):
	def test_quote_matches_the_billing_engine(self):
		from court_booking_tech.billing import compute_vat_breakdown

		quote = portal.get_quote(COURT_A, 2)
		expected = compute_vat_breakdown(quote["total_amount"], "VAT", 12)
		self.assertEqual(quote["total_amount"], 600)
		self.assertEqual(quote["vat_mode"], "VAT")
		self.assertEqual(quote["vatable_amount"], expected["vatable_amount"])
		self.assertEqual(quote["vat_amount"], expected["vat_amount"])
		# The centavo invariant the printed statement depends on (S6 gotcha).
		self.assertEqual(
			quote["vatable_amount"] + quote["vat_amount"], quote["total_amount"]
		)

	def test_quote_for_non_vat_company_carries_no_vat_figures(self):
		quote = portal.get_quote(QCSM_COURT, 1)
		self.assertEqual(quote["vat_mode"], "NON-VAT")
		self.assertIsNone(quote["vatable_amount"])
		self.assertIsNone(quote["vat_amount"])

	def test_quote_reports_the_effective_expiry_chain(self):
		self.assertEqual(portal.get_quote(COURT_A, 1)["reservation_expiry_minutes"], 30)
		# e2e-fast overrides it to 1 minute (the E2E determinism fixture).
		self.assertEqual(
			portal.get_quote("E2EF-main-court-1", 1)["reservation_expiry_minutes"], 1
		)

	def test_quote_refuses_suspended_company(self):
		frappe.db.set_value("CBT Company", QCSM, "status", "Suspended")
		self.addCleanup(frappe.db.set_value, "CBT Company", QCSM, "status", "Active")
		with self.assertRaises(frappe.ValidationError):
			portal.get_quote(QCSM_COURT, 1)


class TestPortalQuoteStaffParams(PortalTestCase):
	"""Section-18 (Backlog B4) — the staff-gated `customer` / `discount_percent`.

	Before them the board's quick-book dialog multiplied the discount in JS
	against a server subtotal, because the discount here came from the SESSION
	user (the staff member) rather than the customer being booked. One money area
	with two authorities. These rows pin the new one.

	COURT_A is AYALA-makati-court-a, ₱300/hr FLAT and carrying no rate rules, so
	every figure below is exact to the peso without a date. The segmented
	equivalent lives in test_rate_rules.py, which owns the ruled fixture court.
	"""

	def test_customer_param_derives_that_customers_membership(self):
		"""The bug B4 names, fixed: STELLA is quoting, MIA is playing.

		Without the param the discount would be Stella's (none), so the desk
		would show list price for a VIP and staff would have to fix it by hand.
		"""
		frappe.set_user(STELLA)
		member = portal.get_quote(COURT_A, 1, customer=MIA)
		plain = portal.get_quote(COURT_A, 1)

		self.assertEqual(flt(member["discount_percent"]), 20.0)
		self.assertEqual(flt(member["subtotal"]), 300.0)
		self.assertEqual(flt(member["total_amount"]), 240.0)
		self.assertEqual(flt(member["discount_amount"]), 60.0)
		# The control: the SAME session, no param — the staff member's own
		# (non-existent) membership. This is what the board used to get.
		self.assertEqual(flt(plain["discount_percent"]), 0.0)
		self.assertEqual(flt(plain["total_amount"]), 300.0)

	def test_membership_is_company_scoped_through_this_param_too(self):
		"""MIA's QCSM membership expired in 2024 (seeds), and QCSM is a different
		tenant anyway — so QCSM staff quoting her get list price, not 20%."""
		frappe.set_user(SAMUEL)
		quote = portal.get_quote(QCSM_COURT, 1, customer=MIA)
		self.assertEqual(flt(quote["discount_percent"]), 0.0)

	def test_explicit_discount_beats_the_membership(self):
		frappe.set_user(STELLA)
		quote = portal.get_quote(COURT_A, 1, customer=MIA, discount_percent=50)
		self.assertEqual(flt(quote["discount_percent"]), 50.0)
		self.assertEqual(flt(quote["total_amount"]), 150.0)

	def test_an_explicit_zero_beats_the_membership(self):
		"""The row the tri-state exists for. An untouched frappe Percent field
		reads 0 (S11 as-built 5), and the board sends what staff can SEE — so a 0
		has to mean "no discount", not "work it out for me". A truthiness check
		here would silently charge a VIP price nobody typed."""
		frappe.set_user(STELLA)
		quote = portal.get_quote(COURT_A, 1, customer=MIA, discount_percent=0)
		self.assertEqual(flt(quote["discount_percent"]), 0.0)
		self.assertEqual(flt(quote["total_amount"]), 300.0)
		# And the same through the GET-string path a real request arrives on.
		as_string = portal.get_quote(COURT_A, 1, customer=MIA, discount_percent="0")
		self.assertEqual(flt(as_string["total_amount"]), 300.0)

	def test_empty_strings_are_not_values(self):
		"""A query string carries "" for an omitted param (and jQuery serialises
		a JS null the same way), so "" must fall through to the OLD behaviour
		rather than arming the gate or being read as a 0 discount."""
		frappe.set_user(PIA)
		quote = portal.get_quote(
			COURT_A, 1, customer="", discount_percent="", hourly_rate=""
		)
		self.assertEqual(flt(quote["discount_percent"]), 0.0)
		self.assertEqual(flt(quote["total_amount"]), 300.0)

	# --- Backlog B15 (2026-08-27): the staff-gated `hourly_rate` -------------

	def test_hourly_rate_param_prices_the_window_at_that_flat_rate(self):
		"""The reschedule dialog's case: a booking priced FLAT inherits its rate
		on a move (api/bookings copies `original.hourly_rate`), so the quote must
		be able to price at a rate the court's rules would never produce — and
		come back in the SAME shape the booking will carry (no segments)."""
		frappe.set_user(STELLA)
		quote = portal.get_quote(COURT_A, 2, hourly_rate=999, discount_percent=50)
		self.assertEqual(flt(quote["hourly_rate"]), 999.0)
		self.assertEqual(flt(quote["subtotal"]), 1998.0)
		self.assertEqual(flt(quote["discount_amount"]), 999.0)
		self.assertEqual(flt(quote["total_amount"]), 999.0)
		# (The empty-`segments` shape is pinned where it can actually fail —
		# test_reschedule's parity row quotes a WINDOW inside a rate rule.)
		# And the control: without the param the same call prices the court.
		plain = portal.get_quote(COURT_A, 2, discount_percent=50)
		self.assertEqual(flt(plain["hourly_rate"]), 300.0)
		self.assertEqual(flt(plain["total_amount"]), 300.0)

	def test_hourly_rate_param_still_validates_the_window(self):
		"""Only the MONEY is taken from the caller, never the schedule: a run
		that overflows the branch's grid is refused exactly as it is without
		the param, so the dialog can never show a price for a slot the insert
		would reject. Makati closes at 22:00, so 21:00 + 2 slots overflows."""
		frappe.set_user(STELLA)
		priced = portal.get_quote(
			COURT_A,
			1,
			booking_date=TEST_DATE,
			start_time="21:00:00",
			hourly_rate=999,
			discount_percent=0,  # pinned, so STELLA's own membership can never decide it
		)
		self.assertEqual(flt(priced["total_amount"]), 999.0)
		with self.assertRaises(frappe.ValidationError):
			portal.get_quote(
				COURT_A, 2, booking_date=TEST_DATE, start_time="21:00:00", hourly_rate=999
			)

	def test_hourly_rate_param_refuses_garbage_and_negatives(self):
		"""flt("abc") is 0.0 — a free court hour nobody typed. And float("nan")
		PARSES, then fails every range check in both directions, so it would
		ride into total_amount as non-JSON; inf likewise."""
		frappe.set_user(STELLA)
		for bad in ("abc", "nan", "inf", -1):
			with self.subTest(rate=bad):
				self.assertRaisesRegex(
					frappe.ValidationError,
					"Rate",
					portal.get_quote,
					COURT_A,
					1,
					hourly_rate=bad,
				)

	def test_quote_matches_the_booking_it_produces_for_a_member(self):
		"""The parity that makes B4 worth doing: what the desk SEES is what the
		booking and its statement say, to the centavo, through the single
		rounding the controller and the quote share."""
		from court_booking_tech.api.bookings import create_booking

		frappe.set_user(STELLA)
		quote = portal.get_quote(COURT_A, 2, customer=MIA)

		with patch(CLOCK, return_value=T0):
			booked = create_booking(
				court=COURT_A,
				booking_date=TEST_DATE,
				# 18:00-20:00. Every other COURT_A time in this module is taken
				# (09..13, 14+2 slots, 15), and makati closes at 22:00. Its courts
				# carry no rate rules, so ₱300/hr is flat across the run.
				start_time="18:00:00",
				payment_method="Cash",
				customer=MIA,
				# Must MATCH the quote above — a 2-slot quote against a 1-slot
				# booking compares two different products and the parity claim
				# means nothing.
				number_of_slots=2,
			)
		self._cleanup_booking(booked["name"])

		doc = frappe.get_doc("CBT Court Booking", booked["name"])
		self.assertEqual(flt(quote["total_amount"]), flt(doc.total_amount))
		self.assertEqual(flt(quote["discount_percent"]), flt(doc.discount_percent))
		self.assertEqual(flt(doc.total_amount), 480.0)  # 600 − 20%

		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(flt(quote["total_amount"]), flt(invoice.total_amount))
		self.assertEqual(flt(quote["discount_amount"]), flt(invoice.discount_amount))


class TestPortalDeepLink(PortalTestCase):
	def test_resolver_returns_branded_header_and_branch(self):
		data = portal.resolve_book_page("ayala-courts", "bgc")
		self.assertEqual(data["company_name"], "Ayala Courts")
		self.assertEqual(data["vat_registration"], "VAT")
		self.assertEqual(data["selected"]["branch"], "AYALA-bgc")

	def test_resolver_orders_branches_by_distance_for_a_pinned_customer(self):
		frappe.set_user(PIA)  # BGC-adjacent saved pin
		data = portal.resolve_book_page("ayala-courts")
		self.assertEqual(
			[row["branch_slug"] for row in data["branches"]], ["bgc", "makati"]
		)

	def test_resolver_ships_the_card_strings_for_the_chooser(self):
		"""Backlog B48: the chooser is the badge's SECOND face, and the leak-free
		test on get_branches never reaches it — so its strings are pinned here,
		on a fixed clock (Monday 05:30: closed, opens at six)."""
		from datetime import datetime

		with patch("court_booking_tech.clock.now_dt", return_value=datetime(2026, 7, 27, 5, 30)):
			data = portal.resolve_book_page("ayala-courts", "bgc")
		for row in data["branches"]:
			self.assertEqual(row["hours_summary"], "Daily 6 AM – 10 PM", row)
			self.assertFalse(row["open_now"], row)
			self.assertEqual(row["status_label"], "Opens 6 AM", row)
		self.assertEqual(data["selected"]["hours_summary"], "Daily 6 AM – 10 PM")
		with patch("court_booking_tech.clock.now_dt", return_value=datetime(2026, 7, 27, 12, 0)):
			data = portal.resolve_book_page("ayala-courts")
		self.assertTrue(all(row["status_label"] == "Book now" for row in data["branches"]))

	def test_resolver_hides_suspended_company(self):
		frappe.db.set_value("CBT Company", QCSM, "status", "Suspended")
		self.addCleanup(frappe.db.set_value, "CBT Company", QCSM, "status", "Active")
		with self.assertRaises(frappe.DoesNotExistError):
			portal.resolve_book_page("qc-smash", "timog")

	def test_resolver_rejects_unknown_slugs(self):
		with self.assertRaises(frappe.DoesNotExistError):
			portal.resolve_book_page("no-such-company")
		with self.assertRaises(frappe.DoesNotExistError):
			portal.resolve_book_page("ayala-courts", "no-such-branch")

	def test_resolver_excludes_inactive_branches(self):
		frappe.db.set_value("CBT Branch", "AYALA-makati", "is_active", 0)
		self.addCleanup(
			frappe.db.set_value, "CBT Branch", "AYALA-makati", "is_active", 1
		)
		data = portal.resolve_book_page("ayala-courts")
		self.assertNotIn(
			"makati", [row["branch_slug"] for row in data["branches"]]
		)


class TestPortalArtefactGuards(PortalTestCase):
	"""Owner-or-staff on the two artefacts that cross the customer/staff line."""

	def _booking_with_proof(self):
		result = self._reserve()
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			proof = create_proof(result["booking"], "p.jpg", _proof_bytes())
		return result["booking"], proof["proof"]

	def test_owner_gets_own_proof_bytes(self):
		booking, proof = self._booking_with_proof()
		frappe.set_user(PIA)
		portal.get_proof_file(proof)
		self.assertTrue(frappe.local.response.filecontent)
		self.assertEqual(frappe.local.response.display_content_as, "inline")

	def test_proof_payload_ships_the_apps_upload_stamp_not_frappes(self):
		"""Backlog B10, closed in section-22.

		`get_my_booking_detail` shipped `proof.creation` under the key
		`uploaded_at`. The two are equal in production, which is why this sat
		latent for five sections — but `uploaded_at` comes from
		`clock.now_dt()` and `creation` never does, so the moment a clock is not
		the real one they diverge, and the customer's own booking page shows an
		upload time inconsistent with the verification deadline computed FROM
		uploaded_at. The countdown and the timestamp explaining it disagreed.

		The second assertion is the one that would have caught it: this fixture
		uploads under a clock patched to March 2027 while `creation` is the real
		wall clock, so a payload still reading `creation` fails here. The board
		has always shipped the doc field (api/board.py) — only the customer's
		copy lied — and `test_verification.py` asserts the DOC, never the
		payload. This row is that missing half.
		"""
		booking, proof = self._booking_with_proof()
		stamps = frappe.db.get_value(
			"CBT Payment Proof", proof, ["uploaded_at", "creation"], as_dict=True
		)
		frappe.set_user(PIA)
		detail = portal.get_my_booking_detail(booking)
		row = next(item for item in detail["proofs"] if item["name"] == proof)

		self.assertEqual(row["uploaded_at"], stamps.uploaded_at)
		self.assertNotEqual(
			row["uploaded_at"],
			stamps.creation,
			"the payload is still shipping frappe's insert stamp (B10)",
		)

	def test_other_customer_cannot_read_a_proof(self):
		booking, proof = self._booking_with_proof()
		frappe.set_user(NOEL)
		with self.assertRaises(frappe.PermissionError):
			portal.get_proof_file(proof)

	def test_company_staff_can_read_their_customers_proof(self):
		booking, proof = self._booking_with_proof()
		frappe.set_user(STELLA)
		portal.get_proof_file(proof)
		self.assertTrue(frappe.local.response.filecontent)

	def test_other_company_staff_cannot_read_a_proof(self):
		booking, proof = self._booking_with_proof()
		frappe.set_user(SAMUEL)
		with self.assertRaises(frappe.PermissionError):
			portal.get_proof_file(proof)

	def test_billing_statement_renders_for_its_owner(self):
		result = self._reserve()
		frappe.set_user(PIA)
		rendered = portal.render_billing_statement(result["booking"])
		self.assertIn("BILLING STATEMENT", rendered["body"])
		self.assertIn("Ayala Courts Sports Corp.", rendered["body"])
		self.assertIn("UNPAID", rendered["body"])

	def test_billing_statement_is_refused_to_another_customer(self):
		result = self._reserve(user=PIA)
		frappe.set_user(NOEL)
		with self.assertRaises(frappe.PermissionError):
			portal._booking_viewer(result["booking"])

	def test_print_permission_flag_is_restored_after_rendering(self):
		"""render_billing_statement flips ignore_print_permissions around its
		own guard — a leaked flag would silently open every print in the
		request."""
		result = self._reserve()
		frappe.set_user(PIA)
		portal.render_billing_statement(result["booking"])
		self.assertFalse(frappe.flags.ignore_print_permissions)

	def test_invoice_pdf_guard_and_magic_bytes(self):
		result = self._reserve()
		frappe.set_user(PIA)
		portal.download_invoice_pdf(result["booking"])
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))


class TestOfficeHoursSummary(PortalTestCase):
	def test_weekday_run_collapses_into_a_range(self):
		# AYALA: Mon–Fri 09:00–18:00 (seeds).
		self.assertEqual(office_hours_summary(AYALA), "Mon–Fri 9:00 AM–6:00 PM")

	def test_saturday_with_a_different_window_forms_its_own_group(self):
		company = frappe.get_doc("CBT Company", QCSM)
		# QCSM seeds: Mon–Sat 10:00–17:00 — one uniform run.
		self.assertEqual(office_hours_summary(company), "Mon–Sat 10:00 AM–5:00 PM")

	def test_empty_table_degrades_to_a_vague_phrase(self):
		company = frappe.get_doc("CBT Company", AYALA)
		company.office_hours = []
		self.assertEqual(office_hours_summary(company), "during business hours")


class TestWalkInIsInvisibleToThePortal(FrappeTestCase):
	"""Section-13: a walk-in is a STAFF record. The portal's whole security
	model is `customer = session.user`, so a customer-less booking must be
	unreachable from every customer endpoint — not merely unlisted."""

	# October 2027 = section-13's backend month (September is test_reports'
	# integer-claimed empty-control month — see seeds WALKIN_DATE).
	WALK_DATE = "2027-10-07"  # Thursday
	WALK_T0 = datetime(2027, 10, 7, 8, 0)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		throttle.clear_all_buckets()

	def setUp(self):
		frappe.set_user("Administrator")
		payload = {
			"doctype": "CBT Court Booking",
			"court": COURT_A,
			"customer_name": "Walk-in Wilma",
			"customer_phone": "0917-444-5555",
			"booking_date": self.WALK_DATE,
			"start_time": "10:00:00",
			"number_of_slots": 1,
			"payment_method": "Cash",
		}
		with patch(CLOCK, return_value=self.WALK_T0):
			self.walkin = frappe.get_doc(payload).insert(ignore_permissions=True)
		self.addCleanup(self._purge, self.walkin.name)

	def _purge(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc(
			"CBT Court Booking", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

	def test_walk_in_never_appears_in_get_my_bookings(self):
		for user in (PIA, NOEL):
			frappe.set_user(user)
			data = portal.get_my_bookings()
			names = {row["booking"] for row in data["bookings"]}
			self.assertNotIn(
				self.walkin.name, names, f"{user} can see a walk-in booking"
			)

	def test_own_booking_refuses_a_walk_in_for_every_customer(self):
		frappe.set_user(PIA)
		with self.assertRaises(frappe.PermissionError):
			portal._own_booking(self.walkin.name)

	def test_get_my_booking_detail_is_unreachable(self):
		frappe.set_user(PIA)
		with self.assertRaises(frappe.PermissionError):
			portal.get_my_booking_detail(self.walkin.name)

	def test_customer_cannot_self_cancel_a_walk_in(self):
		frappe.set_user(PIA)
		with self.assertRaises(frappe.PermissionError):
			portal.cancel_my_booking(self.walkin.name)

	def test_staff_can_still_open_the_walk_ins_statement(self):
		"""_booking_viewer falls through to the tenancy gate when the session
		user is not the (absent) customer — the desk must still be able to
		reprint the receipt it just handed over."""
		frappe.set_user(STELLA)
		doc = portal._booking_viewer(self.walkin.name)
		self.assertEqual(doc.name, self.walkin.name)

	def test_a_customer_cannot_open_the_walk_ins_statement(self):
		frappe.set_user(PIA)
		with self.assertRaises(frappe.PermissionError):
			portal._booking_viewer(self.walkin.name)
