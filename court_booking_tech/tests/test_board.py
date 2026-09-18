"""
Court Booking Tech — Court Board API tests (section-7)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_board

Covers the board data plane (get_board_data payload + layout, pending-payments
ordering/effective-deadline semantics incl. lapsed-but-unswept holds,
get_booking_detail) and the desk quick-book/block endpoints — including
STAFF-POSITIVE rows (ducky condition: E2E file 06 runs as Administrator, so the
permission-checked inserts inside these endpoints are proven here under a real
staff session).

Fixture discipline: every mutating test builds its OWN bookings on its OWN
date (2027-01-22..27 — seeds own 2027-01-15/16, isolation tests own
2027-02-12/13) so the load-bearing seeded fixtures stay untouched
(S5 as-built 11) and tests are order-independent under class-level rollback
(S6 as-built 8).
"""

from datetime import timedelta
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from court_booking_tech.api.board import (
	customer_query,
	get_board_data,
	get_booking_detail,
	get_pending_payments,
)
from court_booking_tech.api.bookings import create_block, create_booking
from court_booking_tech.seeds.seed_test_data import seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"
STELLA = "staff.ayala@example.com"
CARLA = "cust.carla@example.com"
BGC = "AYALA-bgc"
MAKATI = "AYALA-makati"


def _make_booking(court, booking_date, start_time, payment_method="Fund Transfer"):
	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CARLA,
			"booking_date": booking_date,
			"start_time": start_time,
			"number_of_slots": 1,
			"payment_method": payment_method,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


class TestBoardAPIs(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _suspend(self, company):
		frappe.db.set_value("CBT Company", company, "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", company, "status", "Active"
		)

	# --- get_board_data ---------------------------------------------------

	def test_board_data_layout_and_shape(self):
		data = get_board_data(BGC, "2027-01-22")
		self.assertEqual(data["company"], AYALA)
		self.assertTrue(data["server_now"])
		self.assertEqual(data["layout"]["rows"], 2)
		self.assertEqual(data["layout"]["columns"], 2)
		self.assertEqual(len(data["layout"]["cells"]), 3)  # 2×2 with one gap
		self.assertEqual(len(data["courts"]), 3)
		for court_row in data["courts"]:
			self.assertTrue(court_row["slots"], court_row["court"])

	def test_board_data_enriches_booked_slots(self):
		booking = _make_booking(f"{BGC}-court-2", "2027-01-22", "10:00:00")
		data = get_board_data(BGC, "2027-01-22")
		court_row = next(c for c in data["courts"] if c["court"] == f"{BGC}-court-2")
		slot = next(s for s in court_row["slots"] if s["start_time"] == "10:00:00")
		self.assertEqual(slot["status"], "booked")
		self.assertEqual(slot["booking"], booking.name)
		self.assertEqual(slot["booking_status"], "Reserved")
		self.assertEqual(slot["payment_method"], "Fund Transfer")
		self.assertTrue(slot["reservation_expires_at"])
		self.assertEqual(slot["pending_proof_count"], 0)
		self.assertEqual(slot["rejection_count"], 0)
		self.assertEqual(slot["total_amount"], booking.total_amount)

	def test_board_data_branch_without_layout(self):
		data = get_board_data(MAKATI, "2027-01-22")
		self.assertEqual(data["layout"]["cells"], [])

	# --- get_pending_payments ---------------------------------------------

	def test_pending_payments_ordering_and_effective_deadline(self):
		# Base-clock-only booking vs a verification-clock booking whose
		# deadline is EARLIER — the verification clock governs its item and
		# sorts it first.
		base_only = _make_booking(f"{BGC}-court-1", "2027-01-23", "10:00:00")
		with_verify = _make_booking(f"{BGC}-court-2", "2027-01-23", "10:00:00")
		earlier = get_datetime(base_only.reservation_expires_at).replace(
			microsecond=0
		) - timedelta(minutes=10)
		frappe.db.set_value(
			"CBT Court Booking",
			with_verify.name,
			"verification_deadline_at",
			earlier,
			update_modified=False,
		)

		payload = get_pending_payments(AYALA)
		items = {item["name"]: item for item in payload["items"]}
		self.assertIn(base_only.name, items)
		self.assertIn(with_verify.name, items)
		self.assertEqual(
			get_datetime(items[with_verify.name]["effective_deadline"]), earlier
		)
		self.assertEqual(
			get_datetime(items[base_only.name]["effective_deadline"]),
			get_datetime(base_only.reservation_expires_at),
		)
		order = [item["name"] for item in payload["items"]]
		self.assertLess(order.index(with_verify.name), order.index(base_only.name))

		# Payload purity: every row belongs to the requested company.
		for item in payload["items"]:
			self.assertEqual(
				frappe.db.get_value("CBT Court Booking", item["name"], "company"),
				AYALA,
				item["name"],
			)

	def test_pending_payments_includes_lapsed_unswept_hold(self):
		# Both clocks lapsed but the sweep hasn't flipped it: staff can still
		# confirm/reject (S5 dead-hold semantics), so the panel MUST show it
		# with its deadline in the past.
		lapsed = _make_booking(f"{BGC}-court-3", "2027-01-24", "10:00:00")
		frappe.db.set_value(
			"CBT Court Booking",
			lapsed.name,
			"reservation_expires_at",
			"2020-01-01 00:00:00",
			update_modified=False,
		)
		payload = get_pending_payments(AYALA)
		item = next(
			(i for i in payload["items"] if i["name"] == lapsed.name), None
		)
		self.assertIsNotNone(item)
		self.assertLess(
			get_datetime(item["effective_deadline"]),
			get_datetime(payload["server_now"]),
		)

	def test_pending_payments_staff_session_resolves_own_company(self):
		# Staff-positive row: no company arg — the session binding resolves it.
		frappe.set_user(STELLA)
		payload = get_pending_payments()
		self.assertEqual(payload["company"], AYALA)

	# --- get_booking_detail -----------------------------------------------

	def test_booking_detail_proofs_and_invoice(self):
		from court_booking_tech.api.proofs import create_proof

		booking = _make_booking(f"{BGC}-court-1", "2027-01-25", "10:00:00")
		sample = Path(
			frappe.get_app_path("court_booking_tech", "seeds", "files", "proof_sample.jpg")
		).read_bytes()
		create_proof(booking.name, "proof_sample.jpg", sample, reference_no="REF-77")

		detail = get_booking_detail(booking.name)
		self.assertEqual(detail["booking_status"], "Reserved")
		self.assertEqual(detail["invoice_status"], "Unpaid")
		self.assertTrue(detail["verification_deadline_at"])
		self.assertEqual(len(detail["proofs"]), 1)
		proof = detail["proofs"][0]
		self.assertEqual(proof["status"], "Pending")
		self.assertEqual(proof["reference_no"], "REF-77")
		self.assertFalse(proof["is_pdf"])
		self.assertEqual(proof["source"], "Staff")

	# --- create_booking (desk quick-book) ---------------------------------

	def test_create_booking_staff_cash_derives_chain_and_confirms(self):
		# Staff-positive row (ducky condition): the permission-checked insert
		# runs under a REAL staff session, not Administrator.
		frappe.set_user(STELLA)
		result = create_booking(
			court=f"{BGC}-court-2",
			booking_date="2027-01-26",
			start_time="10:00:00",
			customer=CARLA,
			payment_method="Cash",
		)
		self.assertEqual(result["booking_status"], "Confirmed")
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(doc.company, AYALA)  # derived, never client-supplied
		self.assertEqual(doc.branch, BGC)
		self.assertEqual(doc.hourly_rate, 450)  # court-2 seeded rate
		self.assertEqual(doc.confirmed_by, STELLA)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status"),
			"Paid & Verified",
		)

	def test_create_booking_suspended_company_rejected(self):
		self._suspend(QCSM)
		self.assertRaises(
			frappe.ValidationError,
			create_booking,
			court="QCSM-timog-court-1",
			booking_date="2027-01-26",
			start_time="10:00:00",
			customer=CARLA,
			payment_method="Cash",
		)

	# --- create_block ------------------------------------------------------

	def test_create_block_court_level_and_branch_wide(self):
		frappe.set_user(STELLA)  # staff-positive row
		court_block = create_block(
			branch=BGC,
			block_date="2027-01-27",
			start_time="08:00:00",
			end_time="09:00:00",
			reason="Maintenance",
			court=f"{BGC}-court-1",
		)
		branch_block = create_block(
			branch=BGC,
			block_date="2027-01-27",
			start_time="20:00:00",
			end_time="21:00:00",
			reason="Holiday",
		)
		self.assertEqual(
			frappe.db.get_value("CBT Slot Block", court_block["name"], "company"),
			AYALA,
		)
		self.assertFalse(
			frappe.db.get_value("CBT Slot Block", branch_block["name"], "court")
		)

		# The board reflects both: court-1 alone at 08:00, EVERY court at 20:00.
		data = get_board_data(BGC, "2027-01-27")
		for court_row in data["courts"]:
			slot_8 = next(
				s for s in court_row["slots"] if s["start_time"] == "08:00:00"
			)
			slot_20 = next(
				s for s in court_row["slots"] if s["start_time"] == "20:00:00"
			)
			if court_row["court"] == f"{BGC}-court-1":
				self.assertEqual(slot_8["status"], "blocked")
			else:
				self.assertEqual(slot_8["status"], "available")
			self.assertEqual(slot_20["status"], "blocked", court_row["court"])

	def test_create_block_on_suspended_company_allowed(self):
		# Blocking is housekeeping (allow_suspended=True) — a suspended company
		# still manages its calendar (S4 as-built 5).
		self._suspend(QCSM)
		result = create_block(
			branch="QCSM-timog",
			block_date="2027-01-27",
			start_time="10:00:00",
			end_time="11:00:00",
			reason="Maintenance",
		)
		self.assertTrue(result["name"])

	# --- customer link query ----------------------------------------------

	def test_customer_query_returns_only_cbt_customers(self):
		rows = customer_query("User", "", "name", 0, 50, {})
		emails = {row[0] for row in rows}
		self.assertIn(CARLA, emails)
		self.assertNotIn(STELLA, emails)  # staff hold no CBT Customer role
		for email in emails:
			self.assertTrue(
				frappe.db.exists(
					"Has Role",
					{"parent": email, "role": "CBT Customer", "parenttype": "User"},
				),
				email,
			)
