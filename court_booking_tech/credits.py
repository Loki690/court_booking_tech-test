# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Store credit engine (Backlog B39, section-26 as-built 6-11).

A credit is a PAYMENT, never a discount: `total_amount` and the VAT footer are
untouched, and the money reports net the credit out of what was COLLECTED while
leaving revenue whole. Full reasoning in docs/sections/section-26.md.
"""

import frappe
from frappe import _
from frappe.utils import flt

from court_booking_tech import clock

ACTIVE = "Active"


def available_credit(company: str, customer: str | None) -> float:
	"""Spendable balance this customer holds at this company."""
	if not customer or not company:
		return 0.0
	return flt(
		frappe.db.sql(
			"""
			SELECT SUM(balance) FROM `tabCBT Customer Credit`
			WHERE company = %s AND customer = %s AND status = %s
			""",
			(company, customer, ACTIVE),
		)[0][0],
		2,
	)


def balances_by_company(customer: str | None) -> dict:
	"""Every company where this customer still holds spendable credit.

	Backlog B44. The per-company `available_credit` above cannot answer this:
	it needs the company already, so it can only be asked about companies the
	caller already knows — and the whole point of the portal line is a credit at
	a company the customer may not have booked at recently. Same predicate as
	`available_credit`, deliberately: `status = Active` is what excludes a Void
	credit, and `balance > 0` drops one that has been fully spent.
	"""
	if not customer:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT company, SUM(balance) AS balance
		FROM `tabCBT Customer Credit`
		WHERE customer = %s AND status = %s AND balance > 0
		GROUP BY company
		""",
		(customer, ACTIVE),
		as_dict=True,
	)
	return {row.company: flt(row.balance, 2) for row in rows if flt(row.balance) > 0}


def plan_spend(company: str, customer: str | None, amounts) -> list:
	"""What store credit WOULD settle, row by row, for a CART of these totals —
	the read-only twin of `take_credit`, in insert order.

	⚠ WHY THIS IS NOT `min(available_credit, cart_total)`, which is what the
	portal checkout printed until Backlog B45/B46 (2026-09-05). `take_credit`
	runs PER BOOKING and spends from exactly ONE credit document — the oldest
	Active one with anything left (`_next_credit`) — capped at THAT row's total.
	A second document is never reached for the same row.

	So with credits of ₱500 + ₱500 against a cart of three ₱200 rows: rows 1 and
	2 take ₱200 each from the first document, row 3 finds ₱100 left in it and
	takes ₱100 — ₱500 spent, not the ₱600 a `min()` promises. The screen said
	one number and the bookings did another, on a money surface, which is the
	exact defect S18/B4 exists to make impossible.

	Both cart quotes (the customer's and the desk's) call this, so the figure on
	either screen is the figure the inserts will produce. It is a PLAN, not a
	reservation: nothing is locked here, and a concurrent spend can still move
	it — the insert path's `FOR UPDATE` is what settles the money, and it stays
	the only authority.
	"""
	amounts = [flt(amount, 2) for amount in amounts]
	if not company or not customer:
		return [0.0 for _amount in amounts]

	# Same predicate and same ORDER as `_next_credit`, deliberately — a planner
	# that walked a different order would be a second rule.
	rows = frappe.db.sql(
		"""
		SELECT balance FROM `tabCBT Customer Credit`
		WHERE company = %s AND customer = %s AND status = %s AND balance > 0
		ORDER BY creation ASC
		""",
		(company, customer, ACTIVE),
		as_dict=True,
	)
	balances = [flt(row.balance, 2) for row in rows]

	planned = []
	for amount in amounts:
		spend = 0.0
		if amount > 0:
			for index, balance in enumerate(balances):
				if balance <= 0:
					continue
				spend = flt(min(balance, amount), 2)
				balances[index] = flt(balance - spend, 2)
				break  # ONE document per booking — take_credit stops here too.
		planned.append(spend)
	return planned


def _next_credit(company: str, customer: str) -> str | None:
	"""The oldest Active credit with something left, LOCKED for update.

	⚠ The lock is the whole point: two concurrent inserts for one customer would
	otherwise both read the same balance and both spend it (ducky STOP,
	2026-09-05). ONE document per booking — multiple credits are simply used one
	booking at a time.
	"""
	rows = frappe.db.sql(
		"""
		SELECT name FROM `tabCBT Customer Credit`
		WHERE company = %s AND customer = %s AND status = %s AND balance > 0
		ORDER BY creation ASC LIMIT 1
		FOR UPDATE
		""",
		(company, customer, ACTIVE),
	)
	return rows[0][0] if rows else None


def take_credit(booking, apply_credit: bool):
	"""Settle part or all of `booking` with store credit, on the INSERT path.

	⚠ Called on EVERY insert, not only when applying: the fields are read_only
	on the form but read_only is a UI property, so a REST insert could otherwise
	post `credit_applied` for money nobody ever credited (ducky STOP,
	2026-09-05). Not applying means writing ZERO.
	"""
	booking.credit_applied = 0
	booking.credit_document = None
	booking.credit_restored_at = None

	if not apply_credit or not booking.customer:
		return
	if booking.payment_method == "Free" or flt(booking.total_amount) <= 0:
		return

	credit_name = _next_credit(booking.company, booking.customer)
	if not credit_name:
		return
	credit = frappe.get_doc("CBT Customer Credit", credit_name)
	spend = min(flt(credit.balance, 2), flt(booking.total_amount, 2))
	if spend <= 0:
		return

	credit.balance = flt(credit.balance - spend, 2)
	credit.save(ignore_permissions=True)
	booking.credit_applied = spend
	booking.credit_document = credit.name


def restore_credit(booking_name: str) -> float:
	"""Give a dead booking's credit back. Idempotent through
	`credit_restored_at`; a NO SHOW is deliberately NOT restored — the money is
	forfeit, which is what a no-show costs (section-26 as-built 10)."""
	row = frappe.db.get_value(
		"CBT Court Booking",
		booking_name,
		["credit_applied", "credit_document", "credit_restored_at"],
		as_dict=True,
	)
	if not row or not row.credit_document or flt(row.credit_applied) <= 0:
		return 0.0
	if row.credit_restored_at:
		return 0.0

	credit = frappe.get_doc("CBT Customer Credit", row.credit_document)
	credit.balance = min(
		flt(credit.balance + flt(row.credit_applied), 2), flt(credit.amount, 2)
	)
	if credit.status != "Void":
		credit.status = ACTIVE
	credit.save(ignore_permissions=True)
	# The booking KEEPS credit_applied: it is the historical record the money
	# reports net the refund against. Only the live balance moves.
	frappe.db.set_value(
		"CBT Court Booking",
		booking_name,
		"credit_restored_at",
		clock.now_dt(),
		update_modified=False,
	)
	return flt(row.credit_applied, 2)


def _mint(
	company: str,
	customer: str,
	amount,
	reason: str,
	*,
	source_booking=None,
	source_invoice=None,
) -> str:
	"""The one writer of a CBT Customer Credit. `source_booking` is optional —
	an open-play participant has an invoice but no booking (B53)."""
	credit = frappe.get_doc(
		{
			"doctype": "CBT Customer Credit",
			"company": company,
			"customer": customer,
			"amount": amount,
			"balance": amount,
			"status": ACTIVE,
			"source_booking": source_booking,
			"source_invoice": source_invoice,
			"reason": reason,
		}
	)
	credit.insert(ignore_permissions=True)
	return credit.name


def issue_credit(booking, reason: str) -> str:
	"""Mint store credit for a refunded booking instead of paying cash out."""
	if not booking.customer:
		frappe.throw(
			_(
				"A walk-in has no account to hold store credit — refund them in cash."
			)
		)
	amount = flt(booking.total_amount, 2) - flt(booking.credit_applied, 2)
	if amount <= 0:
		frappe.throw(_("There is nothing to credit on this booking."))

	return _mint(
		booking.company,
		booking.customer,
		amount,
		reason,
		source_booking=booking.name,
		source_invoice=booking.billing_doc,
	)


def issue_participant_credit(company: str, row, reason: str) -> str | None:
	"""B53: store credit for ONE open-play participant whose PAID document was
	just cancelled.

	Unlike the booking path this REPORTS rather than throws: a walk-in sitting in
	the roster must never stop a flooded session from being cancelled. The
	`source_invoice` check is the belt — `cancel_session`'s status guard is the
	braces — so a second pass can never mint twice.
	"""
	if not row.customer or not row.billing_doc:
		return None
	if frappe.db.exists("CBT Customer Credit", {"source_invoice": row.billing_doc}):
		return None
	total, applied = frappe.db.get_value(
		"CBT Booking Invoice", row.billing_doc, ["total_amount", "credit_applied"]
	) or (0, 0)
	amount = flt(total, 2) - flt(applied, 2)
	if amount <= 0:
		return None
	return _mint(company, row.customer, amount, reason, source_invoice=row.billing_doc)
