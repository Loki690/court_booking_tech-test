"""
Court Booking Tech — THE DESK BOOKS A CART (Backlog B46, 2026-09-05)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_desk_cart

The user, 2026-09-05: *"THE CLIENT CAN NOW BOOK MULTIPLE DATES, MULTIPLE COURTS,
MULTIPLE TIME AND THE CBT-COURT-BOARD STILL RELY ON FUCKING SLOT AS INTEGER."*

A customer booking two courts online has had ONE payment, ONE countdown and ONE
billing statement since B35/B36. The same customer phoning the desk got two of
each, because the operator had no way to express the request: Quick Book took a
court, a start and an INTEGER. So the product's whole reconciliation story was
available to strangers on the internet and not to the staff taking the money.

WHAT IS UNDER TEST HERE is `api.bookings.create_desk_cart` and
`api.board.get_desk_cart_quote` — the staff twins of `reserve_cart` /
`get_cart_quote`. They share the normalizer, the arithmetic and the group stamp
with the portal (that sharing is the point), and differ in exactly three places:
the GATE, the IDENTITY (an account OR a walk-in) and the PAYMENT METHOD (Cash
and Free exist only at the desk).

⚠ `reserve_cart` IS NOT GENERALISED, and must not be:
`test_isolation.test_reserve_cart_takes_no_customer_parameter` pins its
signature precisely because a list-shaped payload is where a caller-named
customer would look natural.

THE ROW THAT MATTERS MOST IS THE CREDIT ONE, and it started as a finding against
the PORTAL. `credits.take_credit` runs per BOOKING and spends from exactly ONE
credit document — so a cart's spend is a WALK, not `min(balance, total)`, which
is what the customer's checkout printed until this batch. TestCartCredit builds
the case where the two answers differ by ₱100 and pins the walk.

CAST. AYALA-makati ("Makati Arena") — two Badminton courts at ₱300 FLAT with no
rate rules, open 06:00-22:00 every day, buffer 0, so the grid is one slot per
hour from 06:00 and every peso below is hand-checkable. AYALA ships
billing_mode=Percentage, so no platform fee lands on these totals.

MONTH. **November 2028** — claimed by no other module (test_booking_fee owns May
2028, test_cart September 2028, test_billing February 2027).

Every booking is deleted in addCleanup: FrappeTestCase has no per-test savepoint
on this bench.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt

from court_booking_tech import credits
from court_booking_tech.api.board import get_desk_cart_quote
from court_booking_tech.api.bookings import create_desk_cart
from court_booking_tech.seeds.seed_test_data import seed_all

AYALA = "ayala-courts"
MAKATI_A = "AYALA-makati-court-a"  # ₱300 flat, no rules
MAKATI_B = "AYALA-makati-court-b"  # ₱300 flat, no rules
BGC_1 = "AYALA-bgc-court-1"  # same company, DIFFERENT branch
QCSM_1 = "QCSM-timog-court-1"  # different company

STELLA = "staff.ayala@example.com"  # AYALA front desk
QUINTIN = "admin.qcsm@example.com"  # QC Smash — the cross-tenant probe
PIA = "cust.pia@example.com"  # a customer: no AYALA membership by seed design

DAY = "2028-11-14"
NEXT_DAY = "2028-11-15"
NOW = datetime(2028, 11, 14, 8, 0)  # before every 13:00 slot booked below
CLOCK = "court_booking_tech.clock.now_dt"

RATE = 300.0


class DeskCartTestCase(FrappeTestCase):
	"""Shared fixtures; holds NO test methods (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	# -- helpers -----------------------------------------------------------

	def _item(self, court=MAKATI_A, start_time="13:00:00", date=DAY):
		"""ONE SLOT. The client always sends slots; the server merges them into
		runs, which is the whole reason a desk cart and a portal cart cannot
		disagree about what "one booking" is."""
		return {
			"court": court,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": 1,
		}

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Court Booking",
				name,
				force=True,
				ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	def _book(self, items, user=STELLA, at=NOW, payment_method="Cash", **kwargs):
		kwargs.setdefault("customer", PIA)
		frappe.set_user(user)
		with patch(CLOCK, return_value=at):
			result = create_desk_cart(items, payment_method=payment_method, **kwargs)
		for row in result["bookings"]:
			self._cleanup_booking(row["name"])
		return result

	def _quote(self, items, user=STELLA, at=NOW, **kwargs):
		frappe.set_user(user)
		with patch(CLOCK, return_value=at):
			return get_desk_cart_quote(items, **kwargs)

	def _doc(self, name):
		frappe.set_user("Administrator")
		return frappe.get_doc("CBT Court Booking", name)


class TestDeskCartShape(DeskCartTestCase):
	"""What the desk can now EXPRESS, and what the server makes of it."""

	def test_two_courts_on_one_date_are_two_bookings_one_group_and_one_document(self):
		"""B46's whole point, and B36's: the customer at the counter gets ONE
		payment and ONE piece of paper, exactly as they would online."""
		result = self._book([self._item(MAKATI_A), self._item(MAKATI_B)])

		self.assertEqual(result["count"], 2)
		self.assertTrue(result["booking_group"], "two runs must share a group")
		self.assertTrue(result["booking_group"].startswith("CART-AYALA-"), result)

		invoices = {
			self._doc(row["name"]).billing_doc for row in result["bookings"]
		}
		self.assertEqual(len(invoices), 1, f"two documents for one cart: {invoices}")
		invoice = frappe.get_doc("CBT Booking Invoice", invoices.pop())
		self.assertEqual(invoice.booking_group, result["booking_group"])
		self.assertEqual(flt(invoice.total_amount, 2), flt(RATE * 2, 2))
		self.assertEqual(flt(result["total_amount"], 2), flt(RATE * 2, 2))

	def test_two_adjacent_hours_on_one_court_are_ONE_booking_and_no_group(self):
		"""The merge is the SERVER's rule (_normalise_cart), which is why the
		board can stay a dumb set of taps. One booking is not a cart, so it
		takes no group and its statement is the ordinary 1:1 one."""
		result = self._book(
			[self._item(start_time="13:00:00"), self._item(start_time="14:00:00")]
		)
		self.assertEqual(result["count"], 1)
		self.assertIsNone(result["booking_group"])
		doc = self._doc(result["bookings"][0]["name"])
		self.assertEqual(cint(doc.number_of_slots), 2)
		self.assertEqual(str(doc.start_time), "13:00:00")
		self.assertEqual(flt(doc.total_amount, 2), flt(RATE * 2, 2))

	def test_a_broken_selection_on_one_court_is_two_bookings(self):
		"""1-2pm and 4-5pm on the same court is NOT continuous, so it is two
		bookings — the other half of the same ruling."""
		result = self._book(
			[self._item(start_time="13:00:00"), self._item(start_time="16:00:00")]
		)
		self.assertEqual(result["count"], 2)
		self.assertTrue(result["booking_group"])

	def test_a_cart_can_span_two_dates(self):
		"""Saturday AND Sunday in one payment — the thing the integer could not
		say at all."""
		result = self._book(
			[self._item(date=DAY), self._item(date=NEXT_DAY)]
		)
		self.assertEqual(result["count"], 2)
		dates = sorted(str(row["booking_date"]) for row in result["bookings"])
		self.assertEqual(dates, [DAY, NEXT_DAY])

	def test_a_cart_cannot_mix_branches(self):
		"""_cart_courts refuses it outright, which is why the board clears its
		cart when the branch selector changes rather than carrying picks the
		server would reject."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._book([self._item(MAKATI_A), self._item(BGC_1)])
		self.assertIn("one branch at a time", str(caught.exception))


class TestDeskCartIdentityAndMoney(DeskCartTestCase):
	def test_cash_confirms_every_row_instantly(self):
		result = self._book(
			[self._item(MAKATI_A), self._item(MAKATI_B)], payment_method="Cash"
		)
		self.assertEqual(result["booking_status"], "Confirmed")
		for row in result["bookings"]:
			doc = self._doc(row["name"])
			self.assertEqual(doc.booking_status, "Confirmed")
			self.assertIsNone(doc.reservation_expires_at)
			self.assertEqual(
				frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status"),
				"Paid & Verified",
			)

	def test_fund_transfer_holds_every_row_on_ONE_clock(self):
		"""B35's rule, restated at the desk: a cart is ONE hold with ONE pay-by
		time, or the customer is chasing three different deadlines for one
		payment."""
		result = self._book(
			[self._item(MAKATI_A), self._item(MAKATI_B)],
			payment_method="Fund Transfer",
		)
		self.assertEqual(result["booking_status"], "Reserved")
		clocks = {
			str(self._doc(row["name"]).reservation_expires_at)
			for row in result["bookings"]
		}
		self.assertEqual(len(clocks), 1, f"a cart must hold ONE clock: {clocks}")

	def test_a_walk_in_may_hold_a_cart(self):
		"""DECIDED 2026-09-05, and it is the commonest desk cart there is:
		"three courts for the tournament, cash". Nothing in the group path needs
		an account — billing._billable_rows groups on the stamp alone and the
		statement prints customer_name."""
		result = self._book(
			[self._item(MAKATI_A), self._item(MAKATI_B)],
			customer=None,
			customer_name="Tournament Tess",
			customer_phone="0917 555 0101",
		)
		self.assertEqual(result["count"], 2)
		self.assertTrue(result["booking_group"])
		for row in result["bookings"]:
			doc = self._doc(row["name"])
			self.assertIsNone(doc.customer)
			self.assertEqual(doc.customer_name, "Tournament Tess")
		invoice = frappe.get_doc(
			"CBT Booking Invoice",
			self._doc(result["bookings"][0]["name"]).billing_doc,
		)
		self.assertEqual(invoice.customer_name, "Tournament Tess")

	def test_both_identities_at_once_is_refused(self):
		"""Section-13: an API caller who sends both has declared two conflicting
		intents and is told so, rather than having one silently dropped."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._book(
				[self._item(MAKATI_A)], customer=PIA, customer_name="Both At Once"
			)
		self.assertIn("not both", str(caught.exception))

	def test_neither_identity_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._book([self._item(MAKATI_A)], customer=None)

	def test_the_quote_is_what_the_cart_charges(self):
		"""WYSIWYG money (S18/B4) across a whole basket: the figure the operator
		reads out is the figure the inserts produce, to the centavo."""
		items = [
			self._item(MAKATI_A, "13:00:00"),
			self._item(MAKATI_A, "14:00:00"),
			self._item(MAKATI_B, "16:00:00"),
		]
		quote = self._quote(items, customer=PIA, discount_percent=10)
		result = self._book(items, customer=PIA, discount_percent=10)
		self.assertEqual(quote["count"], 2, "13-15 merges; 16-17 stands alone")
		self.assertEqual(result["count"], 2)
		self.assertEqual(
			flt(quote["total_amount"], 2), flt(result["total_amount"], 2)
		)
		# 3 hours at ₱300 less 10% = ₱810
		self.assertEqual(flt(result["total_amount"], 2), 810.0)

	def test_a_discount_of_zero_is_a_real_value(self):
		"""S11 as-built 5 / S18 B4: an untouched Percent field reads 0, and the
		board always sends what staff can SEE. A member must not be silently
		re-discounted behind the operator's back."""
		result = self._book([self._item(MAKATI_A)], discount_percent=0)
		self.assertEqual(flt(self._doc(result["bookings"][0]["name"]).discount_percent), 0.0)
		self.assertEqual(flt(result["total_amount"], 2), RATE)


class TestDeskCartRefusals(DeskCartTestCase):
	def test_a_blocked_hour_refuses_the_WHOLE_cart_and_leaves_nothing_behind(self):
		"""All-or-nothing. A partial cart is worse than a refusal: the operator
		has already said a total out loud."""
		frappe.set_user("Administrator")
		block = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "AYALA-makati",
				"company": AYALA,
				"court": MAKATI_B,
				"block_date": DAY,
				"start_time": "13:00:00",
				"end_time": "14:00:00",
				"reason": "Maintenance",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc(
				"CBT Slot Block", block.name, force=True, ignore_permissions=True
			)
		)
		before = frappe.db.count("CBT Court Booking", {"booking_date": DAY})

		with self.assertRaises(frappe.ValidationError) as caught:
			self._book([self._item(MAKATI_A), self._item(MAKATI_B)])
		self.assertIn("no longer available", str(caught.exception))
		# And it names the slot in the app's ONE time language (B45).
		self.assertIn("1 PM", str(caught.exception))

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.count("CBT Court Booking", {"booking_date": DAY}),
			before,
			"a refused cart booked something anyway",
		)

	def test_a_customer_cannot_book_a_desk_cart(self):
		"""Fail-closed tenancy: a portal customer holds no CBT Company User
		binding, so require_company_access refuses before anything is read."""
		with self.assertRaises(frappe.PermissionError):
			self._book([self._item(MAKATI_A)], user=PIA)

	def test_cross_tenant_staff_are_refused(self):
		"""QC Smash's admin cannot book Ayala's courts — through the cart any
		more than through create_booking."""
		with self.assertRaises(frappe.PermissionError):
			self._book([self._item(MAKATI_A)], user=QUINTIN)

	def test_the_company_cap_applies_to_the_desk_too(self):
		"""max_cart_items is the TENANT's own knob, so the desk honours it: one
		rule for both faces beats two."""
		frappe.set_user("Administrator")
		company = frappe.get_doc("CBT Company", AYALA)
		previous = company.max_cart_items
		company.max_cart_items = 2
		company.save(ignore_permissions=True)

		def _restore():
			frappe.set_user("Administrator")
			doc = frappe.get_doc("CBT Company", AYALA)
			doc.max_cart_items = previous
			doc.save(ignore_permissions=True)

		self.addCleanup(_restore)

		with self.assertRaises(frappe.ValidationError) as caught:
			self._book(
				[
					self._item(MAKATI_A, "13:00:00"),
					self._item(MAKATI_A, "16:00:00"),
					self._item(MAKATI_B, "13:00:00"),
				]
			)
		self.assertIn("at most 2 bookings", str(caught.exception))

	def test_the_proof_hold_cap_binds_fund_transfer_and_NOT_cash(self):
		"""The slot cap is the PAYABILITY guarantee — it exists because
		_check_customer_holds_cap runs at proof upload, so an over-large hold
		would book and then fail to be payable. A Cash sale mints no hold and
		needs no receipt: the money is in the drawer before Book is clicked, and
		capping a tournament's twelve courts at the proof limit would refuse a
		sale the facility has already taken."""
		frappe.set_user("Administrator")
		settings = frappe.get_single("CBT Platform Settings")
		previous = settings.max_active_proof_holds_per_customer
		settings.max_active_proof_holds_per_customer = 2
		settings.save(ignore_permissions=True)
		frappe.db.commit()

		def _restore():
			frappe.set_user("Administrator")
			doc = frappe.get_single("CBT Platform Settings")
			doc.max_active_proof_holds_per_customer = previous
			doc.save(ignore_permissions=True)
			frappe.db.commit()

		self.addCleanup(_restore)

		three_hours = [
			self._item(MAKATI_A, "13:00:00"),
			self._item(MAKATI_A, "14:00:00"),
			self._item(MAKATI_A, "15:00:00"),
		]
		with self.assertRaises(frappe.ValidationError) as caught:
			self._book(three_hours, payment_method="Fund Transfer")
		self.assertIn("3 hours in one checkout", str(caught.exception))

		# The SAME cart, paid in cash, goes through.
		result = self._book(three_hours, payment_method="Cash")
		self.assertEqual(result["count"], 1)
		self.assertEqual(cint(self._doc(result["bookings"][0]["name"]).number_of_slots), 3)


class TestDeskCartCredit(DeskCartTestCase):
	"""THE ROW THIS BATCH FOUND, and it was a defect on the PORTAL first.

	`credits.take_credit` runs per BOOKING and spends from ONE credit document —
	the oldest Active one with anything left, capped at THAT row's total. So a
	cart's spend is a WALK, and `min(available, cart_total)` — which is what the
	customer's checkout printed until 2026-09-05 — can promise money the inserts
	never spend.
	"""

	def _credit(self, amount, customer=PIA):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "CBT Customer Credit",
				"company": AYALA,
				"customer": customer,
				"amount": amount,
				"balance": amount,
				"status": "Active",
				"reason": "test_desk_cart",
			}
		).insert(ignore_permissions=True)

		def _park():
			# ⚠ A credit CANNOT be deleted — the controller refuses it ("Store
			# credit cannot be deleted"), because a spent liability that can
			# vanish is not a liability. So the cleanup VOIDS it, which is the
			# same thing the product does, and which every query in credits.py
			# filters on.
			frappe.set_user("Administrator")
			frappe.db.set_value(
				"CBT Customer Credit",
				doc.name,
				{"status": "Void", "balance": 0},
				update_modified=False,
			)

		self.addCleanup(_park)
		return doc

	def _void_other_credits(self):
		"""Pia may already hold seeded credit at AYALA; this class needs to own
		the balance it reasons about, so anything pre-existing is parked and put
		back afterwards."""
		frappe.set_user("Administrator")
		existing = frappe.get_all(
			"CBT Customer Credit",
			filters={"company": AYALA, "customer": PIA, "status": "Active"},
			pluck="name",
		)
		for name in existing:
			frappe.db.set_value("CBT Customer Credit", name, "status", "Void")

		def _restore():
			frappe.set_user("Administrator")
			for name in existing:
				frappe.db.set_value("CBT Customer Credit", name, "status", "Active")

		self.addCleanup(_restore)

	def test_the_plan_is_a_WALK_not_a_cap(self):
		"""₱500 + ₱500 against three ₱300 rows settles ₱800, not ₱900 — because
		row 2 finds only ₱200 left in the first document and row 3 starts a
		fresh one. This is the pure planner, asserted against the arithmetic."""
		self._void_other_credits()
		self._credit(500)
		self._credit(500)
		planned = credits.plan_spend(AYALA, PIA, [300, 300, 300])
		self.assertEqual(planned, [300.0, 200.0, 300.0])
		self.assertEqual(flt(sum(planned), 2), 800.0)
		# The number the screens used to print, which is NOT the same.
		self.assertNotEqual(
			flt(sum(planned), 2),
			min(credits.available_credit(AYALA, PIA), 900.0),
			"the plan has collapsed back into min(balance, total)",
		)

	def test_the_quote_prints_the_plan_and_the_cart_spends_it(self):
		"""End to end: the figure on the screen and the money the inserts move
		are the same, on the case where the old rule differed."""
		self._void_other_credits()
		self._credit(500)
		self._credit(500)
		items = [
			self._item(MAKATI_A, "13:00:00"),
			self._item(MAKATI_A, "15:00:00"),
			self._item(MAKATI_B, "13:00:00"),
		]
		quote = self._quote(items, customer=PIA)
		self.assertEqual(quote["count"], 3)
		self.assertEqual(flt(quote["total_amount"], 2), 900.0)
		self.assertEqual(flt(quote["credit_available"], 2), 1000.0)
		self.assertEqual(flt(quote["credit_spend"], 2), 800.0)

		result = self._book(items, customer=PIA, apply_credit=1)
		self.assertEqual(
			flt(result["credit_applied"], 2),
			flt(quote["credit_spend"], 2),
			"the screen and the bookings disagree about the credit spent",
		)
		self.assertEqual(flt(result["credit_applied"], 2), 800.0)

	def test_a_walk_in_spends_no_credit(self):
		"""There is no account to hold it. credits.take_credit returns early on
		a missing customer, and the planner answers 0 for the same reason."""
		self._void_other_credits()
		self._credit(500)
		self.assertEqual(credits.plan_spend(AYALA, None, [300]), [0.0])
		result = self._book(
			[self._item(MAKATI_A)],
			customer=None,
			customer_name="No Account Nina",
			apply_credit=1,
		)
		self.assertEqual(flt(result["credit_applied"], 2), 0.0)


class TestDeskCartRunningHour(DeskCartTestCase):
	def test_the_desk_may_cart_the_hour_that_is_running(self):
		"""S4 as-built 6 / S18 B7: staff back-record a walk-in who turned up
		mid-session, and create_booking has always accepted it. The cart's
		pre-insert availability check reads the STAFF grid for exactly this
		reason — with the portal's past-line it would refuse the very hour the
		board offered."""
		mid_session = datetime(2028, 11, 14, 13, 30)
		result = self._book([self._item(MAKATI_A, "13:00:00")], at=mid_session)
		self.assertEqual(result["count"], 1)
		self.assertEqual(
			str(self._doc(result["bookings"][0]["name"]).start_time), "13:00:00"
		)
