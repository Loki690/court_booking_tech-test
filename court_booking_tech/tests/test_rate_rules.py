"""
Court Booking Tech — Rate Rules (section-14, PLAN §8b)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_rate_rules

Resolution precedence and boundary inclusivity, the per-slot segment snapshot,
the money doctrine (single rounding, resave stability, the blend is display
only), the staff-override tri-state, quote == reserve to the centavo, the
extension's two branches, invoice line-per-segment, and rule validation.

FIXTURE COURT. Every ruled court here is built IN-TEST on AYALA-makati and
deleted in tearDownClass. Rules are never added to a seeded court: Pia's ₱800
at BGC and the §4 2027-01-15 cast are asserted to the peso in half a dozen
green files. Makati specifically because BGC's 3-court floor plan is pinned by
E2E test_02/test_06 (`.cbt-court-card == 3`), and this suite creates a court.

MONTH. October 2027 (month-per-module discipline, S11 as-built 20). Dates
10-01/06/07/08/15/22 belong to section-13 and the walk-in seed; this file uses
10-13 (Wednesday), 10-16 (Saturday) and 10-18 (Monday). Months claimed by
INTEGER elsewhere are 4, 8 and 9 — a date-string grep cannot see those, and
September must stay empty (S13 as-built 1b).
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech import pricing
from court_booking_tech.api.bookings import (
	confirm_booking,
	create_booking,
	extend_booking,
)
from court_booking_tech.api.portal import get_quote
from court_booking_tech.billing import sync_invoice_for_booking
from court_booking_tech.seeds.seed_test_data import CUSTOMER_EMAIL, seed_all

AYALA = "ayala-courts"
MAKATI = "AYALA-makati"  # open 06:00–22:00 every day, 60-minute slots

# Wednesday / Saturday / Monday in October 2027.
WEDNESDAY = "2027-10-13"
SATURDAY = "2027-10-16"
MONDAY = "2027-10-18"

T0 = datetime(2027, 10, 13, 8, 0)
CLOCK = "court_booking_tech.clock.now_dt"

BASE_RATE = 500
NIGHT_RATE = 400
FRIDAY_RATE = 900
WEEKEND_RATE = 250

# Base ₱500; ₱400 from 18:00 every day; ₱250 on weekend mornings; ₱900 on
# Friday evenings — the specific-day row deliberately OVERLAPS the All Days
# night rule, which is exactly how precedence is meant to be expressed.
FIXTURE_RULES = [
	{
		"day_scope": "All Days",
		"start_time": "18:00:00",
		"end_time": "22:00:00",
		"hourly_rate": NIGHT_RATE,
		"label": "Night rate",
	},
	{
		"day_scope": "Weekends",
		"start_time": "06:00:00",
		"end_time": "09:00:00",
		"hourly_rate": WEEKEND_RATE,
		"label": "Weekend early-bird",
	},
	{
		"day_scope": "Friday",
		"start_time": "18:00:00",
		"end_time": "22:00:00",
		"hourly_rate": FRIDAY_RATE,
		"label": "Friday night",
	},
]


# seed_all() is idempotent but not free, and this module has eight classes.
# Seeding once per MODULE rather than once per class keeps the file inside the
# section-12 suite budget; the data it writes is committed, so it survives every
# per-test rollback in between.
_SEEDED = False


class RateRuleTestCase(FrappeTestCase):
	"""Shared fixture court. Holds NO test methods — inheriting a TestCase that
	does would re-run every parent test in each subclass (S13 as-built 11)."""

	COURT_NAME = "Rate Rules Fixture"
	COURT = "AYALA-makati-rate-rules-fixture"

	@classmethod
	def setUpClass(cls):
		global _SEEDED
		super().setUpClass()
		if not _SEEDED:
			seed_all()
			_SEEDED = True
		cls._ensure_fixture_court()

	@classmethod
	def _ensure_fixture_court(cls):
		frappe.set_user("Administrator")
		if not frappe.db.exists("CBT Court", cls.COURT):
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court",
					"branch": MAKATI,
					"court_name": cls.COURT_NAME,
					"court_type": "Pickleball",
					"hourly_rate": BASE_RATE,
					"is_active": 1,
				}
			)
			for rule in FIXTURE_RULES:
				doc.append("rate_rules", rule)
			doc.insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Court", cls.COURT)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for booking in frappe.get_all(
			"CBT Court Booking", filters={"court": cls.COURT}, pluck="name"
		):
			frappe.delete_doc(
				"CBT Court Booking", booking, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		frappe.delete_doc(
			"CBT Court", cls.COURT, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _book(self, start_time, date=WEDNESDAY, slots=1, court=None, **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court or self.COURT,
			"customer": CUSTOMER_EMAIL,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": "Cash",
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert()
		self._cleanup(doc.name)
		return doc

	def _cleanup(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	def _rate(self, date, hhmm):
		return pricing.resolve_slot_rate(self.COURT, date, hhmm)


class TestRateResolution(RateRuleTestCase):
	"""The seam itself — no bookings involved."""

	def test_precedence_specific_day_beats_group_beats_all_days_beats_base(self):
		# 2027-10-15 is a Friday: the Friday rule and the All Days night rule
		# both cover 19:00, and the more specific one has to win.
		self.assertEqual(self._rate("2027-10-15", "19:00:00"), FRIDAY_RATE)
		# Wednesday 19:00 -> only All Days applies.
		self.assertEqual(self._rate(WEDNESDAY, "19:00:00"), NIGHT_RATE)
		# Saturday 07:00 -> the Weekends group rule.
		self.assertEqual(self._rate(SATURDAY, "07:00:00"), WEEKEND_RATE)
		# Monday 07:00 -> nothing applies; the court's base rate stands.
		self.assertEqual(self._rate(MONDAY, "07:00:00"), BASE_RATE)

	def test_weekday_and_weekend_classification_on_absolute_dates(self):
		# The E2E fixture deliberately avoids weekend windows because its dates
		# are RELATIVE — so classification is pinned here, on real dates.
		self.assertEqual(self._rate(SATURDAY, "08:00:00"), WEEKEND_RATE)
		self.assertEqual(self._rate("2027-10-17", "08:00:00"), WEEKEND_RATE)  # Sunday
		self.assertEqual(self._rate(MONDAY, "08:00:00"), BASE_RATE)

	def test_rule_start_is_inclusive_and_rule_end_is_exclusive(self):
		# Exactly AT the start -> the rule applies.
		self.assertEqual(self._rate(WEDNESDAY, "18:00:00"), NIGHT_RATE)
		# One slot earlier -> it does not.
		self.assertEqual(self._rate(WEDNESDAY, "17:00:00"), BASE_RATE)
		# Exactly AT the end -> it does NOT (end is exclusive), so a rule that
		# stops at 22:00 never prices the 22:00 slot.
		self.assertEqual(self._rate(WEDNESDAY, "22:00:00"), BASE_RATE)

	def test_midnight_window_resolves(self):
		"""timedelta(0) is FALSY but a perfectly valid opening time — the S4
		as-built 9 bug class, which has already silently closed a whole branch
		once. A truthiness check anywhere in the seam re-opens it."""
		pricing_ctx = {
			"court": "unsaved",
			"base_rate": 100,
			"rules": [
				{
					"day_scope": "All Days",
					"start_time": "00:00:00",
					"end_time": "06:00:00",
					"hourly_rate": 60,
					"label": "Graveyard",
				}
			],
		}
		self.assertEqual(
			pricing.resolve_slot_rate(pricing_ctx, WEDNESDAY, "00:00:00"), 60
		)
		self.assertEqual(
			pricing.resolve_slot_rate(pricing_ctx, WEDNESDAY, "05:00:00"), 60
		)
		self.assertEqual(
			pricing.resolve_slot_rate(pricing_ctx, WEDNESDAY, "06:00:00"), 100
		)

	def test_a_rule_less_court_yields_no_segments_at_all(self):
		"""The flat-rate path every pre-section-14 court still takes."""
		branch = frappe.get_doc("CBT Branch", MAKATI)
		from court_booking_tech.slots import get_slot_grid

		grid = get_slot_grid(branch, WEDNESDAY)
		self.assertEqual(
			pricing.build_rate_segments("AYALA-makati-court-a", WEDNESDAY, grid[:2]),
			[],
		)


class TestRateSegments(RateRuleTestCase):
	"""What the booking stores, and the money that comes out of it."""

	def test_span_across_a_boundary_makes_two_segments(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		self.assertEqual(len(doc.rate_segments), 2)
		day, night = doc.rate_segments
		self.assertEqual(flt(day.hourly_rate), BASE_RATE)
		self.assertEqual(day.hours, 1.0)
		self.assertEqual(flt(day.amount), 500)
		self.assertIsNone(day.label)
		self.assertEqual(flt(night.hourly_rate), NIGHT_RATE)
		self.assertEqual(night.label, "Night rate")
		self.assertEqual(flt(night.amount), 400)
		self.assertEqual(flt(doc.total_amount), 900)
		# The blend is the OUTPUT: ₱900 over 2h.
		self.assertEqual(flt(doc.hourly_rate), 450)

	def test_contiguous_same_rate_slots_merge_into_one_segment(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("18:00:00", slots=3)
		self.assertEqual(len(doc.rate_segments), 1)
		segment = doc.rate_segments[0]
		self.assertEqual(str(segment.start_time), "18:00:00")
		self.assertEqual(str(segment.end_time), "21:00:00")
		self.assertEqual(segment.hours, 3.0)
		self.assertEqual(flt(segment.amount), 1200)
		self.assertEqual(flt(doc.total_amount), 1200)
		self.assertEqual(flt(doc.hourly_rate), NIGHT_RATE)

	def test_segment_hours_sum_to_the_billable_duration(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		self.assertEqual(
			flt(sum(flt(s.hours) for s in doc.rate_segments), 2),
			flt(doc.duration_hours, 2),
		)

	def test_resave_does_not_drift_by_a_centavo(self):
		"""₱500×1h + ₱400×2h = ₱1,300, which blends to 433.33. Re-deriving the
		total from that blend gives ₱1,299.99 — one centavo shaved off a
		document the customer has already been handed, every single save."""
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3)
		self.assertEqual(flt(doc.total_amount), 1300)
		self.assertEqual(flt(doc.hourly_rate), 433.33)

		for _ in range(3):
			doc.save()
			doc.reload()
			self.assertEqual(flt(doc.total_amount), 1300)
			self.assertEqual(flt(doc.hourly_rate), 433.33)
		self.assertEqual(len(doc.rate_segments), 2)

	def test_discount_edit_keeps_segments_and_re_totals_from_them(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3)
		doc.discount_percent = 10
		doc.save()
		doc.reload()
		self.assertEqual(len(doc.rate_segments), 2)
		self.assertEqual(flt(doc.total_amount), 1170)  # 1300 − 10%

	def test_a_segmented_booking_survives_confirmation(self):
		"""confirm_booking is a doc.save(), so it runs the whole update path on
		every Fund Transfer verification in production."""
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2, payment_method="Fund Transfer")
			self.assertEqual(doc.booking_status, "Reserved")
			confirm_booking(doc.name)
		doc.reload()
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertEqual(len(doc.rate_segments), 2)
		self.assertEqual(flt(doc.total_amount), 900)


class TestRateOverride(RateRuleTestCase):
	"""Staff price beats the rules, and survives every later save."""

	def test_explicit_rate_on_create_booking_skips_the_rules(self):
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=self.COURT,
				booking_date=WEDNESDAY,
				start_time="17:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				number_of_slots=2,
				hourly_rate=300,
			)
		self._cleanup(result["name"])
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(doc.rate_segments, [])
		self.assertEqual(flt(doc.hourly_rate), 300)
		self.assertEqual(flt(doc.total_amount), 600)

	def test_an_override_equal_to_the_court_base_is_still_an_override(self):
		"""The base-rate comparison cannot see this one — which is exactly why
		the APIs set flags.rate_override explicitly instead of inferring it."""
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=self.COURT,
				booking_date=WEDNESDAY,
				start_time="18:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				number_of_slots=1,
				hourly_rate=BASE_RATE,
			)
		self._cleanup(result["name"])
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(doc.rate_segments, [])
		self.assertEqual(flt(doc.total_amount), BASE_RATE)  # NOT the ₱400 night rate

	def test_omitting_the_rate_lets_the_rules_price_it(self):
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=self.COURT,
				booking_date=WEDNESDAY,
				start_time="18:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				number_of_slots=1,
			)
		self._cleanup(result["name"])
		doc = frappe.get_doc("CBT Court Booking", result["name"])
		self.assertEqual(len(doc.rate_segments), 1)
		self.assertEqual(flt(doc.total_amount), NIGHT_RATE)

	def test_editing_the_rate_after_insert_clears_the_segments(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		self.assertEqual(len(doc.rate_segments), 2)

		doc.hourly_rate = 250
		doc.save()
		doc.reload()
		self.assertEqual(doc.rate_segments, [])
		self.assertEqual(flt(doc.total_amount), 500)  # 250 × 2h, flat

		# One-way door: nothing rebuilds them, so a later unrelated save keeps
		# the price the staff agreed rather than reverting to the rules.
		doc.discount_percent = 10
		doc.save()
		doc.reload()
		self.assertEqual(doc.rate_segments, [])
		self.assertEqual(flt(doc.total_amount), 450)


class TestRateQuote(RateRuleTestCase):
	"""quote == reserve, to the centavo."""

	def test_quote_without_date_and_time_is_unchanged(self):
		"""Strictly additive: the pre-section-14 contract still answers with the
		court's base rate and no segments."""
		quote = get_quote(self.COURT, 2)
		self.assertEqual(quote["segments"], [])
		self.assertEqual(flt(quote["hourly_rate"]), BASE_RATE)
		self.assertEqual(flt(quote["subtotal"]), 1000)
		self.assertEqual(flt(quote["total_amount"]), 1000)

	def test_quote_with_date_and_time_prices_each_slot(self):
		quote = get_quote(self.COURT, 2, booking_date=WEDNESDAY, start_time="17:00:00")
		self.assertEqual(len(quote["segments"]), 2)
		self.assertEqual(flt(quote["subtotal"]), 900)
		self.assertEqual(flt(quote["total_amount"]), 900)
		self.assertEqual(flt(quote["hourly_rate"]), 450)  # the blend
		self.assertEqual(quote["segments"][1]["label"], "Night rate")

	def test_quote_refuses_a_window_the_branch_cannot_sell(self):
		"""Refusing beats guessing: a base-rate answer for an unsellable window
		would put a wrong price on a customer's screen."""
		with self.assertRaises(frappe.ValidationError):
			get_quote(
				self.COURT, 1, booking_date=WEDNESDAY, start_time="17:30:00"
			)  # not on the grid
		with self.assertRaises(frappe.ValidationError):
			get_quote(
				self.COURT, 4, booking_date=WEDNESDAY, start_time="21:00:00"
			)  # runs past 22:00 closing

	def test_quote_matches_the_booking_it_produces_across_a_boundary(self):
		quote = get_quote(self.COURT, 3, booking_date=WEDNESDAY, start_time="17:00:00")
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3)
		self.assertEqual(flt(quote["total_amount"]), flt(doc.total_amount))
		self.assertEqual(flt(quote["hourly_rate"]), flt(doc.hourly_rate))
		self.assertEqual(len(quote["segments"]), len(doc.rate_segments))

	def test_quote_matches_a_member_booking_across_a_boundary(self):
		"""A member discount rounds ONCE, on the discounted total — the seam
		both sides share (S11 as-built 6).

		Section-18 (Backlog B4) let this row assert what it always meant. It used
		to multiply the discount HERE (`subtotal * 0.8`) because get_quote could
		not be told one — which is exactly the client-side arithmetic the board's
		quick-book dialog was doing, and exactly what B4 removed. The server is
		now asked for the discounted total directly, so this row compares two
		SERVER numbers instead of comparing the server to a re-implementation of
		its own formula in the test.
		"""
		quote = get_quote(
			self.COURT,
			3,
			booking_date=WEDNESDAY,
			start_time="17:00:00",
			discount_percent=20,
		)
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3, discount_percent=20)

		self.assertEqual(flt(quote["total_amount"]), flt(doc.total_amount))
		self.assertEqual(flt(doc.total_amount), 1040)  # 1300 − 20%
		# The blend is display-only, but quote and booking must still agree on it,
		# or the dialog's per-segment breakdown describes a different booking.
		self.assertEqual(flt(quote["hourly_rate"]), flt(doc.hourly_rate))
		self.assertEqual(len(quote["segments"]), len(doc.rate_segments))

	def test_a_segmented_member_quote_matches_the_statement_it_produces(self):
		"""quote == booking == INVOICE, across a rate boundary AND a discount —
		the full chain the desk's "Total" now promises.

		The invoice is the half a customer keeps, so it is the half worth pinning:
		a quote that agrees with the booking but not with the printed statement is
		still a money bug, just a later one.
		"""
		quote = get_quote(
			self.COURT,
			3,
			booking_date=WEDNESDAY,
			start_time="17:00:00",
			discount_percent=20,
		)
		with patch(CLOCK, return_value=T0):
			doc = self._book("18:00:00", slots=2, discount_percent=20)
		# 18:00 and 19:00 are both NIGHT_RATE on a Wednesday, so this booking is
		# deliberately a DIFFERENT window from the quote above — the assertions
		# below re-quote for it rather than reusing that number.
		booked_quote = get_quote(
			self.COURT,
			2,
			booking_date=WEDNESDAY,
			start_time="18:00:00",
			discount_percent=20,
		)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)

		self.assertEqual(flt(booked_quote["total_amount"]), flt(doc.total_amount))
		self.assertEqual(flt(booked_quote["total_amount"]), flt(invoice.total_amount))
		self.assertEqual(flt(booked_quote["subtotal"]), flt(invoice.subtotal))
		self.assertEqual(
			flt(booked_quote["discount_amount"]), flt(invoice.discount_amount)
		)
		self.assertEqual(flt(doc.total_amount), 640)  # 800 − 20%
		# The first quote is still asserted, so the row cannot pass by re-quoting
		# the same window twice: a 3-slot run from 17:00 crosses the boundary and
		# must NOT equal the 2-slot night run.
		self.assertNotEqual(
			flt(quote["total_amount"]), flt(booked_quote["total_amount"])
		)


class TestRateExtension(RateRuleTestCase):
	"""The extension prices at ITS OWN window — unless the original was flat."""

	def test_extending_into_the_night_re_resolves_at_the_new_window(self):
		"""The blocker this file exists for: copying the original's
		`hourly_rate` would copy its BLEND, and fetch_if_empty never overwrites
		a value already set — so the extra hour would price at a rate on no
		rule at all."""
		with patch(CLOCK, return_value=T0):
			original = self._book("17:00:00", slots=1)
			self.assertEqual(flt(original.total_amount), BASE_RATE)
			extension_name = extend_booking(original.name, slots=1)
		self._cleanup(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertEqual(str(extension.start_time), "18:00:00")
		self.assertEqual(len(extension.rate_segments), 1)
		self.assertEqual(flt(extension.rate_segments[0].hourly_rate), NIGHT_RATE)
		self.assertEqual(flt(extension.total_amount), NIGHT_RATE)

	def test_extending_a_blended_booking_does_not_inherit_the_blend(self):
		with patch(CLOCK, return_value=T0):
			original = self._book("16:00:00", slots=3)  # 500 + 500 + 400
			self.assertEqual(flt(original.total_amount), 1400)
			self.assertEqual(flt(original.hourly_rate), 466.67)  # the blend
			extension_name = extend_booking(original.name, slots=1)
		self._cleanup(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertEqual(flt(extension.total_amount), NIGHT_RATE)
		self.assertNotEqual(flt(extension.hourly_rate), 466.67)

	def test_extending_a_flat_override_inherits_the_agreed_rate(self):
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=self.COURT,
				booking_date=WEDNESDAY,
				start_time="17:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				number_of_slots=1,
				hourly_rate=300,
			)
			self._cleanup(result["name"])
			extension_name = extend_booking(result["name"], slots=1)
		self._cleanup(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		# 18:00 is night-rate territory, but the desk already agreed ₱300.
		self.assertEqual(extension.rate_segments, [])
		self.assertEqual(flt(extension.hourly_rate), 300)
		self.assertEqual(flt(extension.total_amount), 300)

	def test_extending_an_override_equal_to_the_base_stays_flat(self):
		"""Without the explicit flag on the extension, the base-comparison
		would read this as "no override" and re-price it at the night rate."""
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				court=self.COURT,
				booking_date=WEDNESDAY,
				start_time="17:00:00",
				payment_method="Cash",
				customer=CUSTOMER_EMAIL,
				number_of_slots=1,
				hourly_rate=BASE_RATE,
			)
			self._cleanup(result["name"])
			extension_name = extend_booking(result["name"], slots=1)
		self._cleanup(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertEqual(extension.rate_segments, [])
		self.assertEqual(flt(extension.total_amount), BASE_RATE)

	def test_extending_a_rule_less_court_is_unchanged(self):
		"""Every court before this section. Behaviour must be byte-identical."""
		with patch(CLOCK, return_value=T0):
			original = self._book(
				"10:00:00", court="AYALA-makati-court-a", slots=1
			)
			extension_name = extend_booking(original.name, slots=1)
		self._cleanup(extension_name)

		extension = frappe.get_doc("CBT Court Booking", extension_name)
		self.assertEqual(extension.rate_segments, [])
		self.assertEqual(flt(extension.hourly_rate), flt(original.hourly_rate))


class TestRateInvoice(RateRuleTestCase):
	"""One statement line per segment, and the identities that must hold."""

	def test_invoice_carries_one_line_per_segment(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(len(invoice.items), 2)
		self.assertEqual(flt(invoice.items[0].rate), BASE_RATE)
		self.assertEqual(flt(invoice.items[1].rate), NIGHT_RATE)
		self.assertIn("Night rate", invoice.items[1].description)
		self.assertEqual(flt(invoice.subtotal), 900)
		self.assertEqual(flt(invoice.total_amount), 900)

	def test_zero_discount_prints_no_phantom_centavo(self):
		"""The reason the money base is Σ(segment.amount) and not the unrounded
		Σ(rate × hours): a 0%-discount statement must never read
		"Subtotal ₱1,300.00 / Less discount (0%) ₱0.01 / Total ₱1,299.99"."""
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(flt(invoice.discount_percent), 0)
		self.assertEqual(flt(invoice.discount_amount), 0)
		self.assertEqual(flt(invoice.subtotal), flt(invoice.total_amount))

	def test_subtotal_minus_discount_equals_total_exactly(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=3, discount_percent=15)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(
			flt(invoice.subtotal - invoice.discount_amount, 2),
			flt(invoice.total_amount, 2),
		)

	def test_invoice_sync_is_idempotent_in_line_count(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		for _ in range(3):
			sync_invoice_for_booking(doc.name)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(len(invoice.items), 2)
		self.assertEqual(flt(invoice.subtotal), 900)

	def test_a_flat_booking_still_gets_exactly_one_line(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("10:00:00", court="AYALA-makati-court-a", slots=2)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(len(invoice.items), 1)
		self.assertEqual(flt(invoice.items[0].qty), 2)

	def test_non_vat_company_is_untouched_by_segments(self):
		"""E2EF is NON-VAT and its Court 2 is the seeded ruled court."""
		with patch(CLOCK, return_value=T0):
			doc = self._book(
				"18:00:00", date=WEDNESDAY, court="E2EF-main-court-2", slots=1
			)
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(invoice.vat_mode, "NON-VAT")
		# compute_vat_breakdown returns None for both (pinned as None in
		# test_billing), but these are Currency fields on a PERSISTED document
		# and frappe coerces None to 0.0 on the way in — so the document-level
		# assertion is "no VAT figures", not "literally None".
		self.assertFalse(flt(invoice.vatable_amount))
		self.assertFalse(flt(invoice.vat_amount))
		self.assertEqual(flt(invoice.total_amount), 350)


class TestRateRuleValidation(RateRuleTestCase):
	"""What a court will and will not accept."""

	def _court_with(self, rules):
		court = frappe.get_doc("CBT Court", self.COURT)
		court.set("rate_rules", [])
		for rule in rules:
			court.append("rate_rules", rule)
		return court

	def tearDown(self):
		# Whatever a failing case left behind, put the fixture back — the other
		# classes in this file lean on the exact FIXTURE_RULES windows.
		super().tearDown()
		court = self._court_with(FIXTURE_RULES)
		court.save(ignore_permissions=True)
		frappe.clear_document_cache("CBT Court", self.COURT)

	def _assert_rejected(self, rules, fragment):
		court = self._court_with(rules)
		with self.assertRaises(frappe.ValidationError) as caught:
			court.save(ignore_permissions=True)
		self.assertIn(fragment, str(caught.exception))

	def test_same_tier_overlap_is_rejected(self):
		self._assert_rejected(
			[
				{
					"day_scope": "All Days",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 400,
				},
				{
					"day_scope": "All Days",
					"start_time": "21:00:00",
					"end_time": "23:00:00",
					"hourly_rate": 450,
				},
			],
			"overlap",
		)

	def test_same_specific_day_overlap_is_rejected(self):
		self._assert_rejected(
			[
				{
					"day_scope": "Friday",
					"start_time": "18:00:00",
					"end_time": "20:00:00",
					"hourly_rate": 900,
				},
				{
					"day_scope": "Friday",
					"start_time": "19:00:00",
					"end_time": "21:00:00",
					"hourly_rate": 950,
				},
			],
			"overlap",
		)

	def test_cross_tier_overlap_is_accepted(self):
		"""Not a leniency — it is how precedence is EXPRESSED. Rejecting it
		would make "₱500 all day, ₱900 on Friday nights" unsayable."""
		court = self._court_with(
			[
				{
					"day_scope": "All Days",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 400,
				},
				{
					"day_scope": "Friday",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 900,
				},
				{
					"day_scope": "Weekends",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 700,
				},
			]
		)
		court.save(ignore_permissions=True)  # must not raise
		self.assertEqual(len(court.rate_rules), 3)

	def test_weekdays_and_weekends_never_collide(self):
		"""Same tier, but disjoint days — an identical window is fine."""
		court = self._court_with(
			[
				{
					"day_scope": "Weekdays",
					"start_time": "06:00:00",
					"end_time": "09:00:00",
					"hourly_rate": 300,
				},
				{
					"day_scope": "Weekends",
					"start_time": "06:00:00",
					"end_time": "09:00:00",
					"hourly_rate": 250,
				},
			]
		)
		court.save(ignore_permissions=True)  # must not raise

	def test_touching_windows_are_not_an_overlap(self):
		"""End is exclusive, so 06:00–18:00 and 18:00–22:00 share no slot."""
		court = self._court_with(
			[
				{
					"day_scope": "All Days",
					"start_time": "06:00:00",
					"end_time": "18:00:00",
					"hourly_rate": 300,
				},
				{
					"day_scope": "All Days",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 400,
				},
			]
		)
		court.save(ignore_permissions=True)  # must not raise

	def test_end_before_or_equal_to_start_is_rejected(self):
		self._assert_rejected(
			[
				{
					"day_scope": "All Days",
					"start_time": "22:00:00",
					"end_time": "06:00:00",
					"hourly_rate": 400,
				}
			],
			"must be later than",
		)
		self._assert_rejected(
			[
				{
					"day_scope": "All Days",
					"start_time": "18:00:00",
					"end_time": "18:00:00",
					"hourly_rate": 400,
				}
			],
			"must be later than",
		)

	def test_zero_rate_is_rejected(self):
		self._assert_rejected(
			[
				{
					"day_scope": "All Days",
					"start_time": "18:00:00",
					"end_time": "22:00:00",
					"hourly_rate": 0,
				}
			],
			"greater than zero",
		)


class TestRateInAvailability(RateRuleTestCase):
	"""The grid carries the price — for staff AND for the public portal."""

	def test_every_slot_reports_its_rate(self):
		from court_booking_tech.slots import get_public_availability

		data = get_public_availability(MAKATI, WEDNESDAY)
		court = next(c for c in data["courts"] if c["court"] == self.COURT)
		self.assertTrue(court["has_rate_rules"])
		by_start = {slot["start_time"]: slot for slot in court["slots"]}
		self.assertEqual(flt(by_start["17:00:00"]["rate"]), BASE_RATE)
		self.assertEqual(flt(by_start["18:00:00"]["rate"]), NIGHT_RATE)
		self.assertEqual(flt(by_start["21:00:00"]["rate"]), NIGHT_RATE)

	def test_a_flat_court_reports_its_base_rate_and_no_rules_flag(self):
		from court_booking_tech.slots import get_public_availability

		data = get_public_availability(MAKATI, WEDNESDAY)
		court = next(
			c for c in data["courts"] if c["court"] == "AYALA-makati-court-a"
		)
		self.assertFalse(court["has_rate_rules"])
		for slot in court["slots"]:
			self.assertEqual(flt(slot["rate"]), flt(court["hourly_rate"]))

	def test_weekend_rates_reach_the_grid(self):
		from court_booking_tech.slots import get_public_availability

		data = get_public_availability(MAKATI, SATURDAY)
		court = next(c for c in data["courts"] if c["court"] == self.COURT)
		by_start = {slot["start_time"]: slot for slot in court["slots"]}
		self.assertEqual(flt(by_start["07:00:00"]["rate"]), WEEKEND_RATE)
		self.assertEqual(flt(by_start["10:00:00"]["rate"]), BASE_RATE)


class TestSeededRateCourt(RateRuleTestCase):
	"""The E2E fixture's contract, asserted server-side so a broken seed fails
	the fast backend suite instead of a 3-worker E2E run."""

	def test_seeded_night_rule_prices_the_e2ef_grid(self):
		self.assertEqual(
			pricing.resolve_slot_rate("E2EF-main-court-2", MONDAY, "17:00:00"), 200
		)
		self.assertEqual(
			pricing.resolve_slot_rate("E2EF-main-court-2", MONDAY, "18:00:00"), 350
		)
		# The E2EF branch closes 23:59, which means MIDNIGHT (2026-09-02), so its
		# last slot starts at 23:00. The seeded window ends 23:59 — exclusive,
		# matched by slot START — so both evening slots price at the night rate.
		self.assertEqual(
			pricing.resolve_slot_rate("E2EF-main-court-2", MONDAY, "22:00:00"), 350
		)
		self.assertEqual(
			pricing.resolve_slot_rate("E2EF-main-court-2", MONDAY, "23:00:00"), 350
		)

	def test_seeded_weekend_rule_avoids_every_asserted_hour(self):
		"""E2E dates are RELATIVE, so file 12 runs on a different weekday each
		day. The weekend rule must never touch the hours it asserts, or the
		suite goes red every Saturday."""
		for hour in ("17:00:00", "18:00:00", "19:00:00"):
			self.assertEqual(
				pricing.resolve_slot_rate("E2EF-main-court-2", SATURDAY, hour),
				pricing.resolve_slot_rate("E2EF-main-court-2", MONDAY, hour),
				f"weekend pricing diverges at {hour} — file 12 would flake",
			)
		# It does apply where it is meant to.
		self.assertEqual(
			pricing.resolve_slot_rate("E2EF-main-court-2", SATURDAY, "06:00:00"), 250
		)


class TestRateBackwardCompatibility(RateRuleTestCase):
	"""Nothing about a flat-rate court may have changed."""

	def test_a_flat_court_booking_is_priced_exactly_as_before(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("10:00:00", court="AYALA-makati-court-a", slots=2)
		self.assertEqual(doc.rate_segments, [])
		self.assertEqual(flt(doc.hourly_rate), 300)  # Makati Badminton base
		self.assertEqual(flt(doc.total_amount), 600)

	def test_a_flat_court_still_honours_a_staff_rate_edit(self):
		with patch(CLOCK, return_value=T0):
			doc = self._book("10:00:00", court="AYALA-makati-court-a")
		doc.hourly_rate = 275
		doc.save()
		doc.reload()
		self.assertEqual(flt(doc.total_amount), 275)

	def test_duration_still_excludes_turnover_buffers(self):
		"""Segment hours are SLOT durations summed, never end − start, so a
		buffered branch cannot start billing its turnover time."""
		with patch(CLOCK, return_value=T0):
			doc = self._book("17:00:00", slots=2)
		start = timedelta(hours=17)
		end = pricing._as_timedelta(doc.end_time)
		self.assertLessEqual(
			flt(doc.duration_hours), flt((end - start).total_seconds() / 3600.0)
		)
		self.assertEqual(
			flt(sum(flt(s.hours) for s in doc.rate_segments), 2),
			flt(doc.duration_hours, 2),
		)
