"""
Court Booking Tech — store credit from a refund (Backlog B39, section-26).

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_credits

A credit is a PAYMENT, not a discount: the booking's total and VAT never move,
and the money reports net it out of what was COLLECTED. The rows below pin the
four paths that would otherwise destroy or forge a customer's money — insert
forgery, the expiry sweep, the portal self-cancel and a reschedule.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech import credits
from court_booking_tech.api.bookings import (
	cancel_booking,
	create_booking,
	reschedule_booking,
)
from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL, seed_all
from court_booking_tech.tasks import expire_reservations

AYALA = "ayala-courts"
QCSM = "qc-smash"
ALONA = "admin.ayala@example.com"  # Company Admin — the refund gate
QUINTIN = "admin.qcsm@example.com"

COURT_A = "AYALA-makati-court-a"  # ₱300/hr flat
COURT_B = "AYALA-makati-court-b"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"
TEST_DATE = "2027-06-11"  # a Friday no other module books
T0 = datetime(2027, 6, 11, 8, 0)


class TestCustomerCredit(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()
		cls._drain_foreign_credits()

	@classmethod
	def _drain_foreign_credits(cls):
		"""Zero any credit this customer already holds, or FIFO spends it instead.

		`take_credit` takes the OLDEST Active credit with balance > 0
		(credits.py:122-131), so ONE leftover document makes every balance
		assertion in this file read a row it did not mint. See section-30.
		"""
		frappe.db.sql(
			"""UPDATE `tabCBT Customer Credit` SET balance = 0
			   WHERE company = %s AND customer = %s AND balance > 0""",
			(AYALA, CUSTOMER_EMAIL),
		)
		frappe.db.commit()
		left = frappe.db.sql(
			"""SELECT name, balance FROM `tabCBT Customer Credit`
			   WHERE company = %s AND customer = %s AND balance > 0""",
			(AYALA, CUSTOMER_EMAIL),
		)
		assert not left, (
			f"a foreign credit survives and take_credit will spend IT, not this "
			f"module's: {left}. Every balance assertion here would read the wrong row."
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _cleanup(self, doctype, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				doctype, name, force=True, ignore_permissions=True, ignore_missing=True
			)

		self.addCleanup(_do)

	def _book(self, court=COURT_A, start_time="08:00:00", method="Cash", **kwargs):
		frappe.set_user(ALONA)
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=court,
				booking_date=TEST_DATE,
				start_time=start_time,
				payment_method=method,
				customer=CUSTOMER_EMAIL,
				**kwargs,
			)
		self._cleanup("CBT Court Booking", result["name"])
		return frappe.get_doc("CBT Court Booking", result["name"])

	def _refund_to_credit(self, booking, reason="Court flooded"):
		frappe.set_user(ALONA)
		out = cancel_booking(booking.name, reason=reason, issue_credit=1)
		credit = out["credit"] if isinstance(out, dict) else None
		if credit:
			self._cleanup("CBT Customer Credit", credit)
		return credit

	def _credit(self, name):
		return frappe.get_doc("CBT Customer Credit", name)

	# --- issue -----------------------------------------------------------

	def test_a_refund_can_be_paid_as_store_credit_instead_of_cash(self):
		booking = self._book()
		credit = self._credit(self._refund_to_credit(booking))

		self.assertEqual(credit.company, AYALA)
		self.assertEqual(credit.customer, CUSTOMER_EMAIL)
		self.assertEqual(flt(credit.amount, 2), flt(booking.total_amount, 2))
		self.assertEqual(flt(credit.balance, 2), flt(booking.total_amount, 2))
		self.assertEqual(credit.status, "Active")
		self.assertEqual(credit.source_booking, booking.name)
		self.assertEqual(credit.reason, "Court flooded")
		self.assertEqual(credit.issued_by, ALONA)

	def test_a_walk_in_is_refunded_in_cash_because_there_is_no_account(self):
		frappe.set_user(ALONA)
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=COURT_A,
				booking_date=TEST_DATE,
				start_time="09:00:00",
				payment_method="Cash",
				customer_name="Wanda Walkin",
			)
		self._cleanup("CBT Court Booking", result["name"])
		with self.assertRaisesRegex(frappe.ValidationError, "no account"):
			cancel_booking(result["name"], reason="Rained out", issue_credit=1)

	def test_an_unpaid_cancel_mints_nothing(self):
		"""There is no money to give back, so there is nothing to credit."""
		booking = self._book(start_time="10:00:00", method="Fund Transfer")
		self.assertEqual(booking.booking_status, "Reserved")
		frappe.set_user(ALONA)
		with self.assertRaisesRegex(frappe.ValidationError, "nothing to credit"):
			cancel_booking(booking.name, reason="Changed mind", issue_credit=1)

	# --- spend -----------------------------------------------------------

	def test_credit_that_covers_the_whole_price_confirms_a_transfer_booking(self):
		"""A booking already paid must not sit Reserved waiting for a receipt
		for ₱0."""
		source = self._book(start_time="08:00:00")
		credit = self._refund_to_credit(source)

		spent = self._book(start_time="11:00:00", method="Fund Transfer", apply_credit=1)
		self.assertEqual(flt(spent.credit_applied, 2), flt(spent.total_amount, 2))
		self.assertEqual(spent.credit_document, credit)
		self.assertEqual(spent.booking_status, "Confirmed")
		self.assertIsNone(spent.reservation_expires_at)

		after = self._credit(credit)
		self.assertEqual(flt(after.balance, 2), 0.0)
		self.assertEqual(after.status, "Spent")

	def test_the_total_and_the_vat_never_move_because_credit_is_a_payment(self):
		source = self._book(start_time="08:00:00")
		self._refund_to_credit(source)
		plain = self._book(start_time="12:00:00")
		spent = self._book(start_time="13:00:00", apply_credit=1)

		self.assertEqual(flt(spent.total_amount, 2), flt(plain.total_amount, 2))
		paid_invoice = frappe.get_doc("CBT Booking Invoice", spent.billing_doc)
		plain_invoice = frappe.get_doc("CBT Booking Invoice", plain.billing_doc)
		self.assertEqual(
			flt(paid_invoice.vat_amount, 2),
			flt(plain_invoice.vat_amount, 2),
			"store credit moved the VAT — it is a payment, not a discount",
		)
		self.assertEqual(
			flt(paid_invoice.credit_applied, 2), flt(spent.credit_applied, 2)
		)

	def test_a_partial_credit_leaves_the_rest_to_pay(self):
		source = self._book(start_time="08:00:00")  # 1 slot
		credit = self._refund_to_credit(source)

		spent = self._book(
			start_time="14:00:00", method="Fund Transfer", number_of_slots=2, apply_credit=1
		)
		self.assertEqual(
			spent.credit_document, credit, "the booking spent a DIFFERENT credit document"
		)
		self.assertLess(flt(spent.credit_applied, 2), flt(spent.total_amount, 2))
		self.assertGreater(flt(spent.credit_applied, 2), 0)
		self.assertEqual(
			spent.booking_status, "Reserved", "a part-paid hold still needs its receipt"
		)
		self.assertTrue(spent.reservation_expires_at)
		self.assertEqual(flt(self._credit(credit).balance, 2), 0.0)

	def test_the_remainder_of_a_big_credit_is_spendable_again(self):
		source = self._book(start_time="08:00:00", number_of_slots=3)
		credit = self._refund_to_credit(source)
		big = flt(self._credit(credit).balance, 2)

		first = self._book(start_time="15:00:00", apply_credit=1)
		remaining = flt(self._credit(credit).balance, 2)
		self.assertEqual(remaining, flt(big - flt(first.credit_applied), 2))
		self.assertEqual(self._credit(credit).status, "Active")

		second = self._book(start_time="16:00:00", apply_credit=1)
		self.assertEqual(second.credit_document, credit)
		self.assertGreater(flt(second.credit_applied, 2), 0)
		self.assertLessEqual(
			flt(first.credit_applied) + flt(second.credit_applied),
			big,
			"two bookings between them spent more credit than existed",
		)

	def test_credit_is_never_applied_unless_it_is_asked_for(self):
		source = self._book(start_time="08:00:00")
		credit = self._refund_to_credit(source)
		plain = self._book(start_time="17:00:00")

		self.assertEqual(flt(plain.credit_applied, 2), 0.0)
		self.assertIsNone(plain.credit_document)
		self.assertEqual(
			flt(self._credit(credit).balance, 2), flt(source.total_amount, 2)
		)

	def test_a_free_booking_takes_no_credit(self):
		source = self._book(start_time="08:00:00")
		self._refund_to_credit(source)
		free = self._book(start_time="18:00:00", method="Free", apply_credit=1)
		self.assertEqual(flt(free.credit_applied, 2), 0.0)

	def test_credit_cannot_be_forged_on_a_rest_style_insert(self):
		"""⚠ read_only is a UI property. A seat with write on its own bookings
		must not be able to post settled money nobody ever credited."""
		frappe.set_user(ALONA)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": COURT_A,
				"customer": CUSTOMER_EMAIL,
				"booking_date": TEST_DATE,
				"start_time": "19:00:00",
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
				"credit_applied": 500,
			}
		)
		with patch(CLOCK, return_value=T0):
			doc.insert()
		self._cleanup("CBT Court Booking", doc.name)
		self.assertEqual(flt(doc.credit_applied, 2), 0.0, "credit was FORGED at insert")
		self.assertEqual(doc.booking_status, "Reserved")

	def test_credit_cannot_be_inflated_by_a_later_save(self):
		source = self._book(start_time="08:00:00")
		self._refund_to_credit(source)
		spent = self._book(start_time="20:00:00", apply_credit=1)
		settled = flt(spent.credit_applied, 2)

		spent.credit_applied = settled + 999
		spent.save()
		spent.reload()
		self.assertEqual(flt(spent.credit_applied, 2), settled)

	def test_a_credit_never_crosses_to_another_company(self):
		source = self._book(start_time="08:00:00")
		self._refund_to_credit(source)
		self.assertEqual(credits.available_credit(QCSM, CUSTOMER_EMAIL), 0.0)

		frappe.set_user(QUINTIN)
		with patch(CLOCK, return_value=T0):
			other = create_booking(
				court=QCSM_COURT,
				booking_date=TEST_DATE,
				start_time="08:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				apply_credit=1,
			)
		self._cleanup("CBT Court Booking", other["name"])
		self.assertEqual(flt(other["credit_applied"], 2), 0.0)

	# --- the paths that would otherwise DESTROY it ------------------------

	def test_cancelling_a_credit_paid_booking_gives_the_credit_back(self):
		source = self._book(start_time="08:00:00")
		credit = self._refund_to_credit(source)
		spent = self._book(start_time="21:00:00", apply_credit=1)
		self.assertEqual(flt(self._credit(credit).balance, 2), 0.0)

		frappe.set_user(ALONA)
		cancel_booking(spent.name, reason="Customer could not make it")
		back = self._credit(credit)
		self.assertEqual(flt(back.balance, 2), flt(spent.credit_applied, 2))
		self.assertEqual(back.status, "Active")

	def test_the_restore_happens_once_however_often_it_is_called(self):
		source = self._book(start_time="08:00:00")
		credit = self._refund_to_credit(source)
		spent = self._book(start_time="07:00:00", apply_credit=1)
		frappe.set_user(ALONA)
		cancel_booking(spent.name, reason="Double-restore probe")
		once = flt(self._credit(credit).balance, 2)

		credits.restore_credit(spent.name)
		credits.restore_credit(spent.name)
		self.assertEqual(flt(self._credit(credit).balance, 2), once)

	def test_a_lapsed_hold_hands_the_credit_back(self):
		"""The expiry sweep writes with frappe.db.set_value and fires no
		doc_events — without an explicit restore the money would just vanish."""
		source = self._book(start_time="08:00:00", number_of_slots=3)
		credit = self._refund_to_credit(source)
		spent = self._book(
			start_time="14:00:00", method="Fund Transfer", number_of_slots=1,
			apply_credit=1,
		)
		# Fully covered would confirm — this row needs a live Reserved hold.
		frappe.db.set_value(
			"CBT Court Booking", spent.name, "booking_status", "Reserved"
		)
		spent.reload()
		self.assertEqual(spent.booking_status, "Reserved")
		drained = flt(self._credit(credit).balance, 2)

		with patch(CLOCK, return_value=T0 + timedelta(days=1)):
			expire_reservations()

		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", spent.name, "booking_status"),
			"Expired",
		)
		self.assertEqual(
			flt(self._credit(credit).balance, 2),
			flt(drained + flt(spent.credit_applied), 2),
			"the sweep destroyed the customer's store credit",
		)

	def test_a_customer_dropping_their_own_hold_gets_the_credit_back(self):
		source = self._book(start_time="08:00:00", number_of_slots=3)
		credit = self._refund_to_credit(source)
		spent = self._book(
			start_time="14:00:00", method="Fund Transfer", number_of_slots=1,
			apply_credit=1,
		)
		frappe.db.set_value(
			"CBT Court Booking", spent.name, "booking_status", "Reserved"
		)
		drained = flt(self._credit(credit).balance, 2)

		from court_booking_tech.api.portal import cancel_my_booking

		frappe.set_user(CUSTOMER_EMAIL)
		with patch(CLOCK, return_value=T0):
			result = cancel_my_booking(spent.name)
		self.assertEqual(result["booking_status"], "Cancelled")
		self.assertEqual(
			flt(self._credit(credit).balance, 2),
			flt(drained + flt(spent.credit_applied), 2),
		)

	def test_moving_a_credit_paid_booking_carries_the_credit_across(self):
		"""A move is not a new sale — the settlement travels, and the credit
		document is NOT drained a second time."""
		source = self._book(start_time="08:00:00")
		credit = self._refund_to_credit(source)
		spent = self._book(start_time="09:00:00", apply_credit=1)
		balance_before = flt(self._credit(credit).balance, 2)

		frappe.set_user(ALONA)
		with patch(CLOCK, return_value=T0):
			moved = reschedule_booking(
				spent.name, court=COURT_B, booking_date=TEST_DATE, start_time="09:00:00"
			)
		self._cleanup("CBT Court Booking", moved["name"])
		new = frappe.get_doc("CBT Court Booking", moved["name"])

		self.assertEqual(flt(new.credit_applied, 2), flt(spent.credit_applied, 2))
		self.assertEqual(new.credit_document, credit)
		self.assertEqual(
			flt(self._credit(credit).balance, 2),
			balance_before,
			"the move spent the credit a second time",
		)

	# --- the books ---------------------------------------------------------

	def _collections(self, day):
		"""CBT Collections by Channel for ONE day, as {channel_label: row}."""
		from court_booking_tech.court_booking_tech.report.cbt_collections_by_channel import (
			cbt_collections_by_channel,
		)

		frappe.set_user(ALONA)
		_columns, data = cbt_collections_by_channel.execute(
			{"company": AYALA, "from_date": day, "to_date": day}
		)
		return {row["channel_label"]: row for row in data if not row.get("is_total_row")}

	def _totals(self, day):
		from court_booking_tech.court_booking_tech.report.cbt_collections_by_channel import (
			cbt_collections_by_channel,
		)

		frappe.set_user(ALONA)
		_columns, data = cbt_collections_by_channel.execute(
			{"company": AYALA, "from_date": day, "to_date": day}
		)
		return next((row for row in data if row.get("is_total_row")), None)

	def test_a_credit_paid_booking_posts_revenue_but_no_collection(self):
		"""The whole point of B39: the Cash workaround put money in the drawer
		that never arrived. A credit posts NONE — and the row still EXISTS, so
		"zero collected" cannot pass by the bucket simply being absent."""
		source = self._book(start_time="08:00:00")
		self._refund_to_credit(source)
		day = str(T0.date())
		before = self._totals(day)
		before_collected = flt(before["collected"], 2) if before else 0.0
		before_credit = flt(before["credit_applied"], 2) if before else 0.0

		spent = self._book(start_time="11:00:00", apply_credit=1)
		self.assertEqual(flt(spent.credit_applied, 2), flt(spent.total_amount, 2))

		after = self._totals(day)
		self.assertIsNotNone(after, "the paid booking created no report row at all")
		self.assertEqual(
			flt(after["collected"], 2),
			before_collected,
			"store credit was counted as money through the channel",
		)
		self.assertEqual(
			flt(after["credit_applied"], 2),
			flt(before_credit + flt(spent.credit_applied), 2),
			"the credit is not shown beside the collection it replaced",
		)
		self.assertGreater(
			flt(after["court_revenue"], 2),
			0,
			"the tenant earned this court time however it was settled",
		)

	def test_a_partly_credited_booking_posts_only_the_cash_difference(self):
		source = self._book(start_time="08:00:00")  # one slot of credit
		self._refund_to_credit(source)
		day = str(T0.date())
		before = self._totals(day)
		before_collected = flt(before["collected"], 2) if before else 0.0

		spent = self._book(start_time="14:00:00", number_of_slots=2, apply_credit=1)
		expected = flt(flt(spent.total_amount) - flt(spent.credit_applied), 2)
		self.assertGreater(expected, 0)

		after = self._totals(day)
		self.assertEqual(
			flt(after["collected"], 2),
			flt(before_collected + expected, 2),
			"the collected figure does not match what actually moved",
		)

	# --- the customer can SEE it (Backlog B44) ---------------------------

	def test_the_balance_is_grouped_per_company(self):
		"""`available_credit` needs the company already, so it can only be asked
		about companies the caller knows. B44's portal line has to find the
		companies themselves."""
		booking = self._book()
		credit = self._credit(self._refund_to_credit(booking))

		balances = credits.balances_by_company(CUSTOMER_EMAIL)
		self.assertIn(AYALA, balances)
		self.assertEqual(flt(balances[AYALA], 2), flt(credit.balance, 2))
		self.assertEqual(
			flt(balances[AYALA], 2),
			flt(credits.available_credit(AYALA, CUSTOMER_EMAIL), 2),
			"the grouped balance and the per-company one must be the same money",
		)
		self.assertEqual(credits.balances_by_company(None), {})

	def test_a_void_or_spent_credit_is_not_a_balance(self):
		"""Same predicate as available_credit, deliberately — a Void credit is
		not spendable and a fully-spent one is not a balance."""
		booking = self._book()
		credit = self._credit(self._refund_to_credit(booking))

		# The engine writes this doctype with ignore_permissions; no role holds
		# write on it, which is the point of B43's bench-execute helper.
		frappe.set_user("Administrator")
		credit.db_set("status", "Void")
		self.assertNotIn(AYALA, credits.balances_by_company(CUSTOMER_EMAIL))

		credit.db_set("status", "Active")
		credit.db_set("balance", 0)
		self.assertNotIn(
			AYALA,
			credits.balances_by_company(CUSTOMER_EMAIL),
			"a credit with nothing left must not render as a balance",
		)

	def test_my_bookings_carries_the_balance_and_names_the_company(self):
		"""Backlog B44. The customer's own page is where the balance belongs —
		B39 shipped the credit and told nobody, so the only way to answer "do I
		have anything left?" was the raw desk list view."""
		from court_booking_tech.api.portal import get_my_bookings

		booking = self._book()
		credit = self._credit(self._refund_to_credit(booking))

		frappe.set_user(CUSTOMER_EMAIL)
		payload = get_my_bookings()
		rows = {row["company"]: row for row in payload["credits"]}
		self.assertIn(AYALA, rows)
		self.assertEqual(flt(rows[AYALA]["balance"], 2), flt(credit.balance, 2))
		self.assertEqual(
			rows[AYALA]["company_name"],
			frappe.db.get_value("CBT Company", AYALA, "company_name"),
			"a raw slug is not something a customer can act on",
		)

	def test_another_customers_credit_is_never_in_my_payload(self):
		"""The endpoint takes no arguments and keys on the session user — the
		only leak available here would be adding one 'for the test'."""
		from court_booking_tech.api.portal import get_my_bookings

		booking = self._book()
		self._refund_to_credit(booking)

		frappe.set_user("cust.pia@example.com")
		payload = get_my_bookings()
		self.assertEqual(
			payload["credits"],
			[],
			"another customer's balance reached this payload",
		)

	def test_a_customer_with_no_bookings_still_sees_a_balance(self):
		"""The zero-bookings early return used to drop everything but the empty
		list — and a customer whose only booking was refunded into credit and
		cancelled is EXACTLY the person this line exists for."""
		from court_booking_tech.api.portal import get_my_bookings

		booking = self._book()
		credit = self._credit(self._refund_to_credit(booking))

		with patch("frappe.get_all", side_effect=self._no_bookings(booking.name)):
			frappe.set_user(CUSTOMER_EMAIL)
			payload = get_my_bookings()
		self.assertEqual(payload["bookings"], [])
		self.assertEqual(
			[row["company"] for row in payload["credits"]],
			[AYALA],
			"a customer with credit and no bookings was shown nothing at all",
		)
		self.assertEqual(flt(payload["credits"][0]["balance"], 2), flt(credit.balance, 2))

	@staticmethod
	def _no_bookings(_name):
		"""Make ONLY the CBT Court Booking listing come back empty, so the
		zero-rows branch is reached without deleting the credit's own source."""
		real = frappe.get_all

		def _patched(doctype, *args, **kwargs):
			if doctype == "CBT Court Booking":
				return []
			return real(doctype, *args, **kwargs)

		return _patched
