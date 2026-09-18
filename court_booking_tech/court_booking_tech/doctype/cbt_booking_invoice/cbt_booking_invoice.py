# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Booking Invoice — the company's print-only billing document (PLAN §6).

Controller-only lifecycle (mirrors CBT Payment Proof):
- created exclusively by court_booking_tech.billing (flags.via_billing_engine),
  so per-company numbering, the VAT/title snapshot and status derivation can
  never be bypassed from the desk;
- after creation the ONLY hand-editable field is or_number (the staff's manual
  O.R. cross-reference) — any other change is rejected here in validate, on
  top of the permlevel-1 lock on every other field;
- status/amount updates flow through billing.sync_invoice_for_booking, which
  sets the same engine flag;
- never deleted below System Manager: a cancelled document is RETAINED with
  its number consumed; a re-book gets the NEXT number (PLAN §8e).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt, get_datetime, getdate

from court_booking_tech import clock

LAYOUT_FIELDTYPES = {"Section Break", "Column Break", "Tab Break"}
# `cancelled_at` is NOT here on purpose: the guard runs BEFORE the engine's
# own stamp in validate, so the stamp can never trip it — and listing it would
# have made a refund's posting date (the Tenant Ledger's reversal) hand-editable
# on a paid document (ducky finding 3). Same for `refund_reason` /
# `refunded_by` (section-26): the engine writes them on the cancelling save;
# nobody edits a refund's reason after the fact.
HAND_EDITABLE_FIELDS = {"or_number"}


class CBTBookingInvoice(Document):
	def autoname(self):
		company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		self.name = make_autoname(f"INV-{company_code}-.YYYY.-.#####")

	def before_insert(self):
		if not self.flags.via_billing_engine:
			frappe.throw(
				_(
					"Billing documents are created automatically with their "
					"booking — they cannot be created by hand."
				)
			)

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		if not self.is_new() and not self.flags.via_billing_engine:
			self._reject_edits_beyond_or_number()
		self._stamp_cancellation()

	def _stamp_cancellation(self):
		"""Backlog B29: the Tenant Ledger posts a PAID document's reversal on the
		day it was cancelled, so that day has to be recorded. Stamped once, on
		the flip INTO Cancelled; cleared when the document leaves it (the
		Expired → Completed retro-confirm), so a document that is paid again
		does not carry a stale reversal date."""
		if self.status == "Cancelled":
			if not self.cancelled_at:
				self.cancelled_at = clock.now_dt()
		else:
			self.cancelled_at = None
			# Section-26: the refund stamps belong to THAT cancellation; a
			# document paid again does not carry a stale reason or seat.
			self.refund_reason = None
			self.refunded_by = None
			self.refund_cause = None
			self.refund_as_credit = 0

	def on_trash(self):
		if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_(
					"Billing documents are never deleted — a cancelled document "
					"is retained and its number consumed."
				),
				frappe.PermissionError,
			)

	def _reject_edits_beyond_or_number(self):
		scalar_fields = [
			df
			for df in self.meta.fields
			if df.fieldtype not in LAYOUT_FIELDTYPES
			and df.fieldtype != "Table"
			and df.fieldname not in HAND_EDITABLE_FIELDS
		]
		before = frappe.db.get_value(
			"CBT Booking Invoice",
			self.name,
			[df.fieldname for df in scalar_fields],
			as_dict=True,
		)
		if not before:
			return
		for df in scalar_fields:
			if not _values_equal(df.fieldtype, before.get(df.fieldname), self.get(df.fieldname)):
				frappe.throw(
					_(
						"{0} on a billing document cannot be edited — only the "
						"Official O.R. No. is recorded by hand."
					).format(_(df.label or df.fieldname))
				)

		rows_before = [
			(row.description, flt(row.qty, 2), flt(row.rate, 2), flt(row.amount, 2))
			for row in frappe.get_all(
				"CBT Invoice Item",
				filters={"parent": self.name, "parenttype": "CBT Booking Invoice"},
				fields=["description", "qty", "rate", "amount"],
				order_by="idx asc",
			)
		]
		rows_now = [
			(row.description, flt(row.qty, 2), flt(row.rate, 2), flt(row.amount, 2))
			for row in (self.items or [])
		]
		if rows_before != rows_now:
			frappe.throw(
				_(
					"Items on a billing document cannot be edited — only the "
					"Official O.R. No. is recorded by hand."
				)
			)


def _values_equal(fieldtype, old, new) -> bool:
	if fieldtype in ("Currency", "Float", "Percent"):
		# Frappe stores an empty numeric as NULL or 0 depending on the write
		# path — normalize both so the guard never false-trips (NON-VAT
		# invoices carry empty vat figures; display is gated on vat_mode).
		return flt(old, 2) == flt(new, 2)
	if fieldtype == "Int":
		return cint(old) == cint(new)
	if fieldtype == "Date":
		return (old and getdate(old)) == (new and getdate(new))
	if fieldtype == "Datetime":
		return (old and get_datetime(old)) == (new and get_datetime(new))
	return (old or "") == (new or "")
