"""
Court Booking Tech — Per-booking platform fee (Backlog B27, Batches 9–11, 2026-08-27)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_booking_fee

The third billing mode, and the first that the CUSTOMER sees: a Per Booking
tenant's customers pay a peso fee on top of the court, read from MARGINAL
brackets on a MONTHLY count of paid bookings, fixed at checkout and printed as
its own line. Every number here is hand-computed and peso-pinned.

MONTH. **May 2028** — banked free by the ledger (no `"month": 5`, no `2028-05`
anywhere in the tree before this module). 2028-05-03 is a Wednesday;
AYALA-makati is open 06:00–22:00 every day, court-a/-b at ₱300 flat.

TENANT. AYALA is switched to **Per Booking** for each test and RESTORED to
Percentage 10% afterwards (addCleanup), so test_reports' August 2027 ground
truth never sees this module. Tiers are deliberately SMALL — 1–2 ₱15, 3–4 ₱30,
5+ ₱40 — so a module-sized cast crosses two boundaries; the user's real bands
are wide by ruling, which is exactly why a test needs narrow ones.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech.api.bookings import (
	cancel_booking,
	create_booking,
	extend_booking,
	reschedule_booking,
)
from court_booking_tech.api.open_play import add_players, open_session
from court_booking_tech.api.portal import get_quote
from court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close import (
	close_month,
)
from court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement import (
	issue_statements,
)
from court_booking_tech.court_booking_tech.report.cbt_company_revenue import (
	cbt_company_revenue,
)
from court_booking_tech.court_booking_tech.report.cbt_platform_revenue import (
	cbt_platform_revenue,
)
from court_booking_tech.platform_fees import (
	booking_fee,
	booking_fees_for_units,
	next_ordinal,
	tier_fee,
)
from court_booking_tech.seeds.seed_test_data import (
	OPEN_PLAY_CUSTOMERS,
	PLATFORM_ADMIN_EMAIL,
	seed_all,
)

AYALA = "ayala-courts"
QCSM = "qc-smash"
PIA = "cust.pia@example.com"  # no AYALA membership — undiscounted by seed design
STELLA = "staff.ayala@example.com"

MAKATI_A = "AYALA-makati-court-a"  # ₱300 flat, no rules
MAKATI_B = "AYALA-makati-court-b"
QCSM_1 = "QCSM-timog-court-1"  # ₱350

DAY = "2028-05-03"  # Wednesday
T0 = datetime(2028, 5, 3, 8, 0)
T1 = datetime(2028, 5, 3, 8, 20)
AFTER_MAY = datetime(2028, 6, 10, 10, 0)
PERIOD = "2028-05"

CLOCK = "court_booking_tech.clock.now_dt"

TIERS = [
	{"from_count": 1, "to_count": 2, "fee": 15},
	{"from_count": 3, "to_count": 4, "fee": 30},
	{"from_count": 5, "to_count": 0, "fee": 40},
]
OPEN_PLAY_FEE = 10


def _set_per_booking(company=AYALA, tiers=TIERS, open_play_fee=OPEN_PLAY_FEE):
	doc = frappe.get_doc("CBT Company", company)
	doc.billing_mode = "Per Booking"
	doc.set("booking_fee_tiers", [])
	for row in tiers:
		doc.append("booking_fee_tiers", row)
	doc.open_play_fee_per_participant = open_play_fee
	doc.save(ignore_permissions=True)
	return doc


def _restore_ayala():
	frappe.set_user("Administrator")
	doc = frappe.get_doc("CBT Company", AYALA)
	doc.billing_mode = "Percentage"
	doc.commission_percent = 10
	doc.set("booking_fee_tiers", [])
	doc.open_play_fee_per_participant = 0
	doc.save(ignore_permissions=True)


class FeeTestCase(FrappeTestCase):
	"""Shared fixtures; holds NO test methods (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(_restore_ayala)
		_set_per_booking()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _book(self, court=MAKATI_A, start_time="10:00:00", method="Cash", date=DAY, slots=1, at=T0, **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": PIA,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": method,
			"discount_percent": 0,
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
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True, ignore_missing=True
			)

		self.addCleanup(_do)

	def _reload(self, name):
		return frappe.get_doc("CBT Court Booking", name)

	def _invoice(self, booking_name):
		doc = self._reload(booking_name)
		self.assertTrue(doc.billing_doc, f"{booking_name} has no billing document")
		return frappe.get_doc("CBT Booking Invoice", doc.billing_doc)


class TestFeeTiers(FeeTestCase):
	"""The table the user's own first example would have failed."""

	def test_per_booking_refuses_an_empty_tier_table(self):
		"""Refused rather than defaulted to ₱0 — a silent zero is how a tenant
		finds out in December that no fee was ever charged."""
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.set("booking_fee_tiers", [])
		with self.assertRaisesRegex(frappe.ValidationError, "at least one tier"):
			doc.save(ignore_permissions=True)

	def test_tiers_must_be_contiguous_ascending_and_open_ended(self):
		cases = {
			"gap": [(1, 20, 15), (22, 50, 30), (51, 0, 40)],
			# The user's own first example: "21st to 50 is 30 pesos next 31-999".
			"overlap": [(1, 20, 15), (21, 50, 30), (31, 0, 40)],
			"not from 1": [(2, 20, 15), (21, 0, 30)],
			"open-ended not last": [(1, 0, 15), (21, 0, 30)],
			"last is closed": [(1, 20, 15), (21, 50, 30)],
			"to before from": [(1, 20, 15), (21, 10, 30), (51, 0, 40)],
			"negative fee": [(1, 20, -1), (21, 0, 30)],
		}
		for label, rows in cases.items():
			with self.subTest(case=label):
				doc = frappe.get_doc("CBT Company", AYALA)
				doc.set("booking_fee_tiers", [])
				for from_count, to_count, fee in rows:
					doc.append(
						"booking_fee_tiers",
						{"from_count": from_count, "to_count": to_count, "fee": fee},
					)
				self.assertRaises(frappe.ValidationError, doc.save, ignore_permissions=True)
		# And the control — the same shape, valid.
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.set("booking_fee_tiers", [])
		for from_count, to_count, fee in [(1, 20, 15), (21, 50, 30), (51, 0, 40)]:
			doc.append(
				"booking_fee_tiers",
				{"from_count": from_count, "to_count": to_count, "fee": fee},
			)
		doc.save(ignore_permissions=True)
		self.assertEqual(len(frappe.get_doc("CBT Company", AYALA).booking_fee_tiers), 3)

	def test_the_other_modes_ignore_the_table(self):
		"""Tiers only bind under Per Booking — a Percentage tenant with a
		leftover, even broken, table still saves (and charges no fee)."""
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.billing_mode = "Percentage"
		doc.commission_percent = 10
		doc.set("booking_fee_tiers", [])
		doc.append("booking_fee_tiers", {"from_count": 5, "to_count": 2, "fee": 99})
		doc.save(ignore_permissions=True)
		booking = self._book()
		self.assertEqual(flt(booking.platform_fee), 0.0)
		self.assertEqual(booking.platform_fee_seq, 0)
		self.assertEqual(flt(booking.total_amount), 300.0)

	def test_tier_fee_reads_marginal_brackets(self):
		tiers = [frappe._dict(row) for row in TIERS]
		self.assertEqual([tier_fee(tiers, n) for n in (1, 2, 3, 4, 5, 6, 999)], [15, 15, 30, 30, 40, 40, 40])


class TestFeeAtCheckout(FeeTestCase):
	def test_each_booking_takes_the_next_bracket_and_the_quote_predicted_it(self):
		"""(20×15)+(30×30)+(10×40) — the user's arithmetic, at test scale:
		units 1–2 ₱15, 3–4 ₱30, 5 ₱40. The QUOTE taken just before each booking
		reads the same count through the same function, so with nothing landing
		in between it names the fee the booking then carries — the parity the
		screens rely on (the count is taken twice, not carried; see the module
		docstring for the bracket-boundary edge). The quote does NOT reveal the
		unit number — that is the tenant's sales count, and the endpoint is
		guest-open."""
		expected = [15, 15, 30, 30, 40]
		for index, start in enumerate(("10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00")):
			quote = get_quote(MAKATI_A, 1, booking_date=DAY, start_time=start)
			booking = self._book(start_time=start)
			with self.subTest(unit=index + 1):
				self.assertEqual(booking.platform_fee_seq, index + 1)
				self.assertEqual(flt(booking.platform_fee), float(expected[index]))
				self.assertEqual(flt(booking.total_amount), 300.0 + expected[index])
				self.assertNotIn("platform_fee_seq", quote)
				self.assertEqual(flt(quote["platform_fee"]), float(expected[index]))
				self.assertEqual(flt(quote["court_total"]), 300.0)
				self.assertEqual(flt(quote["total_amount"]), flt(booking.total_amount))

	def test_the_statement_prints_the_fee_as_its_own_line_outside_the_vat_footer(self):
		"""User rulings 2026-08-27: "100 + 15, total 115 — VAT-inclusive if the
		tenant is VAT" (B27), refined by B29's "court share only, fix the print":
		the tenant's VAT is on THEIR ₱300, the ₱15 is the platform's non-VAT
		revenue. AYALA is VAT 12%: ₱300 ÷ 1.12 = ₱267.86 VATable, ₱32.14 VAT —
		ONE footer, over the court share; the fee line sits outside it."""
		booking = self._book()
		invoice = self._invoice(booking.name)
		self.assertEqual(flt(invoice.subtotal), 300.0)
		self.assertEqual(flt(invoice.discount_amount), 0.0)
		self.assertEqual(flt(invoice.platform_fee), 15.0)
		self.assertEqual(flt(invoice.total_amount), 315.0)
		self.assertEqual(invoice.vat_mode, "VAT")
		self.assertEqual(flt(invoice.vatable_amount), 267.86)
		self.assertEqual(flt(invoice.vat_amount), 32.14)
		# The fee is NOT an item — the items are the tenant's sale.
		self.assertEqual(len(invoice.items), 1)
		self.assertEqual(flt(invoice.items[0].amount), 300.0)

		html = frappe.get_print("CBT Booking Invoice", invoice.name, "CBT Billing Statement")
		# The fee's OWN cell — "315.00" contains "15.00", so a bare substring
		# check would pass with the fee line missing (ducky, 2026-08-27).
		self.assertRegex(
			html, r"Booking fee \(platform, non-VAT\)</td>\s*<td[^>]*>[^<]*\b15\.00"
		)
		self.assertIn("315.00", html)
		self.assertIn("267.86", html)
		self.assertNotIn("281.25", html)  # the pre-B29 "VAT over court + fee" figure
		self.assertEqual(html.count("VATable Sales"), 1)
		# ORDER: the fee prints AFTER the VAT block — outside it, before TOTAL.
		self.assertLess(html.index("VATable Sales"), html.index("Booking fee (platform"))

	def test_the_discount_never_touches_the_fee(self):
		booking = self._book(discount_percent=50)
		self.assertEqual(flt(booking.total_amount), 165.0)  # 300 − 50% + 15
		invoice = self._invoice(booking.name)
		self.assertEqual(flt(invoice.discount_amount), 150.0)
		self.assertEqual(flt(invoice.platform_fee), 15.0)
		self.assertEqual(flt(invoice.total_amount), 165.0)
		# The quote agrees, through the staff-gated discount.
		frappe.set_user(STELLA)
		quote = get_quote(MAKATI_A, 1, booking_date=DAY, start_time="11:00:00", discount_percent=50)
		self.assertEqual(flt(quote["court_total"]), 150.0)
		self.assertEqual(flt(quote["platform_fee"]), 15.0)  # unit 2
		self.assertEqual(flt(quote["total_amount"]), 165.0)

	def test_a_free_booking_carries_no_unit(self):
		"""The ruling was "every PAID transaction" — a Free booking has no fee
		and does not move the counter, and the quote says so when told."""
		free = self._book(method="Free")
		self.assertEqual(flt(free.platform_fee), 0.0)
		self.assertEqual(free.platform_fee_seq, 0)
		# A Free COURT booking still records the court's price (that is the
		# tenant's own gesture, priced as sold) — only the platform's fee is 0.
		self.assertEqual(flt(free.total_amount), 300.0)
		quote = get_quote(MAKATI_A, 1, booking_date=DAY, start_time="11:00:00", payment_method="Free")
		self.assertEqual(flt(quote["platform_fee"]), 0.0)
		paid = self._book(start_time="11:00:00")
		self.assertEqual(paid.platform_fee_seq, 1)

	def test_a_cancelled_booking_keeps_its_fee_but_frees_its_place(self):
		"""Ruling 4 (2026-08-27): a refund returns the fee with the booking and
		its unit stops counting — so the next sale takes its place in the
		brackets. The cancelled document keeps the figure it was issued with."""
		first = self._book(start_time="10:00:00")
		second = self._book(start_time="11:00:00")
		third = self._book(start_time="12:00:00")
		self.assertEqual([b.platform_fee_seq for b in (first, second, third)], [1, 2, 3])
		self.assertEqual(flt(third.platform_fee), 30.0)

		with patch(CLOCK, return_value=T1):
			cancel_booking(second.name, reason="test: keeps its fee")
		cancelled = self._reload(second.name)
		self.assertEqual(cancelled.booking_status, "Cancelled")
		self.assertEqual(flt(cancelled.platform_fee), 15.0)  # retained, not re-read
		self.assertEqual(self._invoice(second.name).status, "Cancelled")

		self.assertEqual(next_ordinal(AYALA, DAY), 3)
		fourth = self._book(start_time="13:00:00")
		self.assertEqual(fourth.platform_fee_seq, 3)
		self.assertEqual(flt(fourth.platform_fee), 30.0)

	def test_an_extension_is_its_own_unit(self):
		"""User ruling: "extend is another book"."""
		first = self._book(start_time="10:00:00")
		with patch(CLOCK, return_value=T1):
			extension_name = extend_booking(first.name)
		self._cleanup_booking(extension_name)
		extension = self._reload(extension_name)
		self.assertEqual(extension.extended_from, first.name)
		self.assertEqual(extension.platform_fee_seq, 2)
		self.assertEqual(flt(extension.platform_fee), 15.0)
		self.assertEqual(flt(extension.total_amount), 315.0)

	def test_a_reschedule_carries_the_fee_and_counts_once(self):
		"""A move is not a new sale: the replacement keeps the ORIGINAL's fee
		and unit number; the cancelled original stops counting, so the month's
		count is unchanged and the customer pays no second fee."""
		first = self._book(start_time="10:00:00")
		second = self._book(start_time="11:00:00")
		self.assertEqual(second.platform_fee_seq, 2)

		with patch(CLOCK, return_value=T1):
			result = reschedule_booking(first.name, start_time="15:00:00")
		self._cleanup_booking(result["name"])
		moved = self._reload(result["name"])
		self.assertEqual(moved.platform_fee_seq, 1)
		self.assertEqual(flt(moved.platform_fee), 15.0)
		self.assertEqual(flt(moved.total_amount), 315.0)
		self.assertEqual(self._reload(first.name).booking_status, "Cancelled")
		self.assertEqual(next_ordinal(AYALA, DAY), 3)
		# The dialog's quote for that move, with the carried fee sent back.
		frappe.set_user(STELLA)
		quote = get_quote(
			MAKATI_A, 1, booking_date=DAY, start_time="16:00:00",
			hourly_rate=300, discount_percent=0, platform_fee=15,
		)
		self.assertEqual(flt(quote["platform_fee"]), 15.0)
		self.assertEqual(flt(quote["total_amount"]), 315.0)
		# ...and "carries none" is a real value: 0 must not fall back to the tiers.
		quote = get_quote(
			MAKATI_A, 1, booking_date=DAY, start_time="16:00:00",
			hourly_rate=300, discount_percent=0, platform_fee=0,
		)
		self.assertEqual(flt(quote["platform_fee"]), 0.0)
		self.assertEqual(flt(quote["total_amount"]), 300.0)

	def test_the_fee_param_refuses_garbage(self):
		frappe.set_user(STELLA)
		for bad in ("abc", "nan", -1):
			with self.subTest(fee=bad):
				self.assertRaisesRegex(
					frappe.ValidationError, "Booking fee", get_quote, MAKATI_A, 1, platform_fee=bad
				)

	def test_the_counter_is_per_service_month_and_per_tenant(self):
		"""May 31 is still May's unit 3; June's first booking is June's unit 1
		whatever May sold; and a Subscription tenant's customers pay nothing."""
		self._book(start_time="10:00:00")
		self._book(start_time="11:00:00")
		last_of_may = self._book(date="2028-05-31", start_time="10:00:00")
		self.assertEqual(last_of_may.platform_fee_seq, 3)
		self.assertEqual(flt(last_of_may.platform_fee), 30.0)
		june = self._book(date="2028-06-02", start_time="10:00:00")  # Friday
		self.assertEqual(june.platform_fee_seq, 1)
		self.assertEqual(flt(june.platform_fee), 15.0)
		self.assertEqual(next_ordinal(AYALA, "2028-06-15"), 2)
		qcsm = self._book(court=QCSM_1, start_time="10:00:00")
		self.assertEqual(flt(qcsm.platform_fee), 0.0)
		self.assertEqual(qcsm.platform_fee_seq, 0)
		self.assertEqual(flt(qcsm.total_amount), 350.0)

	def test_a_fee_unit_moves_between_live_months_but_never_across_a_closed_one(self):
		"""Both months live: the move is a transfer — May loses the unit, June
		gains it, the fee unchanged. May CLOSED: the move is refused, because the
		frozen May figures would keep a fee June also counts (the ducky's
		finding, 2026-08-27 — the one path that could bill a fee twice)."""
		first = self._book(start_time="10:00:00")
		second = self._book(start_time="11:00:00")
		with patch(CLOCK, return_value=T1):
			result = reschedule_booking(first.name, booking_date="2028-06-02", start_time="10:00:00")
		self._cleanup_booking(result["name"])
		moved = self._reload(result["name"])
		self.assertEqual(flt(moved.platform_fee), 15.0)
		self.assertEqual(next_ordinal(AYALA, DAY), 2)  # May: only `second` counts now
		self.assertEqual(next_ordinal(AYALA, "2028-06-15"), 2)  # June: the moved unit

		def _drop_close():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Platform Month Close", PERIOD, force=True, ignore_permissions=True, ignore_missing=True
			)

		self.addCleanup(_drop_close)
		with patch(CLOCK, return_value=AFTER_MAY):
			close_month(2028, 5)
			# Out of the closed month: refused, naming it.
			with self.assertRaisesRegex(frappe.ValidationError, "2028-05 is a closed month"):
				reschedule_booking(second.name, booking_date="2028-06-09", start_time="10:00:00")
			# Into the closed month: refused the same way.
			with self.assertRaisesRegex(frappe.ValidationError, "2028-05 is a closed month"):
				reschedule_booking(moved.name, booking_date="2028-05-10", start_time="10:00:00")
			# Within the closed month a move is still a move (nothing crosses).
			inside = reschedule_booking(second.name, start_time="15:00:00")
		self._cleanup_booking(inside["name"])
		self.assertEqual(flt(self._reload(inside["name"]).platform_fee), 15.0)
		self.assertEqual(self._reload(second.name).booking_status, "Cancelled")

	def test_the_stored_fee_is_pinned_against_a_write(self):
		"""`read_only` is a UI property. A staff seat with write on its own
		bookings could otherwise zero the fee the tenant owes us and the invoice
		sync would copy it (the ducky's finding, 2026-08-27)."""
		booking = self._book()
		frappe.set_user(STELLA)
		doc = frappe.get_doc("CBT Court Booking", booking.name)
		doc.platform_fee = 0
		doc.platform_fee_seq = 0
		doc.notes = "trying my luck"
		doc.save()
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		saved = self._reload(booking.name)
		self.assertEqual(flt(saved.platform_fee), 15.0)
		self.assertEqual(saved.platform_fee_seq, 1)
		self.assertEqual(flt(saved.total_amount), 315.0)
		self.assertEqual(flt(self._invoice(booking.name).platform_fee), 15.0)

	def test_the_desk_api_and_the_portal_path_both_charge_it(self):
		"""create_booking (the desk) and a customer-created insert (the portal's
		reserve path) both go through the controller — one place, one fee."""
		with patch(CLOCK, return_value=T0):
			created = create_booking(
				court=MAKATI_A, booking_date=DAY, start_time="10:00:00",
				payment_method="Cash", customer=PIA,
			)
		self._cleanup_booking(created["name"])
		self.assertEqual(flt(self._reload(created["name"]).platform_fee), 15.0)

		frappe.set_user(PIA)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": MAKATI_A,
				"customer": PIA,
				"booking_date": DAY,
				"start_time": "11:00:00",
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
				"discount_percent": 0,
			}
		)
		doc.flags.customer_created = True
		with patch(CLOCK, return_value=T0):
			doc.insert(ignore_permissions=True)
		self._cleanup_booking(doc.name)
		self.assertEqual(doc.platform_fee_seq, 2)
		self.assertEqual(flt(doc.platform_fee), 15.0)
		self.assertEqual(flt(doc.total_amount), 315.0)


class TestFeeReports(FeeTestCase):
	"""₱15 + ₱15 + ₱30 = ₱60 of fees on ₱900 of court time, May 2028."""

	def _cast(self):
		self._book(start_time="10:00:00")
		self._book(start_time="11:00:00")
		self._book(start_time="12:00:00")
		self._book(court=QCSM_1, start_time="10:00:00")  # a Subscription tenant, ₱350

	def test_company_revenue_excludes_the_fees_and_shows_them_beside(self):
		self._cast()
		_columns, rows = cbt_company_revenue.execute(
			{"company": AYALA, "from_date": DAY, "to_date": DAY}
		)
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["branch"], "AYALA-makati")
		self.assertEqual(row["bookings_count"], 3)
		self.assertEqual(flt(row["bookings_revenue"]), 900.0)
		self.assertEqual(flt(row["total_revenue"]), 900.0)
		self.assertEqual(flt(row["platform_fees"]), 60.0)
		self.assertIn("platform_fees", [c["fieldname"] for c in _columns])

	def test_platform_revenue_bills_the_fees_collected(self):
		self._cast()
		rows = {r["company"]: r for r in cbt_platform_revenue.compute_rows(2028, 5)}
		ayala = rows[AYALA]
		self.assertEqual(ayala["billing_mode"], "Per Booking")
		self.assertEqual(ayala["booking_count"], 3)
		self.assertEqual(flt(ayala["booking_fees"]), 60.0)
		self.assertEqual(flt(ayala["confirmed_revenue"]), 900.0)
		self.assertEqual(flt(ayala["amount_due"]), 60.0)
		qcsm = rows[QCSM]
		self.assertEqual(qcsm["booking_count"], 0)
		self.assertEqual(flt(qcsm["booking_fees"]), 0.0)
		self.assertEqual(flt(qcsm["confirmed_revenue"]), 350.0)
		self.assertEqual(flt(qcsm["amount_due"]), 2999.0)
		# The hand-built total row sums the two additive fee figures too.
		_columns, data, _message = cbt_platform_revenue.execute({"year": 2028, "month": 5})
		total = data[-1]
		self.assertTrue(total.get("is_total_row"))
		self.assertEqual(total["booking_count"], 3)
		self.assertEqual(flt(total["booking_fees"]), 60.0)

	def test_the_close_and_the_statement_carry_the_units(self):
		self._cast()

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
		with patch(CLOCK, return_value=AFTER_MAY):
			close_month(2028, 5)
			issued = issue_statements(PERIOD)
		self.assertTrue(
			any(name.startswith("PST-AYALA-2028-") for name in issued["issued"]), issued
		)

		close = frappe.get_doc("CBT Platform Month Close", PERIOD)
		row = next(r for r in close.rows if r.company == AYALA)
		self.assertEqual(row.booking_count, 3)
		self.assertEqual(flt(row.booking_fees), 60.0)
		self.assertEqual(flt(row.amount_due), 60.0)

		statement = frappe.get_doc(
			"CBT Platform Statement",
			frappe.get_all(
				"CBT Platform Statement", filters={"period": PERIOD, "company": AYALA}, pluck="name"
			)[0],
		)
		self.assertEqual(statement.billing_mode, "Per Booking")
		self.assertEqual(statement.booking_count, 3)
		self.assertEqual(flt(statement.booking_fees), 60.0)
		self.assertEqual(flt(statement.amount_due), 60.0)
		self.assertEqual(flt(statement.confirmed_revenue), 900.0)
		html = frappe.get_print("CBT Platform Statement", statement.name, "CBT Platform Statement")
		self.assertIn("Booking fees collected from customers for May 2028", html)
		self.assertIn("3 paid booking(s)", html)
		self.assertIn("60.00", html)


class TestOpenPlayFee(FeeTestCase):
	"""Open play never enters the booking count; it has its own flat
	per-participant add-on (the user's "how many we charge as addon for open
	play persons", read as per participant)."""

	OP_DATE = "2028-05-06"  # Saturday, AYALA-bgc, open every day
	PLAYERS = [row[0] for row in OPEN_PLAY_CUSTOMERS]

	def _session(self):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "AYALA-bgc",
				"title": "Fee Open Play",
				"session_date": self.OP_DATE,
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

		def _cleanup():
			frappe.set_user("Administrator")
			for block in frappe.get_all("CBT Slot Block", filters={"open_play_session": doc.name}, pluck="name"):
				frappe.delete_doc("CBT Slot Block", block, force=True, ignore_permissions=True)
			frappe.delete_doc(
				"CBT Open Play Session", doc.name, force=True, ignore_permissions=True, ignore_missing=True
			)

		self.addCleanup(_cleanup)
		with patch(CLOCK, return_value=datetime(2028, 5, 6, 8, 30)):
			open_session(doc.name)
		return doc.name

	def test_each_paying_participant_carries_the_flat_fee(self):
		session = self._session()
		with patch(CLOCK, return_value=datetime(2028, 5, 6, 9, 0)):
			add_players(
				session,
				frappe.as_json(
					[
						{"customer": self.PLAYERS[0], "payment_method": "Cash"},
						{"customer": self.PLAYERS[1], "payment_method": "Cash"},
						{"customer": self.PLAYERS[2], "payment_method": "Free"},
					]
				),
			)
		doc = frappe.get_doc("CBT Open Play Session", session)
		fees = [flt(row.platform_fee) for row in doc.participants]
		self.assertEqual(fees, [10.0, 10.0, 0.0])
		# The tenant's own revenue counter excludes the fee...
		self.assertEqual(flt(doc.total_revenue), 300.0)
		# ...while each participant's document adds it on top of the session fee.
		totals = [flt(frappe.get_doc("CBT Booking Invoice", row.billing_doc).total_amount) for row in doc.participants]
		self.assertEqual(totals, [160.0, 160.0, 0.0])
		invoice = frappe.get_doc("CBT Booking Invoice", doc.participants[0].billing_doc)
		self.assertEqual(flt(invoice.platform_fee), 10.0)
		self.assertEqual(flt(invoice.subtotal), 150.0)
		# AYALA is VAT, so the line says "(platform, non-VAT)" — B29's print.
		self.assertIn("Booking fee (platform, non-VAT)", frappe.get_print("CBT Booking Invoice", invoice.name, "CBT Billing Statement"))

		# Two fee units of ₱10 on the platform's side, none in the booking counter.
		self.assertEqual(next_ordinal(AYALA, self.OP_DATE), 1)
		ayala = next(r for r in cbt_platform_revenue.compute_rows(2028, 5) if r["company"] == AYALA)
		self.assertEqual(ayala["booking_count"], 2)
		self.assertEqual(flt(ayala["booking_fees"]), 20.0)
		self.assertEqual(flt(ayala["confirmed_revenue"]), 300.0)
		self.assertEqual(flt(ayala["amount_due"]), 20.0)


class TestFeesForAWholeCart(FeeTestCase):
	"""Backlog B35: `booking_fees_for_units` — quoting several units at once.

	The B35 row asked for the consecutive-ordinal rule to be pinned HERE, and it
	belongs here: `tests/test_cart.py` proves it end to end through the
	endpoint, while these rows pin the helper's own contract against the tier
	math this module owns. A UNIT is one contiguous run — the portal's cart
	normalizer merges adjacent selections into one before anything reaches this
	function, so four consecutive hours on one court arrive as ONE date.
	"""

	def test_the_ordinal_advances_across_the_units(self):
		"""THE defect this helper exists for. `booking_fee` re-reads
		next_ordinal on every call and nothing is inserted while quoting, so
		three separate calls all price at ordinal 1 — ₱15/₱15/₱15 against the
		real ₱15/₱15/₱30."""
		self.assertEqual(next_ordinal(AYALA, DAY), 1)
		self.assertEqual(
			booking_fees_for_units(AYALA, [DAY, DAY, DAY], "Fund Transfer"),
			[(1, 15.0), (2, 15.0), (3, 30.0)],
		)
		# The bug, stated as the thing that would otherwise have shipped.
		self.assertNotEqual(
			[booking_fee(AYALA, DAY, "Fund Transfer") for _ in range(3)],
			booking_fees_for_units(AYALA, [DAY, DAY, DAY], "Fund Transfer"),
		)

	def test_each_service_month_keeps_its_own_numbering(self):
		"""Attribution is per service month everywhere in this app, so one cart
		spanning a boundary legitimately holds two different ordinal 1s."""
		june = "2028-06-07"
		self.assertEqual(
			booking_fees_for_units(AYALA, [DAY, june, DAY, june], "Fund Transfer"),
			[(1, 15.0), (1, 15.0), (2, 15.0), (2, 15.0)],
		)

	def test_it_starts_from_what_the_month_has_already_sold(self):
		"""Not a fresh counter per cart — it seeds from the DB once per month
		and walks on from there."""
		self._book(start_time="13:00:00", method="Cash")
		self.assertEqual(next_ordinal(AYALA, DAY), 2)
		self.assertEqual(
			booking_fees_for_units(AYALA, [DAY, DAY], "Fund Transfer"),
			[(2, 15.0), (3, 30.0)],
		)

	def test_the_gates_match_booking_fee_exactly(self):
		"""Free carries no unit, and neither does a tenant not billed Per
		Booking — the same two refusals `booking_fee` makes, same shape."""
		self.assertEqual(
			booking_fees_for_units(AYALA, [DAY, DAY], "Free"), [(0, 0.0), (0, 0.0)]
		)
		_restore_ayala()  # back to Percentage
		self.assertEqual(
			booking_fees_for_units(AYALA, [DAY, DAY], "Fund Transfer"),
			[(0, 0.0), (0, 0.0)],
		)

	def test_no_units_is_no_rows(self):
		self.assertEqual(booking_fees_for_units(AYALA, [], "Fund Transfer"), [])
