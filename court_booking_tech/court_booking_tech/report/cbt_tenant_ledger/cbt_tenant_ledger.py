# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Tenant Ledger (Backlog B29) — the JOURNAL a tenant's accountant posts from,
built from documents this app already holds, in the shape of
docs/collection_flow_v1.md. Two books, one filter:

TENANT'S BOOKS (Part 2 of the doc). Per document, on the day it was PAID
(`verified_at`):
    Dr  <channel's ledger account>        total the customer paid
    Cr  Service Revenue                    tenant's share net of VAT
    Cr  Output VAT Payable                 VAT inside the tenant's share (VAT tenants)
    Cr  Due to Platform                    the booking fee held for the platform (B27)
A paid document later cancelled posts the exact REVERSAL on `cancelled_at`.
The platform statement lands too: for Subscription / Percentage tenants the
payable is recognised when the statement is ISSUED (Dr Platform Fees Expense /
Cr Due to Platform — a Per Booking tenant's payable accrued per document above,
so its statement only settles); marking it PAID posts Dr Due to Platform / Cr
Cash/Bank (remitted to platform); a cancelled statement reverses what it had
posted.

PLATFORM'S BOOKS (Part 1), this tenant as the PARTY — platform scope only:
    issued:     Dr Accounts Receivable          / Cr <revenue by billing mode>
    paid:       Dr <platform channel account>   / Cr Accounts Receivable
    cancelled:  the reversals, on `cancelled_at`
The doc books the fee per booking; this app bills it MONTHLY on the statement
(B21), so the platform's revenue is recognised at issue — one entry per tenant
per month, not one per booking. Stated here so nobody "fixes" it into a
double count.

Every voucher balances (the test asserts it); the last row is the total.
Account names are LABELS: the channel's `account_label` for the debit side,
fixed English names for the rest — the tenant maps them onto its own chart.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from court_booking_tech import clock
from court_booking_tech.billing import PAID
from court_booking_tech.payment_channels import PLATFORM, list_channels
from court_booking_tech.reports import resolve_report_company
from court_booking_tech.tenancy import has_platform_scope

TENANT_BOOKS = "Tenant"
PLATFORM_BOOKS = "Platform"

UNALLOCATED = "Unallocated (no channel)"
SERVICE_REVENUE = "Service Revenue"
OUTPUT_VAT = "Output VAT Payable"
DUE_TO_PLATFORM = "Due to Platform"
PLATFORM_FEES_EXPENSE = "Platform Fees Expense"
REMITTED = "Cash/Bank (remitted to platform)"
RECEIVABLE = "Accounts Receivable"
REVENUE_BY_MODE = {
	"Percentage": "Commission Revenue",
	"Subscription": "Subscription Revenue",
	"Per Booking": "Booking Fee Revenue",
}
PER_BOOKING = "Per Booking"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = resolve_report_company(filters.get("company"))
	from_date, to_date = _period(filters)
	books = filters.get("books") or TENANT_BOOKS
	if books == PLATFORM_BOOKS and not has_platform_scope():
		frappe.throw(
			_("The platform's books are a platform-admin view."), frappe.PermissionError
		)
	if books not in (TENANT_BOOKS, PLATFORM_BOOKS):
		frappe.throw(_("Books must be Tenant or Platform."))

	if books == TENANT_BOOKS:
		rows = _tenant_rows(company, from_date, to_date)
	else:
		rows = _platform_rows(company, from_date, to_date)

	# Date, then document — and NOTHING finer: Python's sort is stable, so each
	# voucher's lines stay together in the order they were posted. Sorting on
	# the line index as well interleaved two same-day vouchers of one document
	# (a statement issued AND paid today: Dr AR, Dr GCash, Cr Revenue, Cr AR —
	# E2E finding, 2026-08-27).
	rows.sort(key=lambda r: (r["posting_date"], r["voucher"]))
	total = {
		"posting_date": None,
		"voucher": None,
		"description": _("Total"),
		"account": None,
		"debit": 0.0,
		"credit": 0.0,
		"is_total_row": 1,
	}
	for row in rows:
		row.pop("_line", None)
		total["debit"] = flt(total["debit"] + flt(row["debit"]), 2)
		total["credit"] = flt(total["credit"] + flt(row["credit"]), 2)
	if rows:
		rows.append(total)
	return _columns(books), rows


# ---------------------------------------------------------------------------
# Tenant's books
# ---------------------------------------------------------------------------


def _tenant_rows(company, from_date, to_date) -> list[dict]:
	channels = {
		row.name: row
		for row in list_channels(company, enabled_only=False, fields=("name", "label", "account_label"))
	}
	rows = []

	# Bounded to the window on EITHER stamp: a document paid inside it, or one
	# cancelled inside it (its payment may be months earlier) — never the whole
	# history filtered in Python (ducky finding 5).
	window = (f"{from_date} 00:00:00", f"{to_date} 23:59:59")
	invoices = frappe.get_all(
		"CBT Booking Invoice",
		filters={
			"company": company,
			"verified_at": ("is", "set"),
			"status": ("in", (PAID, "Cancelled")),
		},
		or_filters=[
			["verified_at", "between", window],
			["cancelled_at", "between", window],
		],
		fields=[
			"name",
			"status",
			"customer_name",
			"booking",
			"booking_group",
			"participant_ref",
			"total_amount",
			"platform_fee",
			"vat_amount",
			"verified_at",
			"cancelled_at",
			"payment_channel",
			"refund_reason",
		],
	)
	for inv in invoices:
		paid_day = getdate(inv.verified_at)
		meta = channels.get(inv.payment_channel)
		account = (meta.account_label if meta else None) or UNALLOCATED
		if inv.booking_group:
			what = _("cart {0}").format(inv.booking_group)
		elif inv.booking:
			what = _("booking {0}").format(inv.booking)
		else:
			what = _("open play entry")
		lines = _payment_lines(inv, account)
		if from_date <= paid_day <= to_date:
			rows.extend(
				_voucher(
					paid_day,
					inv.name,
					_("Payment received — {0}, {1}").format(what, inv.customer_name or ""),
					lines,
					channel=inv.payment_channel,
				)
			)
		if inv.status == "Cancelled" and inv.cancelled_at:
			cancel_day = getdate(inv.cancelled_at)
			if from_date <= cancel_day <= to_date:
				# Section-26: the reason the Company Admin typed rides the
				# reversal line — the accountant reads WHY, not just that.
				description = _("Refund / reversal — {0}, {1}").format(what, inv.customer_name or "")
				if inv.refund_reason:
					description = f"{description} — {inv.refund_reason}"
				rows.extend(
					_voucher(
						cancel_day,
						inv.name,
						description,
						_reverse(lines),
						channel=inv.payment_channel,
						reversal=True,
					)
				)

	for st in _statements(company):
		amount = flt(st.amount_due, 2)
		if not amount:
			continue
		issue_lines = None
		if st.billing_mode != PER_BOOKING:
			# The payable for a Per Booking tenant accrued per document above.
			issue_lines = [(PLATFORM_FEES_EXPENSE, amount, 0), (DUE_TO_PLATFORM, 0, amount)]
			issue_day = getdate(st.issued_at or st.statement_date)
			if from_date <= issue_day <= to_date:
				rows.extend(
					_voucher(
						issue_day,
						st.name,
						_("Platform statement issued — {0}").format(st.period),
						issue_lines,
					)
				)
		pay_lines = [(DUE_TO_PLATFORM, amount, 0), (REMITTED, 0, amount)]
		if st.paid_on and from_date <= getdate(st.paid_on) <= to_date:
			rows.extend(
				_voucher(
					getdate(st.paid_on),
					st.name,
					_("Platform statement paid — {0}").format(st.period),
					pay_lines,
				)
			)
		if st.status == "Cancelled" and st.cancelled_at:
			cancel_day = getdate(st.cancelled_at)
			if from_date <= cancel_day <= to_date:
				if issue_lines:
					rows.extend(
						_voucher(
							cancel_day,
							st.name,
							_("Platform statement cancelled — {0}").format(st.period),
							_reverse(issue_lines),
							reversal=True,
						)
					)
				if st.paid_on:
					rows.extend(
						_voucher(
							cancel_day,
							st.name,
							_("Platform statement cancelled after payment — {0}").format(st.period),
							_reverse(pay_lines),
							reversal=True,
						)
					)
	return rows


def _payment_lines(inv, account) -> list[tuple]:
	total = flt(inv.total_amount, 2)
	fee = flt(inv.platform_fee, 2)
	vat = flt(inv.vat_amount, 2)
	revenue = flt(total - fee - vat, 2)
	lines = [(account, total, 0), (SERVICE_REVENUE, 0, revenue)]
	if vat:
		lines.append((OUTPUT_VAT, 0, vat))
	if fee:
		lines.append((DUE_TO_PLATFORM, 0, fee))
	return lines


# ---------------------------------------------------------------------------
# Platform's books, this tenant as the party
# ---------------------------------------------------------------------------


def _platform_rows(company, from_date, to_date) -> list[dict]:
	party = frappe.db.get_value("CBT Company", company, "company_name") or company
	platform_channels = {
		row.name: row
		for row in list_channels(scope=PLATFORM, enabled_only=False, fields=("name", "label", "account_label"))
	}
	rows = []
	for st in _statements(company):
		amount = flt(st.amount_due, 2)
		if not amount:
			continue
		revenue_account = REVENUE_BY_MODE.get(st.billing_mode, _("Platform Fee Revenue"))
		issue_lines = [(RECEIVABLE, amount, 0), (revenue_account, 0, amount)]
		issue_day = getdate(st.issued_at or st.statement_date)
		if from_date <= issue_day <= to_date:
			rows.extend(
				_voucher(
					issue_day,
					st.name,
					_("Statement issued — {0}").format(st.period),
					issue_lines,
					party=party,
				)
			)
		meta = platform_channels.get(st.payment_channel)
		received = (meta.account_label if meta else None) or UNALLOCATED
		pay_lines = [(received, amount, 0), (RECEIVABLE, 0, amount)]
		if st.paid_on and from_date <= getdate(st.paid_on) <= to_date:
			rows.extend(
				_voucher(
					getdate(st.paid_on),
					st.name,
					_("Payment received — {0}").format(st.period),
					pay_lines,
					party=party,
					channel=st.payment_channel,
				)
			)
		if st.status == "Cancelled" and st.cancelled_at:
			cancel_day = getdate(st.cancelled_at)
			if from_date <= cancel_day <= to_date:
				rows.extend(
					_voucher(
						cancel_day,
						st.name,
						_("Statement cancelled — {0}").format(st.period),
						_reverse(issue_lines),
						party=party,
						reversal=True,
					)
				)
				if st.paid_on:
					rows.extend(
						_voucher(
							cancel_day,
							st.name,
							_("Statement cancelled after payment — {0}").format(st.period),
							_reverse(pay_lines),
							party=party,
							channel=st.payment_channel,
							reversal=True,
						)
					)
	return rows


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _statements(company) -> list:
	return frappe.get_all(
		"CBT Platform Statement",
		filters={"company": company},
		fields=[
			"name",
			"period",
			"status",
			"billing_mode",
			"amount_due",
			"statement_date",
			"issued_at",
			"paid_on",
			"payment_channel",
			"cancelled_at",
		],
	)


def _voucher(day, voucher, description, lines, *, party=None, channel=None, reversal=False) -> list[dict]:
	out = []
	for index, (account, debit, credit) in enumerate(lines):
		out.append(
			{
				"posting_date": str(day),
				"voucher": voucher,
				"party": party,
				"description": description if index == 0 else None,
				"account": account,
				"debit": flt(debit, 2),
				"credit": flt(credit, 2),
				"payment_channel": channel if index == 0 else None,
				"is_reversal": 1 if reversal else 0,
				"_line": index,
			}
		)
	return out


def _reverse(lines) -> list[tuple]:
	return [(account, credit, debit) for account, debit, credit in lines]


def _period(filters) -> tuple:
	today = clock.now_dt().date()
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else today
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else today
	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))
	return from_date, to_date


def _columns(books):
	columns = [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "voucher", "label": _("Document"), "fieldtype": "Data", "width": 190},
	]
	if books == PLATFORM_BOOKS:
		columns.append({"fieldname": "party", "label": _("Party (tenant)"), "fieldtype": "Data", "width": 150})
	columns += [
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 280},
		{"fieldname": "account", "label": _("Account"), "fieldtype": "Data", "width": 210},
		{"fieldname": "debit", "label": _("Debit"), "fieldtype": "Currency", "options": "PHP", "width": 120},
		{"fieldname": "credit", "label": _("Credit"), "fieldtype": "Currency", "options": "PHP", "width": 120},
		{
			"fieldname": "payment_channel",
			"label": _("Channel"),
			"fieldtype": "Link",
			"options": "CBT Payment Channel",
			"width": 170,
		},
	]
	return columns
