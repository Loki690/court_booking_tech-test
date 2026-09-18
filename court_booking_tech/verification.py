# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Two-clock verification engine core (section-5, PLAN §5a).

The office-hours WALKER converts "staff get N working hours to review a proof"
into a concrete deadline by accumulating only minutes inside the company's
office-hours windows. It is pure given the company's rows + effective hold
hours; the END cap (min with the booking end) is applied by the CALLER — the
walker never sees the booking.

Also home to the cap-key email normalization and the shared proof status-flip
helpers used by both api/proofs.py and api/bookings.py (keeping them here
avoids a circular import between the two API modules).
"""

from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from court_booking_tech import clock
from court_booking_tech.slots import _as_timedelta

DEFAULT_HOLD_HOURS = 4  # get_single_value yields 0 on a never-saved Singles

# 8 weeks — ducky-defined safety horizon. A weekly table with ANY open time
# repeats, so a sane config exhausts the hold budget within days; still
# walking after 8 weeks means a degenerate config (e.g. one open minute a
# week) and falls back exactly like an empty table.
WALK_HORIZON_DAYS = 56

FALLBACK_HOURS = 24


def compute_verification_deadline(company, uploaded_at) -> datetime:
	"""Deadline = the moment the company's hold budget (office-hours time)
	runs out, walking from uploaded_at.

	Closed days are skipped; rows are one-per-day, same-day-only (validated
	upstream in validate_business_hours — the walker never handles split
	shifts or overnight windows). A table yielding NO open time falls back to
	a linear 24h deadline — a guard for misconfiguration ONLY, never a min()
	bound on a valid walk (a Friday-night upload with a Mon–Fri office must
	survive the weekend).
	"""
	company_doc = (
		frappe.get_doc("CBT Company", company) if isinstance(company, str) else company
	)
	uploaded_at = get_datetime(uploaded_at)
	budget = timedelta(
		hours=company_doc.get_verification_hold_hours() or DEFAULT_HOLD_HOURS
	)

	# None-checks, NOT truthiness: a midnight opening is timedelta(0) — falsy
	# but very much open (S4 as-built 9; the e2e-fast company runs 00:00–23:59).
	rows = {
		row.day: row
		for row in (company_doc.office_hours or [])
		if cint(row.is_open)
		and row.opening_time not in (None, "")
		and row.closing_time not in (None, "")
	}

	for day_offset in range(WALK_HORIZON_DAYS):
		day = uploaded_at.date() + timedelta(days=day_offset)
		row = rows.get(day.strftime("%A"))
		if not row:
			continue
		day_start = datetime.combine(day, time.min)
		open_dt = day_start + _as_timedelta(row.opening_time)
		close_dt = day_start + _as_timedelta(row.closing_time)
		window_start = max(open_dt, uploaded_at)
		if window_start >= close_dt:
			continue  # today's window already over (e.g. night upload)
		available = close_dt - window_start
		if budget <= available:
			return window_start + budget
		budget -= available

	return uploaded_at + timedelta(hours=FALLBACK_HOURS)


DAY_ORDER = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)
DAY_SHORT = {day: day[:3] for day in DAY_ORDER}


def _fmt_office_time(value) -> str:
	"""'09:00:00' -> '9:00 AM' (customer-facing; the walker keeps timedeltas)."""
	minutes = int(_as_timedelta(value).total_seconds() // 60) % (24 * 60)
	hour, minute = divmod(minutes, 60)
	suffix = "AM" if hour < 12 else "PM"
	display_hour = hour % 12 or 12
	return f"{display_hour}:{minute:02d} {suffix}"


def office_hours_summary(company) -> str:
	"""One human sentence for the portal's verification notice (PLAN §5a):
	"Mon–Fri 9:00 AM–6:00 PM · Sat 10:00 AM–2:00 PM".

	Consecutive days sharing a window collapse into a range. An empty or
	fully-closed table degrades to a vague phrase rather than lying — the
	walker's 24h fallback applies there anyway.
	"""
	company_doc = (
		frappe.get_doc("CBT Company", company) if isinstance(company, str) else company
	)
	windows = {}
	for row in company_doc.office_hours or []:
		# Same None-checks as the walker: midnight is timedelta(0), falsy.
		if (
			not cint(row.is_open)
			or row.opening_time in (None, "")
			or row.closing_time in (None, "")
		):
			continue
		windows[row.day] = (
			f"{_fmt_office_time(row.opening_time)}–{_fmt_office_time(row.closing_time)}"
		)
	if not windows:
		return _("during business hours")

	groups = []
	for day in DAY_ORDER:
		window = windows.get(day)
		if not window:
			continue
		if groups and groups[-1][2] == window and DAY_ORDER.index(day) == groups[-1][3] + 1:
			groups[-1][1] = day  # extend the run
			groups[-1][3] = DAY_ORDER.index(day)
		else:
			groups.append([day, day, window, DAY_ORDER.index(day)])

	parts = []
	for first, last, window, _index in groups:
		label = (
			DAY_SHORT[first]
			if first == last
			else f"{DAY_SHORT[first]}–{DAY_SHORT[last]}"
		)
		parts.append(f"{label} {window}")
	return " · ".join(parts)


def normalize_email_for_cap(email: str) -> str:
	"""Cap-key normalization ONLY — never rewrites accounts (PLAN §5a).

	Plus-addressing (foo+9@) and Gmail dot-insensitivity (f.o.o@gmail.com)
	would otherwise mint unlimited "distinct" customers from one inbox and
	pierce the per-customer hold cap.
	"""
	email = (email or "").strip().lower()
	local, sep, domain = email.partition("@")
	if not sep:
		return email
	local = local.split("+", 1)[0]
	if domain in ("gmail.com", "googlemail.com"):
		local = local.replace(".", "")
	return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# Shared proof status flips — controller-driven writes (the proof DocType is
# immutable through doc.save; these run under the CALLER's booking row lock).
# ---------------------------------------------------------------------------


def get_pending_proofs(booking: str) -> list[str]:
	return frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": booking, "status": "Pending"},
		pluck="name",
	)


def has_reviewable_proof(booking: str) -> bool:
	"""Retro-confirm eligibility: at least one proof that is not Rejected."""
	return bool(
		frappe.db.exists(
			"CBT Payment Proof",
			{"booking": booking, "status": ("in", ("Pending", "Accepted"))},
		)
	)


def accept_pending_proofs(booking: str) -> list[str]:
	names = get_pending_proofs(booking)
	for name in names:
		frappe.db.set_value("CBT Payment Proof", name, "status", "Accepted")
	return names


def reject_pending_proofs(booking: str, reason: str, now=None) -> list[str]:
	now = now or clock.now_dt()
	names = get_pending_proofs(booking)
	for name in names:
		frappe.db.set_value(
			"CBT Payment Proof",
			name,
			{
				"status": "Rejected",
				"rejected_by": frappe.session.user,
				"rejected_at": now,
				"rejection_reason": reason,
			},
		)
	return names
