# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Customer Credit — store credit issued instead of cash at a refund
(Backlog B39). Engine-driven only: see court_booking_tech/credits.py and
docs/sections/section-26.md.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt

from court_booking_tech import clock


class CBTCustomerCredit(Document):
	def autoname(self):
		company_code = frappe.db.get_value("CBT Company", self.company, "company_code")
		self.name = make_autoname(f"CR-{company_code}-.YYYY.-.#####")

	def before_insert(self):
		self.issued_by = self.issued_by or frappe.session.user
		self.issued_at = self.issued_at or clock.now_dt()
		if self.balance in (None, ""):
			self.balance = self.amount

	def validate(self):
		if not self.customer:
			frappe.throw(_("Store credit must belong to a customer account."))
		if flt(self.amount) <= 0:
			frappe.throw(_("Store credit must be worth more than nothing."))
		self.balance = flt(self.balance, 2)
		if self.balance < 0 or self.balance > flt(self.amount, 2):
			frappe.throw(
				_("A credit's balance cannot be negative or exceed what was issued.")
			)
		if self.status != "Void":
			self.status = "Spent" if self.balance <= 0 else "Active"

	def on_trash(self):
		if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
			frappe.throw(_("Store credit cannot be deleted."), frappe.PermissionError)
