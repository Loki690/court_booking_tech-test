"""
Court Booking Tech — True Reschedule (section-15, PLAN §8c)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_reschedule

One staff action moves a booking, preserving payment truth. The immutability
doctrine is UNCHANGED: reschedule creates a NEW booking through the FULL insert
pipeline and cancels the original, so the insert-only FOR-UPDATE overlap lock
stays sufficient (S4 as-built 3).

What is pinned here, and why each row exists:
- the payment carry (the stamps are the ORIGINAL's, not the rescheduling
  staff's — the money was verified once, at the desk, by whoever took it);
- the evidence carry (proofs follow the LIVE document, deadline re-capped);
- ATOMICITY: an occupied target leaves the original completely untouched. This
  is why the endpoint inserts first and cancels last — a backend test calling
  the API directly inside assertRaises gets no request-boundary rollback, so a
  cancel-first implementation would silently pass production and fail here;
- the OVERLAP LICENCE (section-15 deviation): the one booking being moved is
  excused from the double-booking guard, and nothing else is.

MONTH. November 2027 (month-per-module discipline, S11 as-built 20). Verified
free BOTH ways before claiming it (S13 as-built 1b): no `"month": 11` filter
anywhere in the test tree and no `2027-11` date string in the app. Months
claimed by INTEGER elsewhere are 4, 8 and 9 — a date-string grep cannot see
those. 2027-11-03/04/05 are Wed/Thu/Fri (derived from test_rate_rules' pinned
2027-10-18 = Monday).

COURTS. Everything runs on ALREADY-SEEDED courts — this module creates none.
The two rate rows reuse `E2EF-main-court-2` (section-14's fixture: base ₱200,
All Days 18:00-23:00 ₱350 "Night rate"). Its OTHER rule is Weekends 05:00-07:00,
so the 17:00 -> 18:00 move asserted below is weekday-independent and cannot
drift. E2EF-main runs 00:00-23:59 every day with 60-minute slots.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, get_datetime

from court_booking_tech.api.bookings import (
	cancel_booking,
	confirm_booking,
	create_booking,
	extend_booking,
	reschedule_booking,
)
from court_booking_tech.api.proofs import create_proof
from court_booking_tech.notifications import (
	BOOKING_CONFIRMED_TEMPLATE,
	RESCHEDULED_TEMPLATE,
)
from court_booking_tech.seeds.seed_test_data import (
	CUSTOMER_EMAIL,
	PLATFORM_ADMIN_EMAIL,
	_proof_sample_bytes,
	seed_all,
)

AYALA = "ayala-courts"
STELLA = "staff.ayala@example.com"

COURT_A = "AYALA-makati-court-a"  # makati: open 06:00-22:00 every day
COURT_B = "AYALA-makati-court-b"
BGC_COURT = "AYALA-bgc-court-1"  # same company, DIFFERENT branch
QCSM_COURT = "QCSM-timog-court-1"  # different company

RATE_COURT = "E2EF-main-court-2"  # section-14 seed: ₱200 base, ₱350 from 18:00
RATE_BASE = 200
NIGHT_RATE = 350

WEDNESDAY = "2027-11-03"
THURSDAY = "2027-11-04"

T0 = datetime(2027, 11, 3, 8, 0)
T1 = datetime(2027, 11, 3, 8, 20)  # 20 minutes later — the move happens here

CLOCK = "court_booking_tech.clock.now_dt"
SENDMAIL = "frappe.sendmail"
# Section-19: the app's OWN mail seam. Patching it (rather than sendmail) is what
# lets a row say WHICH template was queued and with what context — the two things
# B5 is actually about. sendmail stays the surface for "was anybody emailed at
# all", because that is the question the walk-in rows ask.
QUEUE = "court_booking_tech.notifications._queue"

# REAL image bytes: frappe's File controller EXIF-strips images through PIL on
# save, so fake magic-byte blobs are rejected at insert (S5 lesson).
JPG = _proof_sample_bytes()

# seed_all() is idempotent but not free and this module has six classes —
# seeding once per MODULE keeps it inside the section-12 suite budget. Safe
# because seed data is committed and survives the per-test rollbacks between.
_SEEDED = False


class RescheduleTestCase(FrappeTestCase):
	"""Shared helpers. Holds NO test methods — inheriting a TestCase that does
	would re-run every parent test in each subclass (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		global _SEEDED
		super().setUpClass()
		if not _SEEDED:
			seed_all()
			_SEEDED = True

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _book(
		self,
		court=COURT_A,
		start_time="10:00:00",
		payment_method="Cash",
		date=WEDNESDAY,
		slots=1,
		at=T0,
		**overrides,
	):
		"""Insert under a PATCHED clock — a real-clock insert would stamp
		reservation_expires_at in the present and every 2027 scenario would
		then see a dead hold."""
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
			for proof in frappe.get_all(
				"CBT Payment Proof", filters={"booking": name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Payment Proof", proof, force=True,
					ignore_permissions=True, ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Court Booking", name, force=True,
				ignore_permissions=True, ignore_missing=True,
			)

		self.addCleanup(_do)

	def _move(self, booking, at=T1, **kwargs):
		with patch(CLOCK, return_value=at):
			result = reschedule_booking(booking, **kwargs)
		self._cleanup(result["name"])
		return result

	def _reload(self, name):
		return frappe.get_doc("CBT Court Booking", name)

	def _invoice(self, booking_name):
		doc = self._reload(booking_name)
		self.assertTrue(doc.billing_doc, f"{booking_name} has no billing document")
		return frappe.get_doc("CBT Booking Invoice", doc.billing_doc)

	def _end_dt(self, doc):
		return datetime.combine(
			frappe.utils.getdate(doc.booking_date), datetime.min.time()
		) + frappe.utils.get_timedelta(str(doc.end_time))


class TestRescheduleUnpaidHold(RescheduleTestCase):
	"""A Reserved hold that has not been paid for yet."""

	def test_unpaid_move_links_both_ways_and_restarts_the_base_clock(self):
		original = self._book(payment_method="Fund Transfer")
		self.assertEqual(original.booking_status, "Reserved")
		self.assertEqual(original.reservation_expires_at, T0 + timedelta(minutes=30))
		old_invoice = self._invoice(original.name).name

		result = self._move(original.name, start_time="14:00:00")
		new = self._reload(result["name"])
		original.reload()

		# The replacement is a fresh hold — the customer gets the FULL window
		# again, deliberately (a move is not a punishment).
		self.assertEqual(new.booking_status, "Reserved")
		self.assertEqual(new.reservation_expires_at, T1 + timedelta(minutes=30))
		self.assertEqual(new.start_time, timedelta(hours=14))

		# Both links, both directions.
		self.assertEqual(new.rescheduled_from, original.name)
		self.assertEqual(original.rescheduled_to, new.name)
		self.assertEqual(result["rescheduled_from"], original.name)

		# The original leaves the board; its document is retained with its
		# number consumed (§8e), and the replacement minted the NEXT one.
		self.assertEqual(original.booking_status, "Cancelled")
		self.assertEqual(frappe.get_doc("CBT Booking Invoice", old_invoice).status, "Cancelled")
		new_invoice = self._invoice(new.name)
		self.assertEqual(new_invoice.status, "Unpaid")
		self.assertNotEqual(new_invoice.name, old_invoice)
		self.assertGreater(new_invoice.name, old_invoice)

	def test_omitted_parameters_default_to_the_original(self):
		"""'Same everything, one hour longer' must be expressible as ONE
		argument — staff should not have to restate the court and the date."""
		original = self._book(start_time="16:00:00", payment_method="Fund Transfer")
		result = self._move(original.name, number_of_slots=2)
		new = self._reload(result["name"])

		self.assertEqual(new.court, original.court)
		# getdate() on BOTH sides: the replacement went through the API, which
		# normalises to a date object, while `original` still holds the string
		# the fixture inserted. Same class of trap as S14 as-built 12b (Time
		# fields), and it fails on the types rather than on the value.
		self.assertEqual(
			frappe.utils.getdate(new.booking_date),
			frappe.utils.getdate(original.booking_date),
		)
		self.assertEqual(new.start_time, timedelta(hours=16))
		self.assertEqual(new.number_of_slots, 2)

	def test_a_move_can_cross_branches_within_the_company(self):
		original = self._book(start_time="11:00:00", payment_method="Fund Transfer")
		result = self._move(original.name, court=BGC_COURT)
		new = self._reload(result["name"])

		self.assertEqual(new.court, BGC_COURT)
		self.assertEqual(new.branch, "AYALA-bgc")
		self.assertEqual(new.company, AYALA)  # company is what may NOT change


class TestReschedulePaymentCarry(RescheduleTestCase):
	"""The point of the whole feature: money already verified stays verified,
	and it stays verified BY THE PERSON WHO VERIFIED IT."""

	def test_paid_cash_move_carries_the_original_verification_stamps(self):
		# Booked and paid at the desk by Stella...
		frappe.set_user(STELLA)
		original = self._book(start_time="12:00:00", payment_method="Cash")
		self.assertEqual(original.booking_status, "Confirmed")
		self.assertEqual(original.confirmed_by, STELLA)
		self.assertEqual(original.confirmed_at, T0)

		# ...and moved later by somebody else entirely.
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		result = self._move(original.name, start_time="13:00:00")
		new = self._reload(result["name"])

		self.assertEqual(new.booking_status, "Confirmed")
		self.assertEqual(result["booking_status"], "Confirmed")
		# NOT the platform seat that moved it, and NOT T1: the cash was taken once, by Stella.
		self.assertEqual(new.confirmed_by, STELLA)
		self.assertEqual(new.confirmed_at, T0)

		invoice = self._invoice(new.name)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, STELLA)
		self.assertEqual(get_datetime(invoice.verified_at), T0)

	def test_paid_fund_transfer_move_confirms_in_one_call(self):
		"""A Fund Transfer replacement is BORN Reserved, so the carry is a real
		Reserved->Confirmed transition — it must still happen inside the single
		API call, with the original's stamps."""
		frappe.set_user(STELLA)
		original = self._book(start_time="15:00:00", payment_method="Fund Transfer")
		with patch(CLOCK, return_value=T0):
			confirm_booking(original.name)
		original.reload()
		self.assertEqual(original.confirmed_by, STELLA)

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		result = self._move(original.name, start_time="16:00:00")
		new = self._reload(result["name"])

		self.assertEqual(new.booking_status, "Confirmed")
		self.assertEqual(new.confirmed_by, STELLA)
		self.assertEqual(new.confirmed_at, T0)
		self.assertEqual(self._invoice(new.name).status, "Paid & Verified")

	def test_the_carry_mails_the_move_and_not_a_second_confirmation(self):
		"""SUPERSEDED BY SECTION-19 (Backlog B5), rewritten in place.

		It used to assert that the Reserved->Confirmed carry rides the EXISTING
		confirmed-mail seam. It does not any more, and that is the point of B5: a
		payment verified days ago produced a fresh "your payment has been
		verified" email whenever the SLOT moved, which told the customer the one
		thing that had not changed and nothing about the one that had.

		Rewritten rather than deleted-and-replaced-elsewhere because under the new
		code the old assertions would still PASS — sendmail is called, and the
		rescheduled body also contains the new booking's name. A green test whose
		docstring has become false is the worst outcome available, and it would
		have been green at exactly the line a future reader looks.
		"""
		original = self._book(start_time="17:00:00", payment_method="Fund Transfer")
		with patch(CLOCK, return_value=T0):
			confirm_booking(original.name)

		with patch(QUEUE) as queue:
			result = self._move(original.name, start_time="18:00:00")

		templates = [call.args[0] for call in queue.call_args_list]
		self.assertEqual(
			templates,
			[RESCHEDULED_TEMPLATE],
			"the carry must queue the MOVE mail exactly once and no confirmation",
		)
		# ...and it describes the replacement, not the booking left behind.
		self.assertEqual(queue.call_args.args[1].name, result["name"])


class TestRescheduleEvidence(RescheduleTestCase):
	"""An unpaid hold that already carries payment proofs."""

	def _upload(self, booking, at=T0, **kwargs):
		with patch(CLOCK, return_value=at):
			return create_proof(booking, "proof.jpg", JPG, **kwargs)

	def test_proofs_follow_the_live_booking_with_a_recapped_deadline(self):
		# 20:00-21:00 so the walker's deadline lands well BEFORE the booking
		# end and is therefore uncapped on the original.
		original = self._book(start_time="20:00:00", payment_method="Fund Transfer")
		self._upload(original.name)
		self._upload(original.name)
		original.reload()
		old_deadline = get_datetime(original.verification_deadline_at)

		proofs = frappe.get_all(
			"CBT Payment Proof", filters={"booking": original.name}, pluck="name"
		)
		self.assertEqual(len(proofs), 2)
		# Cover EVERY status, not just Pending — the evidence trail moves whole.
		frappe.db.set_value("CBT Payment Proof", proofs[1], "status", "Rejected")
		frappe.db.set_value("CBT Court Booking", original.name, "rejection_count", 1)
		original.reload()

		# Move to a slot that ENDS earlier than the deadline the proof bought.
		result = self._move(original.name, start_time="10:00:00")
		new = self._reload(result["name"])

		moved = frappe.get_all(
			"CBT Payment Proof", filters={"booking": new.name}, pluck="name"
		)
		self.assertEqual(sorted(moved), sorted(proofs), "all proofs must follow")
		self.assertEqual(
			frappe.get_all(
				"CBT Payment Proof", filters={"booking": original.name}, pluck="name"
			),
			[],
			"no evidence may be left on the cancelled half",
		)

		# The walker's hard cap re-applied at the NEW end (S5): a hold can
		# never outlive the slot it holds.
		new_end = self._end_dt(new)
		self.assertLess(new_end, old_deadline, "fixture must move to an EARLIER slot")
		self.assertEqual(get_datetime(new.verification_deadline_at), new_end)

		# The one burned restart is carried — a move must not buy a fresh one.
		self.assertEqual(new.rejection_count, 1)

	def test_a_later_deadline_than_the_new_end_is_kept_unchanged(self):
		"""The cap is a min(), not an overwrite: moving to a slot that ends
		AFTER the deadline keeps the deadline the proof actually bought."""
		original = self._book(start_time="10:00:00", payment_method="Fund Transfer")
		self._upload(original.name)
		original.reload()
		old_deadline = get_datetime(original.verification_deadline_at)

		result = self._move(original.name, start_time="20:00:00")
		new = self._reload(result["name"])

		self.assertGreater(self._end_dt(new), old_deadline)
		self.assertEqual(get_datetime(new.verification_deadline_at), old_deadline)

	def test_the_holds_cap_count_is_unchanged_by_a_move(self):
		"""The per-customer cap counts DISTINCT Reserved bookings joined to
		Pending proofs. One before, one after — a move must not consume a slot
		of somebody's allowance."""
		original = self._book(start_time="09:00:00", payment_method="Fund Transfer")
		self._upload(original.name)

		def _held():
			rows = frappe.db.sql(
				"""
				SELECT DISTINCT b.name
				FROM `tabCBT Court Booking` b
				JOIN `tabCBT Payment Proof` p
				  ON p.booking = b.name AND p.status = 'Pending'
				WHERE b.booking_status = 'Reserved' AND b.customer = %s
				""",
				(CUSTOMER_EMAIL,),
			)
			return len(rows)

		before = _held()
		self._move(original.name, start_time="11:00:00")
		self.assertEqual(_held(), before)


class TestRescheduleRejects(RescheduleTestCase):
	"""Everything the endpoint refuses, each with its own message."""

	def test_an_extended_booking_and_its_child_are_both_refused(self):
		original = self._book(start_time="10:00:00")
		with patch(CLOCK, return_value=T0):
			child = extend_booking(original.name, slots=1, payment_method="Cash")
		self._cleanup(child)
		original.reload()
		self.assertEqual(original.booking_status, "Extended")

		# The parent: its extension would be left behind.
		with self.assertRaises(frappe.ValidationError) as parent_err:
			self._move(original.name, start_time="14:00:00")
		self.assertIn("extension", str(parent_err.exception))

		# The child: moving it would detach it from what it extends.
		with self.assertRaises(frappe.ValidationError) as child_err:
			self._move(child, start_time="15:00:00")
		self.assertIn("extends", str(child_err.exception))

	def test_terminal_statuses_are_refused(self):
		for status in ("Completed", "Cancelled", "Expired"):
			with self.subTest(status=status):
				doc = self._book(start_time="12:00:00", payment_method="Fund Transfer")
				frappe.db.set_value(
					"CBT Court Booking", doc.name, "booking_status", status
				)
				with self.assertRaises(frappe.ValidationError):
					self._move(doc.name, start_time="13:00:00")

	def test_a_court_of_another_company_is_refused(self):
		original = self._book(start_time="10:00:00")
		with self.assertRaises(Exception) as err:
			self._move(original.name, court=QCSM_COURT)
		# Platform scope reaches the same-company guard; a tenant user fails
		# closed one step earlier in require_company_access. Either way the
		# booking never leaves its company.
		self.assertIn("company", str(err.exception).lower())
		original.reload()
		self.assertEqual(original.booking_status, "Confirmed")

	def test_an_identical_target_is_refused(self):
		original = self._book(start_time="10:00:00", slots=2)
		with self.assertRaises(frappe.ValidationError) as err:
			self._move(
				original.name,
				court=COURT_A,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				number_of_slots=2,
			)
		self.assertIn("already", str(err.exception).lower())

		# ...and a no-argument call is the same no-op.
		with self.assertRaises(frappe.ValidationError):
			self._move(original.name)

	def test_a_suspended_company_cannot_reschedule(self):
		"""Occupying a new slot is booking-creating, so the suspension gate is
		ON (PLAN §5) — unlike cancel, which de-escalates."""
		original = self._book(start_time="10:00:00")
		frappe.db.set_value("CBT Company", AYALA, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "status", "Active"
		)
		with self.assertRaises(frappe.ValidationError):
			self._move(original.name, start_time="14:00:00")


class TestRescheduleOverlap(RescheduleTestCase):
	"""The double-booking guard, and the ONE narrow licence section-15 grants."""

	def test_an_occupied_target_fails_clean_and_leaves_the_original_untouched(self):
		"""THE atomicity pin. The licence is one NAMED row — a DIFFERENT
		booking on the target still collides, and because the endpoint inserts
		first and cancels last, the failure costs the original nothing."""
		original = self._book(start_time="10:00:00", payment_method="Fund Transfer")
		blocker = self._book(start_time="14:00:00")
		before = frappe.db.get_value(
			"CBT Court Booking",
			original.name,
			[
				"booking_status",
				"booking_date",
				"start_time",
				"reservation_expires_at",
				"billing_doc",
				"rescheduled_to",
			],
			as_dict=True,
		)
		invoice_before = self._invoice(original.name).status

		with self.assertRaises(frappe.ValidationError) as err:
			self._move(original.name, start_time="14:00:00")
		self.assertIn("Slot already taken", str(err.exception))

		after = frappe.db.get_value(
			"CBT Court Booking",
			original.name,
			[
				"booking_status",
				"booking_date",
				"start_time",
				"reservation_expires_at",
				"billing_doc",
				"rescheduled_to",
			],
			as_dict=True,
		)
		self.assertEqual(after, before, "a failed move must not touch the original")
		self.assertEqual(self._invoice(original.name).status, invoice_before)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", blocker.name, "booking_status"),
			"Confirmed",
			"the booking that won the slot is untouched too",
		)

	def test_a_blocked_window_is_still_refused(self):
		"""The licence excuses ONE booking row; it does not touch the slot-block
		loop, so a move onto a maintenance window is refused as ever."""
		original = self._book(start_time="10:00:00")
		block = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "AYALA-makati",
				"company": AYALA,
				"court": COURT_A,
				"block_date": WEDNESDAY,
				"start_time": "15:00:00",
				"end_time": "16:00:00",
				"reason": "Maintenance",
			}
		)
		block.insert()
		self.addCleanup(
			frappe.delete_doc, "CBT Slot Block", block.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)

		with self.assertRaises(frappe.ValidationError) as err:
			self._move(original.name, start_time="15:00:00")
		self.assertIn("blocked", str(err.exception).lower())

	def test_the_same_slot_can_be_made_longer(self):
		"""Section-15 deviation, and the reason for it: the original still
		holds its own slot until the cancel, so without the licence the two
		commonest desk moves would be impossible."""
		original = self._book(start_time="10:00:00", slots=1)
		result = self._move(original.name, number_of_slots=2)
		new = self._reload(result["name"])
		original.reload()

		self.assertEqual(new.start_time, timedelta(hours=10))
		self.assertEqual(new.number_of_slots, 2)
		self.assertEqual(new.end_time, timedelta(hours=12))
		self.assertEqual(original.booking_status, "Cancelled")

	def test_a_booking_can_be_shifted_within_its_own_window(self):
		original = self._book(start_time="10:00:00", slots=2)  # 10:00-12:00
		result = self._move(original.name, start_time="11:00:00")  # 11:00-13:00
		new = self._reload(result["name"])
		original.reload()

		self.assertEqual(new.start_time, timedelta(hours=11))
		self.assertEqual(original.booking_status, "Cancelled")

	def test_the_replacement_really_occupies_the_new_slot(self):
		"""The licence must not leave a hole: once the move lands, the target
		is taken for everybody else."""
		original = self._book(start_time="10:00:00")
		result = self._move(original.name, start_time="14:00:00")
		self.assertEqual(result["booking_status"], "Confirmed")

		with self.assertRaises(frappe.ValidationError) as err:
			self._book(start_time="14:00:00")
		self.assertIn("Slot already taken", str(err.exception))

		# ...and the slot it LEFT is free again.
		freed = self._book(start_time="10:00:00")
		self.assertEqual(freed.booking_status, "Confirmed")


class TestRescheduleRates(RescheduleTestCase):
	"""Section-14's seam, reached through a move (S14 hand-off)."""

	def test_moving_into_the_night_window_reprices_through_the_seam(self):
		original = self._book(court=RATE_COURT, start_time="17:00:00")
		self.assertEqual(len(original.rate_segments), 1)
		self.assertEqual(flt(original.total_amount), float(RATE_BASE))

		result = self._move(original.name, start_time="18:00:00")
		new = self._reload(result["name"])

		self.assertEqual(len(new.rate_segments), 1)
		self.assertEqual(flt(new.rate_segments[0].hourly_rate), float(NIGHT_RATE))
		self.assertEqual(new.rate_segments[0].label, "Night rate")
		self.assertEqual(flt(new.total_amount), float(NIGHT_RATE))
		# The statement re-derives from the booking, so it moves with it.
		self.assertEqual(flt(self._invoice(new.name).total_amount), float(NIGHT_RATE))

	def test_a_move_that_spans_the_boundary_prices_per_slot(self):
		original = self._book(court=RATE_COURT, start_time="10:00:00")
		result = self._move(original.name, start_time="17:00:00", number_of_slots=2)
		new = self._reload(result["name"])

		self.assertEqual(len(new.rate_segments), 2)
		self.assertEqual(flt(new.rate_segments[0].hourly_rate), float(RATE_BASE))
		self.assertEqual(flt(new.rate_segments[1].hourly_rate), float(NIGHT_RATE))
		self.assertEqual(flt(new.total_amount), float(RATE_BASE + NIGHT_RATE))
		# The blend is DISPLAY ONLY and must never be what was charged.
		self.assertEqual(flt(new.hourly_rate), 275.0)

	def test_a_flat_override_is_inherited_not_repriced(self):
		"""S14 as-built 4, through the move: a rate the desk agreed with the
		customer survives, and the night rule does NOT overwrite it."""
		with patch(CLOCK, return_value=T0):
			created = create_booking(
				court=RATE_COURT,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				hourly_rate=999,
			)
		self._cleanup(created["name"])
		original = self._reload(created["name"])
		self.assertEqual(len(original.rate_segments), 0)

		result = self._move(original.name, start_time="18:00:00")
		new = self._reload(result["name"])

		self.assertEqual(len(new.rate_segments), 0, "an override must stay flat")
		self.assertEqual(flt(new.hourly_rate), 999.0)
		self.assertEqual(flt(new.total_amount), 999.0)

	def test_a_rule_less_court_keeps_its_rate(self):
		"""The inherit branch is `not rate_segments`, which also covers a court
		with no rules at all — byte-identical to extend_booking, and the reason
		a moved booking never silently re-prices off a court whose base rate
		changed after it was sold."""
		original = self._book(start_time="10:00:00")
		rate_before = flt(original.hourly_rate)
		total_before = flt(original.total_amount)

		result = self._move(original.name, start_time="14:00:00")
		new = self._reload(result["name"])

		self.assertEqual(len(new.rate_segments), 0)
		self.assertEqual(flt(new.hourly_rate), rate_before)
		self.assertEqual(flt(new.total_amount), total_before)

	def test_the_discount_travels_with_the_booking(self):
		original = self._book(start_time="10:00:00", discount_percent=50)
		result = self._move(original.name, start_time="14:00:00")
		new = self._reload(result["name"])

		self.assertEqual(flt(new.discount_percent), 50.0)
		self.assertEqual(flt(new.total_amount), flt(original.total_amount))

	def test_the_quote_the_dialog_asks_for_is_what_the_move_charges(self):
		"""Backlog B15 (2026-08-27). The reschedule dialog now sends the
		original's flat `hourly_rate` and `discount_percent` to get_quote instead
		of multiplying them in JS — this is the parity that lets it say "Total".

		The discriminating case: a FLAT override moved INTO the night window. The
		court's rules say ₱350 there; the booking inherits ₱999 and keeps its 50%
		(`test_a_flat_override_is_inherited_not_repriced` +
		`test_the_discount_travels_with_the_booking`, combined). The quote the
		dialog asks for must land on the same centavo as the booking AND its
		statement — and the quote it used to be limited to must not.
		"""
		from court_booking_tech.api.portal import get_quote

		with patch(CLOCK, return_value=T0):
			created = create_booking(
				court=RATE_COURT,
				booking_date=WEDNESDAY,
				start_time="10:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				hourly_rate=999,
				discount_percent=50,
			)
		self._cleanup(created["name"])
		original = self._reload(created["name"])
		self.assertEqual(len(original.rate_segments), 0)

		# Exactly the args cbt_reschedule.js sends for a booking with no segments.
		quote = get_quote(
			RATE_COURT,
			1,
			booking_date=WEDNESDAY,
			start_time="18:00:00",
			discount_percent=original.discount_percent,
			hourly_rate=original.hourly_rate,
		)
		# ...and the one it was limited to before B15 — the court's own price.
		repriced = get_quote(RATE_COURT, 1, booking_date=WEDNESDAY, start_time="18:00:00")

		result = self._move(original.name, start_time="18:00:00")
		new = self._reload(result["name"])

		self.assertEqual(flt(new.total_amount), 499.5)
		self.assertEqual(flt(quote["total_amount"]), flt(new.total_amount))
		self.assertEqual(flt(quote["hourly_rate"]), flt(new.hourly_rate))
		self.assertEqual(quote["segments"], [])
		self.assertEqual(flt(self._invoice(new.name).total_amount), flt(quote["total_amount"]))
		self.assertEqual(flt(repriced["total_amount"]), float(NIGHT_RATE))
		self.assertNotEqual(flt(repriced["total_amount"]), flt(new.total_amount))


class TestRescheduleWalkIn(RescheduleTestCase):
	"""Section-13 identity, carried through the move."""

	def _walk_in(self, start_time, payment_method="Cash"):
		with patch(CLOCK, return_value=T0):
			created = create_booking(
				court=COURT_B,
				booking_date=THURSDAY,
				start_time=start_time,
				payment_method=payment_method,
				customer_name="Walk-in Wanda S15",
				customer_phone="0917-000-3333",
			)
		self._cleanup(created["name"])
		return self._reload(created["name"])

	def test_walk_in_identity_is_carried(self):
		original = self._walk_in("10:00:00")
		self.assertIsNone(original.customer)

		result = self._move(original.name, start_time="14:00:00")
		new = self._reload(result["name"])

		# Without the explicit copy this insert dies on the either-or identity
		# validation: fetch_from fills customer_name only FROM A USER.
		self.assertIsNone(new.customer)
		self.assertEqual(new.customer_name, "Walk-in Wanda S15")
		self.assertEqual(new.customer_phone, "0917-000-3333")
		self.assertEqual(new.booking_status, "Confirmed")
		# The receipt keeps naming the person who paid (the point of B1).
		self.assertEqual(self._invoice(new.name).customer_name, "Walk-in Wanda S15")

	def test_moving_a_walk_in_never_tries_to_email_anybody(self):
		"""A walk-in has no account and therefore no inbox. Asserting 'no Email
		Queue row' would PASS ON UNCHANGED CODE (frappe filters falsy
		recipients before resolving a sender, S13 as-built 6) — so patch
		sendmail and assert it is never called, with an ACCOUNT booking as the
		control that proves the path is live."""
		walk_in = self._walk_in("11:00:00", payment_method="Fund Transfer")
		with patch(CLOCK, return_value=T0):
			confirm_booking(walk_in.name)

		with patch(SENDMAIL) as sendmail:
			result = self._move(walk_in.name, start_time="15:00:00")
		self.assertFalse(
			sendmail.called, "a walk-in move must not attempt to email anyone"
		)
		# Section-19: silence is a SKIP, not a swallowed failure. The move itself
		# has to have landed — otherwise this row would also pass if the new
		# rescheduled mail were throwing and taking the whole move down with it.
		walk_in.reload()
		self.assertEqual(walk_in.booking_status, "Cancelled")
		self.assertEqual(walk_in.rescheduled_to, result["name"])
		self.assertEqual(self._reload(result["name"]).booking_status, "Confirmed")

		# Control: the same path with an account DOES mail.
		account = self._book(
			court=COURT_B, date=THURSDAY, start_time="16:00:00",
			payment_method="Fund Transfer",
		)
		with patch(CLOCK, return_value=T0):
			confirm_booking(account.name)
		with patch(SENDMAIL) as sendmail:
			self._move(account.name, start_time="17:00:00")
		self.assertTrue(sendmail.called, "control: an account move DOES notify")


class TestRescheduleNotification(RescheduleTestCase):
	"""Section-19 (Backlog B5) — a moved booking TELLS its customer.

	COURT_B on WEDNESDAY, which no other class in this module touches (COURT_B
	appears only on THURSDAY here, and every SEEDED makati booking is on
	BOOKING_DATE = 2027-01-15), and every slot at or after 09:00. Both halves of
	that matter: the module's clock is T0 = 08:00 / T1 = 08:20, so a fixture on an
	EARLIER slot would be a booking that has already started — which
	`_auto_check_in` silently marks checked-in, and whose restarted base clock
	(08:50) would sit AFTER a slot that already ended. A mail about paying for a
	slot in the past is not a fixture, it is nonsense.
	"""

	def _queued_templates(self, queue):
		return [call.args[0] for call in queue.call_args_list]

	def _context(self, queue):
		"""The context dict of the LAST _queue call (positional arg 3)."""
		return queue.call_args.args[2]

	def test_a_paid_cash_move_mails_the_move_once(self):
		"""The case that used to be COMPLETELY silent: a Cash booking is born
		Confirmed, so `on_booking_update` returned on flags.in_insert and nothing
		was ever sent. The customer's paid booking moved and their only evidence
		was a Cancelled row in their own history."""
		original = self._book(court=COURT_B, start_time="10:00:00", payment_method="Cash")
		self.assertEqual(original.booking_status, "Confirmed")

		with patch(QUEUE) as queue:
			result = self._move(original.name, start_time="14:00:00")

		self.assertEqual(self._queued_templates(queue), [RESCHEDULED_TEMPLATE])
		context = self._context(queue)
		self.assertEqual(context["old_booking"], original.name)
		self.assertEqual(context["booking"], result["name"])
		# The two slots are BOTH named, and rendered by one formatter so they can
		# never disagree about shape.
		self.assertIn("10:00", context["old_when"])
		self.assertIn("14:00", context["when"])
		# Nothing is owed: a paid move must not ask for money again.
		self.assertEqual(context["pay_by"], "")
		self.assertEqual(context["verify_by"], "")

	def test_a_cross_branch_move_names_the_old_court_and_branch(self):
		"""The branch line exists for exactly one reason: a customer who now has
		to drive somewhere else must be told, and a court name alone does not say
		that. It is emitted ONLY when the branch really changed."""
		same_branch = self._book(court=COURT_B, start_time="11:00:00")
		with patch(QUEUE) as queue:
			self._move(same_branch.name, start_time="15:00:00")
		context = self._context(queue)
		self.assertEqual(context["old_court_name"], "Court B")
		self.assertEqual(
			context["old_branch_name"], "", "a same-branch move must not shout about a branch"
		)

		crossing = self._book(court=COURT_B, start_time="12:00:00")
		with patch(QUEUE) as queue:
			self._move(crossing.name, court=BGC_COURT, start_time="16:00:00")
		context = self._context(queue)
		self.assertTrue(context["old_branch_name"], "a cross-branch move must name it")
		self.assertNotEqual(context["old_branch_name"], context["branch_name"])

	def test_an_unpaid_move_awaiting_verification_promises_a_decision(self):
		"""The wording branch. This customer has ALREADY paid and uploaded a
		proof; the live clock is the staff's verify-by deadline, re-capped at the
		new booking's end. Telling them to go and pay would be the same class of
		misleading mail this section exists to delete."""
		original = self._book(court=COURT_B, start_time="13:00:00", payment_method="Fund Transfer")
		with patch(CLOCK, return_value=T0):
			create_proof(original.name, "p.jpg", JPG)
		original.reload()
		self.assertTrue(original.verification_deadline_at)

		with patch(QUEUE) as queue:
			result = self._move(original.name, start_time="17:00:00")
		new = self._reload(result["name"])
		context = self._context(queue)

		self.assertEqual(new.booking_status, "Reserved")
		self.assertEqual(context["booking_status"], "Reserved")
		# The carried, re-capped deadline — not the original's raw value.
		self.assertEqual(
			context["verify_by"], frappe.utils.format_datetime(new.verification_deadline_at)
		)
		self.assertEqual(
			context["pay_by"], "", "a paid-and-waiting customer must not be asked to pay"
		)

	def test_an_unpaid_move_with_no_proof_carries_the_restarted_pay_clock(self):
		"""A hold with nothing paid yet moves with a FRESH window (S15's
		deliberate kindness). The mail has to carry that deadline: a message that
		announces a move and omits the clock invites the missed payment it exists
		to prevent."""
		original = self._book(court=COURT_B, start_time="18:00:00", payment_method="Fund Transfer")
		with patch(QUEUE) as queue:
			result = self._move(original.name, start_time="19:00:00")
		new = self._reload(result["name"])
		context = self._context(queue)

		self.assertEqual(new.reservation_expires_at, T1 + timedelta(minutes=30))
		self.assertEqual(
			context["pay_by"], frappe.utils.format_datetime(new.reservation_expires_at)
		)
		self.assertEqual(context["verify_by"], "")

	def test_the_shipped_template_really_renders_both_slots_and_both_clocks(self):
		"""The ONE row that lets `_queue` run for real.

		Every other row here patches `_queue` so it can name the template and read
		the context — which means none of them ever renders the fixture. A typo in
		the shipped Jinja would be swallowed by notify's own except and show up
		only as an Error Log row, i.e. as silence, which is the exact failure mode
		B5 exists to remove. So: patch `frappe.sendmail` instead (nothing leaves),
		let the real Email Template render, and read what came out.

		Both money branches are exercised, because they are separate Jinja paths.
		"""
		paid = self._book(court=COURT_B, start_time="10:00:00", payment_method="Cash")
		with patch(SENDMAIL) as sendmail:
			self._move(paid.name, start_time="11:00:00")

		self.assertTrue(sendmail.called, "the shipped template must have rendered")
		subject = sendmail.call_args.kwargs["subject"]
		body = sendmail.call_args.kwargs["message"]
		self.assertIn("moved", subject.lower())
		# Both slots, so the customer can see what changed rather than being told
		# only where they now stand.
		self.assertIn("10:00", body)
		self.assertIn("11:00", body)
		self.assertIn(paid.name, body)
		# The paid branch must not ask for money.
		self.assertNotIn("not paid for yet", body)

		unpaid = self._book(
			court=COURT_B, start_time="12:00:00", payment_method="Fund Transfer"
		)
		with patch(SENDMAIL) as sendmail:
			self._move(unpaid.name, start_time="13:00:00")
		body = sendmail.call_args.kwargs["message"]
		self.assertIn("not paid for yet", body)
		self.assertIn(
			frappe.utils.format_datetime(T1 + timedelta(minutes=30)),
			body,
			"the mail must carry the deadline it is asking the customer to meet",
		)

	def test_a_broken_template_never_breaks_the_move(self):
		"""The S9 failure model, on the newest mail. Staff moving a booking for a
		customer standing at the desk must not be stopped by the mail seam.

		Patches `_queue`, not `notify_booking_rescheduled`: the swallow lives
		INSIDE the notify function, so patching the notify function itself would
		only exercise a redundant second wrapper the endpoint deliberately does
		not have. This is the shape test_notifications already uses.
		"""
		original = self._book(court=COURT_B, start_time="20:00:00", payment_method="Cash")
		errors_before = frappe.db.count("Error Log")

		with patch(QUEUE, side_effect=RuntimeError("smtp down")):
			result = self._move(original.name, start_time="21:00:00")

		original.reload()
		new = self._reload(result["name"])
		self.assertEqual(original.booking_status, "Cancelled")
		self.assertEqual(original.rescheduled_to, new.name)
		self.assertEqual(new.booking_status, "Confirmed")
		# Swallowed, but NOT silent — a broken template has to be findable.
		self.assertGreater(frappe.db.count("Error Log"), errors_before)

	def test_a_later_genuine_confirm_of_a_moved_hold_still_mails_confirmed(self):
		"""The scoping pin for `flags.suppress_confirm_mail`. The flag exists only
		to stop the carry's duplicate; it must not follow the booking around. It
		cannot, because flags die with the request — this row is what would fail
		if somebody ever persisted it as a field."""
		original = self._book(court=COURT_B, start_time="09:00:00", payment_method="Fund Transfer")
		result = self._move(original.name, start_time="10:00:00")
		new = self._reload(result["name"])
		self.assertEqual(new.booking_status, "Reserved")

		with patch(QUEUE) as queue, patch(CLOCK, return_value=T1):
			confirm_booking(new.name)

		self.assertEqual(self._queued_templates(queue), [BOOKING_CONFIRMED_TEMPLATE])


class TestRescheduleCancelledOriginalStays(RescheduleTestCase):
	"""The cancelled half is retained, not deleted (§8e) — the audit trail of
	a move is the pair, not a rewritten single document."""

	def test_the_original_is_retained_with_its_own_document(self):
		original = self._book(start_time="10:00:00")
		invoice_before = self._invoice(original.name).name

		result = self._move(original.name, start_time="14:00:00")
		original.reload()

		self.assertEqual(original.booking_status, "Cancelled")
		self.assertEqual(original.billing_doc, invoice_before)
		# Scheduling fields NEVER move — that is what makes the insert-only
		# overlap lock sufficient (S4 as-built 3).
		self.assertEqual(original.start_time, timedelta(hours=10))
		self.assertEqual(original.court, COURT_A)
		# Verification stamps are kept on the cancelled invoice: audit trail.
		cancelled = frappe.get_doc("CBT Booking Invoice", invoice_before)
		self.assertEqual(cancelled.status, "Cancelled")
		self.assertEqual(cancelled.verified_by, "Administrator")
		self.assertEqual(original.rescheduled_to, result["name"])

	def test_a_moved_booking_can_be_moved_again(self):
		"""Chains are legal — a booking moved twice keeps a walkable trail."""
		first = self._book(start_time="10:00:00")
		second = self._move(first.name, start_time="14:00:00")
		third = self._move(second["name"], start_time="16:00:00")

		middle = self._reload(second["name"])
		last = self._reload(third["name"])
		self.assertEqual(middle.rescheduled_from, first.name)
		self.assertEqual(middle.rescheduled_to, last.name)
		self.assertEqual(last.rescheduled_from, middle.name)
		self.assertEqual(middle.booking_status, "Cancelled")
		self.assertEqual(last.booking_status, "Confirmed")
