# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Per-booking platform fee (Backlog B27, 2026-08-27) — the THIRD billing mode.

Subscription and Percentage are ABSORB modes: the tenant owes us out of what it
earned, the customer never sees it. **Per Booking is an ADD-ON**: every paid
booking carries a peso fee the CUSTOMER pays on top of the court, printed as its
own line ("Booking fee"), which the tenant collects and remits on the monthly
statement (user rulings, 2026-08-20 / 2026-08-27).

The rules, each of which a test pins:

- TIERS are MARGINAL brackets on a MONTHLY counter — "first 20 bookings ₱15,
  21st–50th ₱30, 51st and up ₱40". Contiguous from 1, no gaps, no overlaps,
  ascending, the last one open-ended so every booking has a price. The user's
  own first example contained an overlap (21–50 then 31–999), which is why
  `validate_tiers` is not optional.
- ONE BOOKING = ONE UNIT whatever its length; an EXTENSION is its own unit
  ("extend is another book"); a RESCHEDULE is NOT — the unit was already sold,
  so the replacement CARRIES the original's fee and ordinal and the original
  (cancelled) stops counting.
- Backlog B49 (2026-09-09): a CONTINUED SESSION — bookings on different
  continue-on courts of one checkout whose hours touch — is ONE unit. The
  first hour carries `platform_fee_seq` and the fee; each continuation carries
  `fee_chained_to` + `platform_fee_waived` and NO unit, so `next_ordinal`
  counts one per session and the platform revenue report's `units` column
  counts sessions, not court bookings. Cancelling an unpaid head hands the
  unit to its continuation (the controller); a refunded paid head takes the
  session's fee back with it and the continuation stays waived (ruled).
- The fee is fixed AT INSERT, from the count of units the tenant has sold for
  that SERVICE MONTH so far (+1). Attribution by service month matches every
  report in the app. A cancelled or expired booking stops counting, so a later
  sale may reuse its place in the brackets (an expired hold that is later
  retro-confirmed therefore shares its number with the sale that took its
  place — the ordinal is "the count at the moment of sale", not a unique id);
  an already-sold booking's fee never moves — the number the customer was
  shown is the number on their statement. The checkout QUOTE runs the same
  count moments earlier and is NOT carried into the insert: two inserts in
  the same instant, or a quote and a reserve straddling a bracket boundary,
  can differ by one bracket. The brackets are wide by ruling ("first 50, then
  100, then 150"), so that is a once-a-month edge, and nothing is ever
  double-billed — each booking carries exactly the fee it was inserted with.
- A fee unit never crosses a CLOSED month: a reschedule of a fee-carrying
  booking into or out of a closed month is refused (`api/bookings.py`), because
  the frozen month would keep or lose a fee the live month also counts.
- OPEN PLAY entries carry their own flat fee and are counted on the platform's
  side as fee units (`booking_count` on the report, close and statement counts
  every PAID document that carried a fee — court bookings AND open play
  entries); they never enter the BOOKING ordinal above.
- FREE bookings carry no fee and no unit (the ruling was "every PAID
  transaction"); a REFUNDED booking (cancelled after payment) refunds the fee
  with it and its unit does not count — a refund is not a transaction the
  tenant kept.
- OPEN PLAY is not a CBT Court Booking; it has its own flat per-participant fee
  on the company, charged to each PAYING participant on top of the session fee
  (stamped when the player is added; a Free entry stays free — the engine has
  no path from Free to paying).
- VAT: the fee sits inside the customer's VAT-inclusive total on the tenant's
  billing document — ONE VAT footer, exactly as today (user ruling 2026-08-27:
  "100 + 15, total 115, VAT-inclusive if the tenant is VAT, else no VAT shown").
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate

PER_BOOKING = "Per Booking"

# A booking that has stopped being a sale. Everything else — a Reserved hold
# that may still expire included — holds its place in the month's count: the
# customer was quoted a fee at that ordinal, and nothing after it renumbers.
UNCOUNTED_STATUSES = ("Cancelled", "Expired")


def validate_tiers(tiers, label=None):
	"""Refuse anything that would leave a booking without exactly one price."""
	label = label or _("Booking Fee Tiers")
	rows = list(tiers or [])
	if not rows:
		frappe.throw(
			_(
				"Per Booking billing needs at least one tier in {0} — otherwise "
				"every booking would silently carry no fee."
			).format(label)
		)
	expected_from = 1
	for index, row in enumerate(rows, start=1):
		from_count = cint(row.get("from_count"))
		to_count = cint(row.get("to_count"))
		fee = flt(row.get("fee"))
		if fee < 0:
			frappe.throw(_("Tier {0}: the fee cannot be negative.").format(index))
		if from_count != expected_from:
			frappe.throw(
				_(
					"Tier {0} must start at booking #{1} — tiers run contiguously from 1 "
					"with no gaps and no overlaps (it starts at #{2})."
				).format(index, expected_from, from_count)
			)
		is_last = index == len(rows)
		if to_count == 0:
			if not is_last:
				frappe.throw(
					_(
						"Tier {0} is open-ended but is not the last tier — only the last "
						"tier may leave To Booking # blank."
					).format(index)
				)
		else:
			if to_count < from_count:
				frappe.throw(
					_("Tier {0}: To Booking # ({1}) is before From Booking # ({2}).").format(
						index, to_count, from_count
					)
				)
			expected_from = to_count + 1
	if cint(rows[-1].get("to_count")):
		frappe.throw(
			_(
				"The last tier must be open-ended — leave its To Booking # blank so the "
				"{0}th booking and every one after it has a price."
			).format(expected_from)
		)


def tier_fee(tiers, ordinal: int) -> float:
	"""The fee for the `ordinal`-th unit of the month under `tiers`."""
	for row in tiers or []:
		from_count = cint(row.get("from_count"))
		to_count = cint(row.get("to_count"))
		if from_count <= ordinal and (not to_count or ordinal <= to_count):
			return flt(row.get("fee"), 2)
	return 0.0


def month_window(booking_date) -> tuple:
	anchor = getdate(booking_date)
	return get_first_day(anchor), get_last_day(anchor)


def closed_month_key(booking_date) -> str | None:
	"""The period key ("2028-05") if that service month is CLOSED, else None."""
	anchor = getdate(booking_date)
	period = f"{anchor.year:04d}-{anchor.month:02d}"
	return period if frappe.db.exists("CBT Platform Month Close", period) else None


def next_ordinal(company: str, booking_date) -> int:
	"""1 + the units the tenant has sold for this service month so far.

	Counts bookings that CARRY a unit (`platform_fee_seq > 0`) and are still
	sales — so a Free booking, a booking sold before the tenant was billed Per
	Booking, and a cancelled or expired one all leave the count alone.
	"""
	first, last = month_window(booking_date)
	return (
		frappe.db.count(
			"CBT Court Booking",
			{
				"company": company,
				"booking_date": ["between", [first, last]],
				"booking_status": ["not in", list(UNCOUNTED_STATUSES)],
				"platform_fee_seq": [">", 0],
			},
		)
		+ 1
	)


def company_tiers(company: str) -> list:
	return frappe.get_all(
		"CBT Booking Fee Tier",
		filters={"parent": company, "parenttype": "CBT Company"},
		fields=["from_count", "to_count", "fee"],
		order_by="idx asc",
	)


def booking_fee(company: str, booking_date, payment_method) -> tuple:
	"""(ordinal, fee) a NEW booking would carry — (0, 0.0) when no fee applies.

	The one function both the checkout QUOTE and the booking CONTROLLER call.
	Same tiers, same count rule — but the count is taken TWICE (quote, then
	insert), so the two agree unless a unit lands in between at a bracket
	boundary; see the module docstring.
	"""
	mode = frappe.db.get_value("CBT Company", company, "billing_mode")
	if mode != PER_BOOKING or payment_method == "Free":
		return 0, 0.0
	tiers = company_tiers(company)
	if not tiers:
		# validate refuses saving Per Booking without tiers; a db_set bypass
		# charges nothing rather than inventing a price.
		return 0, 0.0
	ordinal = next_ordinal(company, booking_date)
	return ordinal, tier_fee(tiers, ordinal)


def booking_fees_for_units(company: str, dates, payment_method) -> list:
	"""(ordinal, fee) for a RUN of units quoted together — Backlog B35's cart.

	A UNIT here is what a unit has always been in this module: one
	`CBT Court Booking` row, which is one (date, court, contiguous run). The
	cart's normalizer is what guarantees that — it merges adjacent selections
	into one run before anything reaches this function — so a cart of four
	consecutive hours on one court arrives as ONE date and is charged ONE fee,
	which is the user's ruling 2026-09-03.

	Why this exists at all: `booking_fee` re-reads `next_ordinal` on every call,
	so quoting N items one at a time prices every one of them at the SAME
	ordinal. Nothing has been inserted yet, so the count cannot move. That is
	invisible on a wide bracket and wrong at a boundary, and it would put a
	number on the checkout that the N inserts then disagree with. Here the count
	is taken ONCE per service month and walked forward in the caller's order,
	which is exactly what the N inserts will do to themselves (each insert sees
	its predecessors in the same transaction — UNCOUNTED_STATUSES excludes
	Reserved, so a fresh hold holds its place).

	Attribution stays per SERVICE MONTH, so a cart spanning a month boundary
	takes each month's own numbering. The quote-vs-insert straddle described in
	the module docstring is unchanged in kind — only N wide instead of one.
	"""
	dates = list(dates)
	mode = frappe.db.get_value("CBT Company", company, "billing_mode")
	if mode != PER_BOOKING or payment_method == "Free":
		return [(0, 0.0) for _date in dates]
	tiers = company_tiers(company)
	if not tiers:
		return [(0, 0.0) for _date in dates]

	# One running counter per service month, seeded from the DB the first time
	# that month is seen and advanced by hand afterwards.
	counters: dict = {}
	out = []
	for booking_date in dates:
		anchor = getdate(booking_date)
		key = (anchor.year, anchor.month)
		if key not in counters:
			counters[key] = next_ordinal(company, anchor)
		ordinal = counters[key]
		counters[key] += 1
		out.append((ordinal, tier_fee(tiers, ordinal)))
	return out


def open_play_fee(company: str, payment_method) -> float:
	"""The flat per-participant add-on for a PAYING open play participant."""
	if payment_method == "Free":
		return 0.0
	row = frappe.db.get_value(
		"CBT Company",
		company,
		["billing_mode", "open_play_fee_per_participant"],
		as_dict=True,
	)
	if not row or row.billing_mode != PER_BOOKING:
		return 0.0
	return flt(row.open_play_fee_per_participant, 2)
