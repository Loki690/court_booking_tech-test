# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_time


class CBTBusinessHours(Document):
	pass


def validate_business_hours(rows, label=None):
	"""Shared weekly-hours validator, called from the PARENT's validate.

	Used by CBT Company (office_hours) and, from section-3, CBT Branch (court
	hours). Rules:
	- one row per day (the section-5 office-hours walker assumes this)
	- open rows need both times, closing > opening — SAME-DAY ONLY, overnight
	  rows are rejected (PLAN gap decision p)
	- an empty table is allowed (the walker has a linear-24h fallback guard)
	"""
	label = label or _("Business Hours")
	seen_days = set()
	for row in rows or []:
		if row.day in seen_days:
			frappe.throw(
				_("{0}: duplicate row for {1} — use one row per day.").format(label, _(row.day))
			)
		seen_days.add(row.day)

		if not row.is_open:
			continue
		# None/empty checks, NOT truthiness: a 00:00 opening loads from the DB
		# as timedelta(0) — falsy but very much a time (same bug class as S4
		# as-built 9; the seeded e2e-fast company opens at midnight and any
		# re-save of it used to throw here).
		if row.opening_time in (None, "") or row.closing_time in (None, ""):
			frappe.throw(
				_("{0}: {1} is marked Open but is missing an opening or closing time.").format(
					label, _(row.day)
				)
			)
		if get_time(row.closing_time) <= get_time(row.opening_time):
			frappe.throw(
				_(
					"{0}: {1} closing time must be after the opening time on the same day "
					"(overnight rows are not supported)."
				).format(label, _(row.day))
			)
