# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Tenancy spine — THE single choke point for company scoping (PLAN §3).

Every whitelisted API that touches tenant data must call
require_company_access(company). Desk list/read scoping is wired through the
permission_query_conditions / has_permission hooks below (hooks.py).

Contract every consumer (sections 4+) depends on:
- get_session_company() returns None ONLY for platform scope (Administrator,
  System Manager, CBT Platform Admin), and None means "ALL companies" —
  NEVER "no company". Non-platform users without a CBT Company User binding
  raise frappe.PermissionError (fail-closed: a customer must never fall
  through to platform scope).
"""

import frappe
from frappe import _

PLATFORM_ROLES = {"System Manager", "CBT Platform Admin"}


def has_platform_scope(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(PLATFORM_ROLES & set(frappe.get_roles(user)))


def get_session_company(user: str | None = None) -> str | None:
	"""Company the user is bound to, or None for platform scope.

	None means "ALL companies" — never "no company". Platform roles are
	checked BEFORE the binding lookup (a platform user can never be bound —
	CBT Company User's validate rejects that). Non-platform users without a
	binding raise PermissionError (fail-closed).
	"""
	user = user or frappe.session.user
	if has_platform_scope(user):
		return None
	company = frappe.db.get_value("CBT Company User", {"user": user}, "company")
	if not company:
		raise frappe.PermissionError(
			_("User {0} has no CBT company binding.").format(user)
		)
	return company


def _require_company(company: str):
	if not company:
		# Leak vector 2 at the API level: an empty company must never
		# silently widen scope.
		frappe.throw(_("Company is required."), frappe.PermissionError)


def _reject_if_suspended(company: str):
	"""THE suspension gate (PLAN §5) — one implementation, two callers."""
	if frappe.db.get_value("CBT Company", company, "status") == "Suspended":
		frappe.throw(
			_("This facility is not accepting bookings."),
			frappe.ValidationError,
		)


def require_company_access(company: str, *, allow_suspended: bool = False):
	"""Raise unless the session user may act on `company` AS A TENANT USER.

	The suspension gate (PLAN §5) lives HERE, the one choke point: with
	allow_suspended=False (the default) a Suspended company is rejected for
	EVERYONE — including platform scope — so every booking-creating path
	(insert, confirm, extend; later open play) refuses consistently. Reads and
	de-escalating actions (cancel, staff availability) pass
	allow_suspended=True.

	This is the STAFF/PLATFORM gate: a portal customer has no CBT Company User
	binding and therefore fails closed here, by design. The customer-facing
	counterpart is require_company_bookable (section-9).
	"""
	_require_company(company)
	session_company = get_session_company()
	if session_company is not None and session_company != company:
		frappe.throw(
			_("You may not act on company {0}.").format(company),
			frappe.PermissionError,
		)
	if not allow_suspended:
		_reject_if_suspended(company)


def is_company_admin(user: str | None = None) -> bool:
	"""Platform scope, or a CBT Company User bound as "Company Admin"."""
	user = user or frappe.session.user
	if has_platform_scope(user):
		return True
	return (
		frappe.db.get_value("CBT Company User", {"user": user}, "company_role")
		== "Company Admin"
	)


def require_company_admin(company: str, *, message: str):
	"""The ADMIN gate on top of require_company_access (section-26).

	Money out — a refund, lifting a ban — is a Company Admin's decision, not
	the front desk's. Platform scope passes; a Company Staff seat is refused
	with `message`, the sentence the UI shows in the button's place. Always
	allow_suspended: every action behind this gate de-escalates.
	"""
	require_company_access(company, allow_suspended=True)
	if not is_company_admin():
		frappe.throw(message, frappe.PermissionError)


def require_company_bookable(company: str):
	"""The CUSTOMER-side gate (section-9, PLAN §7): the marketplace lets any
	customer book at ANY company, so there is no tenant-scope check to make —
	but the company must exist, be named, and be accepting bookings.

	Deliberately NOT a relaxation of require_company_access: the two are
	different questions ("may this staff user act on that tenant?" vs "is this
	facility open for business?"). Both funnel through the same
	_reject_if_suspended, so suspension can never be enforced in only one of
	them. Reached ONLY via flags.customer_created, which is server-set (the
	portal API) — customers hold no create DocPerm on CBT Court Booking.
	"""
	_require_company(company)
	if frappe.db.get_value("CBT Company", company, "status") != "Active":
		# Covers Suspended AND any future non-Active state; same message as the
		# staff path so the two gates never diverge in customer-visible wording.
		frappe.throw(
			_("This facility is not accepting bookings."),
			frappe.ValidationError,
		)


def branch_management_allowed(company: str, user: str | None = None) -> bool:
	"""Platform scope always manages branches; tenant users only when their
	company's allow_self_branch_management flag is on (PLAN §3 — DocPerms
	cannot express a per-company create/write gate)."""
	user = user or frappe.session.user
	if has_platform_scope(user):
		return True
	try:
		session_company = get_session_company(user)
	except frappe.PermissionError:
		return False
	if session_company != company:
		return False
	return bool(
		frappe.db.get_value("CBT Company", company, "allow_self_branch_management")
	)


def media_editing_allowed(company: str, user: str | None = None) -> bool:
	"""Platform always; a Company Admin while allow_company_gallery_management is
	on. Deliberately NOT branch_management_allowed (section-29 design 8)."""
	user = user or frappe.session.user
	if has_platform_scope(user):
		return True
	try:
		session_company = get_session_company(user)
	except frappe.PermissionError:
		return False
	if session_company != company:
		return False
	if "CBT Company Admin" not in set(frappe.get_roles(user)):
		return False
	return bool(
		frappe.db.get_value("CBT Company", company, "allow_company_gallery_management")
	)


def reconcile_photos_on_parent_save(doc):
	"""A parent save NEVER adds, reorders, removes or re-mirrors anything. It only
	restores the stored rows so a stale form copy cannot wipe them (section-29).

	⛔ Do not make this function write. A version that absorbed the banner into the
	gallery, reordered it, and re-set a cleared banner shipped on 2026-09-11 and
	mutated customer-facing media on every unrelated company or branch save. The
	user never asked for any of it.
	"""
	if doc.is_new():
		doc.set("photos", [])
		return
	stored = frappe.get_all(
		"CBT Media Item",
		filters={"parent": doc.name, "parenttype": doc.doctype, "parentfield": "photos"},
		fields=["image", "caption", "idx"],
		order_by="idx asc",
		parent_doctype=doc.doctype,
	)
	doc.set("photos", [])
	for row in stored:
		doc.append("photos", {"image": row.image, "caption": row.caption})


def ensure_media_editing_allowed(company: str, user: str | None = None):
	if not media_editing_allowed(company, user):
		frappe.throw(
			_("You do not have permission to change this facility's photos."),
			frappe.PermissionError,
		)


def ensure_branch_management_allowed(company: str, user: str | None = None):
	if not branch_management_allowed(company, user):
		frappe.throw(
			_(
				"Branches for company {0} are managed by the platform "
				"(self branch management is off)."
			).format(company),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Permission hooks (wired in hooks.py) — defense-in-depth layer 2 on top of
# User Permissions. Fail-closed: an unbound non-platform user gets a
# deny-all filter / a read veto, never an open query.
# ---------------------------------------------------------------------------


def _scoped_condition(user: str, column: str) -> str:
	try:
		company = get_session_company(user)
	except frappe.PermissionError:
		return "1=0"
	if company is None:
		return ""
	return f"{column} = {frappe.db.escape(company)}"


def company_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Company`.name")


def company_user_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Company User`.company")


def branch_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Branch`.company")


def court_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Court`.company")


def booking_query(user: str | None = None) -> str:
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Court Booking`.company"
	)


def slot_block_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Slot Block`.company")


def payment_proof_query(user: str | None = None) -> str:
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Payment Proof`.company"
	)


def booking_invoice_query(user: str | None = None) -> str:
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Booking Invoice`.company"
	)


def open_play_session_query(user: str | None = None) -> str:
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Open Play Session`.company"
	)


def membership_query(user: str | None = None) -> str:
	return _scoped_condition(user or frappe.session.user, "`tabCBT Membership`.company")


def customer_ban_query(user: str | None = None) -> str:
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Customer Ban`.company"
	)


def platform_statement_query(user: str | None = None) -> str:
	# Backlog B21(a): the one tenant-readable platform-billing document. A
	# Company Admin lists their own statements and nobody else's.
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Platform Statement`.company"
	)


def customer_credit_query(user: str | None = None) -> str:
	# Backlog B39: store credit is spendable only where it was issued.
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Customer Credit`.company"
	)


def user_query(user: str | None = None) -> str:
	"""Backlog B38: a company seat lists ITSELF and its own company's seats.

	⚠ The outer parentheses are load-bearing. frappe joins hook results with a
	bare `" and ".join(...)` and wraps nothing (frappe/model/db_query.py:1163,
	:1180), so an unwrapped `A OR B` would WIDEN every filtered query.
	"""
	user = user or frappe.session.user
	try:
		company = get_session_company(user)
	except frappe.PermissionError:
		# A customer or an unbound seat sees exactly itself — not "1=0", which
		# would hide a user from their own profile paths.
		return f"`tabUser`.name = {frappe.db.escape(user)}"
	if company is None:
		return ""
	return (
		"(`tabUser`.name = {me} OR `tabUser`.name IN ("
		"SELECT cu.user FROM `tabCBT Company User` cu WHERE cu.company = {company}"
		"))"
	).format(me=frappe.db.escape(user), company=frappe.db.escape(company))


def payment_channel_query(user: str | None = None) -> str:
	# Backlog B29: platform-written, tenant-read. A Platform-scope channel has
	# NO company, so the same `company = X` condition hides the platform's own
	# channels from every tenant seat — a tenant sees exactly its own list.
	return _scoped_condition(
		user or frappe.session.user, "`tabCBT Payment Channel`.company"
	)


def _scoped_has_permission(doc_company: str, user: str) -> bool:
	try:
		company = get_session_company(user)
	except frappe.PermissionError:
		return False
	return company is None or company == doc_company


def company_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.name, user or frappe.session.user)


def company_user_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


WRITE_PTYPES = {"create", "write", "delete", "submit", "cancel", "amend"}


def branch_has_permission(doc, ptype=None, user=None) -> bool:
	user = user or frappe.session.user
	if not _scoped_has_permission(doc.company, user):
		return False
	if ptype in WRITE_PTYPES:
		# The self-branch-management gate (server-side enforcement; the form
		# UX mirror lives in cbt_branch.js via frm.disable_form()).
		return branch_management_allowed(doc.company, user)
	return True


def court_has_permission(doc, ptype=None, user=None) -> bool:
	# Courts carry NO management gate — Company Admins always manage courts.
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def booking_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def slot_block_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def payment_proof_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def booking_invoice_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def open_play_session_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def membership_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def customer_ban_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def platform_statement_has_permission(doc, ptype=None, user=None) -> bool:
	# B21(a). Reads are tenant-scoped; every WRITE path is a whitelisted engine
	# method that re-checks has_platform_scope itself (the DocPerms give no
	# tenant role write, so this hook only ever narrows a read).
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def customer_credit_has_permission(doc, ptype=None, user=None) -> bool:
	return _scoped_has_permission(doc.company, user or frappe.session.user)


def user_has_permission(doc, ptype=None, user=None) -> bool:
	"""B38's read veto, mirroring user_query. Tenant roles hold no create or
	write DocPerm on User, so this only ever narrows a read."""
	user = user or frappe.session.user
	if has_platform_scope(user):
		return True
	if doc.name == user:
		return True
	try:
		company = get_session_company(user)
	except frappe.PermissionError:
		return False
	return bool(
		company
		and frappe.db.exists("CBT Company User", {"user": doc.name, "company": company})
	)


def payment_channel_has_permission(doc, ptype=None, user=None) -> bool:
	# B29. A platform-scope channel carries company=None, and
	# _scoped_has_permission answers True only for platform scope on a None
	# company — so a tenant seat is refused the platform's own channels too.
	return _scoped_has_permission(doc.company, user or frappe.session.user)
