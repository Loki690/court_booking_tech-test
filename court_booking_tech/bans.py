# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Ban enforcement (section-11) — the read seam for CBT Customer Ban.

Deliberately a module of its own rather than a branch inside
tenancy.require_company_bookable: that helper answers "is this FACILITY open
for business?" (S9 as-built 1), and folding a per-CUSTOMER question into it
would make one function mean two things — which is how the suspension gate
and the ban gate would eventually diverge in behaviour and wording.

They compose at exactly one call site (the booking controller's
customer_created branch), in this order:

    require_company_bookable(company)          # is the facility open?
    ensure_customer_not_banned(company, user)  # is this customer welcome?
"""

import frappe
from frappe import _

ACTIVE = "Active"
LIFTED_STATUS = "Lifted"


def get_active_ban(company: str, customer: str) -> str | None:
	"""Name of the customer's active ban at this company, or None."""
	if not company or not customer:
		return None
	return frappe.db.get_value(
		"CBT Customer Ban",
		{"company": company, "customer": customer, "status": ACTIVE},
		"name",
	)


def is_banned(company: str, customer: str) -> bool:
	return get_active_ban(company, customer) is not None


def ensure_customer_not_banned(company: str, customer: str):
	"""Throw if this customer may not book ONLINE at this company.

	The message deliberately differs from the suspension wording ("This
	facility is not accepting bookings") — conflating the two would tell a
	banned customer the venue is closed, which is false and sends them to the
	branch to complain about an outage that isn't happening. It also names the
	branch as the way back: a ban is a business relationship, not a bug, and a
	dead end with no recourse is worse UX than a firm no.

	It does NOT reveal the reason: that text is written by staff for staff.
	"""
	if is_banned(company, customer):
		frappe.throw(
			_(
				"Your account cannot book at this facility online. "
				"Please contact the branch directly."
			),
			frappe.ValidationError,
		)
