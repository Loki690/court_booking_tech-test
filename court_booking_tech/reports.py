# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Shared report scoping (section-11).

Script reports run raw SQL, so they bypass permission_query_conditions entirely
— the tenancy spine does NOT protect them for free. This is the one place that
decides which company a report may read, so a new report cannot accidentally
ship with a weaker rule than the last one (leak vector 3: API drift, applied to
reports).

Rule:
- Tenant users are FORCED onto their own company, whatever the filter says. A
  report filter is user input; honouring a hand-edited company would turn every
  report into a cross-tenant read.
- Platform scope must NAME a company (fail-closed, exactly like
  api/board.get_pending_payments and the open play board): "no filter" must
  never silently mean "every tenant".
"""

import frappe
from frappe import _

from court_booking_tech.tenancy import get_session_company


def resolve_report_company(requested: str | None) -> str:
	"""The company this report run may read (see module docstring)."""
	session_company = get_session_company()
	if session_company is not None:
		# Tenant user: own company, no negotiation. Silently overriding the
		# filter (rather than throwing) keeps the report usable when the desk
		# pre-fills a stale value, and there is nothing to leak either way.
		return session_company
	if not requested:
		frappe.throw(
			_("Select a company — platform scope must choose one tenant."),
			frappe.PermissionError,
		)
	if not frappe.db.exists("CBT Company", requested):
		frappe.throw(
			_("Company {0} not found.").format(requested), frappe.DoesNotExistError
		)
	return requested
