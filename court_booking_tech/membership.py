# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Membership reads (section-11, PLAN §4) — THE single seam every discount path
goes through.

Why one module: the discount is applied at four independent creation seams
(portal reserve, portal quote, desk quick-book, open-play add_players). If each
derived "is this customer a member" itself, they would drift, and a quote that
disagrees with the booking it produces is a customer-facing money bug. All four
call get_member_discount()/has_active_membership() here.

Company scoping is BY CONSTRUCTION: every query filters on company, so a VIP at
one tenant is an ordinary walk-in at the next (the marketplace premise —
PLAN §1: one account, book anywhere).

"Active" is evaluated live against the clock seam — there is no stored flag to
go stale (see cbt_membership.py).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from court_booking_tech import clock
from court_booking_tech.tenancy import require_company_access


def get_active_membership(company: str, customer: str, on_date=None) -> dict | None:
	"""The customer's membership at THIS company on `on_date` (default: today).

	Returns the row dict or None. Deterministic pick when data is somehow
	overlapping despite the controller's guard (e.g. rows predating it): the
	most recently started wins, so the answer never flips between two reads.
	"""
	if not company or not customer:
		return None
	on_date = getdate(on_date) if on_date else clock.now_dt().date()
	rows = frappe.db.sql(
		"""
		SELECT name, company, customer, tier, discount_percent, start_date, end_date
		FROM `tabCBT Membership`
		WHERE company = %(company)s
		  AND customer = %(customer)s
		  AND start_date <= %(on_date)s
		  AND (end_date IS NULL OR end_date >= %(on_date)s)
		ORDER BY start_date DESC, creation DESC
		LIMIT 1
		""",
		{"company": company, "customer": customer, "on_date": on_date},
		as_dict=True,
	)
	return rows[0] if rows else None


def get_member_discount(company: str, customer: str, on_date=None) -> float:
	"""Discount % to auto-fill for this customer at this company (0 if none)."""
	membership = get_active_membership(company, customer, on_date)
	return flt(membership.discount_percent) if membership else 0.0


def has_active_membership(company: str, customer: str, on_date=None) -> bool:
	return get_active_membership(company, customer, on_date) is not None


def get_customer_memberships(customer: str) -> list[dict]:
	"""Every membership this customer holds, across ALL companies, each tagged
	active/future/expired live (powers the /my-profile list — PLAN §7).

	Portal-side read: customers hold no DocPerm on CBT Membership (leak vector
	4), so this is called from the page controller with the session user, never
	with a client-supplied id.
	"""
	if not customer:
		return []
	today = clock.now_dt().date()
	rows = frappe.get_all(
		"CBT Membership",
		filters={"customer": customer},
		fields=["name", "company", "tier", "discount_percent", "start_date", "end_date"],
		order_by="start_date desc",
		ignore_permissions=True,
	)
	if not rows:
		return []
	company_names = {
		row.name: row.company_name
		for row in frappe.get_all(
			"CBT Company",
			filters={"name": ("in", list({r.company for r in rows}))},
			fields=["name", "company_name"],
			ignore_permissions=True,
		)
	}
	out = []
	for row in rows:
		if getdate(row.start_date) > today:
			state = "Future"
		elif row.end_date and getdate(row.end_date) < today:
			state = "Expired"
		else:
			state = "Active"
		out.append(
			{
				"name": row.name,
				"company_name": company_names.get(row.company, row.company),
				"tier": row.tier,
				"discount_percent": flt(row.discount_percent),
				"start_date": str(row.start_date),
				"end_date": str(row.end_date) if row.end_date else None,
				"state": state,
			}
		)
	return out


@frappe.whitelist(methods=["GET"])
def get_member_discount_for(company: str, customer: str) -> dict:
	"""Desk lookup for the board's quick-book dialog (STAFF path).

	Guarded by the tenancy choke point — allow_suspended=True because reading a
	membership is not booking-creating, and a suspended company's staff must
	still be able to see their own book. Returns the tier too so the dialog can
	SHOW why a discount appeared: a silent number in a money field is exactly
	how a failed lookup becomes an invisible mispriced booking.
	"""
	require_company_access(company, allow_suspended=True)
	membership = get_active_membership(company, customer)
	if not membership:
		return {"has_membership": False, "discount_percent": 0.0, "tier": None}
	return {
		"has_membership": True,
		"discount_percent": flt(membership.discount_percent),
		"tier": membership.tier,
		"membership": membership.name,
	}


def describe_membership(company: str, customer: str) -> str | None:
	"""Human sentence for staff UI ("VIP member — 20% off"), or None."""
	membership = get_active_membership(company, customer)
	if not membership:
		return None
	return _("{0} member — {1}% off").format(
		membership.tier, f"{flt(membership.discount_percent):g}"
	)
