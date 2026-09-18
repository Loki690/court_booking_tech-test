# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Payment-channel pickers (Backlog B29) — the READ endpoints the desk dialogs
fill their Selects from. Writes never happen here: a channel is chosen by
passing its name to the booking / proof / open play / statement endpoints,
which all go through payment_channels.resolve_channel.

Two audiences, two gates:
- a TENANT's list is staff-only (require_company_access — a customer session
  fails closed; the customer's own list arrives inside the portal payloads,
  public fields only);
- the PLATFORM's list is platform-only (the statement's Mark paid dialog).
"""

import frappe

from court_booking_tech.payment_channels import (
	PLATFORM,
	channel_kind,
	list_channels,
)
from court_booking_tech.tenancy import has_platform_scope, require_company_access


@frappe.whitelist(methods=["GET"])
def list_company_channels(company: str, payment_method: str | None = None) -> list[dict]:
	"""The company's ENABLED channels, optionally only those a payment method
	can use (Cash → Cash channels, Fund Transfer → Transfer channels, Free →
	none). allow_suspended: reading the picker is not booking-creating."""
	require_company_access(company, allow_suspended=True)
	if payment_method:
		kind = channel_kind(payment_method)
		if not kind:
			return []
		return list_channels(company, kind=kind)
	return list_channels(company)


@frappe.whitelist(methods=["GET"])
def list_platform_channels() -> list[dict]:
	"""The platform's own ENABLED channels (statement Mark paid)."""
	if not has_platform_scope():
		frappe.throw(
			frappe._("The platform's payment channels are a platform-admin view."),
			frappe.PermissionError,
		)
	return list_channels(scope=PLATFORM)
