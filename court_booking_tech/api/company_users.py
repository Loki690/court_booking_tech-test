# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Guarded staff-management API (PLAN §3).

Company Admins manage their OWN company's staff only; Platform Admins manage
any company. Role escalation to CBT Platform Admin is impossible through this
API. All writes happen with ignore_permissions=True ONLY AFTER the guard.
"""

import frappe
from frappe import _
from frappe.utils import validate_email_address

from court_booking_tech.tenancy import get_session_company

ALLOWED_COMPANY_ROLES = {"Company Admin", "Company Staff"}


@frappe.whitelist(methods=["GET"])
def get_my_company() -> str | None:
	"""The session user's bound company; None for platform scope.

	Powers the tenant default of the desk reports' Company filter (section-12
	UAT finding: a reqd Link whose only pickable value is forced server-side
	anyway is a pointless mandatory click). Authority stays server-side —
	reports.resolve_report_company forces tenant scope regardless; this only
	saves the click. Unbound non-platform users inherit get_session_company's
	fail-closed PermissionError.
	"""
	return get_session_company()


def _resolve_target_company(company: str | None) -> str:
	"""Guard + target resolution. Platform scope must name a company;
	a Company Admin is forced onto their own company; everyone else is
	rejected."""
	session_company = get_session_company()  # raises for unbound non-platform users
	if session_company is None:
		if not company:
			frappe.throw(_("Platform Admin must specify the company."))
		return company

	if "CBT Company Admin" not in frappe.get_roles():
		frappe.throw(
			_("Only a Company Admin or Platform Admin may manage staff."),
			frappe.PermissionError,
		)
	if company and company != session_company:
		frappe.throw(
			_("You may not act on company {0}.").format(company),
			frappe.PermissionError,
		)
	return session_company


@frappe.whitelist(methods=["POST"])
def create_company_user(email: str, full_name: str, company_role: str, company: str | None = None):
	company_role = (company_role or "").strip()
	if company_role not in ALLOWED_COMPANY_ROLES:
		# NEVER CBT Platform Admin — no escalation through this API.
		frappe.throw(_("Role must be Company Admin or Company Staff."))

	target_company = _resolve_target_company(company)

	email = (email or "").strip().lower()
	validate_email_address(email, throw=True)

	if frappe.db.exists("CBT Company User", {"user": email}):
		frappe.throw(_("{0} is already bound to a company.").format(email))

	if not frappe.db.exists("User", email):
		first_name, _sep, last_name = (full_name or "").strip().partition(" ")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name or email,
				"last_name": last_name or None,
				"enabled": 1,
				"user_type": "System User",
				# Section-8 carry-in: the welcome/invite mail queues (Email
				# Queue) even with no SMTP sender; real delivery is prod-only.
				"send_welcome_email": 1,
			}
		)
		# Staff roles come ONLY from the binding sync below — without this
		# flag the role-less insert is flipped to Website User by frappe and
		# the default-role hook would tack on CBT Customer (customer.py).
		user.flags.cbt_skip_customer_role = True
		user.insert(ignore_permissions=True)

	binding = frappe.get_doc(
		{
			"doctype": "CBT Company User",
			"user": email,
			"company": target_company,
			"company_role": company_role,
		}
	).insert(ignore_permissions=True)
	return binding.name


@frappe.whitelist(methods=["POST"])
def disable_company_user(name: str):
	if not frappe.db.exists("CBT Company User", name):
		# Fail-closed BEFORE any guard detail can leak.
		frappe.throw(
			_("Company user binding {0} does not exist.").format(name),
			frappe.PermissionError,
		)
	binding = frappe.get_doc("CBT Company User", name)
	_resolve_target_company(binding.company)

	if binding.user == frappe.session.user:
		frappe.throw(_("You cannot disable your own account."))

	user = frappe.get_doc("User", binding.user)
	user.enabled = 0
	user.save(ignore_permissions=True)
	return binding.name
