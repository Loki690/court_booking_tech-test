# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Ban management API (section-11) — the desk surface behind the board's
"Ban customer" action.

STAFF endpoints: every one calls the tenancy choke point, so a company can only
ban customers for ITSELF. allow_suspended=True throughout — banning is
housekeeping on the existing book, not booking-creating, and a suspended tenant
must still be able to manage its own block-list (same reasoning as
cancel_booking / create_block, S4 as-built 5).

Raise vs lift asymmetry (see cbt_customer_ban.py): staff may raise, only a
Company Admin or the platform may lift.
"""

import frappe
from frappe import _

from court_booking_tech.bans import ACTIVE, LIFTED_STATUS, get_active_ban
from court_booking_tech.tenancy import (
	has_platform_scope,
	require_company_access,
	require_company_admin,
)


def _require_ban_lifter(company: str):
	"""Lifting is an ADMIN decision.

	`status` is permlevel 1, so frappe would SILENTLY revert a staff write —
	an API that quietly does nothing is worse than one that refuses, so the
	check is explicit and throws.
	"""
	require_company_admin(company, message=_("Only a Company Admin can lift a ban."))


@frappe.whitelist(methods=["POST"])
def ban_customer(company: str, customer: str, reason: str) -> dict:
	"""Raise a ban (front-desk action). Idempotent-ish: an existing active ban
	is reported back rather than throwing a duplicate-key error at the user."""
	require_company_access(company, allow_suspended=True)
	if not customer:
		# Section-13: a walk-in booking has no account to ban. The board hides
		# the button, but an empty customer would otherwise reach the exists()
		# check below and fail with "Customer None not found." — accurate, and
		# useless to the staff member reading it.
		frappe.throw(
			_(
				"This booking has no customer account — a ban applies to an "
				"account, so there is nothing to ban here."
			)
		)
	if not frappe.db.exists("User", customer):
		frappe.throw(_("Customer {0} not found.").format(customer))

	existing = get_active_ban(company, customer)
	if existing:
		return {"ban": existing, "status": ACTIVE, "already_banned": True}

	doc = frappe.get_doc(
		{
			"doctype": "CBT Customer Ban",
			"company": company,
			"customer": customer,
			"reason": reason,
			"status": ACTIVE,
		}
	)
	doc.insert(ignore_permissions=True)
	return {
		"ban": doc.name,
		"status": doc.status,
		"customer_name": doc.customer_name,
		"already_banned": False,
	}


@frappe.whitelist(methods=["POST"])
def lift_ban(name: str) -> dict:
	"""Clear a ban (Company Admin / platform only)."""
	if not frappe.db.exists("CBT Customer Ban", name):
		frappe.throw(_("Ban {0} not found.").format(name), frappe.DoesNotExistError)
	doc = frappe.get_doc("CBT Customer Ban", name)
	_require_ban_lifter(doc.company)

	if doc.status != ACTIVE:
		frappe.throw(_("This ban has already been lifted."))
	doc.status = LIFTED_STATUS
	# ignore_permissions AFTER the explicit gate above: the permlevel-1 strip
	# would otherwise silently drop this write for anyone but a level-1 writer,
	# turning a legitimate admin lift into a no-op.
	doc.save(ignore_permissions=True)
	return {"ban": doc.name, "status": doc.status}


@frappe.whitelist(methods=["GET"])
def get_customer_ban_status(company: str, customer: str) -> dict:
	"""Does this company currently ban this customer? (board dialog hint)"""
	require_company_access(company, allow_suspended=True)
	ban = get_active_ban(company, customer)
	if not ban:
		return {"banned": False}
	row = frappe.db.get_value(
		"CBT Customer Ban", ban, ["name", "reason", "banned_at"], as_dict=True
	)
	return {"banned": True, "ban": row.name, "reason": row.reason, "banned_at": row.banned_at}
