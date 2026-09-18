"""
Court Booking Tech — No-show release (section-16, PLAN §8s)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_no_show

A paid slot nobody turned up for goes back on sale X minutes after it was due
to start, so the desk can resell the time. Three claims are load-bearing and
every row below defends one of them:

1. IT NEVER TOUCHES THE MONEY. The invoice stays Paid & Verified with the
   original verification stamps, the revenue reports keep counting it, and only
   OCCUPANCY drops it — the court really did stand empty. The payment was real;
   the slot is forfeit, not refunded.
2. IT CANNOT REWRITE HISTORY. The sweep's `now <= end_dt` bound means a
   facility switching the knob on today cannot have years of un-checked-in
   Confirmed bookings retro-flipped: they are all past their end and belong to
   the Completed tidy. This is the single most important row in the file.
3. IT IS REVERSIBLE, AND HONESTLY SO. Undo re-occupies the slot and therefore
   re-runs the double-booking guard — if a walk-in already bought the freed
   time the undo fails clean and the booking stays released, with cancel as the
   remaining correction.

MONTH. December 2027 (month-per-module discipline, S11 as-built 20). Verified
free BOTH ways before claiming it (S13 as-built 1b): no `"month": 12` filter
anywhere in the test tree and no `2027-12` date string in the app — the only
2027-12 literal is test_reports' MAX_DAYS refusal range, which asserts a throw
and reads no rows. 2027-12-01/02 are Wed/Thu (derived from test_rate_rules'
pinned 2027-10-18 = Monday).

COURTS. Everything runs on ALREADY-SEEDED courts; this module creates none.
AYALA-makati opens 06:00-22:00 every day with 60-minute slots, and its two
courts bill ₱300/hr flat — which is what lets the money rows below be pinned to
the peso rather than asserted with "> 0".

THE KNOB IS THE FIXTURE. `no_show_release_minutes` is 0 on every seeded company
(the feature ships OFF), so each class that needs it sets it on AYALA and
restores it in cleanup, with clear_document_cache on both edges (S5 as-built
14). Nothing here touches E2E Fast: file 14 owns that company's knob.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt, get_datetime

from court_booking_tech.api.bookings import (
	cancel_booking,
	check_in,
	create_booking,
	extend_booking,
	reschedule_booking,
	undo_no_show,
)
from court_booking_tech.court_booking_tech.report.cbt_company_revenue import (
	cbt_company_revenue,
)
from court_booking_tech.court_booking_tech.report.cbt_occupancy import cbt_occupancy
from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL, PLATFORM_ADMIN_EMAIL, seed_all
from court_booking_tech.tasks import expire_reservations

AYALA = "ayala-courts"
STELLA = "staff.ayala@example.com"

COURT_A = "AYALA-makati-court-a"  # ₱300/hr, makati opens 06:00-22:00 daily
COURT_B = "AYALA-makati-court-b"  # ₱300/hr
MAKATI = "AYALA-makati"
RATE = 300

WEDNESDAY = "2027-12-01"
THURSDAY = "2027-12-02"

# The booking under test runs 10:00-11:00, so every clock below reads as an
# offset from a slot the reader can hold in their head.
BOOKED_AT = datetime(2027, 12, 1, 9, 0)  # created an hour before it starts
START = datetime(2027, 12, 1, 10, 0)
END = datetime(2027, 12, 1, 11, 0)
GRACE = 15

CLOCK = "court_booking_tech.clock.now_dt"


class NoShowTestCase(FrappeTestCase):
	"""Shared helpers. Holds NO test methods — inheriting a TestCase that does
	would re-run every parent test in each subclass (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- fixtures ---------------------------------------------------------

	def _set_knob(self, minutes: int, company: str = AYALA):
		"""Opt `company` into no-show release, and put it back afterwards.

		clear_document_cache on BOTH edges: db.set_value leaves a cached
		CBT Company document holding the old value, and the accessor,
		api.board.get_board_data's own read and the sweep would each read a
		different truth depending on who loaded the doc first.
		(It used to name slots.releases_no_shows as the third reader. Section-18
		deleted that helper with Backlog B7 — the staff past-line no longer
		depends on this knob at all — but the cache hazard is unchanged.)
		"""
		before = frappe.db.get_value(
			"CBT Company", company, "no_show_release_minutes"
		)

		def _restore():
			frappe.db.set_value(
				"CBT Company", company, "no_show_release_minutes", before
			)
			frappe.clear_document_cache("CBT Company", company)

		frappe.db.set_value(
			"CBT Company", company, "no_show_release_minutes", minutes
		)
		frappe.clear_document_cache("CBT Company", company)
		self.addCleanup(_restore)

	def _book(
		self,
		court=COURT_A,
		start_time="10:00:00",
		payment_method="Cash",
		date=WEDNESDAY,
		slots=1,
		at=BOOKED_AT,
		**overrides,
	):
		"""Insert under a PATCHED clock — a real-clock insert would stamp the
		booking in the present and every 2027 scenario would misread."""
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CUSTOMER_EMAIL,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": payment_method,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		with patch(CLOCK, return_value=at):
			doc.insert()
		self._cleanup(doc.name)
		return doc

	def _cleanup(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Court Booking", name, force=True,
				ignore_permissions=True, ignore_missing=True,
			)

		self.addCleanup(_do)

	def _sweep(self, at):
		with patch(CLOCK, return_value=at):
			return expire_reservations()

	def _status(self, name) -> str:
		"""ALWAYS from the DB. An in-memory doc that failed to save still holds
		the status the caller tried to write (section-15 as-built 2)."""
		return frappe.db.get_value("CBT Court Booking", name, "booking_status")

	def _reload(self, name):
		return frappe.get_doc("CBT Court Booking", name)

	def _invoice(self, name):
		doc = self._reload(name)
		self.assertTrue(doc.billing_doc, f"{name} has no billing document")
		return frappe.get_doc("CBT Booking Invoice", doc.billing_doc)

	def _release(self, booking, at=START + timedelta(minutes=GRACE + 1)):
		"""Take a booking through a real sweep and assert it really released."""
		result = self._sweep(at)
		self.assertIn(booking, result["no_show"], result)
		return result


class TestNoShowKnob(NoShowTestCase):
	"""When the sweep releases, and when it must not."""

	def test_knob_off_never_releases(self):
		"""The shipped default. An overdue, un-checked-in, fully paid booking
		is left completely alone — nothing about this feature happens to a
		facility that has not asked for it."""
		booking = self._book()
		result = self._sweep(START + timedelta(minutes=45))

		self.assertEqual(result["no_show"], [])
		self.assertEqual(self._status(booking.name), "Confirmed")
		self.assertEqual(self._invoice(booking.name).status, "Paid & Verified")

	def test_release_happens_exactly_at_the_grace_boundary(self):
		"""The grace is a promise to the customer who is ten minutes late. It
		is pinned to the minute in BOTH directions, because a boundary asserted
		on one side only is half a test."""
		self._set_knob(GRACE)
		booking = self._book()

		inside = self._sweep(START + timedelta(minutes=GRACE - 1))
		self.assertEqual(inside["no_show"], [], "released while still in grace")
		self.assertEqual(self._status(booking.name), "Confirmed")

		# Exactly ON the boundary is still theirs — `start + X < now`.
		on_the_line = self._sweep(START + timedelta(minutes=GRACE))
		self.assertEqual(on_the_line["no_show"], [], "released ON the boundary")
		self.assertEqual(self._status(booking.name), "Confirmed")

		past = self._sweep(START + timedelta(minutes=GRACE + 1))
		self.assertIn(booking.name, past["no_show"])
		self.assertEqual(self._status(booking.name), "No Show")

	def test_a_checked_in_booking_is_immune(self):
		"""The entire point of check-in: one click and the slot is theirs for
		the rest of the hour, however late they turned up."""
		self._set_knob(GRACE)
		booking = self._book()
		with patch(CLOCK, return_value=START + timedelta(minutes=5)):
			check_in(booking.name)

		result = self._sweep(START + timedelta(minutes=GRACE + 30))
		self.assertEqual(result["no_show"], [])
		self.assertEqual(self._status(booking.name), "Confirmed")

	def test_a_walk_in_releases_and_keeps_its_honest_receipt(self):
		"""Section-13 identity through section-16's flip. A walk-in has no
		account, so nothing about the release may reach for one — and the
		statement must still name the person who handed over the cash."""
		self._set_knob(GRACE)
		with patch(CLOCK, return_value=BOOKED_AT):
			created = create_booking(
				court=COURT_B,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				payment_method="Cash",
				customer_name="Walk-in Wanda S16",
				customer_phone="0917-000-4444",
			)
		self._cleanup(created["name"])

		self._release(created["name"])

		doc = self._reload(created["name"])
		self.assertIsNone(doc.customer)
		self.assertEqual(doc.customer_name, "Walk-in Wanda S16")
		invoice = self._invoice(created["name"])
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.customer_name, "Walk-in Wanda S16")

	def test_only_the_opted_in_company_is_swept(self):
		"""The knob is per company, and the sweep reads a map, not a flag — a
		second tenant's identical booking must be untouched in the same run."""
		self._set_knob(GRACE)
		mine = self._book()
		with patch(CLOCK, return_value=BOOKED_AT):
			theirs = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": "QCSM-timog-court-1",
					"customer": CUSTOMER_EMAIL,
					"booking_date": WEDNESDAY,
					"start_time": "10:00:00",
					"number_of_slots": 1,
					"payment_method": "Cash",
				}
			).insert()
		self._cleanup(theirs.name)

		result = self._release(mine.name)
		self.assertNotIn(theirs.name, result["no_show"])
		self.assertEqual(self._status(theirs.name), "Confirmed")


class TestNoShowSafetyRail(NoShowTestCase):
	"""The bound that makes switching the knob on a live facility safe."""

	def test_a_booking_past_its_end_becomes_completed_not_no_show(self):
		"""THE row this section exists to keep green. Enabling the knob must
		never rewrite a facility's history: every un-checked-in Confirmed
		booking from before today is past its end, so it belongs to the
		Completed tidy and the release step cannot see it."""
		self._set_knob(GRACE)
		booking = self._book()

		result = self._sweep(END + timedelta(minutes=1))

		self.assertNotIn(booking.name, result["no_show"])
		self.assertIn(booking.name, result["completed"])
		self.assertEqual(self._status(booking.name), "Completed")

	def test_the_two_steps_cannot_both_claim_one_booking(self):
		"""Disjoint by construction (`now <= end_dt` vs `end_dt < now`), pinned
		because the release step runs FIRST and a sloppier bound would let it
		swallow rows the tidy is responsible for."""
		self._set_knob(GRACE)
		booking = self._book()
		result = self._sweep(END)  # the exact instant it ends

		self.assertEqual(
			(booking.name in result["no_show"]) + (booking.name in result["completed"]),
			1,
			result,
		)

	def test_the_return_dict_carries_the_no_show_list(self):
		"""The E2E's trigger contract — file 14 reads this key."""
		self._set_knob(GRACE)
		booking = self._book()
		result = self._release(booking.name)

		self.assertEqual(set(result), {"expired", "no_show", "completed"})
		self.assertIsInstance(result["no_show"], list)

	def test_an_unpaid_hold_is_never_released_as_a_no_show(self):
		"""A Reserved booking has its OWN clock (PLAN §5). Releasing it here
		would double-govern it and flip its invoice to a status the expiry path
		never produces."""
		self._set_knob(GRACE)
		booking = self._book(payment_method="Fund Transfer", start_time="20:00:00")
		self.assertEqual(booking.booking_status, "Reserved")

		# Well past a 20:00 start's grace, but its base clock died at 09:30 —
		# so the EXPIRY loop owns it, and only that loop.
		result = self._sweep(datetime(2027, 12, 1, 20, 30))
		self.assertNotIn(booking.name, result["no_show"])
		self.assertIn(booking.name, result["expired"])


class TestNoShowMoney(NoShowTestCase):
	"""Claim 1: the release never touches the money."""

	def test_the_invoice_stays_paid_and_verified_with_the_original_stamps(self):
		"""Not merely "still PAID" — still verified BY THE PERSON WHO TOOK THE
		CASH. _apply_booking_status re-copies the stamps on every sync, so this
		is a real assertion and not a tautology."""
		self._set_knob(GRACE)
		frappe.set_user(STELLA)
		booking = self._book()
		# Administrator on purpose: the release below is the SCHEDULER's act
		# (cron runs as Administrator), not a seat's — Batch 17 left it.
		frappe.set_user("Administrator")
		self.assertEqual(booking.confirmed_by, STELLA)

		self._release(booking.name)

		invoice = self._invoice(booking.name)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, STELLA)
		self.assertEqual(get_datetime(invoice.verified_at), BOOKED_AT)
		self.assertEqual(flt(invoice.total_amount), float(RATE))

	def test_a_plain_resave_of_a_released_booking_keeps_it_paid(self):
		"""The mapping earns its keep OUTSIDE the sweep too. Any doc.save() on
		a released booking — a staff member fixing a typo in the notes — routes
		through the billing seam, and a missing map entry would fall to the
		"Unpaid" default and silently demote a paid statement."""
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		doc = self._reload(booking.name)
		doc.notes = "Rang twice, no answer."
		doc.save()

		self.assertEqual(self._status(booking.name), "No Show")
		self.assertEqual(self._invoice(booking.name).status, "Paid & Verified")

	def test_revenue_still_counts_it_and_occupancy_stops_counting_it(self):
		"""The one place the two reports diverge, pinned in pesos and hours.
		The money is real, so a facility's revenue must not drop when it starts
		releasing no-shows; the court stood empty, so its utilisation must."""
		self._set_knob(GRACE)
		booking = self._book()
		filters = {"company": AYALA, "from_date": WEDNESDAY, "to_date": WEDNESDAY}

		revenue_before = self._revenue(filters)
		occupancy_before = self._occupancy(filters)

		self._release(booking.name)

		self.assertEqual(
			self._revenue(filters),
			revenue_before,
			"a released no-show must keep earning — the payment was real",
		)
		self.assertEqual(
			self._occupancy(filters),
			flt(occupancy_before - 1.0, 2),
			"a released hour is unsold capacity until somebody rebuys it",
		)

	def test_reselling_the_freed_hour_earns_on_top(self):
		"""The revenue story end to end: the forfeited payment stays, and the
		walk-in who takes the slot is new money, not a replacement for it."""
		self._set_knob(GRACE)
		booking = self._book()
		filters = {"company": AYALA, "from_date": WEDNESDAY, "to_date": WEDNESDAY}
		self._release(booking.name)
		revenue_after_release = self._revenue(filters)

		with patch(CLOCK, return_value=START + timedelta(minutes=20)):
			resold = create_booking(
				court=COURT_A,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				payment_method="Cash",
				customer_name="Walk-in Wanda S16",
			)
		self._cleanup(resold["name"])

		self.assertEqual(
			self._revenue(filters), flt(revenue_after_release + RATE, 2)
		)
		self.assertEqual(self._invoice(booking.name).status, "Paid & Verified")
		self.assertEqual(self._invoice(resold["name"]).status, "Paid & Verified")

	def test_cancelling_a_released_booking_writes_the_money_off(self):
		"""The correction path of last resort, and the reason the desk UI warns
		before offering it: unlike cancelling a live booking, this one removes
		revenue that has already been recognised."""
		self._set_knob(GRACE)
		booking = self._book()
		filters = {"company": AYALA, "from_date": WEDNESDAY, "to_date": WEDNESDAY}
		self._release(booking.name)
		revenue_before = self._revenue(filters)

		with patch(CLOCK, return_value=START + timedelta(minutes=30)):
			cancel_booking(booking.name, reason="test: released in error")

		self.assertEqual(self._status(booking.name), "Cancelled")
		self.assertEqual(self._invoice(booking.name).status, "Cancelled")
		self.assertEqual(
			self._revenue(filters), flt(revenue_before - RATE, 2)
		)

	# --- report readers ---------------------------------------------------

	def _revenue(self, filters) -> float:
		_columns, data = cbt_company_revenue.execute(dict(filters))
		return flt(sum(row["bookings_revenue"] for row in data), 2)

	def _occupancy(self, filters) -> float:
		_columns, data = cbt_occupancy.execute(dict(filters))
		return flt(
			sum(row["booked_hours"] for row in data if row["branch"] == MAKATI), 2
		)


class TestNoShowUndo(NoShowTestCase):
	"""Claim 3: reversible, and honest about when it cannot be."""

	def test_undo_puts_a_free_slot_back_and_marks_them_present(self):
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		with patch(CLOCK, return_value=START + timedelta(minutes=20)):
			result = undo_no_show(booking.name)

		self.assertEqual(result["booking_status"], "Confirmed")
		doc = self._reload(booking.name)
		self.assertEqual(doc.booking_status, "Confirmed")
		# Restored AND present — otherwise the very next sweep takes it away
		# again and the undo button looks broken.
		self.assertEqual(get_datetime(doc.checked_in_at), START + timedelta(minutes=20))
		self.assertEqual(doc.checked_in_by, "Administrator")
		self.assertEqual(self._invoice(booking.name).status, "Paid & Verified")

		self.assertEqual(self._sweep(START + timedelta(minutes=40))["no_show"], [])

	def test_undo_keeps_the_original_confirmation_stamps(self):
		"""Coming back from a release is not a new payment. The status machine's
		`or session.user` default must not fire on an already-stamped doc."""
		self._set_knob(GRACE)
		frappe.set_user(STELLA)
		booking = self._book()
		frappe.set_user("Administrator")  # the sweep is the scheduler's act (see above)
		self._release(booking.name)

		with patch(CLOCK, return_value=START + timedelta(minutes=20)):
			undo_no_show(booking.name)

		doc = self._reload(booking.name)
		self.assertEqual(doc.confirmed_by, STELLA)
		self.assertEqual(get_datetime(doc.confirmed_at), BOOKED_AT)

	def test_undo_after_the_slot_was_resold_fails_clean(self):
		"""THE overlap pin. The whole point of releasing was that somebody else
		could buy the hour — so the undo has to re-run the double-booking guard
		by hand, and lose when it should."""
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		with patch(CLOCK, return_value=START + timedelta(minutes=20)):
			resold = create_booking(
				court=COURT_A,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				payment_method="Cash",
				customer_name="Walk-in Wanda S16",
			)
		self._cleanup(resold["name"])

		with self.assertRaises(frappe.ValidationError) as err:
			with patch(CLOCK, return_value=START + timedelta(minutes=25)):
				undo_no_show(booking.name)
		self.assertIn("Slot already taken", str(err.exception))

		# From the DB, not the doc: a throw inside assertRaises does not unwind
		# the way a request boundary does (section-15 as-built 2).
		self.assertEqual(self._status(booking.name), "No Show")
		self.assertEqual(self._status(resold["name"]), "Confirmed")
		self.assertEqual(self._invoice(booking.name).status, "Paid & Verified")

	def test_undo_into_a_window_blocked_since_the_release_is_refused(self):
		"""Documented consequence of reusing the insert-time guard: it re-checks
		SLOT BLOCKS too. Correct — maintenance means the court is unusable — but
		it does not read like a booking clash, so it is pinned and written down."""
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		block = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": MAKATI,
				"company": AYALA,
				"court": COURT_A,
				"block_date": WEDNESDAY,
				"start_time": "10:00:00",
				"end_time": "11:00:00",
				"reason": "Maintenance",
			}
		)
		block.insert()
		self.addCleanup(
			frappe.delete_doc, "CBT Slot Block", block.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)

		with self.assertRaises(frappe.ValidationError) as err:
			with patch(CLOCK, return_value=START + timedelta(minutes=25)):
				undo_no_show(booking.name)
		self.assertIn("blocked", str(err.exception).lower())
		self.assertEqual(self._status(booking.name), "No Show")

	def test_undo_is_refused_for_a_suspended_company(self):
		"""Undo RE-OCCUPIES a slot, so it is booking-creating and the
		suspension gate is ON — the opposite posture from check_in."""
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		frappe.db.set_value("CBT Company", AYALA, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "status", "Active"
		)
		with self.assertRaises(frappe.ValidationError):
			undo_no_show(booking.name)
		self.assertEqual(self._status(booking.name), "No Show")

	def test_only_a_released_booking_can_be_undone(self):
		booking = self._book()
		with self.assertRaises(frappe.ValidationError) as err:
			undo_no_show(booking.name)
		self.assertIn("No Show", str(err.exception))


class TestAutoCheckIn(NoShowTestCase):
	"""Attendance that does not need a click, because the insert itself is the
	person turning up."""

	def test_an_extension_is_born_checked_in(self):
		""""One more hour" is asked for by people already on the court — an
		extension that could be released as a no-show would be absurd."""
		parent = self._book()
		with patch(CLOCK, return_value=START + timedelta(minutes=50)):
			child = extend_booking(parent.name, slots=1, payment_method="Cash")
		self._cleanup(child)

		doc = self._reload(child)
		self.assertIsNotNone(doc.checked_in_at)
		self.assertEqual(doc.checked_in_by, "Administrator")

	def test_a_staff_back_record_of_a_started_slot_is_checked_in(self):
		"""They are standing at the desk. This is also what makes a resold
		no-show slot stick: the replacement walk-in is a back-record, so the
		next sweep leaves it alone."""
		self._set_knob(GRACE)
		booking = self._book(at=START + timedelta(minutes=20))

		doc = self._reload(booking.name)
		self.assertEqual(
			get_datetime(doc.checked_in_at), START + timedelta(minutes=20)
		)
		self.assertEqual(self._sweep(START + timedelta(minutes=40))["no_show"], [])

	def test_a_future_booking_is_not_checked_in_by_anybody(self):
		"""The control for the row above: it is the PAST START that stamps
		attendance, not merely being staff."""
		booking = self._book(at=BOOKED_AT)
		self.assertIsNone(self._reload(booking.name).checked_in_at)

	def test_a_customer_created_booking_never_auto_checks_in(self):
		"""Defence in depth. A customer cannot reach the past-start branch at
		all (_reject_past_for_customers refuses first), so this pins the FLAG
		half of the rule — the half that would otherwise be untested until
		somebody relaxed the other one."""
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": COURT_B,
				"customer": CUSTOMER_EMAIL,
				"booking_date": WEDNESDAY,
				"start_time": "14:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		doc.flags.customer_created = True
		with patch(CLOCK, return_value=BOOKED_AT):
			doc.insert()
		self._cleanup(doc.name)

		self.assertIsNone(self._reload(doc.name).checked_in_at)

	def test_a_reschedule_does_not_inherit_check_in(self):
		"""A move is a new booking and a new attendance question (S15 hand-off).
		The TARGET is deliberately in the future — moving onto an already-started
		slot auto-checks through the back-record branch, which is correct but
		would make this row pass for the wrong reason."""
		booking = self._book(at=START + timedelta(minutes=20))
		self.assertIsNotNone(self._reload(booking.name).checked_in_at)

		with patch(CLOCK, return_value=START + timedelta(minutes=25)):
			result = reschedule_booking(booking.name, start_time="18:00:00")
		self._cleanup(result["name"])

		self.assertIsNone(self._reload(result["name"]).checked_in_at)


class TestCheckInEndpoint(NoShowTestCase):
	"""The one click, and everything it refuses."""

	def test_check_in_stamps_who_and_when(self):
		booking = self._book()
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=START + timedelta(minutes=2)):
			result = check_in(booking.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertFalse(result["already"])
		doc = self._reload(booking.name)
		self.assertEqual(get_datetime(doc.checked_in_at), START + timedelta(minutes=2))
		self.assertEqual(doc.checked_in_by, STELLA)

	def test_check_in_is_idempotent(self):
		"""A busy desk clicks twice. The second click must not rewrite who
		greeted them or when."""
		booking = self._book()
		with patch(CLOCK, return_value=START + timedelta(minutes=2)):
			check_in(booking.name)
		with patch(CLOCK, return_value=START + timedelta(minutes=40)):
			second = check_in(booking.name)

		self.assertTrue(second["already"])
		self.assertEqual(
			get_datetime(self._reload(booking.name).checked_in_at),
			START + timedelta(minutes=2),
		)

	def test_check_in_refuses_an_unpaid_hold(self):
		booking = self._book(payment_method="Fund Transfer", start_time="20:00:00")
		with self.assertRaises(frappe.ValidationError) as err:
			check_in(booking.name)
		self.assertIn("Confirmed or Extended", str(err.exception))

	def test_check_in_on_a_released_booking_points_at_undo(self):
		"""Answering the question they are actually asking. "Wrong status" would
		leave a staff member clicking a button that will never work."""
		self._set_knob(GRACE)
		booking = self._book()
		self._release(booking.name)

		with self.assertRaises(frappe.ValidationError) as err:
			check_in(booking.name)
		self.assertIn("Undo no-show", str(err.exception))

	def test_check_in_works_for_a_suspended_company(self):
		"""Deliberately NOT suspension-gated: a company suspended over a billing
		dispute must not have its customers' courts released out from under them
		for want of a check-in."""
		booking = self._book()
		frappe.db.set_value("CBT Company", AYALA, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "status", "Active"
		)
		with patch(CLOCK, return_value=START + timedelta(minutes=2)):
			check_in(booking.name)
		self.assertIsNotNone(self._reload(booking.name).checked_in_at)


class TestNoShowBoardSurface(NoShowTestCase):
	"""What the desk can see and act on — the board payload's half of the UX."""

	def test_a_released_slot_reads_bookable_to_staff_and_past_to_customers(self):
		"""D1, pinned in both directions. A released booking is by definition
		IN PROGRESS, so without the staff relaxation the freed slot renders
		greyed-out and the resale this feature exists for is impossible at the
		desk. The portal keeps the start-based rule, because the controller
		refuses a customer booking of a started slot and a grid must never
		offer what the server will not take."""
		from court_booking_tech.slots import get_availability, get_public_availability

		self._set_knob(GRACE)
		booking = self._book()
		at = START + timedelta(minutes=GRACE + 1)
		self._release(booking.name, at)

		with patch(CLOCK, return_value=at):
			staff = get_availability(MAKATI, WEDNESDAY)
			public = get_public_availability(MAKATI, WEDNESDAY)

		self.assertEqual(self._slot(staff, "10:00:00")["status"], "available")
		self.assertEqual(self._slot(public, "10:00:00")["status"], "past")

	def test_the_running_hour_is_bookable_to_staff_even_at_knob_zero(self):
		"""Section-18 (Backlog B7) — the row this replaces asserted the OPPOSITE.

		Section-16 gated the staff relaxation on `no_show_release_minutes > 0`,
		so a company that had not opted in kept the start-based rule and its
		board greyed out the hour in progress. That left the desk surface
		refusing what `create_booking` has always accepted (a staff back-record
		of a started slot, S4 as-built 6) and pushed staff to the DocType form,
		where Backlog B3 then bit them. The gate is gone: the relaxation is now
		knob-INDEPENDENT, which is the stronger and simpler rule.

		AYALA's knob is 0 here — `_set_knob` is deliberately NOT called — so this
		is the ungated path, and all three halves of the new invariant are pinned
		together, because each one alone would pass for the wrong reason:
		staff past-line = END, portal past-line = START, always.
		"""
		from court_booking_tech.slots import get_availability, get_public_availability

		self.assertEqual(
			cint(frappe.db.get_value("CBT Company", AYALA, "no_show_release_minutes")),
			0,
			"this row's whole point is the knob being OFF",
		)

		# 10:30 — the 10:00-11:00 slot has STARTED and has not ENDED.
		with patch(CLOCK, return_value=START + timedelta(minutes=30)):
			staff = get_availability(MAKATI, WEDNESDAY)
			public = get_public_availability(MAKATI, WEDNESDAY)

		self.assertEqual(self._slot(staff, "10:00:00")["status"], "available")
		# The portal keeps the start-based rule: the controller refuses a
		# customer booking of a started slot, and a grid must never offer what
		# the server will not take.
		self.assertEqual(self._slot(public, "10:00:00")["status"], "past")
		# ...and the relaxation is END-based, not "never past": the hour BEFORE
		# is over and stays past for staff too. Without this half the row would
		# also pass if past_from_end had been mistaken for "staff see no past".
		self.assertEqual(self._slot(staff, "09:00:00")["status"], "past")

	def test_the_board_ships_the_release_countdown_only_while_it_is_running(self):
		"""Server-computed absolute timestamps (S7 doctrine), and only for the
		booking staff can still save — a countdown on a 20:00 booking seen at
		09:30 is noise, and noise is how the one that matters gets ignored."""
		from court_booking_tech.api.board import get_board_data

		self._set_knob(GRACE)
		booking = self._book()
		later = self._book(court=COURT_B, start_time="20:00:00")

		with patch(CLOCK, return_value=START + timedelta(minutes=5)):
			data = get_board_data(MAKATI, WEDNESDAY)

		running = self._slot(data, "10:00:00")
		self.assertEqual(running["booking"], booking.name)
		self.assertEqual(get_datetime(running["release_at"]), START + timedelta(minutes=GRACE))
		self.assertIsNone(
			self._slot(data, "20:00:00", court=COURT_B)["release_at"],
			f"{later.name} has not started — nothing to count down",
		)

	def test_a_released_booking_reaches_the_desk_through_the_no_shows_list(self):
		"""It holds no slot, so it appears NOWHERE on the grid. Without this
		list a no-show would vanish from the desk's world the instant it
		happened — and with it the only route back."""
		from court_booking_tech.api.board import get_board_data

		self._set_knob(GRACE)
		booking = self._book()
		at = START + timedelta(minutes=GRACE + 1)
		self._release(booking.name, at)

		with patch(CLOCK, return_value=at):
			data = get_board_data(MAKATI, WEDNESDAY)

		listed = {row["name"]: row for row in data["no_shows"]}
		self.assertIn(booking.name, listed)
		self.assertEqual(listed[booking.name]["court"], COURT_A)
		self.assertEqual(listed[booking.name]["start_time"], "10:00:00")
		self.assertEqual(data["no_show_release_minutes"], GRACE)

	def _slot(self, payload, start_time, court=COURT_A):
		court_row = next(c for c in payload["courts"] if c["court"] == court)
		return next(s for s in court_row["slots"] if s["start_time"] == start_time)
