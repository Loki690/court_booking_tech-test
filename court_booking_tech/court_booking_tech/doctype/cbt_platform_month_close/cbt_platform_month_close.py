# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Platform Month Close — Backlog B21(b), 2026-08-27: persist a monthly
snapshot so a CLOSED month stops moving.

The platform's own revenue was REPORT-ONLY: CBT Platform Revenue recomputed
every tenant's confirmed revenue and amount due on every run, so a booking
retro-confirmed or cancelled in October silently changed the September figure
the platform had already billed out of band. This document is the freeze and
nothing more — it is NOT a statement (no numbering, nothing issued to a
tenant, no paid/unpaid; that is B21(a), the section-shaped half). Issuing
stays manual; what changes is that the number being issued can no longer drift
after the fact.

One record per calendar month, named by its period ("2027-08"), so a second
close of the same month is impossible by construction. Rows are copied from
`cbt_platform_revenue.compute_rows` — the SAME computation the report runs
live — at the moment of closing, and the report reads them back instead of
recomputing while the record exists. The record is frozen: any edit after
insert is refused. Deleting it reopens the month (Platform Admin holds
delete), after which it can be closed again.

Platform-only, and doubly so: the DocPerms name only System Manager and CBT
Platform Admin, and every entry point below re-checks `has_platform_scope`
because a whitelisted method is reachable without ever touching DocPerms.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, get_last_day, getdate

from court_booking_tech import clock
from court_booking_tech.tenancy import has_platform_scope


def _require_platform():
	if not has_platform_scope():
		frappe.throw(
			_("Closing a platform month is a platform-admin action."),
			frappe.PermissionError,
		)


def period_key(year, month) -> str:
	year, month = cint(year), cint(month)
	if not (1 <= month <= 12) or year < 2000:
		frappe.throw(_("Month must be 1–12 and the year a real one."))
	return f"{year:04d}-{month:02d}"


class CBTPlatformMonthClose(Document):
	def autoname(self):
		# Named by period so the same month cannot be closed twice — and named
		# HERE rather than through `autoname: field:period`, because
		# set_new_name runs before validate and `period` is derived.
		self.period = period_key(self.year, self.month)
		self.name = self.period

	def validate(self):
		_require_platform()
		if not self.is_new():
			frappe.throw(
				_(
					"{0} is a closed month and its figures are frozen. Delete this "
					"record to reopen the month, then close it again."
				).format(self.name)
			)
		self.period = period_key(self.year, self.month)
		self._require_month_ended()
		if frappe.db.exists(self.doctype, self.period):
			frappe.throw(_("{0} is already closed.").format(self.period))
		self._freeze_rows()

	def _require_month_ended(self):
		"""A month closes AFTER its last day on the site clock — freezing a
		half-finished month would freeze a lie."""
		last_day = get_last_day(getdate(f"{self.period}-01"))
		today = clock.now_dt().date()
		if last_day >= today:
			# Closable from the day AFTER the last day — say that day, not the
			# last day itself (ducky, 2026-08-27: the first draft named
			# last_day and told a 31 August admin that 31 August was allowed).
			frappe.throw(
				_(
					"{0} has not ended yet — a month can be closed from {1} onwards."
				).format(
					self.period,
					frappe.format(add_days(last_day, 1), {"fieldtype": "Date"}),
				)
			)

	def _freeze_rows(self):
		# The report MODULE, not the report package of the same name (the
		# package's __init__ is empty and the import silently resolves to it).
		# ROW_FIELDS comes from the report too: ONE column list, so a column
		# added to the report cannot be silently frozen as NULL here.
		from court_booking_tech.court_booking_tech.report.cbt_platform_revenue.cbt_platform_revenue import (
			ROW_FIELDS,
			compute_rows,
		)

		self.set("rows", [])
		for row in compute_rows(self.year, self.month):
			self.append("rows", {field: row.get(field) for field in ROW_FIELDS})
		self.total_confirmed_revenue = flt(
			sum(flt(r.confirmed_revenue) for r in self.rows), 2
		)
		self.total_amount_due = flt(sum(flt(r.amount_due) for r in self.rows), 2)
		self.closed_at = clock.now_dt()
		self.closed_by = frappe.session.user

	def on_trash(self):
		_require_platform()
		# B21(a): a statement is a snapshot of THIS close. Reopening the month
		# underneath a live statement would let the report and the tenant's
		# statement disagree, so the close stays until those are cancelled.
		# Cancelled statements do not block — they are history, not a claim.
		from court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement import (
			live_statements,
		)

		live = live_statements(self.name)
		if live:
			frappe.throw(
				_(
					"{0} statement(s) were issued from this close ({1}) — cancel them "
					"before reopening the month."
				).format(len(live), ", ".join(row.name for row in live))
			)


@frappe.whitelist(methods=["POST"])
def close_month(year, month) -> dict:
	"""Close one calendar month — the report page's "Close this month" button.

	Platform-only. The same document a Platform Admin could create from the
	desk form; this exists so the closing gesture lives on the report a human
	is already reading.
	"""
	_require_platform()
	doc = frappe.get_doc(
		{"doctype": "CBT Platform Month Close", "year": cint(year), "month": cint(month)}
	)
	doc.insert()
	return {
		"name": doc.name,
		"closed_at": doc.closed_at,
		"closed_by": doc.closed_by,
		"rows": len(doc.rows),
	}


@frappe.whitelist(methods=["GET"])
def get_close_status(year, month) -> dict:
	"""Is this month closed, and by whom — what the report page's indicator
	shows. Platform-only, like everything that names tenants' figures."""
	_require_platform()
	period = period_key(year, month)
	row = frappe.db.get_value(
		"CBT Platform Month Close",
		period,
		["name", "closed_at", "closed_by"],
		as_dict=True,
	)
	if not row:
		return {"closed": False}
	return {
		"closed": True,
		"name": row.name,
		"closed_at": row.closed_at,
		"closed_by": row.closed_by,
	}
