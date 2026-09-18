# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/billing-statement?booking=<name> — the company's billing document, printable
by the people entitled to it (PLAN §6).

Customers hold no read permission on CBT Booking Invoice (S6 perms), so the
desk printview 403s them out of their own statement. This page runs OUR guard
(owner or that company's staff) and then renders the SHIPPED print format, so
the customer's copy is byte-identical to the one the front desk prints.
"""

import frappe

from court_booking_tech.api.portal import (
	_booking_viewer,
	portal_base_context,
	portal_login_redirect,
	render_billing_statement,
)

no_cache = 1


def get_context(context):
	booking = frappe.form_dict.get("booking")
	if frappe.session.user == "Guest":
		portal_login_redirect(f"/billing-statement?booking={booking or ''}")
	portal_base_context(context)

	if not booking:
		frappe.throw(frappe._("No booking specified."), frappe.DoesNotExistError)

	_booking_viewer(booking)  # owner or company staff; PermissionError otherwise
	rendered = render_billing_statement(booking)
	context.statement_body = rendered["body"]
	context.statement_style = rendered["style"]
	context.invoice_name = rendered["invoice"]
	context.booking_name = booking
	context.page_heading = frappe._("Billing statement")
	return context
