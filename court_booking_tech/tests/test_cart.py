"""
Court Booking Tech — Cart: several courts and several dates, one checkout
(Backlog B35, batch 4a-i, 2026-09-04)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_cart

THE UNIT UNDER TEST IS A CONTIGUOUS RUN, NOT A CART ITEM. The user's ruling
(2026-09-03) is "per day, then per court, then per continuous hours — if broken
(1pm-2pm and 4pm-5pm) this is counted as 2 booking fee worth", and section-24's
rule is already "one booking = one unit, whatever its length". Those only agree
if the cart is normalized first, and the first review of this batch caught a
design that was not: it charged one fee per ITEM, so a client sending
"Court A 1-2pm" and "Court A 2-3pm" as two items would have paid twice for one
continuous booking. TestCartNormalisation is that finding, pinned.

CAST. AYALA-makati ("Makati Arena") — two Badminton courts at ₱300 FLAT with no
rate rules, open 06:00-22:00 every day, buffer 0. So the grid is one slot per
hour from 06:00 and index k starts at 06:00 + k hours: 13:00 is index 7.
Two courts is exactly what the ruling's fourth scenario needs, and a flat rate
keeps every peso here hand-checkable. PIA holds no AYALA membership by seed
design, so nothing below is discounted.

MONTH. **September 2028**, and this module deliberately claims NO ordinal
ground truth from it. Every fee expectation is derived from `next_ordinal()` at
run time and, where a bracket boundary is needed, the TIERS are built around
that live number instead of assuming the month is empty. That is what makes
these rows independent of test_booking_fee's May 2028 ledger and of each other
— and it is not a tautology: what is asserted is that the cart ADVANCES the
ordinal across its runs, which is the exact defect (`booking_fee` re-reads the
count on every call, so N quotes taken one at a time all price at the SAME
ordinal).

Every booking created here is deleted in addCleanup — FrappeTestCase has no
per-test savepoint on this bench.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt, getdate

from court_booking_tech.api.bookings import create_booking
from court_booking_tech.api.portal import (
	DEFAULT_MAX_CART_ITEMS,
	MAX_CART_PAYLOAD_ENTRIES,
	cancel_my_booking,
	get_cart_quote,
	get_my_bookings,
	reserve_cart,
)
from court_booking_tech.api.proofs import accept_proofs, create_proof, reject_proofs
from court_booking_tech.platform_fees import next_ordinal, tier_fee
from court_booking_tech.seeds.seed_test_data import _proof_sample_bytes, seed_all

# REAL image bytes required: frappe's File controller EXIF-strips images through
# PIL on save, so fake magic-byte blobs are rejected at insert.
JPG = _proof_sample_bytes()

AYALA = "ayala-courts"

PIA = "cust.pia@example.com"  # no AYALA membership — undiscounted by seed design
STELLA = "staff.ayala@example.com"

MAKATI_A = "AYALA-makati-court-a"  # ₱300 flat, no rules
MAKATI_B = "AYALA-makati-court-b"  # ₱300 flat, no rules
BGC_A = "AYALA-bgc-court-1"  # same COMPANY, different BRANCH
QCSM_1 = "QCSM-timog-court-1"  # different company

DAY = "2028-09-06"
NEXT_DAY = "2028-09-07"
NOW = datetime(2028, 9, 6, 8, 0)  # before the 13:00 slots every row below books

CLOCK = "court_booking_tech.clock.now_dt"

# Narrow bands so a module-sized cart crosses a boundary; the user's real bands
# are wide by ruling, which is exactly why a test needs small ones.
TIERS = [
	{"from_count": 1, "to_count": 2, "fee": 15},
	{"from_count": 3, "to_count": 4, "fee": 30},
	{"from_count": 5, "to_count": 0, "fee": 40},
]

RATE = 300.0


def _restore_ayala():
	frappe.set_user("Administrator")
	doc = frappe.get_doc("CBT Company", AYALA)
	doc.billing_mode = "Percentage"
	doc.commission_percent = 10
	doc.set("booking_fee_tiers", [])
	doc.save(ignore_permissions=True)


class CartTestCase(FrappeTestCase):
	"""Shared fixtures; holds NO test methods (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(_restore_ayala)
		self._set_tiers(TIERS)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def _set_tiers(self, tiers, company=AYALA):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("CBT Company", company)
		doc.billing_mode = "Per Booking"
		doc.set("booking_fee_tiers", [])
		for row in tiers:
			doc.append("booking_fee_tiers", row)
		doc.save(ignore_permissions=True)
		return doc

	# -- helpers ---------------------------------------------------------

	def _item(self, court=MAKATI_A, start_time="13:00:00", slots=1, date=DAY):
		return {
			"court": court,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": slots,
		}

	def _quote(self, items, user=PIA, at=NOW):
		frappe.set_user(user)
		with patch(CLOCK, return_value=at):
			return get_cart_quote(items)

	def _reserve(self, items, user=PIA, at=NOW, **kwargs):
		frappe.set_user(user)
		with patch(CLOCK, return_value=at):
			result = reserve_cart(items, **kwargs)
		for row in result["bookings"]:
			self._cleanup_booking(row["booking"])
		return result

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

	def _my_booking_count(self, user=PIA):
		return frappe.db.count("CBT Court Booking", {"customer": user})

	def _upload(self, booking, at=NOW, user=PIA, **kwargs):
		frappe.set_user(user)
		with patch(CLOCK, return_value=at):
			result = create_proof(booking, "proof.jpg", JPG, **kwargs)
		for name in result.get("group_proofs") or [result["proof"]]:
			self._cleanup_proof(name)
		return result

	def _cleanup_proof(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Payment Proof",
				name,
				force=True,
				ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	def _proofs_of(self, booking):
		return frappe.get_all(
			"CBT Payment Proof", filters={"booking": booking}, pluck="name"
		)


class TestContinuation(CartTestCase):
	"""Backlog B49 (2026-09-09): a session that moves to another court is ONE
	booking fee — the user's table, gamed in chat and confirmed row by row.

	MAKATI A and B carry the opt-in flag for these rows only (set in setUp,
	cleared in cleanup); every other seeded court stays unflagged, so no other
	module can chain by accident. Fees are derived from `next_ordinal()` at run
	time, as the rest of this module does.
	"""

	def setUp(self):
		super().setUp()
		for court in (MAKATI_A, MAKATI_B):
			self._flag(court, 1)
			self.addCleanup(self._flag, court, 0)

	@staticmethod
	def _flag(court, value):
		frappe.set_user("Administrator")
		frappe.db.set_value("CBT Court", court, "allow_continuation", value)
		frappe.clear_document_cache("CBT Court", court)

	def _expected_fee(self):
		return tier_fee(TIERS, next_ordinal(AYALA, DAY))

	# -- the ruled table ---------------------------------------------------

	def test_a_session_that_moves_court_is_two_bookings_and_one_fee(self):
		"""A 1–2 PM, B 2–3 PM: the customer's afternoon, forced onto two courts."""
		fee = self._expected_fee()
		quote = self._quote(
			[self._item(court=MAKATI_A, start_time="13:00:00"), self._item(court=MAKATI_B, start_time="14:00:00")]
		)
		self.assertEqual(quote["count"], 2)
		self.assertEqual(quote["booking_fee_count"], 1)
		head, tail = quote["items"]
		self.assertEqual(flt(head["platform_fee"]), fee)
		self.assertIsNone(head["fee_chained_to_index"])
		self.assertEqual(flt(tail["platform_fee"]), 0.0)
		self.assertEqual(tail["fee_chained_to_index"], 0)
		self.assertEqual(flt(tail["platform_fee_waived"]), fee)
		self.assertEqual(flt(quote["platform_fee"]), fee)
		self.assertEqual(flt(quote["continuous_discount"]), fee)
		self.assertEqual(flt(quote["booking_fee_as_if"]), flt(2 * fee, 2))
		self.assertEqual(flt(quote["total_amount"]), flt(2 * RATE + fee, 2))

	def test_three_hours_across_two_courts_are_one_fee(self):
		"""A 1–2, B 2–3, A 3–4: the chain walks back onto the first court and
		the third hour still points at the FIRST hour, not at the second."""
		fee = self._expected_fee()
		quote = self._quote(
			[
				self._item(court=MAKATI_A, start_time="13:00:00"),
				self._item(court=MAKATI_B, start_time="14:00:00"),
				self._item(court=MAKATI_A, start_time="15:00:00"),
			]
		)
		self.assertEqual(quote["count"], 3)
		self.assertEqual(quote["booking_fee_count"], 1)
		by_start = {line["start_time"]: line for line in quote["items"]}
		head_index = quote["items"].index(by_start["13:00:00"])
		self.assertEqual(by_start["14:00:00"]["fee_chained_to_index"], head_index)
		self.assertEqual(by_start["15:00:00"]["fee_chained_to_index"], head_index)
		self.assertEqual(flt(quote["continuous_discount"]), flt(2 * fee, 2))
		self.assertEqual(flt(quote["platform_fee"]), fee)

	def test_an_overlap_is_not_a_continuation(self):
		"""A 1–3, B 2–4: the hours cross, nobody moved court — two fees."""
		quote = self._quote(
			[
				self._item(court=MAKATI_A, start_time="13:00:00", slots=2),
				self._item(court=MAKATI_B, start_time="14:00:00", slots=2),
			]
		)
		self.assertEqual(quote["booking_fee_count"], 2)
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)

	def test_a_gap_is_not_a_continuation(self):
		quote = self._quote(
			[self._item(court=MAKATI_A, start_time="13:00:00"), self._item(court=MAKATI_B, start_time="16:00:00")]
		)
		self.assertEqual(quote["booking_fee_count"], 2)
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)

	def test_parallel_courts_at_the_same_hour_never_chain(self):
		quote = self._quote([self._item(court=MAKATI_A), self._item(court=MAKATI_B)])
		self.assertEqual(quote["booking_fee_count"], 2)
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)

	def test_an_unflagged_court_never_chains(self):
		self._flag(MAKATI_B, 0)
		quote = self._quote(
			[self._item(court=MAKATI_A, start_time="13:00:00"), self._item(court=MAKATI_B, start_time="14:00:00")]
		)
		self.assertEqual(quote["booking_fee_count"], 2)
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)

	def test_another_date_never_chains(self):
		quote = self._quote(
			[
				self._item(court=MAKATI_A, start_time="13:00:00"),
				self._item(court=MAKATI_B, start_time="14:00:00", date=NEXT_DAY),
			]
		)
		self.assertEqual(quote["booking_fee_count"], 2)
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)

	def test_pairing_is_greedy_in_court_name_order(self):
		"""Three courts, no fee (QC Smash is Subscription-billed): the STRUCTURE
		alone. Court 1 and Court 2 both end at 2 PM; Center Court's 2–3 PM
		continues Court 1 — the first by name — and Court 2 stays its own head."""
		courts = ("QCSM-timog-court-1", "QCSM-timog-court-2", "QCSM-timog-center-court")
		for court in courts:
			self._flag(court, 1)
			self.addCleanup(self._flag, court, 0)
		quote = self._quote(
			[
				self._item(court=courts[0], start_time="13:00:00"),
				self._item(court=courts[1], start_time="13:00:00"),
				self._item(court=courts[2], start_time="14:00:00"),
			]
		)
		by_court = {line["court"]: line for line in quote["items"]}
		head_index = quote["items"].index(by_court[courts[0]])
		self.assertEqual(by_court[courts[2]]["fee_chained_to_index"], head_index)
		self.assertIsNone(by_court[courts[1]]["fee_chained_to_index"])
		self.assertEqual(flt(quote["continuous_discount"]), 0.0)  # no fee to waive

	def _clone_court(self, source, slug="b49-pair-probe"):
		"""A fourth QCSM-timog court; the shape below needs four and the seed has three."""
		frappe.set_user("Administrator")
		source_doc = frappe.get_doc("CBT Court", source)
		# Re-entrant: a killed run would otherwise leave a DuplicateEntryError.
		self._drop_court(f"{source_doc.branch}-{slug}")
		doc = frappe.copy_doc(source_doc)
		doc.court_slug = slug
		doc.court_name = "B49 Pair Probe"
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop_court, doc.name)
		return doc.name

	@staticmethod
	def _drop_court(name):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Court",
			name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)

	def test_two_parallel_pairs_form_two_separate_chains(self):
		"""Two 1-2 PM runs and two 2-3 PM runs are TWO chains, not one or four heads.

		Hours are INTERLEAVED against name order so a positional pairing fails.
		"""
		# Sorted on `court_name` — the key `_chain_runs` orders on.
		names = [
			name
			for _, name in sorted(
				(frappe.db.get_value("CBT Court", name, "court_name"), name)
				for name in (
					"QCSM-timog-court-1",
					"QCSM-timog-court-2",
					"QCSM-timog-center-court",
					self._clone_court("QCSM-timog-court-1"),
				)
			)
		]
		for court in names:
			self._flag(court, 1)
			self.addCleanup(self._flag, court, 0)
		quote = self._quote(
			[
				self._item(court=names[1], start_time="13:00:00"),
				self._item(court=names[3], start_time="13:00:00"),
				self._item(court=names[0], start_time="14:00:00"),
				self._item(court=names[2], start_time="14:00:00"),
			]
		)
		self.assertEqual(quote["count"], 4)
		# Subscription-billed: no fee on any line, so chains are counted by heads.
		self.assertEqual(quote["booking_fee_count"], 0)
		heads = [line for line in quote["items"] if line["fee_chained_to_index"] is None]
		self.assertEqual(len(heads), 2)
		by_court = {line["court"]: line for line in quote["items"]}
		index_of = {court: quote["items"].index(by_court[court]) for court in names}
		# First 2 PM run by name takes the first 1 PM run by name; the rest follow.
		self.assertEqual(by_court[names[0]]["fee_chained_to_index"], index_of[names[1]])
		self.assertEqual(by_court[names[2]]["fee_chained_to_index"], index_of[names[3]])
		self.assertIsNone(by_court[names[1]]["fee_chained_to_index"])
		self.assertIsNone(by_court[names[3]]["fee_chained_to_index"])

	# -- the inserted rows -------------------------------------------------

	def _reserve_chain(self):
		result = self._reserve(
			[self._item(court=MAKATI_A, start_time="13:00:00"), self._item(court=MAKATI_B, start_time="14:00:00")]
		)
		docs = [frappe.get_doc("CBT Court Booking", row["booking"]) for row in result["bookings"]]
		head = next(doc for doc in docs if not doc.fee_chained_to)
		tail = next(doc for doc in docs if doc.fee_chained_to)
		return head, tail

	def test_the_rows_carry_the_chain_and_one_unit_advances_the_month(self):
		before = next_ordinal(AYALA, DAY)
		fee = tier_fee(TIERS, before)
		head, tail = self._reserve_chain()
		self.assertEqual(head.platform_fee_seq, before)
		self.assertEqual(flt(head.platform_fee), fee)
		self.assertEqual(flt(head.platform_fee_waived), 0.0)
		self.assertEqual(tail.fee_chained_to, head.name)
		self.assertEqual(tail.platform_fee_seq, 0)
		self.assertEqual(flt(tail.platform_fee), 0.0)
		self.assertEqual(flt(tail.platform_fee_waived), fee)
		self.assertEqual(flt(tail.total_amount), RATE)
		self.assertEqual(next_ordinal(AYALA, DAY), before + 1)

	def test_the_shared_statement_prints_the_fee_and_the_discount(self):
		head, tail = self._reserve_chain()
		fee = flt(head.platform_fee)
		self.assertEqual(head.billing_doc, tail.billing_doc)
		invoice = frappe.get_doc("CBT Booking Invoice", head.billing_doc)
		self.assertEqual(flt(invoice.platform_fee), fee)
		self.assertEqual(flt(invoice.continuous_discount), fee)
		self.assertEqual(flt(invoice.total_amount), flt(2 * RATE + fee, 2))
		# _reserve_chain leaves the session as PIA, who holds no print DocPerm
		# (leak vector 4); the print is the DOCUMENT's, rendered as the system.
		frappe.set_user("Administrator")
		html = frappe.get_print("CBT Booking Invoice", invoice.name, "CBT Billing Statement")
		self.assertIn("Less: Continuous booking discount", html)
		self.assertIn(f"{2 * fee:,.2f}", html)  # the as-if fee line

	def test_cancelling_the_unpaid_head_hands_the_fee_to_the_continuation(self):
		head, tail = self._reserve_chain()
		seq, fee = head.platform_fee_seq, flt(head.platform_fee)
		frappe.set_user(PIA)
		cancel_my_booking(head.name)
		frappe.set_user("Administrator")
		tail = frappe.get_doc("CBT Court Booking", tail.name)
		self.assertEqual(tail.platform_fee_seq, seq)
		self.assertEqual(flt(tail.platform_fee), fee)
		self.assertEqual(flt(tail.platform_fee_waived), 0.0)
		self.assertFalse(tail.fee_chained_to)
		self.assertEqual(flt(tail.total_amount), flt(RATE + fee, 2))
		invoice = frappe.get_doc("CBT Booking Invoice", tail.billing_doc)
		self.assertEqual(flt(invoice.platform_fee), fee)
		self.assertEqual(flt(invoice.continuous_discount), 0.0)
		# The month still counts ONE unit for the session.
		self.assertEqual(
			frappe.db.count(
				"CBT Court Booking",
				{"company": AYALA, "booking_date": DAY, "platform_fee_seq": seq, "booking_status": "Reserved"},
			),
			1,
		)

	def test_a_paid_continuation_keeps_its_waiver_when_the_head_goes(self):
		"""The other half of the ruling: money already in, nothing moves."""
		head, tail = self._reserve_chain()
		frappe.db.set_value("CBT Court Booking", tail.name, "booking_status", "Confirmed")
		doc = frappe.get_doc("CBT Court Booking", head.name)
		doc.booking_status = "Cancelled"
		doc.flags.customer_cancel = True
		doc.save(ignore_permissions=True)
		tail = frappe.get_doc("CBT Court Booking", tail.name)
		self.assertEqual(tail.fee_chained_to, head.name)
		self.assertEqual(flt(tail.platform_fee), 0.0)
		self.assertEqual(flt(tail.platform_fee_waived), flt(head.platform_fee))

	def test_cancelling_both_leaves_no_fee_behind(self):
		before = next_ordinal(AYALA, DAY)
		head, tail = self._reserve_chain()
		frappe.set_user(PIA)
		cancel_my_booking(head.name)
		cancel_my_booking(tail.name)
		frappe.set_user("Administrator")
		self.assertEqual(next_ordinal(AYALA, DAY), before)

	def test_one_hour_of_a_session_cannot_be_moved(self):
		from court_booking_tech.api.bookings import reschedule_booking

		head, tail = self._reserve_chain()
		frappe.set_user(STELLA)
		for name in (head.name, tail.name):
			with self.assertRaises(frappe.ValidationError) as caught:
				reschedule_booking(name, start_time="16:00:00")
			self.assertIn("continuous session", str(caught.exception))


class TestCartNormalisation(CartTestCase):
	"""The review finding: a cart is a SET OF SLOTS, not a list of items."""

	def test_two_adjacent_items_are_one_booking_and_one_fee(self):
		"""1-2pm and 2-3pm on one court is ONE continuous booking. Charged per
		item it would be two fees, which is precisely what the ruling calls the
		'continuous' case and prices at one."""
		quote = self._quote([self._item(start_time="13:00:00"), self._item(start_time="14:00:00")])
		self.assertEqual(quote["count"], 1)
		self.assertEqual(quote["booking_fee_count"], 1)
		line = quote["items"][0]
		self.assertEqual(line["number_of_slots"], 2)
		self.assertEqual(line["start_time"], "13:00:00")
		self.assertEqual(line["end_time"], "15:00:00")
		self.assertEqual(flt(line["court_total"], 2), flt(RATE * 2, 2))

	def test_the_merge_survives_into_the_reserved_rows(self):
		"""Not merely a quote-time nicety: the CART inserts one row of two
		slots, so the booking the customer ends up holding is the continuous
		one — and it carries exactly one platform fee."""
		result = self._reserve(
			[self._item(start_time="13:00:00"), self._item(start_time="14:00:00")]
		)
		self.assertEqual(result["count"], 1)
		doc = frappe.get_doc("CBT Court Booking", result["bookings"][0]["booking"])
		self.assertEqual(doc.number_of_slots, 2)
		# frappe writes a Time field with str(timedelta) — 13:00 plus two grid
		# hours is 15:00:00, and the row is ONE booking, not two of one hour.
		self.assertEqual(str(doc.end_time), "15:00:00")

	def test_items_out_of_order_still_merge(self):
		"""The client is not required to sort. 2-3pm sent before 1-2pm is the
		same continuous run."""
		quote = self._quote([self._item(start_time="14:00:00"), self._item(start_time="13:00:00")])
		self.assertEqual(quote["count"], 1)
		self.assertEqual(quote["items"][0]["start_time"], "13:00:00")

	def test_overlapping_items_merge_without_inventing_a_slot(self):
		"""1-3pm and 2-4pm is the union {13,14,15}, never 1-5pm. A union can
		only ever contain slots the caller actually sent."""
		quote = self._quote(
			[
				self._item(start_time="13:00:00", slots=2),
				self._item(start_time="14:00:00", slots=2),
			]
		)
		self.assertEqual(quote["count"], 1)
		line = quote["items"][0]
		self.assertEqual(line["number_of_slots"], 3)
		self.assertEqual(line["start_time"], "13:00:00")
		self.assertEqual(line["end_time"], "16:00:00")

	def test_a_duplicate_item_is_not_charged_twice(self):
		self.assertEqual(
			self._quote([self._item(), self._item()])["count"], 1
		)

	def test_a_broken_selection_stays_two_runs(self):
		"""The other half of the ruling, and the reason the merge cannot simply
		be 'collapse everything on one court'."""
		quote = self._quote(
			[self._item(start_time="13:00:00"), self._item(start_time="16:00:00")]
		)
		self.assertEqual(quote["count"], 2)
		self.assertEqual(quote["booking_fee_count"], 2)

	def test_adjacency_is_grid_INDEX_adjacency_not_clock_adjacency(self):
		"""Two runs on DIFFERENT courts at the same hour never merge, however
		adjacent their clock times look."""
		quote = self._quote([self._item(court=MAKATI_A), self._item(court=MAKATI_B)])
		self.assertEqual(quote["count"], 2)

	def test_lines_are_ordered_by_date_then_court_then_time(self):
		quote = self._quote(
			[
				self._item(court=MAKATI_B, start_time="13:00:00", date=NEXT_DAY),
				self._item(court=MAKATI_B, start_time="13:00:00"),
				self._item(court=MAKATI_A, start_time="16:00:00"),
				self._item(court=MAKATI_A, start_time="13:00:00"),
			]
		)
		self.assertEqual(
			[(line["booking_date"], line["court"], line["start_time"]) for line in quote["items"]],
			[
				(DAY, MAKATI_A, "13:00:00"),
				(DAY, MAKATI_A, "16:00:00"),
				(DAY, MAKATI_B, "13:00:00"),
				(NEXT_DAY, MAKATI_B, "13:00:00"),
			],
		)


class TestCartFeeAcceptanceTable(CartTestCase):
	"""The four scenarios the user ruled on 2026-09-03, by fee count."""

	def _fees(self, items):
		quote = self._quote(items)
		return quote["count"], quote["booking_fee_count"]

	def test_1_one_court_one_day_broken_is_two_fees(self):
		# Sep 6, one court, 1-2pm + 4-5pm -> 2
		self.assertEqual(
			self._fees([self._item(start_time="13:00:00"), self._item(start_time="16:00:00")]),
			(2, 2),
		)

	def test_2_one_court_one_day_continuous_is_one_fee(self):
		# Sep 6, one court, 1-5pm -> 1, however the client sliced it
		self.assertEqual(self._fees([self._item(start_time="13:00:00", slots=4)]), (1, 1))
		self.assertEqual(
			self._fees([self._item(start_time=f"{hour}:00:00") for hour in (13, 14, 15, 16)]),
			(1, 1),
		)

	def test_3_two_days_one_court_is_two_fees(self):
		# Sep 6 + Sep 7, one court, 1-5pm each -> 2
		self.assertEqual(
			self._fees(
				[
					self._item(start_time="13:00:00", slots=4),
					self._item(start_time="13:00:00", slots=4, date=NEXT_DAY),
				]
			),
			(2, 2),
		)

	def test_4_two_courts_one_day_is_two_fees(self):
		# Sep 6, two courts, both 1-3pm -> 2 (the ruling's text says "dates"
		# there; the row records it as the slip it is — these are COURTS).
		self.assertEqual(
			self._fees(
				[
					self._item(court=MAKATI_A, start_time="13:00:00", slots=2),
					self._item(court=MAKATI_B, start_time="13:00:00", slots=2),
				]
			),
			(2, 2),
		)


class TestCartQuote(CartTestCase):

	def test_the_fee_ordinal_advances_inside_the_cart(self):
		"""THE bug this endpoint exists to fix. booking_fee() re-reads
		next_ordinal() on every call and nothing is inserted while quoting, so
		three separate quotes would price all three runs at the SAME ordinal.
		The tiers are built around the LIVE ordinal so the boundary lands inside
		the cart whatever else the month already holds."""
		base = next_ordinal(AYALA, DAY)
		self._set_tiers(
			[
				{"from_count": 1, "to_count": base + 1, "fee": 15},
				{"from_count": base + 2, "to_count": 0, "fee": 40},
			]
		)
		quote = self._quote(
			[
				self._item(start_time="13:00:00"),
				self._item(start_time="15:00:00"),
				self._item(start_time="17:00:00"),
			]
		)
		self.assertEqual(quote["count"], 3)
		self.assertEqual(
			[flt(line["platform_fee"], 2) for line in quote["items"]],
			[15.0, 15.0, 40.0],
			"the third run must cross into the next bracket",
		)
		self.assertEqual(flt(quote["platform_fee"], 2), 70.0)

	def test_each_line_is_priced_at_its_own_ordinal(self):
		base = next_ordinal(AYALA, DAY)
		quote = self._quote(
			[self._item(start_time="13:00:00"), self._item(start_time="15:00:00")]
		)
		self.assertEqual(
			[flt(line["platform_fee"], 2) for line in quote["items"]],
			[tier_fee(TIERS, base), tier_fee(TIERS, base + 1)],
		)

	def test_the_grand_total_is_the_sum_of_its_lines(self):
		quote = self._quote(
			[
				self._item(court=MAKATI_A, start_time="13:00:00", slots=2),
				self._item(court=MAKATI_B, start_time="13:00:00"),
			]
		)
		lines = quote["items"]
		self.assertEqual(
			flt(quote["court_total"], 2),
			flt(sum(flt(line["court_total"]) for line in lines), 2),
		)
		self.assertEqual(
			flt(quote["platform_fee"], 2),
			flt(sum(flt(line["platform_fee"]) for line in lines), 2),
		)
		self.assertEqual(
			flt(quote["total_amount"], 2),
			flt(quote["court_total"] + quote["platform_fee"], 2),
		)
		# Hand-checked: 3 hours of a ₱300 flat court, no discount.
		self.assertEqual(flt(quote["court_total"], 2), flt(RATE * 3, 2))

	def test_vat_is_taken_on_the_summed_court_share_only(self):
		"""S25 ruling: the tenant's VAT never covers the platform's fee. The
		rule is linear, so the breakdown of the sum must equal the sum of the
		breakdowns — and neither may include the fee."""
		quote = self._quote(
			[self._item(start_time="13:00:00"), self._item(start_time="15:00:00")]
		)
		# Whatever the tenant's VAT mode, the breakdown covers the court share
		# and stops there — never the fee, which is the platform's line.
		self.assertEqual(
			flt(quote["vatable_amount"] + quote["vat_amount"], 2),
			flt(quote["court_total"], 2),
		)
		self.assertLess(flt(quote["court_total"], 2), flt(quote["total_amount"], 2))
		if quote["vat_mode"] != "VAT":
			self.assertEqual(flt(quote["vat_amount"], 2), 0.0)

		# MEASURED 2026-09-04, and it settles which figure is authoritative:
		# VAT-inclusive extraction is NOT linear across rounding. 600 / 1.12
		# rounds to 535.71, while two lines of 300 each round to 267.86 and sum
		# to 535.72. Both reconcile against their OWN total, so neither is
		# "wrong" — but the customer pays ONE transfer for the grand total, so
		# the cart's footer has to be the breakdown of the SUM. Pinned as a
		# known one-centavo divergence rather than left to be rediscovered as a
		# bug. (Backlog B36's row asserts this rule IS linear; it is not, which
		# matters there because a group invoice prints one footer over N rows.)
		per_line = flt(sum(flt(line["vatable_amount"]) for line in quote["items"]), 2)
		self.assertAlmostEqual(flt(quote["vatable_amount"], 2), per_line, delta=0.01)

	def test_a_guest_can_price_a_cart_but_is_told_nothing_about_anybody(self):
		quote = self._quote([self._item()], user="Guest")
		self.assertEqual(quote["count"], 1)
		self.assertEqual(flt(quote["items"][0]["discount_percent"]), 0.0)
		for line in quote["items"]:
			self.assertNotIn("customer", line)
			self.assertNotIn("platform_fee_seq", line)

	def test_a_cart_may_not_span_two_companies(self):
		with self.assertRaisesRegex(frappe.ValidationError, "one branch at a time"):
			self._quote([self._item(court=MAKATI_A), self._item(court=QCSM_1)])

	def test_a_cart_may_not_span_two_branches_of_one_company(self):
		"""Same tenant, still refused: the whole cart is priced and held against
		ONE branch's grid, hours and expiry knob."""
		with self.assertRaisesRegex(frappe.ValidationError, "one branch at a time"):
			self._quote([self._item(court=MAKATI_A), self._item(court=BGC_A)])

	def test_an_empty_cart_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "cart is empty"):
			self._quote([])

	def test_many_entries_that_MERGE_are_not_refused_as_bookings(self):
		"""The review's finding, pinned. The client sends one entry per selected
		SLOT and lets the server merge, so entries are NOT bookings. Four
		consecutive hours arrive as four entries and are ONE booking; a bound
		that counted entries and said "at most N bookings" refused that with a
		message about bookings the customer never made and could not deselect."""
		quote = self._quote(
			[self._item(start_time=f"{hour}:00:00") for hour in (13, 14, 15, 16)]
		)
		self.assertEqual(quote["count"], 1)
		self.assertEqual(quote["items"][0]["number_of_slots"], 4)

	def test_the_cart_is_capped_on_BOOKINGS_after_merging(self):
		"""The truthful bound: runs, not entries. Alternating hours on two
		courts never merge, so nine entries really are nine bookings."""
		items = [
			self._item(court=court, start_time=f"{hour}:00:00")
			for court in (MAKATI_A, MAKATI_B)
			for hour in (7, 9, 11, 13, 15)
		]
		self.assertGreater(len(items), DEFAULT_MAX_CART_ITEMS)
		with self.assertRaisesRegex(frappe.ValidationError, "at most"):
			self._quote(items)

	def test_a_giant_payload_dies_before_any_query(self):
		"""A guard on a guest-open, unthrottled endpoint — not a product limit,
		and its message must not talk about bookings."""
		items = [self._item(start_time="13:00:00")] * (MAX_CART_PAYLOAD_ENTRIES + 1)
		with self.assertRaisesRegex(frappe.ValidationError, "too many entries"):
			self._quote(items)

	def test_an_unknown_court_is_indistinguishable_from_a_deactivated_one(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self._quote([self._item(court="AYALA-makati-court-does-not-exist")])

	def test_a_time_off_the_grid_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "no longer on this branch"):
			self._quote([self._item(start_time="13:17:00")])

	def test_a_run_past_closing_is_refused(self):
		# 21:00 is the last slot of a 06:00-22:00 day; two from there overruns.
		with self.assertRaisesRegex(frappe.ValidationError, "no longer on this branch"):
			self._quote([self._item(start_time="21:00:00", slots=2)])


class TestReserveCart(CartTestCase):

	def test_one_group_one_clock(self):
		"""The payment ruling in one assertion: one transfer for the grand
		total means one pay-by clock, so every row carries the SAME expiry —
		not N stamps a few milliseconds apart."""
		result = self._reserve(
			[
				self._item(court=MAKATI_A, start_time="13:00:00"),
				self._item(court=MAKATI_B, start_time="13:00:00"),
				self._item(court=MAKATI_A, start_time="13:00:00", date=NEXT_DAY),
			]
		)
		self.assertEqual(result["count"], 3)
		docs = [
			frappe.get_doc("CBT Court Booking", row["booking"])
			for row in result["bookings"]
		]
		groups = {doc.booking_group for doc in docs}
		self.assertEqual(len(groups), 1)
		self.assertTrue(groups.pop().startswith("CART-"))
		self.assertEqual(len({str(doc.reservation_expires_at) for doc in docs}), 1)
		self.assertEqual(
			str(docs[0].reservation_expires_at), str(result["reservation_expires_at"])
		)

	def test_the_group_key_is_per_company_not_global(self):
		"""A global series would leak cross-tenant volume through a key handed
		to the customer."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		code = frappe.db.get_value("CBT Company", AYALA, "company_code")
		self.assertTrue(
			result["booking_group"].startswith(f"CART-{code}-"), result["booking_group"]
		)

	def test_a_cart_of_one_is_just_a_booking(self):
		"""No group for a single run — otherwise /my-bookings prints "one
		payment" over a lone card."""
		result = self._reserve([self._item()])
		self.assertIsNone(result["booking_group"])
		self.assertIsNone(
			frappe.db.get_value(
				"CBT Court Booking", result["bookings"][0]["booking"], "booking_group"
			)
		)

	def test_every_row_is_a_reserved_fund_transfer_hold_owned_by_the_caller(self):
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		for row in result["bookings"]:
			doc = frappe.get_doc("CBT Court Booking", row["booking"])
			self.assertEqual(doc.customer, PIA)
			self.assertEqual(doc.booking_status, "Reserved")
			self.assertEqual(doc.payment_method, "Fund Transfer")
			self.assertTrue(doc.payment_channel)

	def test_fee_ordinals_are_consecutive_and_the_quote_agrees(self):
		"""The insert side of the same rule: each insert sees its predecessors
		in the one transaction (UNCOUNTED_STATUSES excludes Reserved), so the
		rows take consecutive ordinals — and they must match what the checkout
		showed a moment earlier."""
		items = [
			self._item(start_time="13:00:00"),
			self._item(start_time="15:00:00"),
			self._item(start_time="17:00:00"),
		]
		quote = self._quote(items)
		result = self._reserve(items)
		docs = [
			frappe.get_doc("CBT Court Booking", row["booking"])
			for row in result["bookings"]
		]
		seqs = [doc.platform_fee_seq for doc in docs]
		self.assertEqual(seqs, list(range(seqs[0], seqs[0] + 3)))
		self.assertEqual(
			[flt(doc.platform_fee, 2) for doc in docs],
			[flt(line["platform_fee"], 2) for line in quote["items"]],
		)
		self.assertEqual(
			flt(result["total_amount"], 2), flt(quote["total_amount"], 2)
		)

	def test_a_cart_spanning_two_months_takes_each_months_own_numbering(self):
		"""Attribution is by SERVICE MONTH everywhere in this app, so one cart
		can legitimately hold an ordinal 4 and an ordinal 1."""
		october = "2028-10-04"
		sep_base = next_ordinal(AYALA, DAY)
		oct_base = next_ordinal(AYALA, october)
		result = self._reserve(
			[
				self._item(start_time="13:00:00"),
				self._item(start_time="13:00:00", date=october),
			]
		)
		docs = [
			frappe.get_doc("CBT Court Booking", row["booking"])
			for row in result["bookings"]
		]
		seqs = {str(doc.booking_date): doc.platform_fee_seq for doc in docs}
		self.assertEqual(seqs[DAY], sep_base)
		self.assertEqual(seqs[october], oct_base)

	def test_a_taken_slot_refuses_the_WHOLE_cart_and_leaves_nothing_behind(self):
		"""All-or-nothing, and the message names the slot rather than reading as
		somebody else's booking."""
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=NOW):
			taken = create_booking(
				court=MAKATI_A,
				booking_date=DAY,
				start_time="13:00:00",
				payment_method="Cash",
				customer_name="Walk-in blocking the cart",
			)
		self._cleanup_booking(taken["name"])

		before = self._my_booking_count()
		with self.assertRaisesRegex(frappe.ValidationError, "no longer available"):
			self._reserve(
				[
					self._item(court=MAKATI_A, start_time="13:00:00"),
					self._item(court=MAKATI_B, start_time="13:00:00"),
				]
			)
		self.assertEqual(self._my_booking_count(), before, "the cart left rows behind")

	def test_a_guest_cannot_reserve(self):
		with self.assertRaises(frappe.PermissionError):
			self._reserve([self._item()], user="Guest")

	def test_a_cart_may_not_span_two_companies(self):
		with self.assertRaisesRegex(frappe.ValidationError, "one branch at a time"):
			self._reserve([self._item(court=MAKATI_A), self._item(court=QCSM_1)])

	def test_the_customer_cannot_choose_the_price_or_the_discount(self):
		"""Nothing in the payload can reach money: the endpoint has no rate and
		no discount parameter, and the controller resolves both server-side."""
		result = self._reserve([self._item(start_time="13:00:00", slots=2)])
		doc = frappe.get_doc("CBT Court Booking", result["bookings"][0]["booking"])
		self.assertEqual(flt(doc.hourly_rate, 2), flt(RATE, 2))
		self.assertEqual(flt(doc.discount_percent), 0.0)
		self.assertEqual(flt(doc.total_amount, 2), flt(RATE * 2 + doc.platform_fee, 2))


class TestGroupProof(CartTestCase):
	"""One transfer, one receipt — and the whole cart answers to it."""

	def test_one_upload_covers_every_row_of_the_cart(self):
		"""The user's ruling in one assertion: 'the one transfer for the grand
		total'. Uploading against ANY row of the cart puts a Pending proof on
		every live row, all naming the SAME file, because staff review one
		receipt for one payment."""
		result = self._reserve(
			[
				self._item(court=MAKATI_A, start_time="13:00:00"),
				self._item(court=MAKATI_B, start_time="13:00:00"),
				self._item(court=MAKATI_A, start_time="13:00:00", date=NEXT_DAY),
			]
		)
		names = [row["booking"] for row in result["bookings"]]
		# Upload against the LAST row, not the first — nothing may depend on
		# which row the customer happened to be looking at.
		upload = self._upload(names[-1])

		self.assertEqual(len(upload["group_proofs"]), 3)
		for name in names:
			self.assertEqual(len(self._proofs_of(name)), 1, name)
		files = {
			frappe.db.get_value("CBT Payment Proof", proof, "file")
			for proof in upload["group_proofs"]
		}
		self.assertEqual(len(files), 1, "a group is ONE receipt, not N")
		# The proof handed back belongs to the booking the caller named.
		self.assertEqual(
			frappe.db.get_value("CBT Payment Proof", upload["proof"], "booking"),
			names[-1],
		)


	def test_a_cancelled_first_row_cannot_buy_unlimited_hold_extensions(self):
		"""The review's finding, pinned. An earlier design anchored the group's
		proof caps and deadline arming on the LOWEST-NAMED row. A customer may
		legally cancel that row before uploading anything — self-cancel is
		blocked only once a proof exists — and a dead anchor never receives a
		proof, so its pending count reads empty forever: the per-booking cap
		never trips and the verification deadline RE-ARMS on every upload.
		Slots held indefinitely, throttled only by the hourly upload limit.

		The fix is that the caps read the whole LIVE fan-out, so the deadline is
		armed exactly once however many uploads follow."""
		result = self._reserve(
			[
				self._item(court=MAKATI_A, start_time="13:00:00"),
				self._item(court=MAKATI_B, start_time="13:00:00"),
			]
		)
		names = sorted(row["booking"] for row in result["bookings"])
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=NOW):
			cancel_my_booking(names[0])
		survivor = names[1]

		self._upload(survivor)
		armed = frappe.db.get_value(
			"CBT Court Booking", survivor, "verification_deadline_at"
		)
		self.assertIsNotNone(armed)

		# Every later upload must leave the deadline exactly where it is.
		for minute in (5, 10):
			self._upload(survivor, at=NOW + timedelta(minutes=minute))
			self.assertEqual(
				frappe.db.get_value(
					"CBT Court Booking", survivor, "verification_deadline_at"
				),
				armed,
				"a dead anchor let the hold be extended",
			)
		# And the per-booking cap still bites, which it could not do if the
		# pending count were being read off the cancelled row.
		with self.assertRaisesRegex(frappe.ValidationError, "awaiting review"):
			self._upload(survivor, at=NOW + timedelta(minutes=15))


	def test_one_verification_confirms_every_row(self):
		"""The other half of the payment ruling: one transfer, ONE staff action
		that confirms all of it."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		names = [row["booking"] for row in result["bookings"]]
		self._upload(names[0])

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=NOW):
			accept_proofs(names[0])
		for name in names:
			self.assertEqual(
				frappe.db.get_value("CBT Court Booking", name, "booking_status"),
				"Confirmed",
				name,
			)
		# A Confirmed booking never carries Pending proofs — they would poison
		# the holds cap and the expired-with-proof audit.
		self.assertEqual(
			frappe.db.count(
				"CBT Payment Proof",
				{"booking": ("in", names), "status": "Pending"},
			),
			0,
		)

	def test_a_row_that_died_mid_verification_is_reported_not_fatal(self):
		"""The user's ruling 2026-09-04. The sweep expires on the booking's own
		END unconditionally, so a cart CANNOT be made to expire as one: a row
		can die while the transfer is being checked. Staff accepting must still
		confirm everything that is alive, and be told what they could not."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		names = [row["booking"] for row in result["bookings"]]
		self._upload(names[0])

		# Kill ONE row the way the sweep would, without touching the other.
		frappe.set_user("Administrator")
		frappe.db.set_value("CBT Court Booking", names[1], "booking_status", "Expired")

		frappe.set_user(STELLA)
		frappe.clear_messages()
		with patch(CLOCK, return_value=NOW):
			accept_proofs(names[0])

		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", names[0], "booking_status"),
			"Confirmed",
			"the live row must still be confirmed",
		)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", names[1], "booking_status"),
			"Expired",
		)
		# The desk is TOLD, in the place they are looking.
		said = " ".join(str(m) for m in frappe.get_message_log())
		self.assertIn(names[1], said)

	def test_reject_takes_one_reason_and_decides_per_row(self):
		"""One click, one reason — but the outcome cannot be shared. Hold
		liveness is read per booking precisely so a dead hold is never
		regraced, and a mixed cart has rows on both sides of that line."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		names = sorted(row["booking"] for row in result["bookings"])
		self._upload(names[0])

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=NOW):
			outcome = reject_proofs(names[0], "Unreadable")

		# Recoverable reason, first strike, both holds live -> both regraced.
		self.assertEqual(outcome["outcome"], "Regrace")
		self.assertEqual(
			{row["outcome"] for row in outcome["group_outcomes"]}, {"Regrace"}
		)
		for name in names:
			doc = frappe.get_doc("CBT Court Booking", name)
			self.assertEqual(doc.booking_status, "Reserved", name)
			self.assertEqual(cint(doc.rejection_count), 1, name)
			self.assertIsNone(doc.verification_deadline_at, name)
		self.assertEqual(
			frappe.db.count(
				"CBT Payment Proof",
				{"booking": ("in", names), "status": "Rejected"},
			),
			2,
		)

	def test_reject_skips_a_dead_row_instead_of_refusing_the_lot(self):
		"""The single-booking guard throws when the row is not Reserved. Applied
		to a group that would make a partly-dead cart un-rejectable, stranding
		the rows that are still alive."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		names = sorted(row["booking"] for row in result["bookings"])
		self._upload(names[0])
		frappe.set_user("Administrator")
		frappe.db.set_value("CBT Court Booking", names[1], "booking_status", "Expired")

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=NOW):
			outcome = reject_proofs(names[0], "Unreadable")

		by_booking = {row["booking"]: row["outcome"] for row in outcome["group_outcomes"]}
		self.assertEqual(by_booking[names[0]], "Regrace")
		# NAMED, not silently dropped — a row filtered out of the loop is a row
		# nobody is told about, which is the half of the ruling easiest to lose.
		self.assertEqual(by_booking[names[1]], "Skipped")
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", names[1], "booking_status"),
			"Expired",
		)


	def test_a_cart_earns_ONE_confirmation_naming_every_booking(self):
		"""One transfer, one receipt: N per-row mails would be N copies of the
		same news, and suppressing all but one would name one booking for money
		that bought several."""
		result = self._reserve(
			[self._item(court=MAKATI_A), self._item(court=MAKATI_B)]
		)
		names = [row["booking"] for row in result["bookings"]]
		self._upload(names[0])

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=NOW):
			accept_proofs(names[0])

		frappe.set_user("Administrator")
		queued = frappe.get_all(
			"Email Queue",
			filters={
				"reference_doctype": "CBT Court Booking",
				"reference_name": ("in", names),
			},
			fields=["name", "message"],
		)
		self.assertEqual(len(queued), 1, "one payment must not send N mails")
		body = queued[0].message.replace("=\r\n", "").replace("=\n", "")
		for name in names:
			self.assertIn(name, body, "the mail must name every booking it covers")

	def test_my_bookings_carries_the_group_only_for_a_cart(self):
		"""The page groups on this key. An ordinary booking must not grow a
		`booking_group: null` — tests state 'booked on its own' as key absence."""
		cart = self._reserve([self._item(court=MAKATI_A), self._item(court=MAKATI_B)])
		alone = self._reserve([self._item(court=MAKATI_A, start_time="15:00:00")])

		frappe.set_user(PIA)
		with patch(CLOCK, return_value=NOW):
			cards = {row["booking"]: row for row in get_my_bookings()["bookings"]}

		group = cart["booking_group"]
		for row in cart["bookings"]:
			self.assertEqual(cards[row["booking"]]["booking_group"], group)
		self.assertNotIn("booking_group", cards[alone["bookings"][0]["booking"]])


class TestBookingGroupField(CartTestCase):
	"""The field itself — and the NULL-vs-empty-string scar the controller
	already carries for `customer`."""

	def _single(self):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": MAKATI_A,
				"customer": PIA,
				"booking_date": DAY,
				"start_time": "09:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
				"discount_percent": 0,
			}
		)
		with patch(CLOCK, return_value=NOW):
			doc.insert()
		self._cleanup_booking(doc.name)
		return doc

	def test_a_booking_made_on_its_own_carries_no_group(self):
		self.assertIsNone(self._single().booking_group)

	def test_a_group_less_booking_can_be_saved_twice(self):
		"""The scar: booking_group is a read-only Data with no default, so the
		column holds NULL while a round-trip hands back ''. Compared raw by
		_validate_immutables that reads as a change, and every later save of
		every ordinary booking would throw."""
		doc = self._single()
		doc.booking_group = ""
		doc.notes = "second save"
		with patch(CLOCK, return_value=NOW):
			doc.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("CBT Court Booking", doc.name, "booking_group"))

	def test_a_booking_cannot_be_moved_into_a_cart_afterwards(self):
		doc = self._single()
		doc.booking_group = "CART-AYALA-2028-00001"
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
			with patch(CLOCK, return_value=NOW):
				doc.save(ignore_permissions=True)

	def test_a_cart_row_can_still_be_saved_without_losing_its_group(self):
		result = self._reserve([self._item()])
		frappe.set_user("Administrator")
		doc = frappe.get_doc("CBT Court Booking", result["bookings"][0]["booking"])
		group = doc.booking_group
		doc.notes = "staff added a note"
		with patch(CLOCK, return_value=NOW):
			doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", doc.name, "booking_group"), group
		)


class TestCartHoldClock(CartTestCase):

	def test_the_shared_clock_matches_the_company_knob(self):
		"""One helper computes the hold length for both paths — inline in two
		places, a company with the knob at 0 would get `now + 0` from the cart
		and `now + 30` from a single booking."""
		company = frappe.get_doc("CBT Company", AYALA)
		minutes = company.get_reservation_expiry_minutes() or 30
		result = self._reserve([self._item(), self._item(court=MAKATI_B)])
		doc = frappe.get_doc("CBT Court Booking", result["bookings"][0]["booking"])
		self.assertEqual(
			doc.reservation_expires_at, NOW + timedelta(minutes=minutes)
		)

	def test_a_forged_cart_clock_cannot_extend_a_hold(self):
		"""flags cannot be forged over REST (RESERVED_KEYWORDS), but the stamp
		is range-checked anyway — nothing stops a future internal caller."""
		frappe.set_user(PIA)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": MAKATI_A,
				"customer": PIA,
				"booking_date": DAY,
				"start_time": "10:00:00",
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
				"discount_percent": 0,
			}
		)
		doc.flags.customer_created = True
		doc.flags.cart_expires_at = NOW + timedelta(days=7)
		with patch(CLOCK, return_value=NOW):
			doc.insert(ignore_permissions=True)
		self._cleanup_booking(doc.name)
		company = frappe.get_doc("CBT Company", AYALA)
		minutes = company.get_reservation_expiry_minutes() or 30
		self.assertEqual(doc.reservation_expires_at, NOW + timedelta(minutes=minutes))

	def test_getdate_normalisation_is_not_what_pins_the_date(self):
		"""Guards the one thing a date-shaped payload could quietly change."""
		result = self._reserve([self._item(date=DAY)])
		doc = frappe.get_doc("CBT Court Booking", result["bookings"][0]["booking"])
		self.assertEqual(doc.booking_date, getdate(DAY))
