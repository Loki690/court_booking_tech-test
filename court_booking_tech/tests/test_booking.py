"""
Court Booking Tech — Booking Engine (section-4)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_booking

Lifecycle (payment-method branch, transitions, extension), race-safe overlap
(FOR UPDATE lock), per-company numbering, the base-expiry sweep, the
immutability guard, and the suspension gate at EVERY entry (insert + confirm +
extend). Clock is monkeypatched — never wall-clock.
"""

import inspect
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt, get_datetime

from court_booking_tech.api.bookings import (
	cancel_booking,
	confirm_booking,
	extend_booking,
)
from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL, seed_all
from court_booking_tech.tasks import expire_reservations

AYALA = "ayala-courts"
QCSM = "qc-smash"

# A Friday far from the seeded cast's 2027-01-15 (no fixture interference).
TEST_DATE = "2027-02-05"
T0 = datetime(2027, 2, 5, 8, 0)

COURT_A = "AYALA-makati-court-a"
COURT_B = "AYALA-makati-court-b"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"


class TestBooking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _book(self, court, start_time, payment_method="Cash", slots=1, **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CUSTOMER_EMAIL,
			"booking_date": TEST_DATE,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": payment_method,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert()
		self._cleanup_booking(doc.name)
		return doc

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	# --- lifecycle -------------------------------------------------------

	def test_cash_instant_confirm(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_A, "10:00:00")
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertEqual(doc.confirmed_by, "Administrator")
		self.assertEqual(doc.confirmed_at, T0)
		self.assertEqual(doc.end_time, timedelta(hours=11))
		self.assertEqual(doc.duration_hours, 1.0)
		self.assertEqual(doc.total_amount, 300)  # Makati Badminton rate
		self.assertEqual(doc.customer_name, "Carla Courtside")

	def test_fund_transfer_expiry_from_company_chain(self):
		# Company override 0 -> platform (never-saved Singles -> 0) -> 30 guard.
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_A, "10:00:00", "Fund Transfer")
		self.assertEqual(doc.booking_status, "Reserved")
		self.assertEqual(doc.reservation_expires_at, T0 + timedelta(minutes=30))

		# Company override wins over the default.
		frappe.db.set_value("CBT Company", AYALA, "reservation_expiry_minutes", 45)
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "reservation_expiry_minutes", 0
		)
		with patch(CLOCK, return_value=T0):
			doc2 = self._book(COURT_A, "12:00:00", "Fund Transfer")
		self.assertEqual(doc2.reservation_expires_at, T0 + timedelta(minutes=45))

	def test_free_instant_confirm_with_full_discount(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_B, "10:00:00", "Free", discount_percent=100)
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertEqual(doc.total_amount, 0)

	def test_confirm_and_cancel_apis(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_A, "14:00:00", "Fund Transfer")
			confirm_booking(doc.name)
		doc.reload()
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertEqual(doc.confirmed_by, "Administrator")

		cancel_booking(doc.name, reason="test: confirm then cancel")
		doc.reload()
		self.assertEqual(doc.booking_status, "Cancelled")

		# A cancelled booking cannot be cancelled (or confirmed) again.
		self.assertRaises(frappe.ValidationError, cancel_booking, doc.name, "again")
		self.assertRaises(frappe.ValidationError, confirm_booking, doc.name)

	def test_extension_linkage(self):
		with patch(CLOCK, return_value=T0):
			original = self._book(COURT_B, "14:00:00")
			extension_name = extend_booking(original.name, slots=1, payment_method="Cash")
		self._cleanup_booking(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertEqual(extension.extended_from, original.name)
		self.assertEqual(extension.start_time, timedelta(hours=15))
		self.assertEqual(extension.booking_status, "Confirmed")

		original.reload()
		self.assertEqual(original.booking_status, "Extended")

		# The original is no longer Confirmed — it cannot be extended again.
		self.assertRaises(
			frappe.ValidationError, extend_booking, original.name, 1, "Cash"
		)

	def test_illegal_transitions_rejected(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_A, "16:00:00")
		doc.booking_status = "Reserved"  # Confirmed -> Reserved: no such edge
		self.assertRaises(frappe.ValidationError, doc.save)

		doc.reload()
		frappe.db.set_value(
			"CBT Court Booking", doc.name, "booking_status", "Completed"
		)
		doc.reload()
		doc.booking_status = "Cancelled"  # Completed is terminal
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_scheduling_fields_immutable_after_insert(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_A, "18:00:00")
		for fieldname, value in (
			("start_time", "19:00:00"),
			("court", COURT_B),
			("booking_date", "2027-02-06"),
			("number_of_slots", 2),
			("payment_method", "Fund Transfer"),
			("customer", "Administrator"),
		):
			doc.reload()
			doc.set(fieldname, value)
			self.assertRaises(frappe.ValidationError, doc.save)

		# Rate/discount stay staff-editable and recompute the total.
		doc.reload()
		doc.discount_percent = 50
		doc.save()
		self.assertEqual(doc.total_amount, 150)

	def test_customer_created_past_reject(self):
		past = {"booking_date": "2026-01-01", "start_time": "10:00:00"}
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": COURT_A,
				"customer": CUSTOMER_EMAIL,
				"number_of_slots": 1,
				"payment_method": "Cash",
				**past,
			}
		)
		doc.flags.customer_created = True
		self.assertRaises(frappe.ValidationError, doc.insert)

		# Staff (no flag) MAY back-record a walk-in.
		staff_doc = self._book(COURT_A, "10:00:00", booking_date="2026-01-08")
		self.assertEqual(staff_doc.booking_status, "Confirmed")

	def test_a_back_record_into_the_running_hour_is_checked_in_at_knob_zero(self):
		"""Section-18 (Backlog B7). The desk story the ungate exists to serve, end
		to end on a company that has NOT opted into no-show release.

		Section-16 already proved a back-record of a started slot is born checked
		in — but only with the no-show knob ON, because that was the only way its
		board would offer the slot at all. B7 dropped the knob gate, so the API
		behaviour and the board now agree for EVERY tenant, and this row pins both
		halves at knob 0:

		  * the insert stamps attendance (they are standing at the desk), which is
		    also what stops the very next sweep taking the slot away again if the
		    facility later opts in;
		  * the STAFF grid still offers the slot, which is what makes the walk-in
		    bookable from the board rather than only over the API.

		19:00-20:00 on COURT_B is used by no other row in this module, and makati
		closes at 22:00 so it is on the grid.
		"""
		from court_booking_tech.slots import get_availability, get_public_availability

		self.assertEqual(
			cint(frappe.db.get_value("CBT Company", AYALA, "no_show_release_minutes")),
			0,
			"the seeded knob must be OFF — that is what this row is about",
		)

		mid_hour = datetime(2027, 2, 5, 19, 20)
		with patch(CLOCK, return_value=mid_hour):
			doc = self._book(COURT_B, "19:00:00")
			staff = get_availability("AYALA-makati", TEST_DATE)
			public = get_public_availability("AYALA-makati", TEST_DATE)

		# From the DB, not the in-memory doc: a stamp that never persisted would
		# still read back on the object that tried to write it.
		saved = frappe.get_doc("CBT Court Booking", doc.name)
		self.assertEqual(saved.booking_status, "Confirmed")
		self.assertEqual(get_datetime(saved.checked_in_at), mid_hour)
		self.assertEqual(saved.checked_in_by, "Administrator")

		def _slot(payload, court, start):
			row = next(c for c in payload["courts"] if c["court"] == court)
			return next(s for s in row["slots"] if s["start_time"] == start)

		# The slot the booking now occupies reads BOOKED to staff, not past —
		# a running booking must stay visible and actionable on the desk.
		self.assertEqual(_slot(staff, COURT_B, "19:00:00")["status"], "booked")
		# ...and the neighbouring court's running hour is BOOKABLE, which is the
		# ungate itself. COURT_A is free at 19:00 in this module.
		self.assertEqual(_slot(staff, COURT_A, "19:00:00")["status"], "available")
		# The portal still refuses it, because the controller would.
		self.assertEqual(_slot(public, COURT_A, "19:00:00")["status"], "past")

	def test_customer_created_horizon_reject(self):
		"""The far end of the same fence as the past-slot reject: a customer
		may book today .. today + advance_booking_days INCLUSIVE, staff may
		book anything. The portal strip stops at the same number, but the
		strip is client code — this is what actually holds.
		"""
		before = frappe.db.get_value("CBT Company", AYALA, "advance_booking_days")
		frappe.db.set_value("CBT Company", AYALA, "advance_booking_days", 7)
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "advance_booking_days", before
		)

		def _customer_book(booking_date, start_time):
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": COURT_A,
					"customer": CUSTOMER_EMAIL,
					"booking_date": booking_date,
					"start_time": start_time,
					"number_of_slots": 1,
					"payment_method": "Cash",
				}
			)
			doc.flags.customer_created = True
			doc.insert()
			self._cleanup_booking(doc.name)
			return doc

		# T0 is TEST_DATE 08:00, so horizon 7 => 2027-02-12 is the last day.
		with patch(CLOCK, return_value=T0):
			edge = _customer_book("2027-02-12", "10:00:00")
			self.assertEqual(str(edge.booking_date), "2027-02-12")

			with self.assertRaises(frappe.ValidationError) as ctx:
				_customer_book("2027-02-13", "10:00:00")
			# Assert the REASON: 2027-02-13 is a Saturday and Makati is open,
			# but a closed-day/hours failure would also raise ValidationError.
			self.assertIn("7 days", str(ctx.exception))

			# Staff (no flag) may book past the horizon — leagues, tournaments.
			staff_doc = self._book(COURT_A, "10:00:00", booking_date="2027-02-13")
			self.assertEqual(staff_doc.booking_status, "Confirmed")

	def test_horizon_falls_back_to_the_platform_default(self):
		"""0 on the company means "platform default", and the resolver's hard
		constant is what stops a Singles row that predates the field (which
		reads back 0, not the field default) from meaning "today only"."""
		from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
			DEFAULT_ADVANCE_BOOKING_DAYS,
			resolve_advance_booking_days,
		)

		self.assertEqual(resolve_advance_booking_days(45), 45)
		self.assertGreaterEqual(resolve_advance_booking_days(0), DEFAULT_ADVANCE_BOOKING_DAYS)
		self.assertGreaterEqual(resolve_advance_booking_days(None), DEFAULT_ADVANCE_BOOKING_DAYS)

	def test_fetch_mirror_silent_correction(self):
		# An ACCOUNT booking cannot lie about its name: the server re-derives
		# it on every save — assert the correction, not an exception. Two
		# independent mechanisms now guarantee this, and section-13 depends on
		# both: v16's own fetch_from pass (base_document._validate_links, which
		# runs BEFORE validate and does not care that the field is no longer
		# hard read_only — it is now editable so a WALK-IN name can be typed),
		# and _validate_customer_identity's explicit derivation.
		with patch(CLOCK, return_value=T0):
			doc = self._book(COURT_B, "16:00:00")
		doc.customer_name = "Bogus Name"
		doc.save()
		self.assertEqual(doc.customer_name, "Carla Courtside")

	# --- slot grid & hours enforcement -----------------------------------

	def test_misaligned_start_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._book(COURT_A, "10:17:00")

	def test_closed_day_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._book(QCSM_COURT, "10:00:00", booking_date="2027-01-17")  # Sunday

	def test_span_past_closing_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._book(COURT_A, "21:00:00", slots=2)  # 21–23h vs 22:00 close

	# --- overlap & race protection ----------------------------------------

	def test_same_slot_rejected_adjacent_ok(self):
		with patch(CLOCK, return_value=T0):
			self._book(COURT_A, "09:00:00")
			with self.assertRaises(frappe.ValidationError):
				self._book(COURT_A, "09:00:00")
			# Adjacent slot and same slot on ANOTHER court are both fine.
			self._book(COURT_A, "10:00:00")
			self._book(COURT_B, "09:00:00")

	def test_multi_slot_span_conflict(self):
		with patch(CLOCK, return_value=T0):
			self._book(COURT_B, "11:00:00", slots=2)  # 11:00–13:00
			with self.assertRaises(frappe.ValidationError):
				self._book(COURT_B, "12:00:00")

	def test_expired_reserved_slot_rebookable(self):
		with patch(CLOCK, return_value=T0):
			held = self._book(COURT_A, "11:00:00", "Fund Transfer")
		# 40 min later the 30-min hold is dead — the slot books again even
		# though the sweep has not flipped the old row yet.
		with patch(CLOCK, return_value=T0 + timedelta(minutes=40)):
			winner = self._book(COURT_A, "11:00:00")
		self.assertEqual(winner.booking_status, "Confirmed")
		held.reload()
		self.assertEqual(held.booking_status, "Reserved")  # sweep's job, later

	def test_block_overlap_rejected(self):
		# Seeded court-level Maintenance block: AYALA-bgc Court 2, 18:00–20:00.
		with self.assertRaises(frappe.ValidationError):
			self._book("AYALA-bgc-court-2", "18:00:00", booking_date="2027-01-15")
		# Seeded whole-branch Holiday closure: QCSM-timog 2027-01-16.
		with self.assertRaises(frappe.ValidationError):
			self._book(QCSM_COURT, "10:00:00", booking_date="2027-01-16")

	def test_overlap_and_sweep_use_row_locks(self):
		# Spec-sanctioned lock assertion: the guard and the sweep both read
		# with FOR UPDATE (a second-session race test is not possible inside
		# one test transaction).
		from court_booking_tech.court_booking_tech.doctype.cbt_court_booking.cbt_court_booking import (
			CBTCourtBooking,
		)

		self.assertIn(
			"FOR UPDATE",
			inspect.getsource(CBTCourtBooking._validate_no_overlap_locked),
		)
		self.assertIn("FOR UPDATE", inspect.getsource(expire_reservations))

	# --- numbering --------------------------------------------------------

	def test_per_company_series_isolation(self):
		with patch(CLOCK, return_value=T0):
			first = self._book(COURT_A, "06:00:00")
			second = self._book(COURT_A, "07:00:00")
			other = self._book(QCSM_COURT, "06:00:00")
		self.assertRegex(first.name, r"^BK-AYALA-\d{4}-\d{5}$")
		self.assertRegex(other.name, r"^BK-QCSM-\d{4}-\d{5}$")
		self.assertEqual(int(second.name[-5:]), int(first.name[-5:]) + 1)

	# --- base expiry sweep ------------------------------------------------

	def test_sweep_expires_and_completes(self):
		with patch(CLOCK, return_value=T0):
			held = self._book(COURT_A, "13:00:00", "Fund Transfer")  # dies 08:30
			played = self._book(COURT_B, "06:00:00")  # Confirmed, ends 07:00

		day_after = datetime(2027, 2, 6, 0, 0)
		with patch(CLOCK, return_value=day_after):
			result = expire_reservations()

		self.assertIn(held.name, result["expired"])
		self.assertIn(played.name, result["completed"])
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", held.name, "booking_status"),
			"Expired",
		)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", played.name, "booking_status"),
			"Completed",
		)

	def test_sweep_leaves_live_holds_alone(self):
		with patch(CLOCK, return_value=T0):
			held = self._book(COURT_A, "15:00:00", "Fund Transfer")
			result = expire_reservations()  # clock still at T0 — hold is live
		self.assertNotIn(held.name, result["expired"])
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", held.name, "booking_status"),
			"Reserved",
		)

	# --- suspension gate at EVERY entry -----------------------------------

	def test_suspension_gate_insert_confirm_extend(self):
		with patch(CLOCK, return_value=T0):
			held = self._book(QCSM_COURT, "14:00:00", "Fund Transfer")
			playing = self._book(QCSM_COURT, "16:00:00")

		frappe.db.set_value("CBT Company", QCSM, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", QCSM, "status", "Active"
		)

		# Entry 1: insert.
		with self.assertRaisesRegex(frappe.ValidationError, "not accepting"):
			self._book(QCSM_COURT, "18:00:00")
		# Entry 2: confirm.
		with self.assertRaisesRegex(frappe.ValidationError, "not accepting"):
			confirm_booking(held.name)
		# Entry 3: extend.
		with self.assertRaisesRegex(frappe.ValidationError, "not accepting"):
			extend_booking(playing.name, 1, "Cash")

		# De-escalation stays possible while suspended.
		cancel_booking(held.name)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", held.name, "booking_status"),
			"Cancelled",
		)


class TestWalkInIdentity(FrappeTestCase):
	"""Section-13 (Backlog B1): a booking carries EITHER an account OR a
	free-text walk-in name — the stranger paying cash gets an honest, per-person
	receipt without being made to produce an email address.

	Deliberately NOT a subclass of TestBooking: inheriting a TestCase re-runs
	every parent test method (the test_notifications pattern is a helper-only
	base class, which is what this would need instead — not worth it for one
	consumer). WALK_DATE is its own day so these rows never contend with the
	section-4 cast on TEST_DATE.
	"""

	# October 2027 = section-13's backend month. NOT September: test_reports
	# claims `{"month": 9}` as its "no fixtures anywhere" control, by INTEGER —
	# invisible to a date-string collision grep.
	WALK_DATE = "2027-10-22"  # Friday
	WALK_T0 = datetime(2027, 10, 22, 8, 0)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _book(self, court, start_time, payment_method="Cash", slots=1, **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CUSTOMER_EMAIL,
			"booking_date": self.WALK_DATE,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": payment_method,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert()
		self._cleanup_booking(doc.name)
		return doc

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

	def _walkin(self, court, start_time, payment_method="Cash", **overrides):
		payload = {
			"customer": None,
			"customer_name": "Walk-in Wendell",
			"customer_phone": "0917-555-0100",
		}
		payload.update(overrides)
		with patch(CLOCK, return_value=self.WALK_T0):
			return self._book(court, start_time, payment_method, **payload)

	# --- controller -------------------------------------------------------

	def test_walkin_cash_confirms_and_bills_the_typed_name(self):
		doc = self._walkin(COURT_A, "10:00:00")
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertIsNone(doc.customer)
		self.assertEqual(doc.customer_name, "Walk-in Wendell")
		self.assertEqual(doc.customer_phone, "0917-555-0100")

		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.customer_name, "Walk-in Wendell")
		self.assertFalse(invoice.customer)

	def test_walkin_customer_column_is_really_null(self):
		"""Not '' — _validate_immutables compares raw values, so a stored ''
		read back against an in-memory None would throw on every later save."""
		doc = self._walkin(COURT_A, "11:00:00", customer="")
		self.assertIsNone(
			frappe.db.get_value("CBT Court Booking", doc.name, "customer")
		)
		# The save that would have broken: any later edit re-runs the guard.
		doc.reload()
		doc.notes = "paid in 500s"
		doc.save()
		self.assertEqual(doc.booking_status, "Confirmed")

	def test_booking_with_neither_identity_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "walk-in name"):
			self._book(
				COURT_A,
				"12:00:00",
				customer=None,
				booking_date=self.WALK_DATE,
			)

	def test_blank_walkin_name_is_not_an_identity(self):
		with self.assertRaisesRegex(frappe.ValidationError, "walk-in name"):
			self._book(
				COURT_A,
				"12:00:00",
				customer=None,
				customer_name="   ",
				booking_date=self.WALK_DATE,
			)

	def test_portal_flag_can_never_mint_a_walk_in(self):
		"""reserve_booking always sets the session user, so this is the fence
		that makes 'walk-ins are staff-only' structural rather than a habit."""
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": COURT_A,
				"customer_name": "Anonymous Online",
				"booking_date": self.WALK_DATE,
				"start_time": "13:00:00",
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
			}
		)
		doc.flags.customer_created = True
		with self.assertRaisesRegex(frappe.ValidationError, "customer account"):
			with patch(CLOCK, return_value=self.WALK_T0):
				doc.insert()

	def test_account_booking_never_stores_a_walk_in_phone(self):
		"""The phone is hidden behind depends_on for an account booking — an
		API caller must not be able to park a value no screen ever shows."""
		with patch(CLOCK, return_value=self.WALK_T0):
			doc = self._book(
				COURT_B,
				"10:00:00",
				booking_date=self.WALK_DATE,
				customer_phone="0917-999-9999",
			)
		self.assertIsNone(doc.customer_phone)

	def test_walkin_name_stays_editable_for_a_typo_fix(self):
		doc = self._walkin(COURT_B, "11:00:00")
		doc.customer_name = "Walk-in Wendeline"
		doc.save()
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", doc.name, "customer_name"),
			"Walk-in Wendeline",
		)

	# --- API --------------------------------------------------------------

	def test_api_create_booking_walk_in_name_only(self):
		from court_booking_tech.api import bookings as bookings_api

		with patch(CLOCK, return_value=self.WALK_T0):
			result = bookings_api.create_booking(
				court=COURT_A,
				booking_date=self.WALK_DATE,
				start_time="14:00:00",
				payment_method="Cash",
				customer_name="Cash Only Cora",
			)
		self._cleanup_booking(result["name"])
		self.assertEqual(result["booking_status"], "Confirmed")
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertIsNone(doc.customer)
		self.assertEqual(doc.customer_name, "Cash Only Cora")
		self.assertIsNone(doc.customer_phone)

	def test_api_create_booking_walk_in_with_phone(self):
		from court_booking_tech.api import bookings as bookings_api

		with patch(CLOCK, return_value=self.WALK_T0):
			result = bookings_api.create_booking(
				court=COURT_A,
				booking_date=self.WALK_DATE,
				start_time="15:00:00",
				payment_method="Cash",
				customer_name="Cash Only Cora",
				customer_phone="0918-123-4567",
			)
		self._cleanup_booking(result["name"])
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(doc.customer_phone, "0918-123-4567")

	def test_api_refuses_both_identities_and_neither(self):
		from court_booking_tech.api import bookings as bookings_api

		with self.assertRaisesRegex(frappe.ValidationError, "not both"):
			bookings_api.create_booking(
				court=COURT_A,
				booking_date=self.WALK_DATE,
				start_time="16:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				customer_name="Cash Only Cora",
			)
		with self.assertRaisesRegex(frappe.ValidationError, "walk-in name"):
			bookings_api.create_booking(
				court=COURT_A,
				booking_date=self.WALK_DATE,
				start_time="16:00:00",
				payment_method="Cash",
			)

	def test_api_skips_the_membership_lookup_for_a_walk_in(self):
		"""A same-NAMED walk-in at a company where a member exists is not that
		member — there is no account to hold a membership against, so the
		tri-state discount default is a plain 0."""
		from court_booking_tech.api import bookings as bookings_api
		from court_booking_tech.membership import get_member_discount

		# Ground truth: Mia really is a discounted AYALA member.
		self.assertGreater(get_member_discount(AYALA, "cust.mia@example.com"), 0)

		with patch(CLOCK, return_value=self.WALK_T0):
			result = bookings_api.create_booking(
				court=COURT_A,
				booking_date=self.WALK_DATE,
				start_time="17:00:00",
				payment_method="Cash",
				customer_name=frappe.db.get_value(
					"User", "cust.mia@example.com", "full_name"
				),
			)
		self._cleanup_booking(result["name"])
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(flt(doc.discount_percent), 0.0)
		self.assertEqual(doc.total_amount, 300)  # Makati list rate, undiscounted

	# --- extension --------------------------------------------------------

	def test_extending_a_walk_in_carries_name_and_phone(self):
		"""'One more hour' is the walk-in's commonest request. before_insert
		fills customer_name only FROM A USER, so without the explicit carry the
		extension would reach validate with no identity at all — AND the
		original's own save (Confirmed -> Extended) re-runs the same guard."""
		original = self._walkin(COURT_B, "14:00:00")
		with patch(CLOCK, return_value=self.WALK_T0):
			extension_name = extend_booking(original.name, 1, "Cash")
		self._cleanup_booking(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertIsNone(extension.customer)
		self.assertEqual(extension.customer_name, "Walk-in Wendell")
		self.assertEqual(extension.customer_phone, "0917-555-0100")
		self.assertEqual(extension.extended_from, original.name)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", original.name, "booking_status"),
			"Extended",
		)
