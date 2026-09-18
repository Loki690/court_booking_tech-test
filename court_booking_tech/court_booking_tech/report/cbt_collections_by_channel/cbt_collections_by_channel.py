# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Collections by Channel (Backlog B29) — the tenant's income split by WHERE
it landed: Cash, GCash, each bank. One row per channel per day.

The user's words: *"so they can properly see which are cash, which are GCASH,
which are BANK TRANSFER. WE WILL NEED THIS FOR THE RECONCILIATION PART."*

ATTRIBUTION IS BY PAYMENT DAY, deliberately unlike CBT Company Revenue (service
date): a GCash or bank statement is reconciled against the day the money
ARRIVED, and that is `verified_at` — the desk's confirmation for a transfer,
the moment of sale for cash. A paid document later cancelled (refunded) shows
again as a REFUND on its cancellation day, on the same channel, so the
day-by-day view matches what went out as well as what came in.

Money columns: `collected` is what actually MOVED through the channel — the
invoice total minus anything settled with store credit (Backlog B39), which is
the number a bank statement is reconciled against; `credit_applied` is that
credit, shown beside it so the line still adds up. `court_revenue` /
`open_play_revenue` are the tenant's own share (total minus the platform booking
fee, B27) and are deliberately NOT netted — the tenant earned that money however
it was settled; `platform_fees` is the pass-through the tenant holds for the
platform; `vat_amount` is the output VAT inside the tenant's share (B29 ruling:
VAT on the court share only). `refunded` is netted the same way as `collected`:
the credit half goes back to the credit document, not out of the drawer. A row
with NO channel (a
document that pre-dates channels and could not be backfilled) is shown as
"(no channel)" rather than dropped — a reconciliation that silently omits
money is worse than one with an unallocated line.

Scoping: resolve_report_company — tenants are forced onto their own company.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from court_booking_tech import clock
from court_booking_tech.billing import PAID
from court_booking_tech.payment_channels import list_channels
from court_booking_tech.reports import resolve_report_company

NO_CHANNEL = "(no channel)"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = resolve_report_company(filters.get("company"))
	from_date, to_date = _period(filters)
	channels = {
		row.name: row
		for row in list_channels(
			company,
			enabled_only=False,
			fields=("name", "label", "kind", "account_label", "enabled"),
		)
	}

	buckets = {}

	def bucket(channel, day):
		key = (str(day), channel or "")
		if key not in buckets:
			meta = channels.get(channel)
			buckets[key] = {
				"day": str(day),
				"payment_channel": channel or None,
				"channel_label": meta.label if meta else NO_CHANNEL,
				"kind": meta.kind if meta else None,
				"account_label": meta.account_label if meta else None,
				"paid_count": 0,
				"collected": 0.0,
				"credit_applied": 0.0,
				"court_revenue": 0.0,
				"open_play_revenue": 0.0,
				"platform_fees": 0.0,
				"vat_amount": 0.0,
				"refund_count": 0,
				"refunded": 0.0,
			}
		return buckets[key]

	# A document that carries verification stamps WAS paid on that day — even if
	# it has since been Cancelled (refunded): the money came in on its verified
	# day and went out on its cancelled day, and both rows belong here.
	params = {"company": company, "paid": PAID, "from_date": from_date, "to_date": to_date}
	for row in frappe.db.sql(
		"""
		SELECT i.payment_channel AS channel, DATE(i.verified_at) AS day,
		       COUNT(*) AS cnt,
		       SUM(i.total_amount - COALESCE(i.credit_applied, 0)) AS collected,
		       SUM(COALESCE(i.credit_applied, 0)) AS credit,
		       SUM(CASE WHEN COALESCE(i.booking, '') != ''
		                THEN i.total_amount - COALESCE(i.platform_fee, 0) ELSE 0 END) AS court,
		       SUM(CASE WHEN COALESCE(i.booking, '') = ''
		                THEN i.total_amount - COALESCE(i.platform_fee, 0) ELSE 0 END) AS open_play,
		       SUM(COALESCE(i.platform_fee, 0)) AS fees,
		       SUM(COALESCE(i.vat_amount, 0)) AS vat
		FROM `tabCBT Booking Invoice` i
		WHERE i.company = %(company)s AND i.status IN (%(paid)s, 'Cancelled')
		  AND i.verified_at IS NOT NULL
		  AND DATE(i.verified_at) BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY i.payment_channel, DATE(i.verified_at)
		""",
		params,
		as_dict=True,
	):
		entry = bucket(row.channel, row.day)
		entry["paid_count"] += int(row.cnt or 0)
		entry["collected"] += flt(row.collected, 2)
		entry["credit_applied"] += flt(row.credit, 2)
		entry["court_revenue"] += flt(row.court, 2)
		entry["open_play_revenue"] += flt(row.open_play, 2)
		entry["platform_fees"] += flt(row.fees, 2)
		entry["vat_amount"] += flt(row.vat, 2)

	# Refunds: a document that WAS paid (it carries verification stamps) and is
	# now Cancelled — posted on the day it was cancelled.
	for row in frappe.db.sql(
		"""
		SELECT i.payment_channel AS channel, DATE(i.cancelled_at) AS day,
		       COUNT(*) AS cnt,
		       SUM(i.total_amount - COALESCE(i.credit_applied, 0)) AS refunded
		FROM `tabCBT Booking Invoice` i
		WHERE i.company = %(company)s AND i.status = 'Cancelled'
		  AND i.verified_at IS NOT NULL AND i.cancelled_at IS NOT NULL
		  AND DATE(i.cancelled_at) BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY i.payment_channel, DATE(i.cancelled_at)
		""",
		params,
		as_dict=True,
	):
		entry = bucket(row.channel, row.day)
		entry["refund_count"] += int(row.cnt or 0)
		entry["refunded"] += flt(row.refunded, 2)

	data = []
	total = {
		"day": None,
		"channel_label": _("Total"),
		"paid_count": 0,
		"collected": 0.0,
		"credit_applied": 0.0,
		"court_revenue": 0.0,
		"open_play_revenue": 0.0,
		"platform_fees": 0.0,
		"vat_amount": 0.0,
		"refund_count": 0,
		"refunded": 0.0,
		"is_total_row": 1,
	}
	for entry in sorted(buckets.values(), key=lambda e: (e["day"], e["channel_label"])):
		for field in ("collected", "credit_applied", "court_revenue", "open_play_revenue", "platform_fees", "vat_amount", "refunded"):
			entry[field] = flt(entry[field], 2)
			total[field] = flt(total[field] + entry[field], 2)
		total["paid_count"] += entry["paid_count"]
		total["refund_count"] += entry["refund_count"]
		data.append(entry)
	if data:
		data.append(total)
	return _columns(), data


def _period(filters) -> tuple:
	today = clock.now_dt().date()
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else today
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else today
	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))
	return from_date, to_date


def _money(fieldname, label, width=130):
	return {
		"fieldname": fieldname,
		"label": _(label),
		"fieldtype": "Currency",
		"options": "PHP",
		"width": width,
	}


def _columns():
	return [
		{"fieldname": "day", "label": _("Paid On"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "payment_channel",
			"label": _("Channel"),
			"fieldtype": "Link",
			"options": "CBT Payment Channel",
			"width": 170,
		},
		{"fieldname": "channel_label", "label": _("Channel Label"), "fieldtype": "Data", "width": 120},
		{"fieldname": "kind", "label": _("Kind"), "fieldtype": "Data", "width": 80},
		{"fieldname": "account_label", "label": _("Ledger Account"), "fieldtype": "Data", "width": 170},
		{"fieldname": "paid_count", "label": _("Payments"), "fieldtype": "Int", "width": 90},
		_money("collected", "Collected", 130),
		_money("credit_applied", "Paid by Credit", 130),
		_money("court_revenue", "Court Revenue", 130),
		_money("open_play_revenue", "Open Play Revenue", 150),
		_money("platform_fees", "Booking Fees Held", 140),
		_money("vat_amount", "Output VAT", 110),
		{"fieldname": "refund_count", "label": _("Refunds"), "fieldtype": "Int", "width": 80},
		_money("refunded", "Refunded", 120),
	]
