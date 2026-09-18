"""
Court Booking Tech — Two-Clock Verification Engine (section-5)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_verification

The PLAN §10 battery: office-hours walker units, END cap, upload pipeline
(ownership, file limits, caps, race), reject-with-reason semantics,
retro-confirm, sweep integration, normalized-email cap keys, and isolation
rows for CBT Payment Proof. Clock is monkeypatched — never wall-clock.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api.bookings import confirm_booking
from court_booking_tech.api.proofs import (
	accept_proofs,
	create_proof,
	find_reference_duplicates,
	reject_proofs,
	set_proof_reference,
)
from court_booking_tech.seeds.seed_test_data import (
	CUSTOMER_EMAIL,
	PLATFORM_ADMIN_EMAIL,
	_proof_sample_bytes,
	seed_all,
)
from court_booking_tech.tasks import expire_reservations
from court_booking_tech.verification import (
	compute_verification_deadline,
	normalize_email_for_cap,
)

AYALA = "ayala-courts"  # office Mon–Fri 09:00–18:00
QCSM = "qc-smash"  # office Mon–Sat 10:00–17:00
E2EF = "e2e-fast"  # office Mon–Fri 00:00–23:59 (midnight regression)

STELLA = "staff.ayala@example.com"
SAMUEL = "staff.qcsm@example.com"

# 2027-03-05 is a Friday, far from the seeded 2027-01-15 cast and the other
# test modules' dates (2027-02-05 / 2027-02-12) — no fixture interference.
TEST_DATE = "2027-03-05"
T0 = datetime(2027, 3, 5, 8, 0)

COURT_A = "AYALA-makati-court-a"
COURT_B = "AYALA-makati-court-b"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"

# REAL image bytes required: frappe's File controller EXIF-strips images
# through PIL on save, so fake magic-byte blobs are rejected at insert.
JPG = _proof_sample_bytes()

# Walker reference dates (January 2027: Mon 11th … Fri 15th, Sat 16th, Sun 17th)
TUE = datetime(2027, 1, 12, 0, 0)


def _dt(base, hour, minute=0):
	return base.replace(hour=hour, minute=minute)


class TestVerification(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _book(self, court, start_time, payment_method="Fund Transfer", at=T0, **overrides):
		"""Insert under a PATCHED clock (default T0) — a real-clock insert
		would stamp reservation_expires_at in the present and every 2027
		scenario would see a dead hold."""
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CUSTOMER_EMAIL,
			"booking_date": TEST_DATE,
			"start_time": start_time,
			"number_of_slots": 1,
			"payment_method": payment_method,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		with patch(CLOCK, return_value=at):
			doc.insert()
		self._cleanup_booking(doc.name)
		return doc

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
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

		self.addCleanup(_do)

	def _upload(self, booking, at, file_name="proof.jpg", content=JPG, **kwargs):
		with patch(CLOCK, return_value=at):
			return create_proof(booking, file_name, content, **kwargs)

	def _set_knob(self, fieldname, value):
		frappe.db.set_single_value("CBT Platform Settings", fieldname, value)

		def _restore():
			# get_single_value caches per-process and test rollbacks do NOT
			# clear it — reset the value AND the cache or later tests read
			# stale knobs.
			frappe.db.set_single_value("CBT Platform Settings", fieldname, 0)
			frappe.clear_document_cache("CBT Platform Settings", "CBT Platform Settings")

		self.addCleanup(_restore)

	def _make_customer(self, email, first_name="Test", last_name="Customer"):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"last_name": last_name,
					"enabled": 1,
					"user_type": "Website User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
			self.addCleanup(
				frappe.delete_doc, "User", email, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		return email

	def _status(self, name):
		return frappe.db.get_value("CBT Court Booking", name, "booking_status")

	# --- walker units -----------------------------------------------------

	def test_walker_inside_office_hours(self):
		# Tuesday 10:00, office 09:00–18:00, 4h budget -> 14:00 same day.
		self.assertEqual(
			compute_verification_deadline(AYALA, _dt(TUE, 10)), _dt(TUE, 14)
		)

	def test_walker_night_upload_next_opening(self):
		# Tuesday 22:00 (office closed) -> Wednesday 09:00 + 4h = 13:00.
		self.assertEqual(
			compute_verification_deadline(AYALA, _dt(TUE, 22)),
			_dt(TUE + timedelta(days=1), 13),
		)

	def test_walker_one_minute_before_close_edge(self):
		# Tuesday 17:59: 1 minute counts today, 3h59m resume Wednesday 09:00.
		self.assertEqual(
			compute_verification_deadline(AYALA, _dt(TUE, 17, 59)),
			_dt(TUE + timedelta(days=1), 12, 59),
		)

	def test_walker_weekend_span_and_no_fallback_min(self):
		# Friday 20:00, office Mon–Fri -> Monday 09:00 + 4h = 13:00 — more
		# than 24h out, proving the linear fallback is never min()'d onto a
		# valid walk.
		friday_night = datetime(2027, 1, 15, 20, 0)
		deadline = compute_verification_deadline(AYALA, friday_night)
		self.assertEqual(deadline, datetime(2027, 1, 18, 13, 0))
		self.assertGreater(deadline, friday_night + timedelta(hours=24))

	def test_walker_closed_day_skipped(self):
		# QCSM: Saturday 16:00 gives 1h (closes 17:00), Sunday closed,
		# Monday 10:00 + 3h = 13:00.
		saturday = datetime(2027, 1, 16, 16, 0)
		self.assertEqual(
			compute_verification_deadline(QCSM, saturday),
			datetime(2027, 1, 18, 13, 0),
		)

	def test_walker_zero_hours_linear_fallback(self):
		# Unsaved doc — the walker is pure given rows + hold hours.
		company = frappe.get_doc(
			{
				"doctype": "CBT Company",
				"slug": "ts-nohours",
				"company_name": "No Hours",
				"registered_name": "No Hours Corp.",
				"vat_registration": "NON-VAT",
				"office_hours": [],
			}
		)
		self.assertEqual(
			compute_verification_deadline(company, _dt(TUE, 10)),
			_dt(TUE, 10) + timedelta(hours=24),
		)

	def test_walker_midnight_opening_row(self):
		# e2e-fast opens 00:00 — timedelta(0) is falsy but OPEN (the S4
		# bug class); Wednesday 01:00 + 4h = 05:00 same day.
		wednesday = datetime(2027, 1, 13, 1, 0)
		self.assertEqual(
			compute_verification_deadline(E2EF, wednesday),
			datetime(2027, 1, 13, 5, 0),
		)

	def test_walker_hold_hours_override_chain(self):
		company = frappe.get_doc("CBT Company", AYALA)
		# Platform Singles never saved -> hard default 4 (tested above).
		# Company override wins:
		company.verification_hold_hours = 2
		self.assertEqual(
			compute_verification_deadline(company, _dt(TUE, 10)), _dt(TUE, 12)
		)

	# --- upload pipeline --------------------------------------------------

	def test_upload_sets_deadline_and_staff_source(self):
		doc = self._book(COURT_A, "16:00:00")  # ends 17:00
		result = self._upload(doc.name, T0)  # Friday 08:00 -> walker 13:00
		self.assertEqual(
			result["verification_deadline_at"], datetime(2027, 3, 5, 13, 0)
		)
		proof = frappe.get_doc("CBT Payment Proof", result["proof"])
		self.assertEqual(proof.source, "Staff")  # Administrator is not the customer
		self.assertEqual(proof.status, "Pending")
		self.assertEqual(proof.company, AYALA)
		self.assertEqual(proof.uploaded_at, T0)
		file_doc = frappe.get_doc("File", {"file_url": proof.file})
		self.assertEqual(file_doc.is_private, 1)
		self.assertEqual(file_doc.attached_to_name, proof.name)

	def test_upload_end_cap(self):
		doc = self._book(COURT_A, "10:00:00")  # ends 11:00 < walker's 13:00
		result = self._upload(doc.name, T0)
		self.assertEqual(
			result["verification_deadline_at"], datetime(2027, 3, 5, 11, 0)
		)

	def test_upload_customer_path_and_ownership(self):
		doc = self._book(COURT_A, "12:00:00")
		other = self._make_customer("ts.intruder@example.com", "Intruding", "Ivan")

		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			self._upload(doc.name, T0)

		frappe.set_user(CUSTOMER_EMAIL)  # the booking's own customer
		result = self._upload(doc.name, T0)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(
			frappe.db.get_value("CBT Payment Proof", result["proof"], "source"),
			"Customer",
		)

	def test_upload_rejects_bad_type_and_oversize(self):
		doc = self._book(COURT_A, "13:00:00")
		with self.assertRaisesRegex(frappe.ValidationError, "JPG, PNG or PDF"):
			self._upload(doc.name, T0, file_name="malware.exe")
		self._set_knob("proof_max_mb", 1)
		with self.assertRaisesRegex(frappe.ValidationError, "too large"):
			self._upload(doc.name, T0, content=b"x" * (1024 * 1024 + 1))

	def test_upload_on_expired_booking_clean_error(self):
		doc = self._book(COURT_A, "14:00:00")
		frappe.db.set_value("CBT Court Booking", doc.name, "booking_status", "Expired")
		with self.assertRaisesRegex(frappe.ValidationError, "contact the branch"):
			self._upload(doc.name, T0)

	def test_upload_vs_sweep_race_expires_in_lock(self):
		doc = self._book(COURT_A, "15:00:00")  # base clock: insert now + 30
		# 40 min later the hold is dead but the sweep has not run — the
		# upload must flip it Expired itself and fail cleanly.
		with self.assertRaisesRegex(frappe.ValidationError, "contact the branch"):
			self._upload(doc.name, T0 + timedelta(minutes=40))
		self.assertEqual(self._status(doc.name), "Expired")
		self.assertFalse(
			frappe.db.exists("CBT Payment Proof", {"booking": doc.name})
		)

	def test_upload_deadline_set_once(self):
		doc = self._book(COURT_A, "16:00:00")
		first = self._upload(doc.name, T0)
		second = self._upload(doc.name, T0 + timedelta(minutes=10))
		self.assertEqual(
			second["verification_deadline_at"], first["verification_deadline_at"]
		)
		self.assertEqual(
			frappe.db.get_value(
				"CBT Court Booking", doc.name, "verification_deadline_at"
			),
			first["verification_deadline_at"],
		)

	# --- rejection semantics ---------------------------------------------

	def test_reject_fake_expires_immediately(self):
		doc = self._book(COURT_A, "16:00:00")
		result = self._upload(doc.name, T0)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			outcome = reject_proofs(doc.name, "Invalid / suspected fake")
		self.assertEqual(outcome["outcome"], "Expired")
		self.assertEqual(self._status(doc.name), "Expired")
		proof = frappe.get_doc("CBT Payment Proof", result["proof"])
		self.assertEqual(proof.status, "Rejected")
		self.assertEqual(proof.rejection_reason, "Invalid / suspected fake")
		self.assertEqual(proof.rejected_by, "Administrator")
		self.assertIsNone(
			frappe.db.get_value(
				"CBT Court Booking", doc.name, "verification_deadline_at"
			)
		)

	def test_reject_recoverable_grants_exact_regrace_once(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)
		reject_at = T0 + timedelta(minutes=10)
		with patch(CLOCK, return_value=reject_at):
			outcome = reject_proofs(doc.name, "Unreadable")
		self.assertEqual(outcome["outcome"], "Regrace")
		self.assertEqual(self._status(doc.name), "Reserved")
		booking = frappe.db.get_value(
			"CBT Court Booking",
			doc.name,
			["reservation_expires_at", "verification_deadline_at", "rejection_count"],
			as_dict=True,
		)
		# Exactly rejection_reupload_minutes (default 120), deadline cleared.
		self.assertEqual(
			booking.reservation_expires_at, reject_at + timedelta(minutes=120)
		)
		self.assertIsNone(booking.verification_deadline_at)
		self.assertEqual(booking.rejection_count, 1)

	def test_reupload_after_regrace_restarts_walker(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			reject_proofs(doc.name, "Unreadable")
		# Re-upload at 09:30 (inside office hours): fresh walker -> 13:30.
		result = self._upload(doc.name, _dt(T0, 9, 30))
		self.assertEqual(
			result["verification_deadline_at"], datetime(2027, 3, 5, 13, 30)
		)

	def test_second_rejection_any_reason_expires(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			reject_proofs(doc.name, "Unreadable")
		self._upload(doc.name, _dt(T0, 9, 0))
		with patch(CLOCK, return_value=_dt(T0, 9, 30)):
			outcome = reject_proofs(doc.name, "Wrong amount")  # recoverable reason
		self.assertEqual(outcome["outcome"], "Expired")  # …but 2nd rejection
		self.assertEqual(self._status(doc.name), "Expired")
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", doc.name, "rejection_count"), 2
		)

	def test_reject_dead_hold_never_regraces(self):
		doc = self._book(COURT_A, "10:00:00")  # deadline will cap at 11:00
		self._upload(doc.name, T0)
		# Past the END-capped deadline, sweep not run: reject records but
		# expires — a regrace here could squat a slot someone else rebooked.
		with patch(CLOCK, return_value=_dt(T0, 11, 30)):
			outcome = reject_proofs(doc.name, "Unreadable")
		self.assertEqual(outcome["outcome"], "Expired")
		self.assertEqual(self._status(doc.name), "Expired")

	def test_booking_level_rejection_frees_pending_cap(self):
		doc = self._book(COURT_A, "16:00:00")
		for minute in (0, 5, 10):  # default cap: 3 pending per booking
			self._upload(doc.name, T0 + timedelta(minutes=minute))
		with self.assertRaisesRegex(frappe.ValidationError, "awaiting review"):
			self._upload(doc.name, T0 + timedelta(minutes=15))
		with patch(CLOCK, return_value=T0 + timedelta(minutes=20)):
			reject_proofs(doc.name, "Unreadable")
		# ALL Pending flipped at once (booking-level event) — cap is free.
		self.assertEqual(
			frappe.db.count(
				"CBT Payment Proof", {"booking": doc.name, "status": "Rejected"}
			),
			3,
		)
		result = self._upload(doc.name, T0 + timedelta(minutes=30))
		self.assertTrue(result["proof"])

	# --- caps -------------------------------------------------------------

	def test_customer_holds_cap_normalized_across_companies(self):
		self._set_knob("max_active_proof_holds_per_customer", 1)
		holder = self._make_customer("ts.holder@gmail.com", "Holding", "Hank")
		plus_variant = self._make_customer("tsholder+x@gmail.com", "Holding", "Hank2")
		dot_variant = self._make_customer("t.s.holder@gmail.com", "Holding", "Hank3")
		yahoo = self._make_customer("ts.holder@yahoo.com", "Yahoo", "Yolanda")

		first = self._book(COURT_A, "12:00:00", customer=holder)
		self._upload(first.name, T0)  # hold 1 for key tsholder@gmail.com

		# Same cap key via +tag, on ANOTHER company -> blocked.
		blocked = self._book(QCSM_COURT, "12:00:00", customer=plus_variant)
		with self.assertRaisesRegex(frappe.ValidationError, "awaiting payment verification"):
			self._upload(blocked.name, T0)
		# Same cap key via Gmail dots -> blocked.
		blocked2 = self._book(COURT_B, "12:00:00", customer=dot_variant)
		with self.assertRaisesRegex(frappe.ValidationError, "awaiting payment verification"):
			self._upload(blocked2.name, T0)
		# Non-Gmail dots are a DIFFERENT key -> allowed.
		ok = self._book(COURT_B, "13:00:00", customer=yahoo)
		self.assertTrue(self._upload(ok.name, T0)["proof"])

	def test_normalize_email_for_cap_rows(self):
		for raw, expected in [
			("Foo+9@Gmail.com", "foo@gmail.com"),
			("f.o.o@gmail.com", "foo@gmail.com"),
			("f.o.o@googlemail.com", "foo@googlemail.com"),
			("j.doe+x@yahoo.com", "j.doe@yahoo.com"),  # dots kept off-Gmail
			("PLAIN@EXAMPLE.COM", "plain@example.com"),
		]:
			self.assertEqual(normalize_email_for_cap(raw), expected)

	# --- accept / retro-confirm ------------------------------------------

	def test_accept_proofs_confirms_and_accepts(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			accept_proofs(doc.name)
		self.assertEqual(self._status(doc.name), "Confirmed")
		self.assertEqual(
			frappe.db.count(
				"CBT Payment Proof", {"booking": doc.name, "status": "Accepted"}
			),
			1,
		)

	def test_customer_cannot_review_proofs(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)
		frappe.set_user(CUSTOMER_EMAIL)
		self.assertRaises(frappe.PermissionError, accept_proofs, doc.name)
		self.assertRaises(
			frappe.PermissionError, reject_proofs, doc.name, "Unreadable"
		)

	def test_retro_confirm_expired_with_proof(self):
		doc = self._book(COURT_A, "10:00:00")
		self._upload(doc.name, T0)  # deadline caps at END (11:00)
		with patch(CLOCK, return_value=_dt(T0, 12, 0)):
			expire_reservations()  # past END -> Expired (with proof on file)
		self.assertEqual(self._status(doc.name), "Expired")
		retro_at = _dt(T0, 14, 0)
		with patch(CLOCK, return_value=retro_at):
			confirm_booking(doc.name)
		booking = frappe.db.get_value(
			"CBT Court Booking",
			doc.name,
			["booking_status", "confirmed_by", "confirmed_at"],
			as_dict=True,
		)
		self.assertEqual(booking.booking_status, "Completed")
		self.assertEqual(booking.confirmed_by, "Administrator")
		self.assertEqual(booking.confirmed_at, retro_at)
		self.assertEqual(
			frappe.db.count(
				"CBT Payment Proof", {"booking": doc.name, "status": "Accepted"}
			),
			1,
		)

	def test_retro_confirm_requires_proof(self):
		doc = self._book(COURT_A, "13:00:00")
		frappe.db.set_value("CBT Court Booking", doc.name, "booking_status", "Expired")
		with self.assertRaisesRegex(frappe.ValidationError, "payment proof is on file"):
			with patch(CLOCK, return_value=T0):
				confirm_booking(doc.name)

	# --- sweep integration ------------------------------------------------

	def test_sweep_pending_proof_survives_base_clock(self):
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)  # deadline 13:00; base dies T0+30
		with patch(CLOCK, return_value=T0 + timedelta(minutes=40)):
			result = expire_reservations()
		self.assertNotIn(doc.name, result["expired"])
		self.assertEqual(self._status(doc.name), "Reserved")
		# …but past the verification deadline it expires.
		with patch(CLOCK, return_value=_dt(T0, 13, 1)):
			result = expire_reservations()
		self.assertIn(doc.name, result["expired"])
		self.assertEqual(self._status(doc.name), "Expired")

	def test_sweep_end_cap_beats_regrace(self):
		# Design decision 7: a regrace stretching past the booking END must
		# not outlive the slot — Reserved past END expires unconditionally.
		doc = self._book(COURT_A, "10:00:00")  # ends 11:00
		frappe.db.set_value(
			"CBT Court Booking",
			doc.name,
			"reservation_expires_at",
			datetime(2027, 3, 5, 12, 30),  # simulated regrace past END
		)
		with patch(CLOCK, return_value=_dt(T0, 11, 5)):
			result = expire_reservations()
		self.assertIn(doc.name, result["expired"])

	def test_sweep_base_clock_unchanged_without_proof(self):
		doc = self._book(COURT_A, "15:00:00")
		with patch(CLOCK, return_value=T0 + timedelta(minutes=40)):
			result = expire_reservations()
		self.assertIn(doc.name, result["expired"])

	# --- slot holding under the verification clock ------------------------

	def test_proof_hold_keeps_slot_occupied(self):
		# Design decision 1: with a Pending proof the slot must NOT read as
		# free after the base clock lapses — no double-booking.
		doc = self._book(COURT_A, "16:00:00")
		self._upload(doc.name, T0)  # deadline 13:00
		# 40 min in: the BASE clock is dead — only the verification clock
		# still holds the slot (the at= param patches the insert's clock).
		with self.assertRaisesRegex(frappe.ValidationError, "already taken"):
			self._book(
				COURT_A,
				"16:00:00",
				payment_method="Cash",
				at=T0 + timedelta(minutes=40),
			)
		# Past the deadline the hold is dead and the slot books again.
		winner = self._book(
			COURT_A, "16:00:00", payment_method="Cash", at=_dt(T0, 13, 30)
		)
		self.assertEqual(winner.booking_status, "Confirmed")

	# --- immutability & permissions --------------------------------------

	def test_proof_immutable_after_insert(self):
		doc = self._book(COURT_A, "16:00:00")
		result = self._upload(doc.name, T0)
		proof = frappe.get_doc("CBT Payment Proof", result["proof"])
		proof.remarks = "tampered"
		self.assertRaisesRegex(
			frappe.ValidationError, "immutable", proof.save
		)

	def test_proof_delete_blocked_below_system_manager(self):
		doc = self._book(COURT_A, "16:00:00")
		result = self._upload(doc.name, T0)
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			frappe.delete_doc,
			"CBT Payment Proof",
			result["proof"],
		)

	def test_proof_isolation_and_customer_denied(self):
		doc = self._book(COURT_A, "16:00:00")
		result = self._upload(doc.name, T0)

		frappe.set_user(STELLA)  # AYALA staff sees own company's proofs
		names = {row.name for row in frappe.get_list("CBT Payment Proof", limit_page_length=0)}
		self.assertIn(result["proof"], names)

		frappe.set_user(SAMUEL)  # QCSM staff sees NONE of AYALA's
		names = {row.name for row in frappe.get_list("CBT Payment Proof", limit_page_length=0)}
		self.assertNotIn(result["proof"], names)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		proof_doc = frappe.get_doc("CBT Payment Proof", result["proof"])
		frappe.set_user(SAMUEL)
		self.assertFalse(proof_doc.has_permission("read"))

		frappe.set_user(CUSTOMER_EMAIL)  # customers: NO DocPerm at all
		self.assertRaises(
			frappe.PermissionError, frappe.get_list, "CBT Payment Proof"
		)

	# --- regressions ------------------------------------------------------

	def test_business_hours_midnight_company_resaves(self):
		# The e2e-fast company opens at 00:00 — timedelta(0) from the DB used
		# to trip the truthiness check in validate_business_hours and made
		# ANY re-save of the company throw.
		company = frappe.get_doc("CBT Company", E2EF)
		company.save()  # must not raise


class TestWalkInProofs(FrappeTestCase):
	"""Section-13: a walk-in may still pay by transfer, so the two-clock engine
	has to work with no account behind the booking — and the cross-company
	holds cap must not treat every walk-in on the platform as one person."""

	# October 2027 = section-13's backend month (September is test_reports'
	# integer-claimed empty-control month — see seeds WALKIN_DATE).
	WALK_DATE = "2027-10-01"  # Friday
	WALK_T0 = datetime(2027, 10, 1, 8, 0)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _walkin_hold(self, court, start_time, name="Walk-in Wesley", **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer_name": name,
			"booking_date": self.WALK_DATE,
			"start_time": start_time,
			"number_of_slots": 1,
			"payment_method": "Fund Transfer",
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		with patch(CLOCK, return_value=self.WALK_T0):
			doc.insert(ignore_permissions=True)
		self._cleanup_booking(doc.name)
		return doc

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
			for proof in frappe.get_all(
				"CBT Payment Proof", filters={"booking": name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Payment Proof", proof, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
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

	def test_staff_can_upload_a_proof_for_a_walk_in(self):
		"""The desk's "they sent it on Messenger" flow. frappe.session.user can
		never equal a NULL customer, so the actor resolves to Staff and the
		tenancy gate — exactly the right branch."""
		booking = self._walkin_hold(COURT_A, "10:00:00")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0):
			result = create_proof(booking.name, "proof.jpg", JPG)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertTrue(result["proof"])
		self.assertEqual(
			frappe.db.get_value("CBT Payment Proof", result["proof"], "source"),
			"Staff",
		)
		deadline = result["verification_deadline_at"]
		self.assertIsNotNone(deadline, "first proof must arm the verification clock")
		self.assertEqual(
			frappe.db.get_value(
				"CBT Court Booking", booking.name, "verification_deadline_at"
			),
			deadline,
		)

	def test_accepting_a_walk_in_proof_confirms_and_pays_the_invoice(self):
		booking = self._walkin_hold(COURT_A, "11:00:00")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0):
			create_proof(booking.name, "proof.jpg", JPG)
			accept_proofs(booking.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", booking.name, "booking_status"),
			"Confirmed",
		)
		invoice = frappe.db.get_value("CBT Court Booking", booking.name, "billing_doc")
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", invoice, "status"),
			"Paid & Verified",
		)

	def test_holds_cap_exempts_walk_ins_instead_of_merging_them(self):
		"""TENANCY, not convenience: normalize_email_for_cap(None) is "", so
		without the exemption every customer-less booking on the PLATFORM would
		share one cap key — holds at other companies would refuse this one, and
		the error text would disclose a count of another tenant's rows."""
		from court_booking_tech.api.proofs import _check_customer_holds_cap

		self.assertEqual(normalize_email_for_cap(None), "")

		booking = self._walkin_hold(COURT_B, "10:00:00")
		with patch(
			"court_booking_tech.api.proofs._setting", return_value=1
		) as setting:
			# Returns without consulting the cap setting at all.
			_check_customer_holds_cap(booking)
			setting.assert_not_called()

	def test_many_walk_in_holds_never_block_each_other(self):
		"""The end-to-end shape of the same defect: two walk-in holds with
		pending proofs, a cap of 1, and the second upload must still succeed."""
		first = self._walkin_hold(COURT_A, "13:00:00", name="Walk-in One")
		second = self._walkin_hold(COURT_B, "13:00:00", name="Walk-in Two")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0), patch(
			"court_booking_tech.api.proofs._setting",
			side_effect=lambda field, default: 1
			if field == "max_active_proof_holds_per_customer"
			else default,
		):
			create_proof(first.name, "proof.jpg", JPG)
			create_proof(second.name, "proof.jpg", JPG)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		for booking in (first, second):
			self.assertTrue(
				frappe.db.exists(
					"CBT Payment Proof", {"booking": booking.name, "status": "Pending"}
				),
				f"{booking.customer_name}'s proof was refused by the holds cap",
			)

	def test_a_real_customer_cap_is_unaffected_by_walk_in_rows(self):
		"""The converse leak: a walk-in row must never inflate a real
		customer's count. A real email can never normalize to ""."""
		self._walkin_hold(COURT_A, "14:00:00", name="Walk-in Noise")
		self.assertNotEqual(normalize_email_for_cap(CUSTOMER_EMAIL), "")
		self.assertNotEqual(
			normalize_email_for_cap(CUSTOMER_EMAIL), normalize_email_for_cap(None)
		)


class TestProofReference(FrappeTestCase):
	"""Backlog B40 — the transaction reference staff can type, correct and be
	warned about. Section-5 as-built 12. Its own class and its own dates: the
	duplicate check reads the whole company, so it must not see the other
	classes' rows."""

	REF_DATE = "2027-04-09"  # a Friday nothing else in the suite books
	REF_T0 = datetime(2027, 4, 9, 10, 0)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _hold(self, court, start_time, company_customer=CUSTOMER_EMAIL):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": court,
				"customer": company_customer,
				"booking_date": self.REF_DATE,
				"start_time": start_time,
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
			}
		)
		with patch(CLOCK, return_value=self.REF_T0):
			doc.insert()

		def _do():
			frappe.set_user("Administrator")
			for proof in frappe.get_all(
				"CBT Payment Proof", filters={"booking": doc.name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Payment Proof", proof, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Court Booking", doc.name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)
		return doc

	def _upload(self, booking, **kwargs):
		with patch(CLOCK, return_value=self.REF_T0):
			return create_proof(booking, "proof.jpg", JPG, **kwargs)

	def _proof_of(self, booking):
		return frappe.get_all(
			"CBT Payment Proof",
			filters={"booking": booking},
			fields=["name", "status", "reference_no", "reference_set_by"],
			order_by="creation asc",
		)

	# --- the write -------------------------------------------------------

	def test_staff_type_a_reference_the_customer_never_gave(self):
		booking = self._hold(COURT_A, "09:00:00")
		self._upload(booking.name)
		frappe.set_user(STELLA)
		result = set_proof_reference(booking.name, " GC-77123 ")

		self.assertEqual(len(result["updated"]), 1)
		proof = self._proof_of(booking.name)[0]
		self.assertEqual(proof.reference_no, "GC-77123", "not trimmed or not stored")
		self.assertEqual(proof.reference_set_by, STELLA, "the correction is unattributed")

	def test_a_blank_reference_clears_a_wrong_one(self):
		booking = self._hold(COURT_A, "10:00:00")
		self._upload(booking.name, reference_no="TYPO-1")
		frappe.set_user(STELLA)
		set_proof_reference(booking.name, "   ")

		proof = self._proof_of(booking.name)[0]
		self.assertIsNone(proof.reference_no, "a clear is a correction, not a no-op")
		self.assertEqual(proof.reference_set_by, STELLA, "who cleared it is audit too")

	def test_a_fat_fingered_reference_is_still_correctable_after_acceptance(self):
		"""Half the point of the row — 'easier trace back' has to survive the
		moment staff click Accept."""
		booking = self._hold(COURT_A, "11:00:00")
		self._upload(booking.name, reference_no="WRONG-9")
		frappe.set_user(STELLA)
		accept_proofs(booking.name)
		set_proof_reference(booking.name, "RIGHT-9")

		proof = self._proof_of(booking.name)[0]
		self.assertEqual(proof.status, "Accepted")
		self.assertEqual(proof.reference_no, "RIGHT-9")

	def test_a_rejected_receipt_is_dead_evidence_and_never_rewritten(self):
		booking = self._hold(COURT_A, "12:00:00")
		self._upload(booking.name, reference_no="FAKE-1")
		frappe.set_user(STELLA)
		reject_proofs(booking.name, "Invalid / suspected fake")
		set_proof_reference(booking.name, "SOMETHING-ELSE")

		proof = self._proof_of(booking.name)[0]
		self.assertEqual(proof.status, "Rejected")
		self.assertEqual(proof.reference_no, "FAKE-1", "a rejected receipt was rewritten")

	def test_a_carts_rows_all_carry_the_one_reference(self):
		"""create_proof fans ONE reference across every row of a cart; the
		correction must not split it (ducky STOP, 2026-09-05)."""
		group = "CART-REF-1"
		first = self._hold(COURT_A, "13:00:00")
		second = self._hold(COURT_B, "13:00:00")
		for booking in (first, second):
			frappe.db.set_value("CBT Court Booking", booking.name, "booking_group", group)
		first.reload()
		self._upload(first.name, reference_no="CART-REF")
		frappe.set_user(STELLA)
		set_proof_reference(first.name, "CART-REF-FIXED")

		for booking in (first, second):
			proofs = self._proof_of(booking.name)
			self.assertTrue(proofs, f"{booking.name} lost its proof")
			self.assertEqual(
				proofs[0].reference_no,
				"CART-REF-FIXED",
				"the cart's rows now disagree about one payment's reference",
			)

	# --- the warning -----------------------------------------------------

	def test_the_same_receipt_on_a_second_booking_warns_and_still_accepts(self):
		"""The ruling, verbatim: warn if there is an exact record, but do not
		stop them from recording."""
		first = self._hold(COURT_A, "14:00:00")
		second = self._hold(COURT_B, "14:00:00")
		self._upload(first.name, reference_no="DUP-500")
		self._upload(second.name, reference_no="DUP-500")

		frappe.set_user(STELLA)
		matches = find_reference_duplicates(second.name, "DUP-500")
		self.assertEqual(
			[row["booking"] for row in matches],
			[first.name],
			"the warning did not name the booking the receipt is already on",
		)

		accept_proofs(second.name)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", second.name, "booking_status"),
			"Confirmed",
			"the duplicate BLOCKED the accept — the ruling says warn only",
		)

	def test_a_blank_reference_matches_nothing(self):
		"""Every desk-uploaded proof predating B40 has a blank reference — an
		unguarded equality would make the warning permanent noise."""
		booking = self._hold(COURT_A, "15:00:00")
		self._upload(booking.name)
		other = self._hold(COURT_B, "15:00:00")
		self._upload(other.name)

		frappe.set_user(STELLA)
		self.assertEqual(find_reference_duplicates(booking.name, ""), [])
		self.assertEqual(find_reference_duplicates(booking.name, "   "), [])

	def test_a_carts_own_siblings_are_not_its_duplicates(self):
		group = "CART-REF-2"
		first = self._hold(COURT_A, "16:00:00")
		second = self._hold(COURT_B, "16:00:00")
		for booking in (first, second):
			frappe.db.set_value("CBT Court Booking", booking.name, "booking_group", group)
		first.reload()
		self._upload(first.name, reference_no="CART-DUP")

		frappe.set_user(STELLA)
		self.assertEqual(
			find_reference_duplicates(first.name, "CART-DUP"),
			[],
			"a cart warned about its own rows — staff learn to ignore the warning",
		)

	def test_another_companys_identical_reference_is_never_disclosed(self):
		"""A GCash reference is unique to a bank, not to the platform — and a
		cross-tenant match would name another tenant's customer."""
		ayala = self._hold(COURT_A, "17:00:00")
		self._upload(ayala.name, reference_no="SHARED-REF")
		qcsm = self._hold(QCSM_COURT, "11:00:00")
		self._upload(qcsm.name, reference_no="SHARED-REF")

		frappe.set_user(STELLA)
		self.assertEqual(find_reference_duplicates(ayala.name, "SHARED-REF"), [])

	# --- the gates -------------------------------------------------------

	def test_another_companys_staff_cannot_write_the_reference(self):
		booking = self._hold(COURT_A, "18:00:00")
		self._upload(booking.name)
		frappe.set_user(SAMUEL)
		with self.assertRaises(frappe.PermissionError):
			set_proof_reference(booking.name, "NOT-YOURS")

	def test_another_companys_staff_cannot_probe_for_duplicates(self):
		"""The reader hands back another customer's date and amount, so the
		CALLER is gated, not just the matches (ducky STOP, 2026-09-05)."""
		booking = self._hold(COURT_A, "19:00:00")
		self._upload(booking.name, reference_no="PROBE-1")
		frappe.set_user(SAMUEL)
		with self.assertRaises(frappe.PermissionError):
			find_reference_duplicates(booking.name, "PROBE-1")

	def test_the_customer_cannot_write_their_own_reference_after_the_fact(self):
		booking = self._hold(COURT_A, "20:00:00")
		self._upload(booking.name, reference_no="MINE-1")
		frappe.set_user(CUSTOMER_EMAIL)
		with self.assertRaises(frappe.PermissionError):
			set_proof_reference(booking.name, "MINE-2")
