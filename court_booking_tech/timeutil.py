# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Time-field primitives shared by every scheduling surface (section-14 extraction).

These three functions were born in slots.py and are now imported by slots,
billing, the booking controller, the court controller and pricing. Section-14
added the last two, and `pricing` is a LOWER-level seam than `slots` (slots
calls pricing to stamp a rate on each slot) — leaving them in slots would have
forced either an import cycle or a runtime local import inside `_availability`'s
per-court loop. Both are worse than a 30-line module.

slots.py still re-exports all three, so every pre-section-14 import keeps
working unchanged.

THE RULE THAT KEEPS BITING (S4 as-built 9): a Time field at midnight is
`timedelta(0)` — FALSY but perfectly valid. Every caller must None-check these
values, never test them for truthiness.
"""

from datetime import time, timedelta

import frappe
from frappe import _
from frappe.utils import formatdate


def _as_timedelta(value) -> timedelta:
	"""Normalize a Time field value (timedelta | datetime.time | 'HH:MM[:SS]')."""
	if isinstance(value, timedelta):
		return value
	if isinstance(value, time):
		return timedelta(
			hours=value.hour, minutes=value.minute, seconds=value.second
		)
	if isinstance(value, str):
		parts = [int(p) for p in value.split(":")]
		while len(parts) < 3:
			parts.append(0)
		return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
	frappe.throw(_("Invalid time value: {0}").format(value))


END_OF_DAY = timedelta(hours=24)
LAST_MINUTE = timedelta(hours=23, minutes=59)


def closing_boundary(value) -> timedelta:
	"""A closing of 23:59 — the latest a Time field can hold — means MIDNIGHT (24:00).

	`>=` so a seconds-carrying 23:59:59 reads the same way; any earlier closing is itself.
	"""
	closing = _as_timedelta(value)
	return END_OF_DAY if closing >= LAST_MINUTE else closing


def _fmt(td: timedelta) -> str:
	"""timedelta -> 'HH:MM:SS' (JSON-friendly, matches Time field storage)."""
	total = int(td.total_seconds())
	return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _fmt_time(value) -> str:
	"""'HH:MM' — the 24-hour form the transactional emails still use."""
	minutes = int(_as_timedelta(value).total_seconds() // 60)
	return f"{minutes // 60:02d}:{minutes % 60:02d}"


def label_short(value) -> str:
	"""'6 AM', '6:30 AM', '12 MN', '12 NN' — mirrors `labelShort` in
	www/cbt-book.html so the grid and the printed statement say one thing.
	`% 24` is what lands a 24:00 end on the midnight branch."""
	minutes = int(_as_timedelta(value).total_seconds() // 60)
	hour, minute = (minutes // 60) % 24, minutes % 60
	if minute == 0:
		if hour == 0:
			return _("12 MN")
		if hour == 12:
			return _("12 NN")
	shown = hour % 12 or 12
	return "{0} {1}".format(
		shown if minute == 0 else f"{shown}:{minute:02d}",
		"AM" if hour < 12 else "PM",
	)


def label_date(value) -> str:
	"""'Sep-03-2026' — the month spelled out so a date can never be read
	day-first or month-first by mistake (user ruling 2026-09-04)."""
	return formatdate(value, "MMM-dd-yyyy")


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
	"""Half-open interval intersection: [a_start, a_end) × [b_start, b_end)."""
	return a_start < b_end and b_start < a_end
