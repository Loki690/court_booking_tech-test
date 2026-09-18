# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Platform Revenue (section-11, PLAN §7 "Platform desk") — what each tenant
owes us this month, and the commission-evasion audit column.

Two things this report exists to answer:

1. **What are we owed?** Percentage tenants owe a cut of CONFIRMED revenue;
   subscription tenants owe a flat fee regardless; Per Booking tenants
   (Backlog B27, 2026-08-27) owe the BOOKING FEES their customers paid on top
   — collected by the tenant, remitted to us — so `confirmed_revenue` is what
   the facility itself earned (fees excluded) and `booking_fees` / `booking_count`
   are the pass-through. This report computes the number; the month close
   freezes it and issues the statement (B21).
   Backlog B49 (2026-09-09): `booking_count` counts FEE UNITS, and a continued
   session across continue-on courts is ONE unit — two court bookings, one
   fee, one count. A continuation carries `platform_fee = 0`, so it adds
   nothing here by construction; `total` stays right because a continuation's
   `total_amount` excludes the fee. A refunded paid head takes the session's
   fee out of these figures while the continuation stays live (ruled).

2. **Is anyone evading the cut?** A percentage-billed company could take a real
   fund transfer and simply never confirm it in-system: the booking quietly
   expires at its end time and the revenue never appears here. That pattern
   leaves a fingerprint — an EXPIRED booking that nevertheless has a payment
   proof attached — and the expired-with-proof columns surface it for audit
   (PLAN §7). It is a signal, not an accusation: a genuine unverified proof
   looks identical, which is exactly why a human reads the column.
   Retro-confirmed bookings become Completed (S5), so they leave this column
   and enter the revenue one — the two directions are both tested.

Revenue is attributed by SERVICE DATE (the booking's date / the open play
session's date), not by posting or verification date: the commission is owed
for court time sold in the period, and a transfer verified on the 2nd for a
game played on the 31st belongs to the month the court was used.

Scope: platform-only. Script reports bypass permission_query_conditions, so the
gate is explicit here rather than inherited.

CLOSED MONTHS (Backlog B21(b), 2026-08-27). A month a Platform Admin has
closed — a `CBT Platform Month Close` named by its period — is read back from
that record instead of recomputed, so a retro-confirmed or cancelled booking
can no longer move a figure the platform already billed. `compute_rows` is the
ONE computation: the live path and the close both call it, so what gets
frozen is exactly what the report would have shown. `execute` returns a third
value, the closed-month notice, None while the month is live. Issuing is the
close's job (B21(a), same day): "Issue statements" on a CBT Platform Month
Close turns each frozen Amount Due into a numbered, tenant-readable CBT
Platform Statement — this report stays the computation.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate

from court_booking_tech import clock
from court_booking_tech.billing import PAID
from court_booking_tech.tenancy import has_platform_scope


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not has_platform_scope():
		frappe.throw(
			_("CBT Platform Revenue is a platform-admin report."),
			frappe.PermissionError,
		)

	year, month = _year_month(filters)
	close = _closed_month(year, month)
	if close:
		data = [
			{field: row.get(field) for field in ROW_FIELDS} for row in close["rows"]
		]
		message = _(
			"Closed on {0} by {1} — these figures are frozen. Delete {2} to reopen the month."
		).format(
			frappe.format(close["closed_at"], {"fieldtype": "Datetime"}),
			close["closed_by"],
			close["name"],
		)
	else:
		data = compute_rows(year, month)
		message = None

	if data:
		data.append(_total_row(data))
	return _columns(), data, message


ROW_FIELDS = (
	"company",
	"company_name",
	"status",
	"billing_mode",
	"commission_percent",
	"subscription_fee",
	"booking_count",
	"booking_fees",
	"confirmed_revenue",
	"amount_due",
	"expired_with_proof_count",
	"expired_with_proof_value",
)


def _closed_month(year: int, month: int) -> dict | None:
	"""The close for this month, with its rows, or None while the month is live."""
	period = f"{year:04d}-{month:02d}"
	head = frappe.db.get_value(
		"CBT Platform Month Close",
		period,
		["name", "closed_at", "closed_by"],
		as_dict=True,
	)
	if not head:
		return None
	head["rows"] = frappe.get_all(
		"CBT Platform Month Close Row",
		filters={"parenttype": "CBT Platform Month Close", "parent": period},
		fields=list(ROW_FIELDS),
		order_by="idx asc",
	)
	return head


def compute_rows(year: int, month: int) -> list[dict]:
	"""Every tenant's figures for one calendar month, computed LIVE.

	One row per company (suspended ones included), no total row. This is the
	function a month close freezes, so any change to how a figure is derived
	must land here and nowhere else.
	"""
	anchor = getdate(f"{cint(year):04d}-{cint(month):02d}-01")
	from_date, to_date = get_first_day(anchor), get_last_day(anchor)
	companies = frappe.get_all(
		"CBT Company",
		fields=[
			"name",
			"company_name",
			"status",
			"billing_mode",
			"commission_percent",
			"subscription_fee",
		],
		order_by="company_name",
	)

	revenue = _confirmed_revenue_by_company(from_date, to_date)
	evasion = _expired_with_proof_by_company(from_date, to_date)

	data = []
	for company in companies:
		# Suspended tenants stay in the report: their data is retained (PLAN
		# §8l) and a company suspended mid-month still owes for what it sold.
		money = revenue.get(company.name) or {}
		gross = flt(money.get("total", 0), 2)
		fees = flt(money.get("fees", 0), 2)
		units = cint(money.get("units", 0))
		if company.billing_mode == "Percentage":
			amount_due = flt(gross * flt(company.commission_percent) / 100.0, 2)
		elif company.billing_mode == "Subscription":
			amount_due = flt(company.subscription_fee, 2)
		elif company.billing_mode == "Per Booking":
			# Backlog B27: what the tenant's customers paid us through the tenant
			# — the sum of the fee lines on PAID documents, service-dated.
			amount_due = fees
		else:
			amount_due = 0.0
		stats = evasion.get(company.name, {})
		data.append(
			{
				"company": company.name,
				"company_name": company.company_name,
				"status": company.status,
				"billing_mode": company.billing_mode,
				"commission_percent": flt(company.commission_percent),
				"subscription_fee": flt(company.subscription_fee),
				"booking_count": units,
				"booking_fees": fees,
				"confirmed_revenue": gross,
				"amount_due": amount_due,
				"expired_with_proof_count": cint(stats.get("count")),
				"expired_with_proof_value": flt(stats.get("value", 0), 2),
			}
		)
	return data


def _total_row(rows: list) -> dict:
	"""Hand-built total, because add_total_row sums EVERY numeric column.

	A summed commission rate is meaningless — the stock total row rendered
	"3.333%" across three tenants, which reads like a real platform-wide rate
	and is not one. Only the additive figures are totalled here; the per-tenant
	config columns are deliberately left blank.
	"""
	return {
		"company": None,
		"company_name": _("Total (all tenants)"),
		"status": None,
		"billing_mode": None,
		"commission_percent": None,
		"subscription_fee": None,
		"booking_count": sum(cint(r["booking_count"]) for r in rows),
		"booking_fees": flt(sum(flt(r["booking_fees"]) for r in rows), 2),
		"confirmed_revenue": flt(sum(r["confirmed_revenue"] for r in rows), 2),
		"amount_due": flt(sum(r["amount_due"] for r in rows), 2),
		"expired_with_proof_count": sum(r["expired_with_proof_count"] for r in rows),
		"expired_with_proof_value": flt(
			sum(r["expired_with_proof_value"] for r in rows), 2
		),
		"is_total_row": 1,
	}


def _year_month(filters) -> tuple:
	"""The month asked for. Defaults to the current month on the server clock."""
	today = clock.now_dt().date()
	year = cint(filters.get("year")) or today.year
	month = cint(filters.get("month")) or today.month
	return year, month


def _confirmed_revenue_by_company(from_date, to_date) -> dict:
	"""Paid & Verified billing documents, dated by SERVICE date.

	Court bookings carry their own date; open play participants reach theirs
	through the participant row's parent session (S10: participant_ref holds a
	child-row name, and `booking` is empty on those documents).
	"""
	params = {"paid": PAID, "from_date": from_date, "to_date": to_date}
	totals: dict = {}

	def add(company, total, fees, units):
		entry = totals.setdefault(company, {"total": 0, "fees": 0, "units": 0})
		entry["total"] += flt(total)
		entry["fees"] += flt(fees)
		entry["units"] += cint(units)

	for row in frappe.db.sql(
		"""
		SELECT i.company AS company,
		       SUM(b.total_amount - COALESCE(b.platform_fee, 0)) AS total,
		       SUM(COALESCE(b.platform_fee, 0)) AS fees,
		       SUM(CASE WHEN COALESCE(b.platform_fee, 0) > 0 THEN 1 ELSE 0 END) AS units
		FROM `tabCBT Booking Invoice` i
		JOIN `tabCBT Court Booking` b ON b.billing_doc = i.name
		WHERE i.status = %(paid)s
		  AND b.booking_status NOT IN ('Cancelled', 'Expired')
		  AND b.booking_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY i.company
		""",
		params,
		as_dict=True,
	):
		add(row.company, row.total, row.fees, row.units)

	for row in frappe.db.sql(
		"""
		SELECT i.company AS company,
		       SUM(i.total_amount - COALESCE(i.platform_fee, 0)) AS total,
		       SUM(COALESCE(i.platform_fee, 0)) AS fees,
		       SUM(CASE WHEN COALESCE(i.platform_fee, 0) > 0 THEN 1 ELSE 0 END) AS units
		FROM `tabCBT Booking Invoice` i
		JOIN `tabCBT Open Play Participant` p ON p.name = i.participant_ref
		JOIN `tabCBT Open Play Session` s ON s.name = p.parent
		WHERE i.status = %(paid)s
		  AND s.session_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY i.company
		""",
		params,
		as_dict=True,
	):
		add(row.company, row.total, row.fees, row.units)

	# Backlog B27: `total` is the tenant's own revenue (the platform fee inside
	# a customer's total is ours, not theirs); `fees` / `units` are the
	# pass-through and the number of paid UNITS that carried one.
	return totals


def _expired_with_proof_by_company(from_date, to_date) -> dict:
	"""The evasion signal: Expired bookings that HAVE a payment proof.

	Any proof counts, whatever its status — the point is that money was
	claimed to have moved and the booking still died. Retro-confirmed bookings
	are Completed, not Expired, so they are excluded by construction.
	"""
	rows = frappe.db.sql(
		"""
		SELECT b.company AS company, COUNT(*) AS cnt, SUM(b.total_amount) AS value
		FROM `tabCBT Court Booking` b
		WHERE b.booking_status = 'Expired'
		  AND b.booking_date BETWEEN %(from_date)s AND %(to_date)s
		  AND EXISTS (
		        SELECT 1 FROM `tabCBT Payment Proof` pp WHERE pp.booking = b.name
		  )
		GROUP BY b.company
		""",
		{"from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	return {row.company: {"count": row.cnt, "value": row.value} for row in rows}


def _columns():
	return [
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "CBT Company",
			"width": 160,
		},
		{"fieldname": "company_name", "label": _("Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "billing_mode",
			"label": _("Billing Mode"),
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"fieldname": "commission_percent",
			"label": _("Commission %"),
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"fieldname": "subscription_fee",
			"label": _("Subscription"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 110,
		},
		{
			"fieldname": "booking_count",
			"label": _("Fee Units"),
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"fieldname": "booking_fees",
			"label": _("Booking Fees"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 120,
		},
		{
			"fieldname": "confirmed_revenue",
			"label": _("Confirmed Revenue"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 150,
		},
		{
			"fieldname": "amount_due",
			"label": _("Amount Due"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 130,
		},
		{
			"fieldname": "expired_with_proof_count",
			"label": _("Expired w/ Proof"),
			"fieldtype": "Int",
			"width": 130,
		},
		{
			"fieldname": "expired_with_proof_value",
			"label": _("Expired w/ Proof ₱"),
			"fieldtype": "Currency",
			"options": "PHP",
			"width": 150,
		},
	]
