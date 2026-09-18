# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Payment Channel (Backlog B29) — see court_booking_tech/payment_channels.py
for the rules. This controller owns the row's own invariants:

- scope ⇄ company agree (a Tenant channel names its company; a Platform channel
  names none); scope, company and KIND are fixed once created — flipping a
  channel's kind would re-route the state machine under every payment that
  already names it;
- the label is unique per company (case-insensitive) — two "GCash" rows would
  make the tenant's split meaningless;
- the customer-facing fields are a PUBLIC surface (the /book checkout is open to
  logged-out visitors), so control characters never get in;
- a channel with payments on it is never deleted — disable it (§8e retention).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint

from court_booking_tech.payment_channels import (
	CASH,
	KINDS,
	PLATFORM,
	TENANT,
	references,
	slug_for_name,
)

FIXED_AFTER_INSERT = ("scope", "company", "kind")
PUBLIC_TEXT_FIELDS = ("account_name", "account_number", "instructions")


def _has_control_chars(value: str) -> bool:
	return any(ord(ch) < 0x20 and ch not in "\n\r\t" for ch in value) or "\x7f" in value


class CBTPaymentChannel(Document):
	def autoname(self):
		if self.scope == PLATFORM:
			code = "PLATFORM"
		else:
			code = frappe.db.get_value("CBT Company", self.company, "company_code") or "CO"
		self.name = make_autoname(f"PCH-{code}-{slug_for_name(self.label)}-.##")

	def validate(self):
		self.scope = self.scope or TENANT
		self.label = (self.label or "").strip()
		if not self.label:
			frappe.throw(_("Label is required."))
		if self.kind not in KINDS:
			frappe.throw(_("Kind must be Cash or Transfer."))
		if self.scope == PLATFORM:
			self.company = None
		elif not self.company:
			# Leak vector 2: a blank company link on a tenant row is visible to
			# every tenant.
			frappe.throw(_("Company is required for a tenant channel."))
		self._validate_fixed_fields()
		self._validate_unique_label()
		self._apply_defaults()
		self._clean_public_text()

	def _validate_fixed_fields(self):
		if self.is_new():
			return
		before = frappe.db.get_value(
			"CBT Payment Channel", self.name, list(FIXED_AFTER_INSERT), as_dict=True
		)
		if not before:
			return
		for fieldname in FIXED_AFTER_INSERT:
			if (before.get(fieldname) or None) != (self.get(fieldname) or None):
				frappe.throw(
					_(
						"{0} is fixed once a channel is created — disable this one and "
						"add another instead."
					).format(_(self.meta.get_label(fieldname)))
				)

	def _validate_unique_label(self):
		filters = {"scope": self.scope, "name": ("!=", self.name or "")}
		if self.scope == TENANT:
			filters["company"] = self.company
		for row in frappe.get_all("CBT Payment Channel", filters=filters, fields=["label"]):
			if (row.label or "").strip().lower() == self.label.lower():
				frappe.throw(
					_("A channel labelled {0} already exists here — labels are unique per company.").format(
						self.label
					)
				)

	def _apply_defaults(self):
		self.mode_of_payment = (self.mode_of_payment or "").strip() or self.label
		if not (self.account_label or "").strip():
			self.account_label = "Cash on Hand" if self.kind == CASH else self.label
		if not self.sort_order:
			# A channel added later lands LAST: the first enabled channel of a
			# kind is every picker's default, so a bank typed in with the form's
			# blank sort order must not leapfrog GCash and silently become what
			# the checkout pre-selects (E2E finding, 2026-08-27).
			filters = {"scope": self.scope, "name": ("!=", self.name or "")}
			if self.scope == TENANT:
				filters["company"] = self.company
			rows = frappe.get_all(
				"CBT Payment Channel", filters=filters, fields=["sort_order"]
			)
			self.sort_order = max([cint(r.sort_order) for r in rows] + [0]) + 1

	def _clean_public_text(self):
		for fieldname in PUBLIC_TEXT_FIELDS:
			value = self.get(fieldname)
			if value is None:
				continue
			value = str(value).strip()
			if _has_control_chars(value):
				frappe.throw(
					_("{0} contains characters that cannot be shown to customers.").format(
						_(self.meta.get_label(fieldname))
					)
				)
			self.set(fieldname, value or None)

	def on_trash(self):
		used = references(self.name)
		if used:
			frappe.throw(
				_(
					"{0} is on {1} payment record(s) — disable it instead of deleting it; "
					"the payments that used it keep their history."
				).format(self.label, sum(count for _dt, count in used)),
				frappe.LinkExistsError,
			)
