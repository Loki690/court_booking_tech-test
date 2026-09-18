"""
Court Booking Tech — Slot Engine (section-4)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_slots

Slot-grid math (duration/buffer/closed-day/closing edges), availability
classification against the seeded booking/block cast, the guest-safe public
variant, the section-18 staff/portal past-line split (Backlog B7), the clock-seam
hygiene rule, and the section-17 test-clock lever that E2E uses in place of
monkeypatching. Every "now" here is either monkeypatched via
court_booking_tech.clock or moved through that lever — never wall-clock.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
import frappe.utils
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, get_datetime

from court_booking_tech import clock
from court_booking_tech.seeds.seed_test_data import seed_all
from court_booking_tech.slots import (
	get_availability,
	get_public_availability,
	get_slot_grid,
)
from court_booking_tech.tasks import expire_reservations

AYALA = "ayala-courts"
QCSM = "qc-smash"

BOOKING_DATE = "2027-01-15"  # Friday — the seeded booking cast lives here
BRANCH_BLOCK_DATE = "2027-01-16"  # Saturday — seeded whole-branch closure
SUNDAY = "2027-01-17"

# Far enough before the seeded cast that no slot reads as "past".
LONG_BEFORE = datetime(2026, 12, 1, 12, 0)

CLOCK = "court_booking_tech.clock.now_dt"


def _slot(court_rows, court, start):
	row = next(r for r in court_rows if r["court"] == court)
	return next(s for s in row["slots"] if s["start_time"] == start)


class TestSlots(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- grid math -------------------------------------------------------

	def test_grid_default_60_min(self):
		branch = frappe.get_doc("CBT Branch", "AYALA-bgc")
		grid = get_slot_grid(branch, BOOKING_DATE)
		# 06:00–22:00, 60-min slots, no buffer -> 16 slots.
		self.assertEqual(len(grid), 16)
		self.assertEqual(grid[0]["start_time"], timedelta(hours=6))
		self.assertEqual(grid[0]["end_time"], timedelta(hours=7))
		# Last slot ENDS exactly at closing.
		self.assertEqual(grid[-1]["start_time"], timedelta(hours=21))
		self.assertEqual(grid[-1]["end_time"], timedelta(hours=22))

	def test_grid_30_min_override_with_buffer(self):
		branch = frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": AYALA,
				"slug": "ts-grid",
				"branch_name": "Grid Probe",
				"slot_duration_minutes": 30,
				"buffer_minutes": 15,
			}
		)
		branch.insert()
		self.addCleanup(
			frappe.delete_doc,
			"CBT Branch",
			branch.name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
		grid = get_slot_grid(branch, BOOKING_DATE)
		# 06:00–22:00: starts every 45 min while start+30 <= 22:00 -> 21 slots.
		self.assertEqual(len(grid), 21)
		self.assertEqual(grid[1]["start_time"], timedelta(hours=6, minutes=45))
		self.assertEqual(grid[-1]["start_time"], timedelta(hours=21))
		self.assertEqual(grid[-1]["end_time"], timedelta(hours=21, minutes=30))
		# No slot may spill past closing.
		self.assertTrue(all(s["end_time"] <= timedelta(hours=22) for s in grid))

	def test_grid_closed_day_empty(self):
		branch = frappe.get_doc("CBT Branch", "QCSM-timog")
		self.assertEqual(get_slot_grid(branch, SUNDAY), [])  # Sundays closed
		self.assertTrue(get_slot_grid(branch, BOOKING_DATE))  # Friday open

	def test_grid_midnight_opening(self):
		# Regression: a 00:00 opening is timedelta(0) — FALSY but open. A
		# truthiness check here silently closed the e2e-fast branch (caught
		# by the section-4 E2E, fixed with an explicit None-check).
		branch = frappe.get_doc("CBT Branch", "E2EF-main")
		grid = get_slot_grid(branch, BOOKING_DATE)
		self.assertEqual(grid[0]["start_time"], timedelta(0))
		# 23:59 close means MIDNIGHT (2026-09-02): 00:00..23:00 = 24 slots, the
		# last ending at 24:00 — the hour a 24-hour branch could never sell.
		self.assertEqual(len(grid), 24)
		self.assertEqual(grid[-1]["start_time"], timedelta(hours=23))
		self.assertEqual(grid[-1]["end_time"], timedelta(hours=24))

	def test_closing_boundary_23_59_means_midnight(self):
		from court_booking_tech.timeutil import closing_boundary

		day = timedelta(hours=24)
		self.assertEqual(closing_boundary("23:59:00"), day)
		self.assertEqual(closing_boundary(timedelta(hours=23, minutes=59)), day)
		self.assertEqual(
			closing_boundary(timedelta(hours=23, minutes=59, seconds=59)), day
		)
		# Any earlier closing is itself — 22:00 still ends the AYALA grid at 22:00.
		self.assertEqual(closing_boundary("22:00:00"), timedelta(hours=22))
		self.assertEqual(
			closing_boundary(timedelta(hours=23, minutes=58)),
			timedelta(hours=23, minutes=58),
		)
		# A 00:00 closing never passes the validator; the helper must not invent a day.
		self.assertEqual(closing_boundary(timedelta(0)), timedelta(0))

	def test_last_hour_books_and_reads_back_as_24_00(self):
		"""SAVE -> REOPEN -> READ: the 23:00 slot of a 24-hour branch is sellable,
		stores end_time 24:00:00, keeps it across a re-save, cannot be collapsed
		to 00:00 through the API, and refuses a second sale of the same hour."""
		from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL

		def _book():
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": "E2EF-main-court-1",
					"customer": CUSTOMER_EMAIL,
					"booking_date": BOOKING_DATE,
					"start_time": "23:00:00",
					"number_of_slots": 1,
					"payment_method": "Cash",
				}
			)
			with patch(CLOCK, return_value=LONG_BEFORE):
				doc.insert()
			self.addCleanup(
				frappe.delete_doc,
				"CBT Court Booking",
				doc.name,
				force=True,
				ignore_permissions=True,
				ignore_missing=True,
			)
			return doc

		booked = _book()
		saved = frappe.get_doc("CBT Court Booking", booked.name)
		self.assertEqual(saved.end_time, timedelta(hours=24))
		self.assertEqual(saved.duration_hours, 1.0)
		self.assertEqual(saved.booking_status, "Confirmed")

		# A plain re-save (a discount edit) keeps the derived end.
		saved.discount_percent = 10
		with patch(CLOCK, return_value=LONG_BEFORE):
			saved.save()
		again = frappe.get_doc("CBT Court Booking", booked.name)
		self.assertEqual(again.end_time, timedelta(hours=24))

		# The API path cannot collapse it to 00:00 — end_time is immutable.
		again.end_time = "00:00:00"
		with self.assertRaises(frappe.ValidationError):
			with patch(CLOCK, return_value=LONG_BEFORE):
				again.save()

		# The grid shows the hour as taken, and the lock refuses a second sale.
		with patch(CLOCK, return_value=LONG_BEFORE):
			board = get_availability("E2EF-main", BOOKING_DATE)
		chip = _slot(board["courts"], "E2EF-main-court-1", "23:00:00")
		self.assertEqual(chip["status"], "booked")
		self.assertEqual(chip["end_time"], "24:00:00")
		with self.assertRaises(frappe.ValidationError):
			_book()

	def test_a_block_to_midnight_saves_and_resaves_as_24_00(self):
		"""A 23:00–24:00 slot block reads back as 24:00:00, survives the re-save
		that hands the timedelta back (the MariaDB 1292 path), and the grid shows
		exactly that hour blocked. Its own date, so no other test's fixture or
		cleanup can touch the cell."""
		block_date = "2027-01-20"  # a Wednesday; E2EF-main is open every day
		block = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "E2EF-main",
				"court": "E2EF-main-court-1",
				"block_date": block_date,
				"start_time": "23:00:00",
				"end_time": "24:00:00",
				"reason": "Maintenance",
			}
		)
		block.insert()
		self.addCleanup(
			frappe.delete_doc,
			"CBT Slot Block",
			block.name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
		saved = frappe.get_doc("CBT Slot Block", block.name)
		self.assertEqual(saved.end_time, timedelta(hours=24))

		saved.notes = "re-saved"  # a real UPDATE, with end_time loaded as a timedelta
		saved.save()
		again = frappe.get_doc("CBT Slot Block", block.name)
		self.assertEqual(again.end_time, timedelta(hours=24))
		self.assertEqual(again.notes, "re-saved")

		with patch(CLOCK, return_value=LONG_BEFORE):
			board = get_availability("E2EF-main", block_date)
		self.assertEqual(
			_slot(board["courts"], "E2EF-main-court-1", "23:00:00")["status"], "blocked"
		)
		self.assertEqual(
			_slot(board["courts"], "E2EF-main-court-1", "22:00:00")["status"], "available"
		)

	def test_last_hour_segments_survive_a_resave_on_a_priced_court(self):
		"""A court WITH rate rules emits a rate segment for the 23:00 slot, and
		that segment's end reads back as 24:00:00 after the re-save that reloads
		it as a timedelta — the rate_segments leg of _canonicalize_end_times.
		The insert leg pins pricing (build_rate_segments writes the string
		itself); only the re-save exercises the helper. The hour prices at the
		NIGHT rate: the seeded window runs 18:00–23:59 (2026-09-03), exclusive
		end, matched by slot start (test_rate_rules pins the seed's 200 / 350)."""
		from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL

		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": "E2EF-main-court-2",
				"customer": CUSTOMER_EMAIL,
				"booking_date": BOOKING_DATE,
				"start_time": "23:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		with patch(CLOCK, return_value=LONG_BEFORE):
			doc.insert()
		self.addCleanup(
			frappe.delete_doc,
			"CBT Court Booking",
			doc.name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
		saved = frappe.get_doc("CBT Court Booking", doc.name)
		self.assertEqual(len(saved.rate_segments), 1)
		segment = saved.rate_segments[0]
		self.assertEqual(segment.end_time, timedelta(hours=24))
		self.assertEqual(segment.hours, 1.0)
		self.assertEqual(segment.hourly_rate, 350)  # the night rate — see the docstring
		self.assertEqual(segment.amount, 350)
		self.assertEqual(segment.label, "Night rate")

		saved.discount_percent = 10
		with patch(CLOCK, return_value=LONG_BEFORE):
			saved.save()
		again = frappe.get_doc("CBT Court Booking", doc.name)
		self.assertEqual(again.rate_segments[0].end_time, timedelta(hours=24))
		# total = court share after discount + the platform fee (B27), whatever
		# the seed sets the fee to — 315 is 350 × 0.9, the part this test owns.
		self.assertAlmostEqual(
			again.total_amount, 315 + (again.platform_fee or 0), places=2
		)

	# --- availability classification -------------------------------------

	def test_availability_seeded_statuses(self):
		with patch(CLOCK, return_value=LONG_BEFORE):
			board = get_availability("AYALA-bgc", BOOKING_DATE)
		courts = board["courts"]

		reserved = _slot(courts, "AYALA-bgc-court-1", "10:00:00")
		self.assertEqual(reserved["status"], "booked")
		self.assertEqual(reserved["booking_status"], "Reserved")

		confirmed = _slot(courts, "AYALA-bgc-court-2", "10:00:00")
		self.assertEqual(confirmed["status"], "booked")
		self.assertEqual(confirmed["booking_status"], "Confirmed")

		# The seeded Extended pair: original 08:00 Extended, extension 09:00.
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-3", "08:00:00")["booking_status"],
			"Extended",
		)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-3", "09:00:00")["booking_status"],
			"Confirmed",
		)

		# Court-level Maintenance block 18:00–20:00 on court 2.
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-2", "18:00:00")["status"], "blocked"
		)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-2", "19:00:00")["status"], "blocked"
		)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-2", "17:00:00")["status"], "available"
		)

		# The Expired seeded booking does NOT occupy its slot.
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-1", "14:00:00")["status"], "available"
		)

	def test_availability_branch_wide_block(self):
		with patch(CLOCK, return_value=LONG_BEFORE):
			board = get_availability("QCSM-timog", BRANCH_BLOCK_DATE)
		for court_row in board["courts"]:
			for slot in court_row["slots"]:
				self.assertEqual(
					slot["status"], "blocked", f"{court_row['court']} {slot}"
				)

	def test_availability_past_flag(self):
		noon = datetime(2027, 1, 15, 12, 30)
		with patch(CLOCK, return_value=noon):
			board = get_availability("AYALA-bgc", BOOKING_DATE)
		courts = board["courts"]
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-3", "06:00:00")["status"], "past"
		)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-3", "13:00:00")["status"], "available"
		)
		# Booked wins over past — the desk needs to see the booking. (Court 2
		# 10:00 is CONFIRMED, so it is still live at 12:30; the court-1 10:00
		# RESERVED hold lapsed at 09:00 and correctly reads "past" instead.)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-2", "10:00:00")["status"], "booked"
		)
		self.assertEqual(
			_slot(courts, "AYALA-bgc-court-1", "10:00:00")["status"], "past"
		)

	def test_the_past_line_is_the_end_for_staff_and_the_start_for_customers(self):
		"""Section-18 (Backlog B7) — THE engine row for the two views' split.

		Staff may book the hour that is RUNNING (a walk-in who turned up
		mid-session; `create_booking` has always accepted it — S4 as-built 6), so
		their past-line is the slot's END. A customer may not: the controller's
		`_reject_past_for_customers` refuses a started slot, and a grid must never
		offer what the server will not take — so the portal's line is the START.

		Section-16 introduced this split but gated it on the no-show knob;
		section-18 dropped the gate. AYALA's knob is 0 and this row never touches
		it, which is the point: the relaxation is knob-INDEPENDENT.

		Court 3 is the one the seeded cast leaves free at midday (its bookings are
		08:00 Extended + 09:00 Confirmed), so these three chips are classified by
		the clock alone and nothing else.
		"""
		self.assertEqual(
			cint(frappe.db.get_value("CBT Company", AYALA, "no_show_release_minutes")),
			0,
			"the seeded knob must be OFF for this row to mean what it says",
		)
		noon_ish = datetime(2027, 1, 15, 12, 30)
		with patch(CLOCK, return_value=noon_ish):
			staff = get_availability("AYALA-bgc", BOOKING_DATE)
			public = get_public_availability("AYALA-bgc", BOOKING_DATE)

		court = "AYALA-bgc-court-3"
		# The RUNNING hour: 12:00-13:00 has started and has not ended.
		self.assertEqual(_slot(staff["courts"], court, "12:00:00")["status"], "available")
		self.assertEqual(_slot(public["courts"], court, "12:00:00")["status"], "past")
		# The hour BEFORE has ENDED — past for BOTH. Without this the row would
		# also pass if past_from_end were mistaken for "staff never see past".
		self.assertEqual(_slot(staff["courts"], court, "11:00:00")["status"], "past")
		self.assertEqual(_slot(public["courts"], court, "11:00:00")["status"], "past")
		# ...and a future hour is available to both, so the split really is only
		# about the one chip containing `now`.
		self.assertEqual(_slot(staff["courts"], court, "13:00:00")["status"], "available")
		self.assertEqual(_slot(public["courts"], court, "13:00:00")["status"], "available")

	def test_availability_expired_reserved_frees_slot_before_sweep(self):
		# The seeded Reserved hold expires at 09:00 on the booking date; a
		# clock past that (but before the sweep flips it) must read the slot
		# as available again.
		after_expiry = datetime(2027, 1, 15, 9, 30)
		with patch(CLOCK, return_value=after_expiry):
			board = get_availability("AYALA-bgc", BOOKING_DATE)
		slot = _slot(board["courts"], "AYALA-bgc-court-1", "10:00:00")
		self.assertEqual(slot["status"], "available")

	# --- guest-safe public variant ---------------------------------------

	def test_public_availability_strips_customer_identity(self):
		with patch(CLOCK, return_value=LONG_BEFORE):
			staff = get_availability("AYALA-bgc", BOOKING_DATE)
			public = get_public_availability("AYALA-bgc", BOOKING_DATE)

		staff_slot = _slot(staff["courts"], "AYALA-bgc-court-2", "10:00:00")
		self.assertIn("customer_name", staff_slot)
		self.assertIn("booking", staff_slot)

		for court_row in public["courts"]:
			for slot in court_row["slots"]:
				self.assertNotIn("customer_name", slot)
				self.assertNotIn("booking", slot)
		public_slot = _slot(public["courts"], "AYALA-bgc-court-2", "10:00:00")
		self.assertEqual(public_slot["status"], "booked")

	def test_public_availability_hides_suspended_company(self):
		frappe.db.set_value("CBT Company", QCSM, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", QCSM, "status", "Active"
		)
		with self.assertRaises(frappe.ValidationError):
			get_public_availability("QCSM-timog", BOOKING_DATE)

	def test_public_availability_hides_inactive_branch(self):
		frappe.db.set_value("CBT Branch", "QCSM-annex", "is_active", 0)
		self.addCleanup(
			frappe.db.set_value, "CBT Branch", "QCSM-annex", "is_active", 1
		)
		with self.assertRaises(frappe.ValidationError):
			get_public_availability("QCSM-annex", BOOKING_DATE)

	# --- clock-seam hygiene ----------------------------------------------

	def test_no_direct_time_reads_outside_clock(self):
		"""The section-4 grep rule, enforced: every now-read goes through
		court_booking_tech.clock so tests can monkeypatch ONE symbol."""
		import court_booking_tech

		pkg_dir = Path(court_booking_tech.__file__).parent
		# Concatenated so this test file never matches its own detector.
		tokens = ("now_" + "datetime(", "now" + "date(")
		offenders = []
		for path in pkg_dir.rglob("*.py"):
			if path.name == "clock.py":
				continue
			text = path.read_text(encoding="utf-8")
			if any(token in text for token in tokens):
				offenders.append(str(path.relative_to(pkg_dir)))
		self.assertEqual(
			offenders,
			[],
			"Direct time reads found — route them through clock.now_dt(): "
			f"{offenders}",
		)


# --- the section-17 test clock lever (Backlog B8) ---------------------


class TestTestClockLever(FrappeTestCase):
	"""The test-only clock offset that lets E2E move the server clock.

	Backend tests monkeypatch `clock.now_dt` and never need this; E2E drives a
	real HTTP server in another process and cannot patch anything, so the offset
	is its only lever. These rows pin the CONTRACT E2E relies on: two gates, a
	delete that really deletes, and a sweep that evaluates against the pretend
	now rather than SQL's.

	REDIS IS NOT ROLLED BACK by the test harness. `FrappeTestCase` (the
	`deprecation_dumpster` shim) commits in `setUpClass` and registers ONE
	class-level `_rollback_db` — there is no per-test savepoint at all — and no
	layer of it touches redis. So every row here deletes the key in cleanup, and
	the one row that writes to the DB takes its own savepoint. A leaked offset
	would make every later module in the same `bench run-tests` process lie
	about time.
	"""

	# Concatenated so this class does not trip the grep rule enforced by
	# TestSlots.test_no_direct_time_reads_outside_clock, which scans THIS FILE
	# too. The rule is about production paths routing through clock.now_dt();
	# a test that asserts the seam itself needs the real reading to compare to.
	_real_now = staticmethod(getattr(frappe.utils, "now_" + "datetime"))

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		# Clear BEFORE as well as after: an earlier crashed row in this process
		# would otherwise hand its offset to this one.
		frappe.cache.delete_value(clock.CLOCK_OFFSET_KEY)
		self.addCleanup(frappe.cache.delete_value, clock.CLOCK_OFFSET_KEY)

	def _set_key(self, minutes):
		# Same TTL testing.set_test_clock_offset uses, so the claim in its
		# docstring ("the key carries a 1-hour TTL") is true of EVERY writer. A
		# Ctrl-C'd backend run would otherwise leave dev.localhost lying about
		# time indefinitely — and that is the very site E2E's gw0 worker uses.
		frappe.cache.set_value(
			clock.CLOCK_OFFSET_KEY, minutes, expires_in_sec=3600
		)

	def _drift(self) -> float:
		"""Seconds between the app's clock and the real one."""
		return (clock.now_dt() - self._real_now()).total_seconds()

	def test_no_key_means_real_time(self):
		self.assertAlmostEqual(self._drift(), 0, delta=2)

	def test_offset_moves_the_clock_both_ways(self):
		# Backwards is the case E2E actually uses (23:30 -> 14:05); forwards is
		# what a sweep row needs. Both must work — it is a SIGNED offset.
		for minutes in (-575, 90):
			with self.subTest(minutes=minutes):
				self._set_key(minutes)
				self.assertAlmostEqual(self._drift(), minutes * 60, delta=2)

	def test_allow_tests_is_the_first_gate(self):
		"""A site without allow_tests ignores the key ENTIRELY.

		The offset is gated twice on purpose: one key left behind in redis
		would otherwise make a whole site lie about time, and production must
		not even pay the redis read.
		"""
		self._set_key(-575)
		conf = frappe.local.conf.copy()
		conf.pop("allow_tests", None)
		with patch.object(frappe.local, "conf", conf):
			self.assertAlmostEqual(self._drift(), 0, delta=2)
		# ...and the lever resumes the moment the gate is back, so the row
		# above cannot pass merely because the key failed to land.
		self.assertAlmostEqual(self._drift(), -575 * 60, delta=2)

	def test_set_test_clock_offset_sets_and_deletes(self):
		"""The E2E-facing entry point, including the delete path.

		0 DELETES rather than storing a zero: `now_dt` treats any falsy offset
		as "no offset", so a stored 0 would work by accident — but it would
		leave a key behind for the next file to inherit, which is the failure
		mode the whole teardown discipline exists to prevent.
		"""
		from court_booking_tech.testing import set_test_clock_offset

		result = set_test_clock_offset(-575)
		self.assertEqual(result["offset_minutes"], -575)
		self.assertEqual(frappe.cache.get_value(clock.CLOCK_OFFSET_KEY), -575)
		# The returned now is the PRETEND now. That return value is the only
		# thing E2E can assert on — it calls this through `bench execute`, in a
		# different process, and cannot see this one.
		self.assertAlmostEqual(
			(get_datetime(result["server_now"]) - self._real_now()).total_seconds(),
			-575 * 60,
			delta=5,
		)

		self.assertEqual(set_test_clock_offset(0)["offset_minutes"], 0)
		self.assertIsNone(frappe.cache.get_value(clock.CLOCK_OFFSET_KEY))

	def test_the_sweep_evaluates_against_the_pretend_clock(self):
		"""The `%(now)s` contract, end to end.

		`expire_reservations` binds `clock.now_dt()` into its SQL and never
		calls SQL `NOW()`, which is what lets ONE offset make the sweep, the
		boards and the portal agree about a pretend now. This row is the proof,
		and it is deliberately the only row here that touches the database.
		"""
		hold = frappe.db.get_value(
			"CBT Court Booking",
			{
				"court": "AYALA-bgc-court-1",
				"booking_date": BOOKING_DATE,
				"start_time": "10:00:00",
			},
		)
		self.assertTrue(hold, "the seeded bgc Reserved hold is this row's fixture")

		# There is NO per-test savepoint in this harness (see the class
		# docstring), and the sweep flips whatever else has genuinely lapsed —
		# so take one explicitly rather than leaking a swept site into the next
		# module.
		frappe.db.savepoint("cbt_clock_sweep")
		self.addCleanup(frappe.db.rollback, save_point="cbt_clock_sweep")

		# Move the ROW, not the year. The seeded hold's clocks sit in 2027, so
		# an offset big enough to lapse them (~+243,000 minutes) would sweep the
		# entire seeded cast and prove nothing about this one booking. With the
		# base clock 30 real minutes out, the ONLY difference between the two
		# sweeps below is the redis key — which is exactly the claim.
		frappe.db.set_value(
			"CBT Court Booking",
			hold,
			"reservation_expires_at",
			self._real_now() + timedelta(minutes=30),
		)

		# The seeds keep this hold proof-free on purpose, so the BASE clock
		# governs it (a Pending proof would hand it to the verification clock).
		self.assertNotIn(hold, expire_reservations()["expired"])
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", hold, "booking_status"),
			"Reserved",
		)

		self._set_key(60)
		self.assertIn(hold, expire_reservations()["expired"])
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", hold, "booking_status"),
			"Expired",
		)
