# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Rate rules (section-14, PLAN §4/§8b) — THE single seam every price goes through.

Why one module (membership.py doctrine): a court's price is now resolved at
five independent places — the availability grid, the portal quote, the portal
reserve, the desk quick-book and the extension. If each derived "what does this
slot cost" itself they would drift, and a quote that disagrees with the booking
it produces is a customer-facing money bug.

PRECEDENCE (highest wins), PLAN §8b:
    specific weekday  >  Weekdays / Weekends  >  All Days  >  court.hourly_rate
Cross-tier overlap is LEGAL — that is exactly how precedence is expressed
("₱300 all day, but ₱500 on Friday nights"). Same-tier overlap is rejected by
CBT Court's validation, because there the answer would be arbitrary.

MATCHING is by the slot's START only, and the rule's end is EXCLUSIVE:
    rule.start <= slot_start < rule.end
So one slot is never split across two rates, an 18:00–23:00 night rule catches
the 22:00 slot, and a rule ending at 18:00 does NOT catch the 18:00 slot.

THE MONEY RULE (S11 as-built 6, extended here): a booking's pre-discount base is
`Σ(segment.amount)` where each `amount` is already rounded to the centavo — NOT
the unrounded `Σ(rate × hours)`. Those differ by a centavo on some inputs, and
the rounded form is the one the printed statement can reproduce: the invoice's
subtotal is the sum of its ITEM LINES, and the lines ARE the segments. With the
unrounded form a 0%-discount booking could print
"Subtotal ₱1,000.00 / Less discount (0%) ₱0.01 / Total ₱999.99".
Exactly ONE rounding is then applied, on the discount step.
"""

from datetime import timedelta

import frappe
from frappe.utils import flt, getdate

from court_booking_tech.timeutil import _as_timedelta, _fmt

# Monday-indexed, matching datetime.date.weekday().
DAY_NAMES = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)

WEEKEND_INDEXES = (5, 6)  # Saturday, Sunday

DAY_SCOPES = ("All Days", "Weekdays", "Weekends", *DAY_NAMES)

RULE_FIELDS = ("day_scope", "start_time", "end_time", "hourly_rate", "label")

# Specificity tiers. 0 = the rule does not apply to this date at all.
TIER_ALL_DAYS = 1
TIER_DAY_GROUP = 2
TIER_SPECIFIC_DAY = 3


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def get_court_pricing(court) -> dict:
	"""{'court', 'base_rate', 'rules'} for one court.

	Accepts a court NAME, a CBT Court document, or an already-built pricing
	dict (idempotent — callers can pass their cached one straight back in, so
	no code path is forced to re-query just to satisfy a signature).
	"""
	if isinstance(court, dict) and "base_rate" in court:
		return court
	if isinstance(court, str):
		row = frappe.db.get_value(
			"CBT Court", court, ["name", "hourly_rate"], as_dict=True
		)
		if not row:
			return {"court": court, "base_rate": 0.0, "rules": []}
		return {
			"court": row.name,
			"base_rate": flt(row.hourly_rate),
			"rules": _fetch_rules([row.name]).get(row.name, []),
		}
	# A CBT Court document — its child rows are already in memory.
	return {
		"court": court.name,
		"base_rate": flt(court.hourly_rate),
		"rules": [
			{field: rule.get(field) for field in RULE_FIELDS}
			for rule in (court.get("rate_rules") or [])
		],
	}


def get_rate_rules_map(court_names) -> dict:
	"""{court: [rule, ...]} for many courts in ONE query.

	The availability grid prices every court × every slot; fetching per slot
	(or even per court) turns one board load into dozens of round trips.
	"""
	names = [name for name in court_names if name]
	if not names:
		return {}
	return _fetch_rules(names)


def _fetch_rules(court_names) -> dict:
	rows = frappe.get_all(
		"CBT Court Rate Rule",
		filters={"parenttype": "CBT Court", "parent": ("in", list(court_names))},
		fields=["parent", *RULE_FIELDS],
		order_by="parent asc, idx asc",
		ignore_permissions=True,
	)
	grouped: dict[str, list] = {}
	for row in rows:
		grouped.setdefault(row.parent, []).append(
			{field: row.get(field) for field in RULE_FIELDS}
		)
	return grouped


def has_rate_rules(court) -> bool:
	return bool(get_court_pricing(court)["rules"])


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def rule_tier(day_scope: str, weekday: int) -> int:
	"""How specific is this rule for a date with this weekday? 0 = N/A."""
	if not day_scope:
		return 0
	if day_scope == DAY_NAMES[weekday]:
		return TIER_SPECIFIC_DAY
	if day_scope == "Weekends" and weekday in WEEKEND_INDEXES:
		return TIER_DAY_GROUP
	if day_scope == "Weekdays" and weekday not in WEEKEND_INDEXES:
		return TIER_DAY_GROUP
	if day_scope == "All Days":
		return TIER_ALL_DAYS
	return 0


def _resolve(pricing: dict, date, start_td: timedelta):
	"""(rate, label) for a slot starting at `start_td` on `date`."""
	weekday = getdate(date).weekday()
	best_tier = 0
	best = None
	for rule in pricing["rules"]:
		tier = rule_tier(rule.get("day_scope"), weekday)
		if tier <= best_tier:
			# Ties keep the FIRST row by idx. Same-tier overlap is rejected on
			# save, so a tie here means legacy data — answer deterministically
			# rather than letting two reads of the same booking disagree.
			continue
		start, end = rule.get("start_time"), rule.get("end_time")
		# None-checks, NOT truthiness: a 00:00 window is timedelta(0) — falsy
		# and perfectly valid (S4 as-built 9, the bug class that silently
		# closed the whole e2e-fast branch once already).
		if start is None or end is None:
			continue
		if not (_as_timedelta(start) <= start_td < _as_timedelta(end)):
			continue
		best_tier = tier
		best = rule
	if best is None:
		return flt(pricing["base_rate"]), None
	return flt(best.get("hourly_rate")), (best.get("label") or None)


def resolve_slot_rate(court, date, start_td) -> float:
	"""The hourly rate that applies to the slot starting at `start_td`."""
	return _resolve(get_court_pricing(court), date, _as_timedelta(start_td))[0]


def build_rate_segments(court, date, grid_slice) -> list[dict]:
	"""The booking's OWN pricing snapshot — [] when the court has no rules.

	Rules can be edited or deleted later, and invoice amounts re-derive from
	the BOOKING on every sync (S6), so a priced booking has to carry its own
	segments rather than re-reading the court.

	Adjacent slots at the same (rate, label) MERGE into one segment. `hours` is
	the sum of the SLOT durations, never end − start: turnover buffers sit
	between slots and are not billable (S4 as-built 2).
	"""
	pricing = get_court_pricing(court)
	if not pricing["rules"] or not grid_slice:
		return []

	segments: list[dict] = []
	for slot in grid_slice:
		start = _as_timedelta(slot["start_time"])
		end = _as_timedelta(slot["end_time"])
		rate, label = _resolve(pricing, date, start)
		hours = flt((end - start).total_seconds() / 3600.0, 2)
		previous = segments[-1] if segments else None
		if previous and previous["hourly_rate"] == rate and previous["label"] == label:
			previous["end_time"] = _fmt(end)
			previous["hours"] = flt(previous["hours"] + hours, 2)
			previous["amount"] = flt(previous["hourly_rate"] * previous["hours"], 2)
			continue
		segments.append(
			{
				"start_time": _fmt(start),
				"end_time": _fmt(end),
				"label": label,
				"hourly_rate": rate,
				"hours": hours,
				"amount": flt(rate * hours, 2),
			}
		)
	return segments


# ---------------------------------------------------------------------------
# Money (one formula, four callers)
# ---------------------------------------------------------------------------


def _value(segment, fieldname):
	"""Segments arrive as dicts (fresh from build_rate_segments) or as child
	documents (loaded from a saved booking). `.get()` reads both, and going
	through one accessor is what lets the quote and the controller share every
	money helper below."""
	return segment.get(fieldname)


def segments_subtotal(segments) -> float:
	"""Pre-discount base = Σ of the ALREADY-ROUNDED line amounts (see header)."""
	return flt(sum(flt(_value(seg, "amount"), 2) for seg in segments or []), 2)


def total_from_segments(segments, discount_percent) -> float:
	"""The one discount formula — SINGLE rounding (S11 as-built 6)."""
	return flt(
		segments_subtotal(segments) * (1 - flt(discount_percent) / 100.0), 2
	)


def blended_rate(segments, duration_hours) -> float:
	"""DISPLAY-ONLY average rate.

	Never an input: recomputing a total as `blended_rate × duration_hours`
	drifts (₱500×1h + ₱400×2h = ₱1,300 → blend 433.33 → 433.33×3 = ₱1,299.99).
	Every recompute goes through total_from_segments instead.
	"""
	hours = flt(duration_hours)
	if not hours:
		return 0.0
	return flt(segments_subtotal(segments) / hours, 2)


def segments_payload(segments) -> list[dict]:
	"""JSON-safe segment rows for the quote / board / portal responses."""
	return [
		{
			"start_time": _payload_time(_value(seg, "start_time")),
			"end_time": _payload_time(_value(seg, "end_time")),
			"label": _value(seg, "label"),
			"hourly_rate": flt(_value(seg, "hourly_rate")),
			"hours": flt(_value(seg, "hours"), 2),
			"amount": flt(_value(seg, "amount"), 2),
		}
		for seg in segments or []
	]


def _payload_time(value):
	return _fmt(_as_timedelta(value)) if value is not None else None
