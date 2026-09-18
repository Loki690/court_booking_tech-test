# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Platform Statement — Backlog B21(a), 2026-08-27: the platform's own
billing document, issued PER TENANT from a closed month.

B21(b) froze a month (`CBT Platform Month Close`); this is the half that turns
the frozen Amount Due into something a tenant can be handed, read and pay:

- ISSUED from the close, never by hand — `issue_statements(period)` copies
  each tenant's row (billing mode, rate, confirmed revenue, amount due) into
  one statement and freezes it there. The figures are a SNAPSHOT AT ISSUE
  TIME: reopening and re-closing the month later does not move a statement
  that already exists (and the close refuses to be deleted while a live
  statement hangs off it — see the close's `on_trash`).
- NUMBERED per tenant, PLAN §8e: `PST-<COMPANYCODE>-YYYY-#####`. A cancelled
  statement is RETAINED with its number consumed; a re-issue takes the next
  number; nothing below System Manager can delete one.
- PAID / CANCELLED only through the two engine methods below. Every field is
  read-only and `validate` refuses any other edit, so the desk form cannot
  quietly change a figure the tenant already has.
- TENANT-READABLE: CBT Company Admin holds `read` + `print`, scoped to their
  own company through the tenancy hooks (`tenancy.platform_statement_query` /
  `_has_permission`), so a tenant sees exactly what they owe and nothing about
  any other tenant. Platform scope writes; the tenant reads.

Money still never flows through us (PLAN D5): the statement is a CLAIM, and
"paid" is the platform admin recording that the tenant's transfer arrived —
which is why `mark_paid` takes a date and a reference rather than a gateway
callback.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cstr, flt, getdate

from court_booking_tech import clock
from court_booking_tech.payment_channels import resolve_platform_channel
from court_booking_tech.tenancy import has_platform_scope

ISSUED = "Issued"
PAID = "Paid"
CANCELLED = "Cancelled"
# A statement that still counts: it is what a tenant owes or has paid for the
# month. Only a CANCELLED one stands aside — and stays on record.
LIVE_STATUSES = (ISSUED, PAID)

# The figures copied from a close row. Named EXACTLY as the report's ROW_FIELDS
# name them, so a column renamed there fails loudly here rather than freezing
# NULL into a tenant's statement.
FIGURE_FIELDS = (
	"billing_mode",
	"commission_percent",
	"subscription_fee",
	# Backlog B27: the Per Booking figures — units and the fees passed through.
	"booking_count",
	"booking_fees",
	"confirmed_revenue",
	"amount_due",
)


def _require_platform():
	if not has_platform_scope():
		frappe.throw(
			_("Issuing, paying and cancelling platform statements is a platform-admin action."),
			frappe.PermissionError,
		)


class CBTPlatformStatement(Document):
	def autoname(self):
		company_code = frappe.db.get_value("CBT Company", self.company, "company_code")
		if not company_code:
			frappe.throw(
				_("Company {0} has no company code — statements are numbered by it.").format(
					self.company
				)
			)
		# The year is the STATEMENT's year, not the wall clock's: `.YYYY.` would
		# read real time while statement_date comes from the clock seam, and a
		# statement dated 2027 named 2026 is the kind of thing an auditor asks
		# about. Same per-year counter shape as the tenant invoices.
		year = getdate(self.statement_date or clock.now_dt().date()).year
		self.name = make_autoname(f"PST-{company_code}-{year:04d}-.#####")

	def before_insert(self):
		if not self.flags.via_statement_engine:
			frappe.throw(
				_(
					"Platform statements are issued from a closed month "
					"(CBT Platform Month Close → Issue statements) — they cannot be "
					"created by hand."
				)
			)

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		if not self.is_new() and not self.flags.via_statement_engine:
			frappe.throw(
				_(
					"A platform statement is frozen once issued — use Mark paid or "
					"Cancel statement."
				)
			)

	def on_trash(self):
		if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_(
					"Platform statements are never deleted — a cancelled statement is "
					"retained and its number consumed."
				),
				frappe.PermissionError,
			)


def live_statements(period: str) -> list[dict]:
	"""The Issued/Paid statements of one month — what blocks a reopen."""
	return frappe.get_all(
		"CBT Platform Statement",
		filters={"period": period, "status": ("in", LIVE_STATUSES)},
		fields=["name", "company", "status"],
		order_by="name asc",
	)


@frappe.whitelist(methods=["POST"])
def issue_statements(period) -> dict:
	"""Issue one statement per tenant with an Amount Due, from the close.

	Idempotent on re-run: a tenant already holding a LIVE statement for the
	month is skipped (so a cancelled one can be re-issued, and only that one);
	a tenant owing nothing gets no statement. Platform-only.
	"""
	_require_platform()
	period = cstr(period).strip()
	# Lock the close row for the rest of this transaction: two "Issue
	# statements" clicks landing together would otherwise both read an empty
	# `already` below and bill every tenant twice (ducky, 2026-08-27).
	close = period and frappe.db.get_value(
		"CBT Platform Month Close", period, "name", for_update=True
	)
	if not close:
		frappe.throw(
			_(
				"{0} is not a closed month — close it on CBT Platform Revenue first; "
				"statements are issued from the frozen figures."
			).format(period or _("(no month)"))
		)

	rows = frappe.get_all(
		"CBT Platform Month Close Row",
		filters={"parenttype": "CBT Platform Month Close", "parent": period},
		fields=["company", "company_name", *FIGURE_FIELDS],
		order_by="idx asc",
	)
	already = {row.company for row in live_statements(period)}
	now = clock.now_dt()
	issued, already_issued, nothing_due = [], [], []
	for row in rows:
		if row.company in already:
			already_issued.append(row.company)
			continue
		if flt(row.amount_due) <= 0:
			nothing_due.append(row.company)
			continue
		doc = frappe.get_doc(
			{
				"doctype": "CBT Platform Statement",
				"company": row.company,
				"company_name": row.company_name,
				"period": period,
				"statement_date": now.date(),
				"status": ISSUED,
				"issued_by": frappe.session.user,
				"issued_at": now,
				**{field: row.get(field) for field in FIGURE_FIELDS},
			}
		)
		doc.flags.via_statement_engine = True
		doc.insert(ignore_permissions=True)
		issued.append(doc.name)
	return {
		"period": period,
		"issued": issued,
		"already_issued": already_issued,
		"nothing_due": nothing_due,
	}


@frappe.whitelist(methods=["POST"])
def mark_paid(name, paid_on, payment_reference=None, payment_channel=None) -> dict:
	"""Record that the tenant's payment arrived. Platform-only.

	`paid_on` is the day the money landed (never in the future on the site
	clock); `payment_reference` is the tenant's transfer/deposit reference and
	is printed on the statement. `payment_channel` (Backlog B29, user ruling
	"yes, include it") is the PLATFORM's own channel it arrived through — one
	of the Platform-scope `CBT Payment Channel` rows — so the platform's books
	reconcile on the same key as the tenant's. Optional: a statement paid
	before channels existed, or by a route nobody set up, records none.
	"""
	_require_platform()
	payment_channel = resolve_platform_channel(payment_channel)
	doc = frappe.get_doc("CBT Platform Statement", name)
	if doc.status == PAID:
		frappe.throw(_("{0} is already paid.").format(doc.name))
	if doc.status == CANCELLED:
		frappe.throw(
			_("{0} is cancelled — issue a new statement for the month instead.").format(
				doc.name
			)
		)
	if not paid_on:
		frappe.throw(_("Paid On is required."))
	paid_on = getdate(paid_on)
	now = clock.now_dt()
	if paid_on > now.date():
		frappe.throw(_("Paid On cannot be in the future."))

	doc.flags.via_statement_engine = True
	doc.status = PAID
	doc.paid_on = paid_on
	doc.payment_reference = cstr(payment_reference).strip() or None
	doc.payment_channel = payment_channel
	doc.paid_by = frappe.session.user
	doc.paid_at = now
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status, "paid_on": doc.paid_on}


@frappe.whitelist(methods=["POST"])
def cancel_statement(name, reason) -> dict:
	"""Void a statement — it stays on record (number consumed) with the reason
	printed across it. Allowed from Issued AND from Paid: cancel-and-reissue is
	the only undo for a payment recorded against the wrong statement. Platform-only.
	"""
	_require_platform()
	doc = frappe.get_doc("CBT Platform Statement", name)
	if doc.status == CANCELLED:
		frappe.throw(_("{0} is already cancelled.").format(doc.name))
	reason = cstr(reason).strip()
	if not reason:
		frappe.throw(_("Give a reason — it is printed on the cancelled statement."))

	doc.flags.via_statement_engine = True
	doc.status = CANCELLED
	doc.cancel_reason = reason
	doc.cancelled_by = frappe.session.user
	doc.cancelled_at = clock.now_dt()
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}
