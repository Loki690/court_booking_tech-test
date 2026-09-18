# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Membership (section-11, PLAN §4) — a COMPANY-SCOPED discount entitlement.

Two invariants the rest of section-11 leans on:

- **Company-scoped by construction.** A customer may hold memberships at MANY
  companies (the marketplace premise: one account, book anywhere), and a
  discount earned at one tenant must never surface at another. Every read goes
  through membership.get_active_membership(company, customer), which always
  filters on company.
- **"Active" is DERIVED, never stored.** A stored is_active Check goes stale the
  day after end_date and would need a nightly job to stay honest. The window is
  evaluated against the clock seam on every read instead, so the answer is
  correct at the moment money is computed.

Overlap rule: at most ONE membership may be active for a given
(company, customer) on any given day — otherwise "the" discount is ambiguous.
An empty end_date means lifetime (open-ended interval).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt, getdate


class CBTMembership(Document):
	def autoname(self):
		# Naming runs BEFORE fetch_from/validate on insert, so the company code
		# is resolved by query here — a `format:` rule could only read this
		# doc's own fields (S4 gotcha; same pattern as BK-/INV-/OPS-).
		company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		self.name = make_autoname(f"MEM-{company_code}-.#####")

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		self._validate_customer()
		self._validate_window()
		self._validate_discount()
		self._validate_no_overlapping_membership()

	def _validate_customer(self):
		if not self.customer:
			frappe.throw(_("Customer is required."))
		if "CBT Customer" not in set(frappe.get_roles(self.customer)):
			frappe.throw(
				_(
					"{0} is not a portal customer — memberships apply to customers, "
					"not staff accounts."
				).format(self.customer)
			)

	def _validate_window(self):
		if not self.start_date:
			frappe.throw(_("Start Date is required."))
		if self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date."))

	def _validate_discount(self):
		discount = flt(self.discount_percent)
		if discount < 0 or discount > 100:
			frappe.throw(_("Discount (%) must be between 0 and 100."))

	def _validate_no_overlapping_membership(self):
		"""One active membership per (company, customer) at a time.

		Interval intersection with NULL end_date meaning +infinity:
		    new.start <= other.end   AND   other.start <= new.end
		Desk-only creation, so the insert race is accepted (two admins adding
		the same member in the same second) — the read side is deterministic
		regardless because get_active_membership orders its pick.
		"""
		clash = frappe.db.sql(
			"""
			SELECT name FROM `tabCBT Membership`
			WHERE company = %(company)s
			  AND customer = %(customer)s
			  AND name != %(name)s
			  AND (end_date IS NULL OR end_date >= %(start_date)s)
			  AND (%(end_date)s IS NULL OR start_date <= %(end_date)s)
			LIMIT 1
			""",
			{
				"company": self.company,
				"customer": self.customer,
				"name": self.name or "",
				"start_date": getdate(self.start_date),
				"end_date": getdate(self.end_date) if self.end_date else None,
			},
		)
		if clash:
			frappe.throw(
				_(
					"{0} already has a membership at this company overlapping these "
					"dates ({1}). End that one first — two active memberships make "
					"the discount ambiguous."
				).format(self.customer, clash[0][0])
			)
