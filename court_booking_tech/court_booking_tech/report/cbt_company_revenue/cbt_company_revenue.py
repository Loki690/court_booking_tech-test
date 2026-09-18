# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Company Revenue (section-11, PLAN §7 desk reports) — what a facility earned,
per branch per day, split between court rentals and open play.

Attribution is by SERVICE DATE, matching CBT Platform Revenue: the two reports
must agree on what a month earned, or the tenant and the platform will argue
about a commission bill using different numbers.

Scoping (script reports bypass permission_query_conditions, so it is explicit):
a tenant user is FORCED onto their own company whatever the filter says —
report filters are user input and must never be a way around the tenancy spine.

NO-SHOWS COUNT AS REVENUE (section-16, PLAN §8s) — deliberately, and it needs
no code here: this report keys on the INVOICE being Paid & Verified, and a
released booking keeps that status because the money really moved. The slot was
forfeited, not refunded, and if the desk resold the freed time that second
booking earns on top. CBT Platform Revenue joins the same way, so the
commission figure and the tenant's own figure cannot disagree — which is the
whole reason both reports attribute by service date. The one place a no-show is
deliberately NOT counted is CBT Occupancy: released time is unsold unless
somebody bought it again.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from court_booking_tech import clock
from court_booking_tech.billing import PAID
from court_booking_tech.reports import resolve_report_company


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = resolve_report_company(filters.get("company"))
	from_date, to_date = _period(filters)

	branch_names = {
		row.name: row.branch_name
		for row in frappe.get_all(
			"CBT Branch", filters={"company": company}, fields=["name", "branch_name"]
		)
	}

	buckets = {}

	def bucket(branch, day):
		key = (branch, str(day))
		return buckets.setdefault(
			key,
			{
				"branch": branch,
				"branch_name": branch_names.get(branch, branch),
				"day": str(day),
				"bookings_count": 0,
				"bookings_revenue": 0.0,
				"open_play_revenue": 0.0,
				"platform_fees": 0.0,
			},
		)

	# Backlog B27: a Per Booking tenant's documents carry the platform's own
	# fee inside `total_amount`. That peso is collected FOR us, not earned by
	# the facility, so revenue is `total − platform_fee` and the fees are shown
	# in their own column — the amount the tenant will remit on its statement.
	for row in frappe.db.sql(
		"""
		SELECT b.branch AS branch, b.booking_date AS day,
		       COUNT(*) AS cnt,
		       SUM(b.total_amount - COALESCE(b.platform_fee, 0)) AS total,
		       SUM(COALESCE(b.platform_fee, 0)) AS fees
		FROM `tabCBT Booking Invoice` i
		JOIN `tabCBT Court Booking` b ON b.billing_doc = i.name
		WHERE i.company = %(company)s AND i.status = %(paid)s
		  AND b.booking_status NOT IN ('Cancelled', 'Expired')
		  AND b.booking_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY b.branch, b.booking_date
		""",
		{"company": company, "paid": PAID, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	):
		entry = bucket(row.branch, row.day)
		entry["bookings_count"] += int(row.cnt or 0)
		entry["bookings_revenue"] += flt(row.total, 2)
		entry["platform_fees"] += flt(row.fees, 2)

	for row in frappe.db.sql(
		"""
		SELECT s.branch AS branch, s.session_date AS day,
		       SUM(i.total_amount - COALESCE(i.platform_fee, 0)) AS total,
		       SUM(COALESCE(i.platform_fee, 0)) AS fees
		FROM `tabCBT Booking Invoice` i
		JOIN `tabCBT Open Play Participant` p ON p.name = i.participant_ref
		JOIN `tabCBT Open Play Session` s ON s.name = p.parent
		WHERE i.company = %(company)s AND i.status = %(paid)s
		  AND s.session_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY s.branch, s.session_date
		""",
		{"company": company, "paid": PAID, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	):
		entry = bucket(row.branch, row.day)
		entry["open_play_revenue"] += flt(row.total, 2)
		entry["platform_fees"] += flt(row.fees, 2)

	data = []
	for entry in sorted(buckets.values(), key=lambda e: (e["day"], e["branch"])):
		entry["bookings_revenue"] = flt(entry["bookings_revenue"], 2)
		entry["open_play_revenue"] = flt(entry["open_play_revenue"], 2)
		entry["total_revenue"] = flt(
			entry["bookings_revenue"] + entry["open_play_revenue"], 2
		)
		entry["platform_fees"] = flt(entry["platform_fees"], 2)
		data.append(entry)
	return _columns(), data


def _period(filters) -> tuple:
	today = clock.now_dt().date()
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else today
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else today
	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))
	return from_date, to_date


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
		{"fieldname": "bookings_count", "label": _("Bookings"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "bookings_revenue",
			"label": _("Court Revenue"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 140,
		},
		{
			"fieldname": "open_play_revenue",
			"label": _("Open Play Revenue"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 160,
		},
		{
			"fieldname": "total_revenue",
			"label": _("Total"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 140,
		},
		{
			# Backlog B27: collected from customers FOR the platform, remitted on
			# the monthly statement — never part of the facility's own revenue.
			"fieldname": "platform_fees",
			"label": _("Booking Fees Collected"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 160,
		},
	]
