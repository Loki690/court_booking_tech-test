# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Occupancy (section-11, PLAN §7 desk reports) — how much of a branch's
bookable capacity actually got used.

Definition (MVP, recorded so the number is arguable rather than mysterious):

    booked    = Σ duration_hours of Confirmed / Extended / Completed bookings
    available = (slots in that branch's grid for that weekday)
                × slot length in hours
                × number of ACTIVE courts at the branch
    occupancy = booked ÷ available

Deliberate MVP choices:
- **Blocks do not shrink capacity.** A court closed for maintenance still
  counts as unsold capacity — which is the honest reading for a manager asking
  "how much of my building earned money?". (Netting blocks out would let a
  branch reach 100% by blocking everything it failed to sell.)
- **Reserved holds are NOT booked.** An unpaid hold is not utilisation; it
  becomes one when it confirms.
- **Released no-shows are NOT booked** (section-16). BOOKED_STATUSES excludes
  "No Show" on purpose: the court stood empty, so counting it as utilisation
  would let a facility read a busy day off hours nobody played. The money still
  counts on the revenue reports — the two numbers answer different questions,
  and this is the one place they diverge. If the desk resells the freed time,
  the replacement booking is Confirmed and lands here normally.
- Capacity comes from slots.get_slot_grid — the SAME engine that decides what
  is bookable, so the denominator can never disagree with the booking screen.
- A closed day yields zero capacity and is SKIPPED entirely rather than shown
  as 0% (dividing by it is the bug; showing it is the lie).
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from court_booking_tech import clock
from court_booking_tech.reports import resolve_report_company
from court_booking_tech.slots import get_slot_grid

BOOKED_STATUSES = ("Confirmed", "Extended", "Completed")

# A range guard: occupancy is a per-day scan over branches, and an accidental
# multi-year filter would run thousands of grid builds.
MAX_DAYS = 92


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = resolve_report_company(filters.get("company"))
	from_date, to_date = _period(filters)

	branches = frappe.get_all(
		"CBT Branch",
		filters={"company": company, "is_active": 1},
		fields=["name", "branch_name"],
		order_by="name",
	)
	if not branches:
		return _columns(), []

	court_counts = _active_court_counts([b.name for b in branches])
	booked = _booked_hours(company, from_date, to_date)

	data = []
	for branch in branches:
		courts = court_counts.get(branch.name, 0)
		if not courts:
			continue
		branch_doc = frappe.get_doc("CBT Branch", branch.name)
		slot_minutes = branch_doc.get_slot_duration_minutes() or 60
		day = from_date
		while day <= to_date:
			grid = get_slot_grid(branch_doc, day)
			if grid:
				available = flt(len(grid) * (slot_minutes / 60.0) * courts, 2)
				used = flt(booked.get((branch.name, str(day)), 0), 2)
				data.append(
					{
						"day": str(day),
						"branch": branch.name,
						"branch_name": branch.branch_name,
						"courts": courts,
						"available_hours": available,
						"booked_hours": used,
						"occupancy_percent": flt(used / available * 100.0, 2)
						if available
						else 0.0,
					}
				)
			day = add_days(day, 1)

	data.sort(key=lambda row: (row["day"], row["branch"]))
	return _columns(), data


def _period(filters) -> tuple:
	today = clock.now_dt().date()
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else today
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else today
	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))
	if (to_date - from_date).days > MAX_DAYS:
		frappe.throw(_("Choose a range of {0} days or fewer.").format(MAX_DAYS))
	return from_date, to_date


def _active_court_counts(branches: list) -> dict:
	# Raw SQL, not get_all: frappe v16 rejects SQL functions passed as field
	# STRINGS ("count(name) as cnt") — and the rest of this report is raw SQL
	# anyway, so aggregating the same way keeps one style.
	rows = frappe.db.sql(
		"""
		SELECT branch, COUNT(name) AS cnt
		FROM `tabCBT Court`
		WHERE is_active = 1 AND branch IN %(branches)s
		GROUP BY branch
		""",
		{"branches": tuple(branches)},
		as_dict=True,
	)
	return {row.branch: int(row.cnt or 0) for row in rows}


def _booked_hours(company: str, from_date, to_date) -> dict:
	rows = frappe.db.sql(
		"""
		SELECT branch, booking_date AS day, SUM(duration_hours) AS hours
		FROM `tabCBT Court Booking`
		WHERE company = %(company)s
		  AND booking_status IN %(statuses)s
		  AND booking_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY branch, booking_date
		""",
		{
			"company": company,
			"statuses": BOOKED_STATUSES,
			"from_date": from_date,
			"to_date": to_date,
		},
		as_dict=True,
	)
	return {(row.branch, str(row.day)): row.hours for row in rows}


def _columns():
	return [
		{"fieldname": "day", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "branch",
			"label": _("Branch"),
			"fieldtype": "Link",
			"options": "CBT Branch",
			"width": 150,
		},
		{"fieldname": "branch_name", "label": _("Branch Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "courts", "label": _("Courts"), "fieldtype": "Int", "width": 80},
		{
			"fieldname": "available_hours",
			"label": _("Available Hours"),
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"fieldname": "booked_hours",
			"label": _("Booked Hours"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "occupancy_percent",
			"label": _("Occupancy %"),
			"fieldtype": "Percent",
			"width": 130,
		},
	]
