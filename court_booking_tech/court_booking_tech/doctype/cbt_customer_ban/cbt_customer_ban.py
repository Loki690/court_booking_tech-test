# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Customer Ban (section-11, user-requested scope) — a company's block-list
entry against a customer who keeps making reservations that never materialise.

Scope decisions (deliberate, recorded in section-11 as-built):

- **Company-scoped ONLY.** A ban at one tenant says nothing about the customer
  anywhere else — the marketplace runs on one shared account (PLAN §1), so a
  platform-wide ban would be a different (and much heavier) product decision.
  IP-level bans were considered and REJECTED: PH mobile carriers put hundreds
  of unrelated users behind one CGNAT address (the same reasoning that keeps
  the signup rate limit per-user, PLAN §4), so an IP ban mostly hits bystanders.
- **It blocks exactly one thing: ONLINE (portal) booking creation.** Front-desk
  staff can still book a banned customer who is standing at the counter with
  cash, open play still admits them, and their existing bookings stay fully
  operable (proof upload, staff cancel). The abuse being stopped is unattended
  slot-squatting, not the person's existence.
- **Raise is staff-level, lift is admin-level.** `status` sits at permlevel 1
  with write for Company Admin + platform only, so a front-desk account cannot
  quietly clear a ban its manager raised. Frappe SILENTLY reverts permlevel
  writes for roles without level-1 write — the lift API therefore also checks
  explicitly and throws, because an API that no-ops is worse than one that
  refuses.
- **Never deleted below System Manager** — the reason text is the audit trail
  (PLAN §8l: suspended/offboarded data is retained, never deleted).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from court_booking_tech import clock

ACTIVE = "Active"
LIFTED = "Lifted"


class CBTCustomerBan(Document):
	def autoname(self):
		# Same naming discipline as MEM-/BK-/INV-: the company code is resolved
		# by query because naming precedes fetch_from (S4 gotcha).
		company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		self.name = make_autoname(f"BAN-{company_code}-.#####")

	def before_insert(self):
		self.status = self.status or ACTIVE
		self.banned_by = frappe.session.user
		self.banned_at = clock.now_dt()

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		if not self.customer:
			frappe.throw(_("Customer is required."))
		if not (self.reason or "").strip():
			frappe.throw(_("A reason is required — the ban record is the audit trail."))
		self._validate_single_active_ban()
		self._stamp_status_change()

	def _validate_single_active_ban(self):
		"""At most one ACTIVE ban per (company, customer).

		Without this, "lift the ban" is ambiguous (which one?) and the
		enforcement read would have to reason about a set instead of a row.
		"""
		if self.status != ACTIVE:
			return
		clash = frappe.db.get_value(
			"CBT Customer Ban",
			{
				"company": self.company,
				"customer": self.customer,
				"status": ACTIVE,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_("{0} is already banned at this company ({1}).").format(
					self.customer, clash
				)
			)

	def _stamp_status_change(self):
		if self.is_new():
			return
		before = frappe.db.get_value("CBT Customer Ban", self.name, "status")
		if before == self.status:
			return
		if before == ACTIVE and self.status == LIFTED:
			self.lifted_by = frappe.session.user
			self.lifted_at = clock.now_dt()
			return
		# Re-arming a lifted ban would silently reuse the original reason and
		# banned_at stamps — a NEW ban is the honest record of a new decision.
		frappe.throw(
			_(
				"A lifted ban cannot be re-activated — raise a new ban so the "
				"reason and date reflect the new decision."
			)
		)

	def on_trash(self):
		if "System Manager" not in set(frappe.get_roles()):
			frappe.throw(
				_("Ban records are retained for audit — lift the ban instead of deleting it."),
				frappe.PermissionError,
			)
