"""
Court Booking Tech — Billing Document Tests (section-6)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_billing

VAT-INCLUSIVE math (pure function), per-company INV numbering, the
create-on-insert + derived-status lifecycle across EVERY flip path (confirm,
accept-proofs, retro-confirm, sweep expiry, rejection expiry, cancel), the
creation-time VAT snapshot, the or_number-only edit guard, and the print
format's PAID & VERIFIED / UNPAID / CANCELLED / NON-VAT rendering.
Clock is monkeypatched — never wall-clock.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech.api.bookings import (
	cancel_booking,
	confirm_booking,
	reschedule_booking,
)
from court_booking_tech.api.proofs import accept_proofs, create_proof, reject_proofs
from court_booking_tech.billing import PAID, compute_vat_breakdown
from court_booking_tech.timeutil import label_date, label_short
from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL, PLATFORM_ADMIN_EMAIL, seed_all
from court_booking_tech.tasks import expire_reservations

AYALA = "ayala-courts"
QCSM = "qc-smash"
STELLA = "staff.ayala@example.com"

# A Friday no other test file claims (booking/verification use 2027-02-05/06/12,
# seeds use 2027-01-15/16).
TEST_DATE = "2027-02-19"
T0 = datetime(2027, 2, 19, 8, 0)

COURT_A = "AYALA-makati-court-a"  # rate 300, makati hours 06:00-22:00
COURT_B = "AYALA-makati-court-b"  # rate 300 — print-test fixtures
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"

PROOF_JPG = Path(__file__).resolve().parents[1] / "seeds" / "files" / "proof_sample.jpg"


class TestBilling(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _book(self, court, start_time, payment_method="Cash", slots=1):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": court,
				"customer": CUSTOMER_EMAIL,
				"booking_date": TEST_DATE,
				"start_time": start_time,
				"number_of_slots": slots,
				"payment_method": payment_method,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def _invoice(self, booking):
		name = frappe.db.get_value("CBT Court Booking", booking.name, "billing_doc")
		self.assertTrue(name, f"booking {booking.name} has no invoice")
		return frappe.get_doc("CBT Booking Invoice", name)

	def _upload_proof(self, booking):
		return create_proof(
			booking.name, "proof_sample.jpg", PROOF_JPG.read_bytes()
		)

	# --- VAT math (pure function) ----------------------------------------

	def test_vat_breakdown_basic(self):
		result = compute_vat_breakdown(1120, "VAT", 12)
		self.assertEqual(result["vatable_amount"], 1000.00)
		self.assertEqual(result["vat_amount"], 120.00)

	def test_vat_breakdown_rounding_exact_sum(self):
		# Odd totals must never drift by a centavo: vat is the DIFFERENCE.
		for total in (333.33, 999.99, 450.00, 1.01, 12345.67):
			result = compute_vat_breakdown(total, "VAT", 12)
			self.assertEqual(
				flt(result["vatable_amount"] + result["vat_amount"], 2),
				flt(total, 2),
				f"drift at {total}",
			)
		result = compute_vat_breakdown(333.33, "VAT", 12)
		self.assertEqual(result["vatable_amount"], 297.62)
		self.assertEqual(result["vat_amount"], 35.71)

	def test_vat_breakdown_non_vat_nulls(self):
		result = compute_vat_breakdown(1120, "NON-VAT", 12)
		self.assertIsNone(result["vatable_amount"])
		self.assertIsNone(result["vat_amount"])

	# --- creation & linkage ----------------------------------------------

	def test_invoice_created_and_linked_unpaid(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "06:00:00", "Fund Transfer")
		invoice = self._invoice(booking)
		self.assertEqual(invoice.booking, booking.name)
		self.assertEqual(invoice.status, "Unpaid")
		self.assertEqual(invoice.company, AYALA)
		self.assertEqual(invoice.customer, CUSTOMER_EMAIL)
		# Snapshot: AYALA is VAT / 12% / default title.
		self.assertEqual(invoice.vat_mode, "VAT")
		self.assertEqual(flt(invoice.vat_percent), 12)
		self.assertEqual(invoice.doc_title, "BILLING STATEMENT")
		# One rental line: qty = billable hours, rate = court rate.
		self.assertEqual(len(invoice.items), 1)
		self.assertIn("Court A", invoice.items[0].description)
		self.assertEqual(flt(invoice.items[0].qty), 1)
		self.assertEqual(flt(invoice.items[0].rate), 300)
		self.assertEqual(flt(invoice.subtotal), 300)
		self.assertEqual(flt(invoice.total_amount), 300)
		self.assertEqual(flt(invoice.vatable_amount), flt(300 / 1.12, 2))
		self.assertEqual(
			flt(invoice.vatable_amount + invoice.vat_amount, 2), 300.00
		)

	def test_cash_booking_invoice_paid_immediately(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "07:00:00", "Cash")
		invoice = self._invoice(booking)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, booking.confirmed_by)
		self.assertEqual(str(invoice.verified_at), str(booking.confirmed_at))

	# --- numbering --------------------------------------------------------

	def test_per_company_series_and_sequence(self):
		with patch(CLOCK, return_value=T0):
			first = self._invoice(self._book(COURT_A, "08:00:00"))
			second = self._invoice(self._book(COURT_A, "09:00:00"))
			other = self._invoice(self._book(QCSM_COURT, "06:00:00"))
		self.assertRegex(first.name, r"^INV-AYALA-\d{4}-\d{5}$")
		self.assertRegex(other.name, r"^INV-QCSM-\d{4}-\d{5}$")
		self.assertEqual(int(second.name[-5:]), int(first.name[-5:]) + 1)

	def test_cancel_consumes_number_rebook_gets_next(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "10:00:00")
			first = self._invoice(booking)
			cancel_booking(booking.name, reason="test: consumes the number")
			rebooked = self._book(COURT_A, "10:00:00")
		second = self._invoice(rebooked)
		# Cancelled document RETAINED, number consumed; reissue = next number.
		self.assertEqual(int(second.name[-5:]), int(first.name[-5:]) + 1)
		first.reload()
		self.assertEqual(first.status, "Cancelled")
		self.assertEqual(
			frappe.db.count("CBT Booking Invoice", {"booking": booking.name}), 1
		)

	# --- lifecycle flips (every path) ------------------------------------

	def test_confirm_flips_invoice_paid(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "11:00:00", "Fund Transfer")
			self.assertEqual(self._invoice(booking).status, "Unpaid")
			confirm_booking(booking.name)
		invoice = self._invoice(booking)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, "Administrator")
		self.assertTrue(invoice.verified_at)

	def test_accept_proofs_flips_invoice_paid(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "12:00:00", "Fund Transfer")
			self._upload_proof(booking)
			accept_proofs(booking.name)
		invoice = self._invoice(booking)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, "Administrator")

	def test_reject_fake_cancels_invoice(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "13:00:00", "Fund Transfer")
			self._upload_proof(booking)
			result = reject_proofs(booking.name, "Invalid / suspected fake")
		self.assertEqual(result["outcome"], "Expired")
		self.assertEqual(self._invoice(booking).status, "Cancelled")

	def test_sweep_base_expiry_cancels_invoice(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "14:00:00", "Fund Transfer")
		with patch(CLOCK, return_value=datetime(2027, 2, 19, 8, 45)):
			result = expire_reservations()
		self.assertIn(booking.name, result["expired"])
		self.assertEqual(self._invoice(booking).status, "Cancelled")

	def test_retro_confirm_flips_cancelled_invoice_back_to_paid(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "15:00:00", "Fund Transfer")
			self._upload_proof(booking)  # AYALA office 09:00-18:00 → deadline 13:00
		with patch(CLOCK, return_value=datetime(2027, 2, 19, 13, 30)):
			result = expire_reservations()
			self.assertIn(booking.name, result["expired"])
			self.assertEqual(self._invoice(booking).status, "Cancelled")
			# Money really arrived; finance confirmed later (PLAN §5a): the
			# SAME document flips back — number retained, nothing reissued.
			confirm_booking(booking.name)
		booking.reload()
		self.assertEqual(booking.booking_status, "Completed")
		invoice = self._invoice(booking)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.verified_by, "Administrator")

	def test_cancel_booking_cancels_invoice(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "16:00:00", "Cash")
			cancel_booking(booking.name, reason="test: cancels the invoice")
		self.assertEqual(self._invoice(booking).status, "Cancelled")

	# --- amount resync & snapshot immunity -------------------------------

	def test_rate_edit_resyncs_invoice_amounts(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "17:00:00", "Cash")
			booking.hourly_rate = 500
			booking.save(ignore_permissions=True)
		invoice = self._invoice(booking)
		self.assertEqual(flt(invoice.items[0].rate), 500)
		self.assertEqual(flt(invoice.subtotal), 500)
		self.assertEqual(flt(invoice.total_amount), 500)
		self.assertEqual(flt(invoice.vatable_amount), flt(500 / 1.12, 2))

	def test_company_vat_change_never_rewrites_history(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "18:00:00", "Cash")
		frappe.db.set_value("CBT Company", AYALA, "vat_registration", "NON-VAT")
		frappe.clear_document_cache("CBT Company", AYALA)
		self.addCleanup(
			frappe.db.set_value, "CBT Company", AYALA, "vat_registration", "VAT"
		)
		self.addCleanup(frappe.clear_document_cache, "CBT Company", AYALA)
		# Trigger a resync — the SNAPSHOT fields must survive it.
		with patch(CLOCK, return_value=T0):
			booking.reload()
			booking.hourly_rate = 320
			booking.save(ignore_permissions=True)
		invoice = self._invoice(booking)
		self.assertEqual(invoice.vat_mode, "VAT")
		self.assertEqual(flt(invoice.vat_percent), 12)
		self.assertEqual(flt(invoice.vatable_amount), flt(320 / 1.12, 2))

	# --- guards -----------------------------------------------------------

	def test_manual_creation_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be created by hand"):
			frappe.get_doc(
				{
					"doctype": "CBT Booking Invoice",
					"company": AYALA,
					"customer": CUSTOMER_EMAIL,
				}
			).insert(ignore_permissions=True)

	def test_edits_beyond_or_number_rejected(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "19:00:00", "Cash")
		invoice = self._invoice(booking)
		invoice.total_amount = 999
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be edited"):
			invoice.save(ignore_permissions=True)

	def test_staff_records_or_number(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "20:00:00", "Cash")
		frappe.set_user(STELLA)
		invoice = frappe.get_doc("CBT Booking Invoice", booking.billing_doc)
		invoice.or_number = "OR-STAFF-7"
		invoice.save()
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", invoice.name, "or_number"),
			"OR-STAFF-7",
		)

	def test_staff_cannot_delete_invoice(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_A, "21:00:00", "Cash")
		frappe.set_user(STELLA)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("CBT Booking Invoice", booking.billing_doc)

	# --- print format -----------------------------------------------------
	# Own fixtures on COURT_B, NOT the seeded 2027-01-15 cast: sweep-running
	# tests in this class legitimately expire the seeded Reserved holds
	# (their fixed expiries are past any patched clock), and FrappeTestCase
	# only rolls back at CLASS teardown — the seeds contract is asserted in
	# test_seed_data.py instead, where nothing ever runs a sweep.

	def _print(self, invoice_name):
		return frappe.get_print(
			"CBT Booking Invoice", invoice_name, print_format="CBT Billing Statement"
		)

	def test_print_paid_vat(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_B, "06:00:00", "Cash")
		frappe.db.set_value(
			"CBT Booking Invoice", booking.billing_doc, "or_number", "OR-7777"
		)
		html = self._print(booking.billing_doc)
		self.assertIn("PAID &amp; VERIFIED", html)
		self.assertNotIn('class="cbt-bs-watermark"', html)
		self.assertIn("BILLING STATEMENT", html)
		self.assertIn("Ayala Courts Sports Corp.", html)
		self.assertIn("VATable Sales", html)
		self.assertIn("267.86", html)  # 300 / 1.12
		self.assertIn("32.14", html)  # exact-difference VAT
		self.assertIn("OR-7777", html)
		self.assertIn("official invoice/receipt issued manually by", html)

	def test_print_unpaid_vat(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_B, "07:00:00", "Fund Transfer")
		html = self._print(booking.billing_doc)
		self.assertIn("UNPAID", html)
		self.assertNotIn("PAID &amp; VERIFIED", html)
		self.assertIn("VATable Sales", html)

	def test_print_non_vat(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(QCSM_COURT, "07:00:00", "Cash")
		html = self._print(booking.billing_doc)
		self.assertIn("NON-VAT", html)
		self.assertNotIn("VATable Sales", html)
		self.assertIn("QC Smash Badminton Center Inc.", html)
		self.assertIn("PAID &amp; VERIFIED", html)

	def test_print_cancelled_watermark(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_B, "08:00:00", "Cash")
			cancel_booking(booking.name, reason="test: watermark")
		html = self._print(booking.billing_doc)
		self.assertIn("CANCELLED", html)
		self.assertNotIn("PAID &amp; VERIFIED", html)

	# --- the DEFAULT print path (Backlog B23) ------------------------------
	# Every other print test names the format. The desk print page
	# (/desk/print/<dt>/<name>) and the form's own Print action name NOTHING:
	# frappe resolves `meta.default_print_format` and falls back to the generic
	# "Standard" layout (printview.get_print_format_doc). Reproduced 2026-08-27:
	# with no default the desk page rendered field labels ("Customer Name:",
	# "Discount (%): 0.0%") instead of the statement. The markers below are
	# Jinja-only — the invoice's own `doc_title` field VALUE is the string
	# "BILLING STATEMENT", so that string is NOT a discriminator.

	def test_print_without_naming_a_format_renders_the_billing_statement(self):
		with patch(CLOCK, return_value=T0):
			booking = self._book(COURT_B, "09:00:00", "Cash")
		html = frappe.get_print("CBT Booking Invoice", booking.billing_doc)
		self.assertIn('class="cbt-bs', html)
		self.assertIn("official invoice/receipt issued manually by", html)
		self.assertIn("Ayala Courts Sports Corp.", html)
		self.assertIn("PAID &amp; VERIFIED", html)
		# The Standard layout's label style must be gone, not merely joined.
		self.assertNotIn("Customer Name:", html)

	def test_default_print_format_is_the_billing_statement(self):
		"""Pinned on RUNTIME meta, not the JSON text: a Property Setter or a
		Customize Form save defeats a JSON-level pin silently (the B17 lesson)."""
		self.assertEqual(
			frappe.get_meta("CBT Booking Invoice").default_print_format,
			"CBT Billing Statement",
		)


class TestWalkInBilling(FrappeTestCase):
	"""Section-13: the walk-in's whole point is an HONEST receipt — the printed
	statement names the actual person, with no fake User row behind it."""

	# October 2027 = section-13's backend month (September is test_reports'
	# integer-claimed empty-control month — see seeds WALKIN_DATE).
	WALK_DATE = "2027-10-08"  # Friday
	WALK_T0 = datetime(2027, 10, 8, 8, 0)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _walkin(self, court, start_time, payment_method="Cash", **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer_name": "Walk-in Winnie",
			"customer_phone": "0917-222-3333",
			"booking_date": self.WALK_DATE,
			"start_time": start_time,
			"number_of_slots": 1,
			"payment_method": payment_method,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		with patch(CLOCK, return_value=self.WALK_T0):
			doc.insert(ignore_permissions=True)
		self.addCleanup(self._purge, doc.name)
		return doc

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

	def _print(self, invoice_name):
		return frappe.get_print(
			"CBT Booking Invoice", invoice_name, print_format="CBT Billing Statement"
		)

	def test_walk_in_statement_prints_the_typed_name(self):
		booking = self._walkin(COURT_A, "10:00:00")
		html = self._print(booking.billing_doc)
		self.assertIn("Walk-in Winnie", html)
		self.assertIn("PAID &amp; VERIFIED", html)
		# The receipt must not fall back to a blank or a literal "None".
		self.assertNotIn("Customer:</strong> None", html)

	def test_typo_fix_reaches_the_statement(self):
		"""A name typed at a busy desk gets corrected; the print must follow
		(S6 doctrine: the print never shows stale data)."""
		booking = self._walkin(COURT_A, "11:00:00")
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", booking.billing_doc, "customer_name"),
			"Walk-in Winnie",
		)
		booking.reload()
		booking.customer_name = "Walk-in Winifred"
		booking.save()
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", booking.billing_doc, "customer_name"),
			"Walk-in Winifred",
		)
		self.assertIn("Walk-in Winifred", self._print(booking.billing_doc))

	def test_account_name_on_an_issued_document_is_a_snapshot(self):
		"""The other half of the same rule, and the reason the identity sync is
		gated on `not booking.customer`: a customer who renames their ACCOUNT
		must not rewrite the printed name on an already-issued, PAID document.
		"""
		with patch(CLOCK, return_value=self.WALK_T0):
			booking = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": COURT_B,
					"customer": CUSTOMER_EMAIL,
					"booking_date": self.WALK_DATE,
					"start_time": "10:00:00",
					"number_of_slots": 1,
					"payment_method": "Cash",
				}
			).insert(ignore_permissions=True)
		self.addCleanup(self._purge, booking.name)
		issued_name = frappe.db.get_value(
			"CBT Booking Invoice", booking.billing_doc, "customer_name"
		)
		self.assertEqual(issued_name, "Carla Courtside")

		user = frappe.get_doc("User", CUSTOMER_EMAIL)
		original_last = user.last_name
		user.last_name = "Renamed"
		user.save(ignore_permissions=True)

		def _restore():
			frappe.set_user("Administrator")
			restored = frappe.get_doc("User", CUSTOMER_EMAIL)
			restored.last_name = original_last
			restored.save(ignore_permissions=True)

		self.addCleanup(_restore)

		booking.reload()
		booking.notes = "any later edit re-syncs the invoice"
		booking.save()

		# The BOOKING re-derives (it is a live record)...
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", booking.name, "customer_name"),
			"Carla Renamed",
		)
		# ...the ISSUED DOCUMENT does not.
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", booking.billing_doc, "customer_name"),
			issued_name,
		)

	def test_walk_in_invoice_carries_no_customer_link(self):
		booking = self._walkin(COURT_B, "11:00:00")
		invoice = frappe.get_doc("CBT Booking Invoice", booking.billing_doc)
		self.assertFalse(invoice.customer)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(flt(invoice.total_amount), 300.0)

class TestCartBilling(FrappeTestCase):
	"""Backlog B36 — ONE billing document per cart, and the labels it prints."""

	CART_DATE = "2027-02-19"
	NEXT_DATE = "2027-02-20"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _book(self, court, start_time, payment_method="Cash", group=None, day=None):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": court,
				"customer": CUSTOMER_EMAIL,
				"booking_date": day or self.CART_DATE,
				"start_time": start_time,
				"number_of_slots": 1,
				"payment_method": payment_method,
				"booking_group": group,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def _cart(self, rows, group, payment_method="Cash"):
		"""Rows are (court, start[, date]) — inserted ONE AT A TIME, the way
		api/portal.reserve_cart does."""
		return [
			self._book(
				row[0],
				row[1],
				payment_method=payment_method,
				group=group,
				day=row[2] if len(row) > 2 else None,
			)
			for row in rows
		]

	def _grouped(self, group):
		return frappe.get_doc(
			"CBT Booking Invoice",
			frappe.db.get_value("CBT Booking Invoice", {"booking_group": group}, "name"),
		)

	# --- statement time + date labels --------------------------------------

	def test_statement_labels_match_the_booking_grid(self):
		"""One convention, two surfaces: labelShort in www/cbt-book.html and
		this. A 24:00 end is midnight, not noon."""
		self.assertEqual(label_short("13:00:00"), "1 PM")
		self.assertEqual(label_short("06:30:00"), "6:30 AM")
		self.assertEqual(label_short("00:00:00"), "12 MN")
		self.assertEqual(label_short("24:00:00"), "12 MN")
		self.assertEqual(label_short("12:00:00"), "12 NN")
		self.assertEqual(label_short("12:30:00"), "12:30 PM")
		self.assertEqual(label_date("2026-09-03"), "Sep-03-2026")

	# --- one document per cart (Backlog B36) ------------------------------

	def test_a_cart_bills_one_document_for_every_row(self):
		with patch(CLOCK, return_value=T0):
			rows = self._cart(
				[(COURT_A, "16:00:00"), (COURT_B, "16:00:00")], "CART-B36-A"
			)
		links = {row.billing_doc for row in rows}
		self.assertEqual(len(links), 1, f"a cart must bill ONE document: {links}")
		invoice = frappe.get_doc("CBT Booking Invoice", links.pop())
		self.assertEqual(invoice.booking_group, "CART-B36-A")
		self.assertEqual(len(invoice.items), 2)
		self.assertEqual(flt(invoice.total_amount), 600.0)
		self.assertEqual(flt(invoice.subtotal), 600.0)

	def test_a_single_booking_is_still_one_document_of_its_own(self):
		with patch(CLOCK, return_value=T0):
			first = self._book(COURT_A, "17:00:00")
			second = self._book(COURT_B, "17:00:00")
		self.assertNotEqual(first.billing_doc, second.billing_doc)
		self.assertFalse(
			frappe.db.get_value("CBT Booking Invoice", first.billing_doc, "booking_group")
		)

	def test_group_lines_are_ordered_by_date_then_court_then_start(self):
		with patch(CLOCK, return_value=T0):
			self._cart(
				[
					(COURT_B, "19:00:00"),
					(COURT_A, "20:00:00"),
					(COURT_A, "18:00:00", "2027-02-20"),
				],
				"CART-B36-ORDER",
			)
		invoice = frappe.get_doc(
			"CBT Booking Invoice",
			frappe.db.get_value(
				"CBT Booking Invoice", {"booking_group": "CART-B36-ORDER"}, "name"
			),
		)
		self.assertEqual(len(invoice.items), 3)
		# 19 Feb Court A 8 PM, 19 Feb Court B 7 PM, 20 Feb Court A 6 PM.
		self.assertIn("Feb-19-2027", invoice.items[0].description)
		self.assertIn("Court A", invoice.items[0].description)
		self.assertIn("8 PM", invoice.items[0].description)
		self.assertIn("Court B", invoice.items[1].description)
		self.assertIn("Feb-20-2027", invoice.items[2].description)

	def test_vat_is_computed_once_on_the_summed_court_share(self):
		"""The user's ruling, and the centavo that proves it was obeyed: two
		₱300 rows summed give 535.71, while VAT taken per row and added would
		give 535.72. The row's claim that the rule is linear is false."""
		with patch(CLOCK, return_value=T0):
			self._cart(
				[(COURT_A, "13:00:00"), (COURT_B, "13:00:00")], "CART-B36-VAT"
			)
		invoice = frappe.get_doc(
			"CBT Booking Invoice",
			frappe.db.get_value(
				"CBT Booking Invoice", {"booking_group": "CART-B36-VAT"}, "name"
			),
		)
		self.assertEqual(flt(invoice.total_amount), 600.0)
		self.assertEqual(flt(invoice.vatable_amount), 535.71)
		self.assertEqual(flt(invoice.vat_amount), 64.29)
		self.assertNotEqual(flt(invoice.vatable_amount), 535.72)
		self.assertEqual(
			flt(invoice.vatable_amount + invoice.vat_amount, 2), 600.00
		)

	def test_refunding_one_row_carves_it_onto_its_own_document(self):
		with patch(CLOCK, return_value=T0):
			rows = self._cart(
				[(COURT_A, "14:00:00"), (COURT_B, "14:00:00")], "CART-B36-REFUND"
			)
			group_invoice = rows[0].billing_doc
			self.assertEqual(
				frappe.db.get_value("CBT Booking Invoice", group_invoice, "status"), PAID
			)
			cancel_booking(rows[1].name, reason="customer could not make it")

		carved_name = frappe.db.get_value(
			"CBT Court Booking", rows[1].name, "billing_doc"
		)
		self.assertNotEqual(carved_name, group_invoice)
		carved = frappe.get_doc("CBT Booking Invoice", carved_name)
		self.assertEqual(carved.status, "Cancelled")
		self.assertEqual(carved.refund_reason, "customer could not make it")
		self.assertEqual(flt(carved.total_amount), 300.0)
		self.assertFalse(carved.booking_group)
		# The cart's own document keeps its NUMBER, drops the line, stays paid.
		survivor = frappe.get_doc("CBT Booking Invoice", group_invoice)
		self.assertEqual(survivor.status, PAID)
		self.assertEqual(len(survivor.items), 1)
		self.assertEqual(flt(survivor.total_amount), 300.0)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", rows[0].name, "billing_doc"),
			group_invoice,
		)

	def test_an_unpaid_row_cancelled_just_shrinks_the_cart_document(self):
		with patch(CLOCK, return_value=T0):
			rows = self._cart(
				[(COURT_A, "15:00:00"), (COURT_B, "15:00:00")],
				"CART-B36-SHRINK",
				payment_method="Fund Transfer",
			)
			before = frappe.db.count("CBT Booking Invoice", {"booking_group": "CART-B36-SHRINK"})
			cancel_booking(rows[1].name)
		self.assertEqual(
			frappe.db.count("CBT Booking Invoice", {"booking_group": "CART-B36-SHRINK"}),
			before,
			"an unpaid cancel must mint no second document",
		)
		invoice = frappe.get_doc("CBT Booking Invoice", rows[0].billing_doc)
		self.assertEqual(invoice.status, "Unpaid")
		self.assertEqual(len(invoice.items), 1)
		self.assertEqual(flt(invoice.total_amount), 300.0)

	def test_moving_one_row_of_a_paid_cart_mints_no_refund_document(self):
		"""`booking_group` is NOT in reschedule_booking's copy list, so a moved
		row leaves the cart. That cancel must NOT read as money out — the
		carve-off is gated on the refund reason, never on leaving."""
		with patch(CLOCK, return_value=T0):
			rows = self._cart(
				[(COURT_A, "09:00:00"), (COURT_B, "09:00:00")], "CART-B36-MOVE"
			)
			group_invoice = rows[0].billing_doc
			moved = reschedule_booking(rows[1].name, start_time="10:00:00")

		new_invoice = frappe.db.get_value(
			"CBT Court Booking", moved["name"], "billing_doc"
		)
		self.assertNotEqual(new_invoice, group_invoice)
		fresh = frappe.get_doc("CBT Booking Invoice", new_invoice)
		self.assertEqual(fresh.status, PAID)
		self.assertFalse(fresh.booking_group, "a moved row leaves the cart")
		self.assertIsNone(fresh.refund_reason)
		# The original's document is the cart's own, shrunk — not a refund.
		original_doc = frappe.get_doc(
			"CBT Booking Invoice",
			frappe.db.get_value("CBT Court Booking", rows[1].name, "billing_doc"),
		)
		self.assertEqual(original_doc.name, group_invoice)
		self.assertIsNone(original_doc.refund_reason)
		self.assertEqual(original_doc.status, PAID)
		self.assertEqual(len(original_doc.items), 1)
		self.assertEqual(flt(original_doc.total_amount), 300.0)

	def test_an_expired_cart_still_prints_every_row(self):
		"""The sweep expires a cart one row at a time. Dropping each in turn
		would leave the last survivor holding the whole statement — a ₱600 cart
		printing ₱300."""
		with patch(CLOCK, return_value=T0):
			rows = self._cart(
				[(COURT_A, "21:00:00"), (COURT_B, "21:00:00")],
				"CART-B36-EXPIRY",
				payment_method="Fund Transfer",
			)
		with patch(CLOCK, return_value=datetime(2027, 2, 19, 8, 45)):
			result = expire_reservations()
		for row in rows:
			self.assertIn(row.name, result["expired"])
		invoice = frappe.get_doc("CBT Booking Invoice", rows[0].billing_doc)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(len(invoice.items), 2)
		self.assertEqual(flt(invoice.total_amount), 600.0)
