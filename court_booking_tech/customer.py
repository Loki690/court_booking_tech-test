# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Portal customer identity (PLAN §11.4, section-8).

CBT Customer Profile is a cross-company customer asset — no `company` field,
no tenancy hooks, and NO customer DocPerms (leak vector 4): the portal reads
and writes it exclusively through api/profile.py.
"""

import frappe

CUSTOMER_ROLE = "CBT Customer"


def ensure_customer_profile(user: str | None = None):
	"""Get-or-create the profile for `user` (default: session user).
	Idempotent; safe under the concurrent-first-use race."""
	user = user or frappe.session.user
	name = frappe.db.exists("CBT Customer Profile", {"user": user})
	if name:
		return frappe.get_doc("CBT Customer Profile", name)
	try:
		doc = frappe.get_doc({"doctype": "CBT Customer Profile", "user": user})
		doc.insert(ignore_permissions=True)
		return doc
	except frappe.DuplicateEntryError:
		frappe.clear_document_cache("CBT Customer Profile", user)
		return frappe.get_doc("CBT Customer Profile", {"user": user})


def website_user_home_page(user: str):
	"""home_page resolver (hooks.get_website_user_home_page) — the marketplace
	is the front door for SHOPPERS, but desk users must still land on /app.

	Returns "find-court" for guests and portal customers; None for System
	Users, letting frappe's own get_home_page fall through to its "me"->"desk"
	remap (so staff/platform-admin logins land on the desk, not the portal).
	A global `home_page` hook cannot make this distinction — it would redirect
	everyone, which broke every desk E2E's admin-login fixture."""
	if user == "Guest":
		return "find-court"
	if frappe.db.get_value("User", user, "user_type") == "System User":
		return None
	if CUSTOMER_ROLE in frappe.get_roles(user):
		return "find-court"
	return None


def on_user_after_insert(doc, method=None):
	"""Belt-and-suspenders default role (PLAN §7): Portal Settings
	default_role covers the stock signup path; this hook covers every OTHER
	way a Website User can appear (social login first-visit, admin-created,
	API). System Users are never touched.

	Gotcha this flag exists for: frappe flips a ROLE-LESS "System User" to
	Website User in validate (set_system_user — desk access is derived from
	roles), and staff/platform users are deliberately created bare so their
	roles come only from the CBT Company User sync — without the flag every
	staff user would collect a stray CBT Customer role at insert."""
	if doc.flags.get("cbt_skip_customer_role"):
		return
	if doc.user_type != "Website User":
		return
	if CUSTOMER_ROLE in {r.role for r in doc.roles}:
		return
	doc.add_roles(CUSTOMER_ROLE)
