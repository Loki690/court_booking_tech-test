"""
Court Booking Tech — Refunds are a Company Admin's decision (section-26, 2026-08-27)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_refunds

THE RULING. Cancelling a PAID document is money OUT — a refund — so it is the
Company Admin's action (platform scope passes), never the front desk's, and it
carries a REASON for every seat, platform included. An unpaid booking stays the
desk's to cancel. Open play: cancelling a session's billing while any player is
paid is the same refund; removing one player never refunds (untouched).
A refund out of a CLOSED month is allowed: it books on the day it happened
(`cancelled_at`) and the statement already issued for that month stays as billed.

MONTH. **November 2028** — banked free by the ledger (Sep/Oct 2028 belong to
test_payment_channels, May/Jun 2028 to test_booking_fee). 2028-11-08 is a
Wednesday; AYALA-makati (court-a ₱300 flat) and AYALA-bgc are open every day.
AYALA is VAT 12%, Percentage 10% — nothing here changes a billing mode.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech import billing
from court_booking_tech.api.bookings import cancel_booking
from court_booking_tech.api.open_play import add_players, cancel_session, open_session
from court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close import (
	close_month,
)
from court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement import (
	issue_statements,
)
from court_booking_tech.court_booking_tech.report.cbt_collections_by_channel import (
	cbt_collections_by_channel,
)
from court_booking_tech.court_booking_tech.report.cbt_tenant_ledger import cbt_tenant_ledger
from court_booking_tech.seeds.seed_test_data import (
	CUSTOMER_EMAIL,
	OPEN_PLAY_CUSTOMERS,
	PLATFORM_ADMIN_EMAIL,
	seed_all,
)

AYALA = "ayala-courts"
STELLA = "staff.ayala@example.com"  # Company Staff
ALONA = "admin.ayala@example.com"  # Company Admin

MAKATI_A = "AYALA-makati-court-a"  # ₱300 flat
RATE = 300.0

DAY = "2028-11-08"  # Wednesday
T0 = datetime(2028, 11, 8, 8, 0)
T_CANCEL = datetime(2028, 11, 9, 9, 0)
CANCEL_DAY = "2028-11-09"
AFTER_NOV = datetime(2028, 12, 3, 10, 0)
T_LATE_REFUND = datetime(2028, 12, 10, 11, 0)
LATE_REFUND_DAY = "2028-12-10"
PERIOD = "2028-11"

CLOCK = "court_booking_tech.clock.now_dt"
PLAYERS = [row[0] for row in OPEN_PLAY_CUSTOMERS]

POLICY_FIELDS = ("refund_policy", "facility_fault_cancel_by")

ADMIN_ONLY = "Only a Company Admin can cancel a paid booking"
SESSION_ADMIN_ONLY = "Only a Company Admin can cancel a session with paid players"
REASON_REQUIRED = "A reason is required"


class RefundTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- fixtures -----------------------------------------------------------

	def _book(self, start_time="10:00:00", method="Cash", court=MAKATI_A, day=DAY):
		"""Cash → Confirmed (PAID) at insert; Fund Transfer → Reserved (Unpaid)."""
		with patch(CLOCK, return_value=T0):
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": court,
					"customer": CUSTOMER_EMAIL,
					"booking_date": day,
					"start_time": start_time,
					"number_of_slots": 1,
					"payment_method": method,
				}
			)
			doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop_booking, doc.name)
		return doc

	def _drop_booking(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc("CBT Court Booking", name, force=True, ignore_permissions=True, ignore_missing=True)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True, ignore_missing=True
			)

	def _status(self, name):
		return frappe.db.get_value("CBT Court Booking", name, "booking_status")

	def _invoice(self, booking_name):
		return frappe.get_doc(
			"CBT Booking Invoice", frappe.db.get_value("CBT Court Booking", booking_name, "billing_doc")
		)

	def _session(self):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "AYALA-bgc",
				"title": "Refund Open Play",
				"session_date": DAY,
				"start_time": "09:00:00",
				"end_time": "12:00:00",
				"court_type": "Pickleball",
				"rotation_mode": "Timed",
				"rotation_minutes": 15,
				"entry_fee": 150,
				"courts": [{"court": "AYALA-bgc-court-1"}, {"court": "AYALA-bgc-court-2"}],
			}
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop_session, doc.name)
		open_session(doc.name)
		return frappe.get_doc("CBT Open Play Session", doc.name)

	def _drop_session(self, name):
		frappe.set_user("Administrator")
		for block in frappe.get_all("CBT Slot Block", filters={"open_play_session": name}, pluck="name"):
			frappe.delete_doc("CBT Slot Block", block, force=True, ignore_permissions=True)
		invoices = [
			row.billing_doc
			for row in frappe.get_all(
				"CBT Open Play Participant", filters={"parent": name}, fields=["billing_doc"]
			)
			if row.billing_doc
		]
		frappe.delete_doc("CBT Open Play Session", name, force=True, ignore_permissions=True, ignore_missing=True)
		for invoice in invoices:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True, ignore_missing=True
			)

	def _add(self, session, emails, method="Cash"):
		add_players(
			session,
			frappe.as_json([{"customer": e, "payment_method": method} for e in emails]),
		)
		return frappe.get_doc("CBT Open Play Session", session)


class TestRefundGate(RefundTestCase):
	def test_staff_still_cancels_an_unpaid_booking_with_or_without_a_reason(self):
		held = self._book("10:00:00", method="Fund Transfer")
		held_too = self._book("11:00:00", method="Fund Transfer")
		self.assertEqual(self._status(held.name), "Reserved")
		frappe.set_user(STELLA)
		cancel_booking(held.name)  # nothing was paid: no gate, no reason needed
		cancel_booking(held_too.name, reason="customer changed their mind")  # a reason does no harm
		self.assertEqual(self._status(held.name), "Cancelled")
		self.assertEqual(self._status(held_too.name), "Cancelled")
		self.assertIsNone(self._invoice(held_too.name).refund_reason)  # not a refund

	def test_staff_may_not_cancel_a_paid_booking(self):
		paid = self._book("12:00:00")
		self.assertEqual(self._status(paid.name), "Confirmed")
		frappe.set_user(STELLA)
		with self.assertRaisesRegex(frappe.PermissionError, ADMIN_ONLY):
			cancel_booking(paid.name, reason="a reason does not make it hers")
		self.assertEqual(self._status(paid.name), "Confirmed")
		self.assertEqual(self._invoice(paid.name).status, "Paid & Verified")

	def test_admin_needs_a_reason(self):
		paid = self._book("13:00:00")
		frappe.set_user(ALONA)
		with self.assertRaisesRegex(frappe.ValidationError, REASON_REQUIRED):
			cancel_booking(paid.name)
		with self.assertRaisesRegex(frappe.ValidationError, REASON_REQUIRED):
			cancel_booking(paid.name, reason="   ")
		self.assertEqual(self._status(paid.name), "Confirmed")

	def test_admin_refunds_with_a_reason_and_every_paper_reads_it(self):
		paid = self._book("14:00:00")
		before = self._invoice(paid.name)
		self.assertTrue(before.verified_at)

		frappe.set_user(ALONA)  # the seat under test — NOT Administrator (ducky)
		with patch(CLOCK, return_value=T_CANCEL):
			cancel_booking(paid.name, reason="Court flooded, session could not be held")
		self.assertEqual(self._status(paid.name), "Cancelled")

		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.refund_reason, "Court flooded, session could not be held")
		self.assertEqual(invoice.refunded_by, ALONA)
		self.assertEqual(invoice.cancelled_at, T_CANCEL)
		self.assertEqual(invoice.verified_at, before.verified_at)  # the audit trail survives

		# The Tenant Ledger's reversal line carries the reason.
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": AYALA, "from_date": CANCEL_DAY, "to_date": CANCEL_DAY, "books": "Tenant"}
		)
		reversal = [r for r in rows if r.get("voucher") == invoice.name and r.get("description")]
		self.assertEqual(len(reversal), 1, rows)
		self.assertIn("Refund / reversal", reversal[0]["description"])
		self.assertIn("Court flooded, session could not be held", reversal[0]["description"])
		self.assertTrue(reversal[0]["is_reversal"])

		# And so does the printed statement: who, when, why.
		html = frappe.get_print("CBT Booking Invoice", invoice.name, "CBT Billing Statement")
		self.assertIn("CANCELLED", html)
		self.assertIn("refunded", html)
		self.assertIn("Alona AyalaAdmin", html)
		self.assertIn("Court flooded, session could not be held", html)
		self.assertNotIn("PAID &amp; VERIFIED", html)

	def test_the_platform_seat_may_refund_but_also_needs_a_reason(self):
		paid = self._book("15:00:00")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with self.assertRaisesRegex(frappe.ValidationError, REASON_REQUIRED):
			cancel_booking(paid.name)
		cancel_booking(paid.name, reason="platform: duplicate booking")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.refund_reason, "platform: duplicate booking")
		self.assertEqual(invoice.refunded_by, PLATFORM_ADMIN_EMAIL)

	def test_a_released_no_show_is_still_paid_money_so_the_gate_applies(self):
		"""Cancelling a No Show writes off money already recognised (section-16)
		— a refund by any other name. The release itself stays the desk's
		(test_no_show proves that path); this arranges the state directly."""
		paid = self._book("16:00:00")
		frappe.db.set_value("CBT Court Booking", paid.name, "booking_status", "No Show")
		self.assertEqual(self._invoice(paid.name).status, "Paid & Verified")
		frappe.set_user(STELLA)
		with self.assertRaisesRegex(frappe.PermissionError, ADMIN_ONLY):
			cancel_booking(paid.name, reason="housekeeping")
		frappe.set_user(ALONA)
		cancel_booking(paid.name, reason="released in error, customer refunded")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.refund_reason, "released in error, customer refunded")

	def test_a_document_paid_again_drops_the_old_refund_stamps(self):
		"""The invoice controller's rule for cancelled_at, extended: a document
		that leaves Cancelled (the Expired → Completed retro-confirm shape)
		carries no stale reason or seat."""
		paid = self._book("17:00:00")
		frappe.set_user(ALONA)
		cancel_booking(paid.name, reason="temporary")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.refund_reason, "temporary")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		invoice.status = "Paid & Verified"
		invoice.flags.via_billing_engine = True
		invoice.save(ignore_permissions=True)
		invoice.reload()
		self.assertIsNone(invoice.cancelled_at)
		self.assertIsNone(invoice.refund_reason)
		self.assertIsNone(invoice.refunded_by)


class TestSessionRefund(RefundTestCase):
	def test_cancelling_paid_players_billing_is_admin_only_with_a_reason(self):
		doc = self._session()
		doc = self._add(doc.name, [PLAYERS[0]], method="Cash")  # Paid
		doc = self._add(doc.name, [PLAYERS[1]], method="Fund Transfer")  # Unpaid
		paid_invoice, unpaid_invoice = (row.billing_doc for row in doc.participants)
		self.assertEqual(frappe.db.get_value("CBT Booking Invoice", paid_invoice, "status"), "Paid & Verified")

		frappe.set_user(STELLA)
		with self.assertRaisesRegex(frappe.PermissionError, SESSION_ADMIN_ONLY):
			cancel_session(doc.name, cancel_billing=1, reason="rained out")
		self.assertEqual(frappe.db.get_value("CBT Open Play Session", doc.name, "status"), "Open")

		frappe.set_user(ALONA)
		with self.assertRaisesRegex(frappe.ValidationError, REASON_REQUIRED):
			cancel_session(doc.name, cancel_billing=1)
		result = cancel_session(doc.name, cancel_billing=1, reason="rained out")
		self.assertEqual(set(result["invoices_cancelled"]), {paid_invoice, unpaid_invoice})
		paid_doc = frappe.get_doc("CBT Booking Invoice", paid_invoice)
		unpaid_doc = frappe.get_doc("CBT Booking Invoice", unpaid_invoice)
		self.assertEqual((paid_doc.status, paid_doc.refund_reason, paid_doc.refunded_by), ("Cancelled", "rained out", ALONA))
		self.assertEqual((unpaid_doc.status, unpaid_doc.refund_reason), ("Cancelled", None))  # nothing to refund

	def test_staff_still_cancels_a_session_without_touching_paid_billing(self):
		doc = self._session()
		doc = self._add(doc.name, [PLAYERS[2]], method="Cash")
		invoice = doc.participants[0].billing_doc
		frappe.set_user(STELLA)
		cancel_session(doc.name, cancel_billing=0)
		self.assertEqual(frappe.db.get_value("CBT Open Play Session", doc.name, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("CBT Booking Invoice", invoice, "status"), "Paid & Verified")

	def test_staff_still_cancels_an_unpaid_only_session_with_its_billing(self):
		doc = self._session()
		doc = self._add(doc.name, [PLAYERS[3]], method="Fund Transfer")
		invoice = doc.participants[0].billing_doc
		frappe.set_user(STELLA)
		cancel_session(doc.name, cancel_billing=1)  # no paid player: no gate, no reason
		self.assertEqual(frappe.db.get_value("CBT Booking Invoice", invoice, "status"), "Cancelled")
		self.assertIsNone(frappe.db.get_value("CBT Booking Invoice", invoice, "refund_reason"))


class TestRefundAfterTheClose(RefundTestCase):
	def test_a_refund_out_of_a_closed_month_books_today_and_leaves_the_bill_alone(self):
		"""Ruling 2026-08-27: no reopen. The closed month's figures and the
		statement issued on them stay as billed; the reversal lands on the day
		the refund happened, in the tenant's ledger and the by-channel split."""
		paid = self._book("10:00:00")

		def _drop_close():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Platform Month Close", PERIOD, force=True, ignore_permissions=True, ignore_missing=True
			)

		def _drop_statements():
			frappe.set_user("Administrator")
			for name in frappe.get_all("CBT Platform Statement", filters={"period": PERIOD}, pluck="name"):
				frappe.delete_doc(
					"CBT Platform Statement", name, force=True, ignore_permissions=True, ignore_missing=True
				)

		self.addCleanup(_drop_close)
		self.addCleanup(_drop_statements)  # LIFO: statements first, then the close

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with patch(CLOCK, return_value=AFTER_NOV):
			close_month(2028, 11)
			issue_statements(PERIOD)
		close = frappe.get_doc("CBT Platform Month Close", PERIOD)
		row_before = next(r for r in close.rows if r.company == AYALA)
		self.assertGreaterEqual(flt(row_before.confirmed_revenue), RATE)
		statement_name = frappe.get_all(
			"CBT Platform Statement", filters={"period": PERIOD, "company": AYALA}, pluck="name"
		)[0]
		due_before = flt(frappe.db.get_value("CBT Platform Statement", statement_name, "amount_due"))
		self.assertGreater(due_before, 0)

		# The refund, a week into the next month — allowed, not refused.
		frappe.set_user(ALONA)
		with patch(CLOCK, return_value=T_LATE_REFUND):
			cancel_booking(paid.name, reason="double-charged, refunded after the close")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.cancelled_at, T_LATE_REFUND)

		# The bill did not move.
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		close.reload()
		row_after = next(r for r in close.rows if r.company == AYALA)
		self.assertEqual(flt(row_after.confirmed_revenue), flt(row_before.confirmed_revenue))
		self.assertEqual(flt(row_after.amount_due), flt(row_before.amount_due))
		self.assertEqual(
			flt(frappe.db.get_value("CBT Platform Statement", statement_name, "amount_due")), due_before
		)

		# The reversal is a DECEMBER event, on the day it happened.
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": AYALA, "from_date": LATE_REFUND_DAY, "to_date": LATE_REFUND_DAY, "books": "Tenant"}
		)
		reversal = [r for r in rows if r.get("voucher") == invoice.name]
		self.assertEqual([(r["account"], flt(r["debit"]), flt(r["credit"])) for r in reversal][:1], [("Cash on Hand", 0.0, RATE)])
		self.assertIn("double-charged, refunded after the close", reversal[0]["description"])
		_columns, rows = cbt_collections_by_channel.execute(
			{"company": AYALA, "from_date": LATE_REFUND_DAY, "to_date": LATE_REFUND_DAY}
		)
		cash = next(r for r in rows if r.get("channel_label") == "Cash")
		self.assertEqual((cash["refund_count"], flt(cash["refunded"])), (1, RATE))
		# And NOT a November one.
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": AYALA, "from_date": "2028-11-01", "to_date": "2028-11-30", "books": "Tenant"}
		)
		self.assertFalse([r for r in rows if r.get("voucher") == invoice.name and r.get("is_reversal")])


class TestRefundPolicy(RefundTestCase):
	"""B53: the facility's CASH policy, and who may overrule it for its own fault.

	⚠ Every test here restores the company's policy in `addCleanup`. `Refund` is
	the default and roughly thirty tests in other modules cancel a paid booking
	with no cause at all — leaving `Reschedule only` on a shared seeded company
	would turn them red somewhere far from here.
	"""

	def _policy(self, company=AYALA, **values):
		before = frappe.db.get_value("CBT Company", company, POLICY_FIELDS, as_dict=True)

		def restore():
			frappe.set_user("Administrator")
			for field in POLICY_FIELDS:
				frappe.db.set_value("CBT Company", company, field, before.get(field))

		self.addCleanup(restore)
		for field, value in values.items():
			frappe.db.set_value("CBT Company", company, field, value)

	def _drop_credit(self, name):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Customer Credit", name, force=True, ignore_permissions=True, ignore_missing=True
		)

	# --- the default, which every other module leans on ----------------------

	def test_refund_is_the_default_so_the_rest_of_the_suite_is_unaffected(self):
		self.assertEqual(billing.refund_policy(AYALA), "Refund")
		self.assertEqual(billing.facility_fault_cancel_by(AYALA), "Company Admin only")

	def test_a_refund_company_cancels_exactly_as_before_and_stamps_the_cause(self):
		paid = self._book()
		frappe.set_user(ALONA)
		cancel_booking(paid.name, reason="Customer changed their mind")
		frappe.set_user("Administrator")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.refund_cause, "Customer request")
		self.assertEqual(invoice.refund_as_credit, 0)

	# --- Reschedule only ------------------------------------------------------

	def test_reschedule_only_refuses_cash_and_names_the_facility(self):
		self._policy(refund_policy="Reschedule only")
		paid = self._book()
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.ValidationError) as caught:
			cancel_booking(paid.name, reason="Customer changed their mind")
		message = str(caught.exception)
		frappe.set_user("Administrator")
		self.assertIn(frappe.db.get_value("CBT Company", AYALA, "company_name"), message)
		self.assertIn("store credit", message)
		# The sentence says what the facility OFFERS — a Company Admin can always
		# declare a facility fault, so "cannot refund" would be a false claim.
		self.assertNotIn("cannot", message.lower())
		self.assertEqual(self._status(paid.name), "Confirmed")

	def test_reschedule_only_still_issues_store_credit_and_the_document_says_so(self):
		self._policy(refund_policy="Reschedule only")
		paid = self._book()
		frappe.set_user(ALONA)
		out = cancel_booking(paid.name, reason="Customer changed their mind", issue_credit=1)
		frappe.set_user("Administrator")
		self.addCleanup(self._drop_credit, out["credit"])
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.refund_as_credit, 1)
		self.assertEqual(invoice.refund_cause, "Customer request")
		credit = frappe.get_doc("CBT Customer Credit", out["credit"])
		self.assertEqual(flt(credit.amount, 2), flt(invoice.total_amount, 2))

	def test_a_facility_fault_returns_cash_even_at_a_reschedule_only_venue(self):
		self._policy(refund_policy="Reschedule only")
		paid = self._book()
		frappe.set_user(ALONA)
		cancel_booking(paid.name, reason="Court flooded", cause="Facility fault")
		frappe.set_user("Administrator")
		self.assertEqual(self._status(paid.name), "Cancelled")
		invoice = self._invoice(paid.name)
		self.assertEqual(invoice.refund_cause, "Facility fault")
		self.assertEqual(invoice.refund_as_credit, 0)

	# --- who may declare a facility fault ------------------------------------

	def test_staff_cannot_declare_a_facility_fault_by_default(self):
		paid = self._book()
		frappe.set_user(STELLA)
		with self.assertRaises(frappe.PermissionError) as caught:
			cancel_booking(paid.name, reason="Court flooded", cause="Facility fault")
		message = str(caught.exception)
		frappe.set_user("Administrator")
		self.assertIn("facility's own fault", message)
		self.assertEqual(self._status(paid.name), "Confirmed")

	def test_staff_and_above_lets_the_desk_record_a_facility_fault_refund(self):
		self._policy(refund_policy="Reschedule only", facility_fault_cancel_by="Staff and above")
		paid = self._book()
		frappe.set_user(STELLA)
		cancel_booking(paid.name, reason="Court flooded", cause="Facility fault")
		frappe.set_user("Administrator")
		self.assertEqual(self._status(paid.name), "Cancelled")
		self.assertEqual(self._invoice(paid.name).refund_cause, "Facility fault")

	def test_the_seat_setting_applies_at_a_refund_company_too(self):
		self._policy(facility_fault_cancel_by="Staff and above")
		self.assertEqual(billing.refund_policy(AYALA), "Refund")
		paid = self._book()
		frappe.set_user(STELLA)
		cancel_booking(paid.name, reason="Burst pipe", cause="Facility fault")
		frappe.set_user("Administrator")
		self.assertEqual(self._status(paid.name), "Cancelled")

	def test_staff_still_cannot_cancel_a_paid_booking_as_a_customer_request(self):
		self._policy(facility_fault_cancel_by="Staff and above")
		paid = self._book()
		frappe.set_user(STELLA)
		with self.assertRaises(frappe.PermissionError) as caught:
			cancel_booking(paid.name, reason="They changed their mind")
		message = str(caught.exception)
		frappe.set_user("Administrator")
		self.assertIn(ADMIN_ONLY, message)

	def test_a_forged_cause_never_reaches_the_record(self):
		paid = self._book()
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.ValidationError) as caught:
			cancel_booking(paid.name, reason="Whatever", cause="Act of God")
		message = str(caught.exception)
		frappe.set_user("Administrator")
		self.assertIn("Act of God", message)
		self.assertEqual(self._status(paid.name), "Confirmed")

	# --- open play ------------------------------------------------------------

	def test_a_reschedule_only_session_credits_every_paid_player(self):
		self._policy(refund_policy="Reschedule only")
		session = self._add(self._session().name, PLAYERS[:2])
		frappe.set_user(ALONA)
		out = cancel_session(
			session.name, cancel_billing=1, reason="Court flooded", issue_credit=1
		)
		frappe.set_user("Administrator")
		for name in out["credits_issued"]:
			self.addCleanup(self._drop_credit, name)
		self.assertEqual(len(out["credits_issued"]), 2)
		self.assertEqual(out["credit_skipped"], [])
		for name in out["credits_issued"]:
			credit = frappe.get_doc("CBT Customer Credit", name)
			# A participant has an invoice but no booking — that is why
			# `source_booking` is not mandatory on CBT Customer Credit.
			self.assertFalse(credit.source_booking)
			self.assertTrue(credit.source_invoice)
			self.assertEqual(
				flt(credit.amount, 2),
				flt(frappe.db.get_value("CBT Booking Invoice", credit.source_invoice, "total_amount"), 2),
			)

	def test_a_session_credit_is_minted_once_however_often_it_is_asked_for(self):
		self._policy(refund_policy="Reschedule only")
		session = self._add(self._session().name, PLAYERS[:2])
		invoices = [row.billing_doc for row in session.participants]
		frappe.set_user(ALONA)
		out = cancel_session(
			session.name, cancel_billing=1, reason="Court flooded", issue_credit=1
		)
		for name in out["credits_issued"]:
			self.addCleanup(self._drop_credit, name)
		with self.assertRaises(frappe.ValidationError):
			cancel_session(
				session.name, cancel_billing=1, reason="Court flooded", issue_credit=1
			)
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.count("CBT Customer Credit", {"source_invoice": ("in", invoices)}), 2
		)

	def test_crediting_players_whose_documents_still_stand_is_refused(self):
		session = self._add(self._session().name, PLAYERS[:2])
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.ValidationError) as caught:
			cancel_session(
				session.name, cancel_billing=0, reason="Court flooded", issue_credit=1
			)
		message = str(caught.exception)
		frappe.set_user("Administrator")
		self.assertIn("nothing to credit", message)
