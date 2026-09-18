# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Billing engine (section-6, PLAN §6) — creates and maintains the CBT Booking
Invoice for every booking.

Contract:
- ONE invoice per CART (Backlog B36): a booking made in a multi-booking
  checkout shares its group's document; a booking with no `booking_group` is
  1:1 as it always was. Created at booking creation (after_insert), linked back
  via booking.billing_doc.
- Invoice STATUS is DERIVED from the booking (Reserved → Unpaid;
  Confirmed/Extended/Completed → Paid & Verified; Cancelled/Expired →
  Cancelled) — which makes retro-confirm (Expired → Completed) flip the SAME
  document Cancelled → Paid & Verified with its number retained.
- AMOUNTS re-derive from the booking on every sync: staff may edit
  rate/discount after creation (S4 as-built 3) and the print must never show
  a stale total. Only vat_mode / vat_percent / doc_title are SNAPSHOTTED at
  creation — a later company VAT-status change must not rewrite history.
- The doc_events in hooks.py cover every doc.save path; the frappe.db.set_value
  status flips (expiry sweep, proof rejection, dead-hold upload) call
  sync_invoice_for_booking explicitly — set_value fires no doc_events.
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

from court_booking_tech import clock
from court_booking_tech.tenancy import require_company_access, require_company_admin
from court_booking_tech.timeutil import label_date, label_short

PAID = "Paid & Verified"
DEAD_STATUSES = ("Cancelled", "Expired")


# ---------------------------------------------------------------------------
# Refunds (section-26). Cancelling a PAID document is money OUT: a Company
# Admin's decision, with a reason a person can read later on the ledger line
# and the receipt. The sentences are the contract the UI and the tests share.
# ---------------------------------------------------------------------------


def refund_admin_sentence(what: str = "booking") -> str:
	if what == "session":
		return _(
			"Only a Company Admin can cancel a session with paid players — it is a refund."
		)
	return _("Only a Company Admin can cancel a paid booking — it is a refund.")


def refund_reason_sentence(what: str = "booking") -> str:
	if what == "session":
		return _("A reason is required to refund paid players.")
	return _("A reason is required to cancel a paid booking.")


CAUSE_CUSTOMER = "Customer request"
CAUSE_FACILITY = "Facility fault"
CAUSES = (CAUSE_CUSTOMER, CAUSE_FACILITY)

RESCHEDULE_ONLY = "Reschedule only"
STAFF_AND_ABOVE = "Staff and above"


def refund_policy(company: str) -> str:
	return frappe.db.get_value("CBT Company", company, "refund_policy") or "Refund"


def facility_fault_cancel_by(company: str) -> str:
	return (
		frappe.db.get_value("CBT Company", company, "facility_fault_cancel_by")
		or "Company Admin only"
	)


def no_cash_refund_sentence(company: str, what: str = "booking") -> str:
	"""What the facility OFFERS, never what it cannot do — a Company Admin can
	always declare a facility fault, so a 'cannot refund' claim would be false."""
	name = frappe.db.get_value("CBT Company", company, "company_name") or company
	subject = _("session") if what == "session" else _("booking")
	return _(
		"{0} moves a paid {1} to another time, or issues store credit, instead of"
		" refunding cash."
	).format(name, subject)


def facility_fault_seat_sentence() -> str:
	return _("Only a Company Admin can record a cancellation as the facility's own fault.")


def require_refund(
	company: str,
	reason,
	*,
	what: str = "booking",
	cause: str | None = None,
	as_credit: bool = False,
) -> tuple[str, str]:
	"""The refund gate: seat, then a non-empty reason, then the company's CASH
	policy — for EVERY seat, platform included (ruling 2026-08-27: a reversal
	nobody can explain is the thing being fixed). Returns (reason, cause).

	B53: store credit is not a refund, so `as_credit` passes the cash gate; a
	`Facility fault` cause returns cash whatever the policy says, and who may
	declare one is the company's `facility_fault_cancel_by` seat setting.
	"""
	cause = (cause or CAUSE_CUSTOMER).strip()
	if cause not in CAUSES:
		# Client-supplied text landing on a financial record: never stamp it unread.
		frappe.throw(
			_("{0} is not a cancellation cause.").format(cause), frappe.ValidationError
		)

	if cause == CAUSE_FACILITY:
		if facility_fault_cancel_by(company) == STAFF_AND_ABOVE:
			require_company_access(company, allow_suspended=True)
		else:
			require_company_admin(company, message=facility_fault_seat_sentence())
	else:
		require_company_admin(company, message=refund_admin_sentence(what))

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(refund_reason_sentence(what), frappe.ValidationError)

	if (
		cause == CAUSE_CUSTOMER
		and not as_credit
		and refund_policy(company) == RESCHEDULE_ONLY
	):
		frappe.throw(no_cash_refund_sentence(company, what), frappe.ValidationError)

	return reason, cause


def _apply_refund(invoice, was_paid: bool, reason, cause=None, as_credit=False):
	"""Stamp the reason, the seat, the CAUSE and whether the money left as store
	credit on the flip PAID → Cancelled — the same save that stamps
	`cancelled_at`, so the ledger's reversal line and the receipt read them the
	moment the money moves. Nothing is touched on any other transition (the
	invoice controller clears the stamps if the document ever leaves Cancelled).

	B53: without `refund_as_credit` a money report cannot tell a cash refund from
	store credit, and a Reschedule only venue would appear to pay out cash.
	"""
	if was_paid and invoice.status == "Cancelled" and reason:
		invoice.refund_reason = reason
		invoice.refunded_by = frappe.session.user
		invoice.refund_cause = cause or CAUSE_CUSTOMER
		invoice.refund_as_credit = 1 if as_credit else 0

# Section-16: "No Show" maps to PAID, and the entry is load-bearing well beyond
# the sweep. Any ordinary doc.save() on a released booking — a staff member
# fixing a typo in the notes — routes through on_booking_update, and a missing
# key would fall to the .get(..., "Unpaid") default and silently DEMOTE a paid
# statement. The mapping is also unreachable from a state that never paid: the
# machine allows only Confirmed -> No Show, and Confirmed is already PAID.
BOOKING_TO_INVOICE_STATUS = {
	"Reserved": "Unpaid",
	"Confirmed": PAID,
	"Extended": PAID,
	"Completed": PAID,
	"Cancelled": "Cancelled",
	"Expired": "Cancelled",
	"No Show": PAID,
}


def compute_vat_breakdown(total, vat_mode, vat_percent) -> dict:
	"""Pure VAT-INCLUSIVE math (PLAN §6): vatable = total ÷ (1 + VAT%).

	vat_amount is computed as the DIFFERENCE, never total × rate, so
	vatable + vat == total EXACTLY after ₱-rounding — a 1-centavo drift on a
	printed statement is a real client complaint. NON-VAT documents carry no
	VAT figures at all.
	"""
	total = flt(total, 2)
	if vat_mode != "VAT":
		return {"vatable_amount": None, "vat_amount": None}
	vatable = flt(total / (1 + flt(vat_percent) / 100.0), 2)
	return {"vatable_amount": vatable, "vat_amount": flt(total - vatable, 2)}


def _billable_rows(booking) -> list:
	"""The bookings this document bills, date → court → start. Cancelled rows
	leave it; Expired rows stay (section-6 as-built 13)."""
	if not booking.booking_group:
		return [booking]
	rows = [
		frappe.get_doc("CBT Court Booking", row.name)
		for row in frappe.get_all(
			"CBT Court Booking",
			filters={"booking_group": booking.booking_group},
			fields=["name"],
			order_by="booking_date asc, court asc, start_time asc",
			ignore_permissions=True,
		)
	]
	rows = [booking if row.name == booking.name else row for row in rows]
	live = [row for row in rows if row.booking_status != "Cancelled"]
	return live or rows


def create_invoice_for_booking(booking) -> str:
	"""Create or join the booking's invoice (idempotent — returns the existing
	link if one is already attached). Runs under the caller's insert
	transaction, so a cart's rows join the document one insert at a time."""
	if isinstance(booking, str):
		booking = frappe.get_doc("CBT Court Booking", booking)
	if booking.billing_doc:
		return booking.billing_doc

	shared = _group_invoice(booking)
	if shared:
		_link_booking_to_invoice(booking, shared)
		sync_invoice_for_booking(booking.name)
		return shared

	company = frappe.get_doc("CBT Company", booking.company)
	invoice = frappe.get_doc(
		{
			"doctype": "CBT Booking Invoice",
			"company": booking.company,
			"branch": booking.branch,
			"booking": booking.name,
			"booking_group": booking.booking_group or None,
			# Walk-in (section-13): `customer` is empty and customer_name IS
			# the identity. The controller guarantees one of the two is set, so
			# no User fallback is needed here — reading `User` with a None name
			# would just yield None anyway.
			"customer": booking.customer,
			"customer_name": booking.customer_name,
			"posting_date": clock.now_dt().date(),
			# The creation-time snapshot (never re-read; PLAN §6). vat_percent
			# comes from the accessor — the raw field still carries the hidden
			# default (12) on NON-VAT companies (S2 as-built 9).
			"vat_mode": company.vat_registration,
			"vat_percent": company.get_vat_percent(),
			"doc_title": company.billing_doc_title or "BILLING STATEMENT",
		}
	)
	rows = _billable_rows(booking)
	_apply_booking_amounts(invoice, rows)
	_apply_booking_status(invoice, rows)
	_apply_channel(invoice, booking)
	invoice.flags.via_billing_engine = True
	invoice.insert(ignore_permissions=True)
	_link_booking_to_invoice(booking, invoice.name)
	return invoice.name


def _group_invoice(booking) -> str | None:
	"""The document this booking's cart is already billing on, if any."""
	if not booking.booking_group:
		return None
	return frappe.db.get_value(
		"CBT Booking Invoice", {"booking_group": booking.booking_group}, "name"
	)


def _link_booking_to_invoice(booking, invoice_name: str):
	# update_modified=False or the caller's fresh doc goes stale and 417s (S6 as-built 6).
	frappe.db.set_value(
		"CBT Court Booking",
		booking.name,
		"billing_doc",
		invoice_name,
		update_modified=False,
	)
	booking.billing_doc = invoice_name


def sync_invoice_for_booking(
	booking_name: str, *, refund_reason=None, refund_cause=None, refund_as_credit=False
) -> str | None:
	"""Re-derive the invoice's status + amounts from its booking. Idempotent;
	no-op when the booking has no invoice (pre-section-6 rows before the seed
	backfill runs). `refund_reason` rides the cancelling save (section-26) —
	one write, so there is no window in which the document is Cancelled and
	the reason is not yet on it. B53 rides the CAUSE and the store-credit flag
	on that same save, for the same reason."""
	invoice_name = frappe.db.get_value(
		"CBT Court Booking", booking_name, "billing_doc"
	)
	if not invoice_name:
		return None
	booking = frappe.get_doc("CBT Court Booking", booking_name)
	invoice = frappe.get_doc("CBT Booking Invoice", invoice_name)

	if refund_reason and invoice.booking_group and invoice.status == PAID:
		carved = _carve_out_refund(
			invoice,
			booking,
			refund_reason,
			refund_cause=refund_cause,
			refund_as_credit=refund_as_credit,
		)
		if carved:
			return carved

	was_paid = invoice.status == PAID
	rows = _billable_rows(booking)
	_apply_booking_amounts(invoice, rows)
	_apply_booking_identity(invoice, booking)
	_apply_booking_status(invoice, rows)
	_apply_channel(invoice, booking)
	_apply_refund(invoice, was_paid, refund_reason, refund_cause, refund_as_credit)
	invoice.flags.via_billing_engine = True
	# ignore_permissions is load-bearing: cancelled_at / refund_reason /
	# refunded_by are permlevel 1 (System Manager write), and a Company Admin's
	# cancel must land them — frappe skips the permlevel reset on this flag.
	invoice.save(ignore_permissions=True)
	return invoice.name


def _carve_out_refund(
	invoice, booking, reason: str, *, refund_cause=None, refund_as_credit=False
) -> str | None:
	"""Split `booking` onto its own Cancelled document (section-6 as-built 14).
	None when nothing survives — there is no cart left to split from."""
	survivors = [
		row
		for row in _billable_rows(booking)
		if row.name != booking.name and row.booking_status not in DEAD_STATUSES
	]
	if not survivors:
		return None

	carved = frappe.get_doc(
		{
			"doctype": "CBT Booking Invoice",
			"company": invoice.company,
			"branch": booking.branch,
			"booking": booking.name,
			"customer": invoice.customer,
			"customer_name": invoice.customer_name,
			"posting_date": invoice.posting_date,
			"vat_mode": invoice.vat_mode,
			"vat_percent": invoice.vat_percent,
			"doc_title": invoice.doc_title,
			"verified_by": invoice.verified_by,
			"verified_at": invoice.verified_at,
			"payment_channel": invoice.payment_channel,
			"status": "Cancelled",
			"refund_reason": reason,
			"refunded_by": frappe.session.user,
			"refund_cause": refund_cause or CAUSE_CUSTOMER,
			"refund_as_credit": 1 if refund_as_credit else 0,
		}
	)
	_apply_booking_amounts(carved, [booking])
	carved.flags.via_billing_engine = True
	carved.insert(ignore_permissions=True)
	_link_booking_to_invoice(booking, carved.name)

	_apply_booking_amounts(invoice, survivors)
	_apply_booking_status(invoice, survivors)
	invoice.flags.via_billing_engine = True
	invoice.save(ignore_permissions=True)
	return carved.name


def _apply_channel(invoice, source):
	"""Backlog B29: the invoice carries the channel its booking / participant
	was paid through — RE-COPIED on every sync, so a staff correction at
	verification reaches the document the reports read. `get` rather than an
	attribute: a participant is a child row and a booking is a document, and
	both spell the field the same way."""
	invoice.payment_channel = source.get("payment_channel") or None


def _apply_booking_identity(invoice, booking):
	"""Refresh the printed name — WALK-INS ONLY (section-13).

	A walk-in name is free text typed at a busy desk, so a typo fix must reach
	the statement (S6 doctrine: the print never shows stale data).

	An ACCOUNT booking's name is deliberately NOT re-derived here. It is
	snapshotted at creation like vat_mode / vat_percent / doc_title: the
	booking's own customer_name re-derives from User.full_name on every save,
	so syncing it through would let a customer who renames their account
	rewrite the printed name on an already-issued, PAID & VERIFIED document.
	"""
	if not booking.customer:
		invoice.customer_name = booking.customer_name


def _booking_lines(booking) -> list[dict]:
	"""One line per rate SEGMENT (section-14); a flat-rate booking keeps its
	single line."""
	court_name = (
		frappe.db.get_value("CBT Court", booking.court, "court_name") or booking.court
	)
	day = label_date(booking.booking_date)
	segments = booking.get("rate_segments")
	if not segments:
		qty = flt(booking.duration_hours, 2)
		rate = flt(booking.hourly_rate, 2)
		return [
			{
				"description": _("Court rental — {0}, {1} {2} – {3}").format(
					court_name,
					day,
					label_short(booking.start_time),
					label_short(booking.end_time),
				),
				"qty": qty,
				"rate": rate,
				"amount": flt(qty * rate, 2),
			}
		]

	lines = []
	for segment in segments:
		description = _("Court rental — {0}, {1} {2} – {3} @ {4}/hr").format(
			court_name,
			day,
			label_short(segment.start_time),
			label_short(segment.end_time),
			fmt_money(flt(segment.hourly_rate), 2, "PHP"),
		)
		if segment.label:
			description = f"{description} · {segment.label}"
		lines.append(
			{
				"description": description,
				"qty": flt(segment.hours, 2),
				"rate": flt(segment.hourly_rate, 2),
				"amount": flt(segment.amount, 2),
			}
		)
	return lines


def _apply_booking_amounts(invoice, bookings):
	"""Rebuild the invoice's lines and money from the booking(s) it bills — one
	booking, or every row of a cart (Backlog B36, section-6 as-built 13)."""
	if not isinstance(bookings, (list, tuple)):
		bookings = [bookings]
	invoice.set("items", [])
	for booking in bookings:
		for line in _booking_lines(booking):
			invoice.append("items", line)

	percents = {flt(booking.discount_percent) for booking in bookings}
	booking = bookings[0]
	invoice.subtotal = flt(sum(flt(row.amount, 2) for row in invoice.items), 2)
	invoice.discount_percent = percents.pop() if len(percents) == 1 else 0
	# The booking's total is authoritative (what the customer pays); the
	# discount amount is DERIVED so subtotal − discount + fee == total exactly.
	# Backlog B27: the platform booking fee is NOT an item — it is the
	# platform's line, printed between the discount and the VAT footer, and it
	# is never discounted. The one VAT footer covers the whole total (ruling).
	invoice.total_amount = flt(sum(flt(row.total_amount) for row in bookings), 2)
	invoice.platform_fee = flt(sum(flt(row.platform_fee) for row in bookings), 2)
	# Backlog B49: what the continuations would have carried — printed as a
	# discount under a fee line showing the as-if figure. Never in the total.
	invoice.continuous_discount = flt(
		sum(flt(row.get("platform_fee_waived")) for row in bookings), 2
	)
	# Backlog B39: a PAYMENT, not a discount — it never touches total or VAT.
	invoice.credit_applied = flt(
		sum(flt(row.get("credit_applied")) for row in bookings), 2
	)
	invoice.discount_amount = flt(
		invoice.subtotal - (invoice.total_amount - invoice.platform_fee), 2
	)
	# Backlog B29 ruling (2026-08-27, replacing B27's "one footer over court +
	# fee"): the tenant's VAT is on the COURT SHARE only — the booking fee is the
	# platform's non-VAT revenue passing through (docs/collection_flow_v1.md:
	# 100 → 89.29 + 10.71, the 15 outside). One VAT footer still, over
	# total − fee; the fee line prints outside it.
	breakdown = compute_vat_breakdown(
		invoice.total_amount - invoice.platform_fee, invoice.vat_mode, invoice.vat_percent
	)
	invoice.vatable_amount = breakdown["vatable_amount"]
	invoice.vat_amount = breakdown["vat_amount"]


def _apply_booking_status(invoice, bookings):
	"""Derive the invoice's status from the booking(s). verified_by/at are
	RE-COPIED on every sync — see section-6 as-built 2 and 13."""
	if not isinstance(bookings, (list, tuple)):
		bookings = [bookings]
	statuses = [
		BOOKING_TO_INVOICE_STATUS.get(row.booking_status, "Unpaid") for row in bookings
	]
	if all(status == "Cancelled" for status in statuses):
		invoice.status = "Cancelled"
	elif "Unpaid" in statuses:
		invoice.status = "Unpaid"
	else:
		invoice.status = PAID
	if invoice.status == PAID:
		paid = next(row for row, s in zip(bookings, statuses) if s == PAID)
		invoice.verified_by = paid.confirmed_by
		invoice.verified_at = paid.confirmed_at
	# On Cancelled any earlier verification stamps are kept — audit trail.


# ---------------------------------------------------------------------------
# Open play participants (section-10)
# ---------------------------------------------------------------------------
# The S6 hand-off: create_invoice_for_booking is booking-coupled, so open play
# gets its own creation function rather than a booking-shaped fake. Same rules
# apply — engine flag, per-company numbering, VAT/title snapshotted once at
# creation — but the document bills a PARTICIPANT ROW (participant_ref) and
# carries no booking.

PARTICIPANT_STATUS_TO_INVOICE = {"Unpaid": "Unpaid", "Paid": PAID}


def create_invoice_for_participant(session, participant_row) -> str:
	"""One billing document per open play participant (PLAN §6). Idempotent —
	returns the existing link if the row already has one."""
	if participant_row.billing_doc:
		return participant_row.billing_doc

	company = frappe.get_doc("CBT Company", session.company)
	invoice = frappe.get_doc(
		{
			"doctype": "CBT Booking Invoice",
			"company": session.company,
			"branch": session.branch,
			# `booking` stays EMPTY: this document bills a session participant.
			"participant_ref": participant_row.name,
			# Walk-in (section-20): `customer` is empty and customer_name IS the
			# identity. The User fallback is guarded rather than relied on to
			# miss — `get_value("User", None, ...)` does return None
			# (`database.get_values` falls straight through when filters is None
			# and the doctype is not Single), but a money document is the last
			# place to lean on that.
			"customer": participant_row.customer,
			"customer_name": participant_row.customer_name
			or (
				frappe.db.get_value("User", participant_row.customer, "full_name")
				if participant_row.customer
				else None
			),
			"posting_date": clock.now_dt().date(),
			"vat_mode": company.vat_registration,
			"vat_percent": company.get_vat_percent(),
			"doc_title": company.billing_doc_title or "BILLING STATEMENT",
		}
	)
	_apply_participant_amounts(invoice, session, participant_row)
	_apply_participant_status(invoice, participant_row)
	_apply_channel(invoice, participant_row)
	invoice.flags.via_billing_engine = True
	invoice.insert(ignore_permissions=True)
	# The caller owns the session document and saves it — writing the link into
	# the in-memory row keeps this inside their transaction.
	participant_row.billing_doc = invoice.name
	return invoice.name


def sync_invoice_for_participant(
	participant_row,
	*,
	status_override=None,
	verified_by=None,
	verified_at=None,
	refund_reason=None,
	refund_cause=None,
	refund_as_credit=False,
) -> str | None:
	"""Re-derive a participant's document from their payment status, or force
	`status_override` ("Cancelled" when they leave unpaid, or when the session
	is cancelled with its billing). Amounts are NOT re-derived: a participant's
	fee is frozen when they are added. `refund_reason` lands only on a PAID
	document being cancelled (section-26)."""
	if not participant_row.billing_doc:
		return None
	invoice = frappe.get_doc("CBT Booking Invoice", participant_row.billing_doc)
	was_paid = invoice.status == PAID
	if status_override:
		invoice.status = status_override
	else:
		_apply_participant_status(
			invoice, participant_row, verified_by=verified_by, verified_at=verified_at
		)
	_apply_channel(invoice, participant_row)
	_apply_refund(invoice, was_paid, refund_reason, refund_cause, refund_as_credit)
	invoice.flags.via_billing_engine = True
	invoice.save(ignore_permissions=True)
	return invoice.name


def _apply_participant_amounts(invoice, session, row):
	description = _("Open Play — {0}, {1} {2} – {3}").format(
		session.title,
		label_date(session.session_date),
		label_short(session.start_time),
		label_short(session.end_time),
	)
	fee = flt(row.fee, 2)
	invoice.set("items", [])
	invoice.append(
		"items",
		{"description": description, "qty": 1, "rate": fee, "amount": fee},
	)
	invoice.subtotal = fee
	invoice.discount_percent = flt(row.discount_percent)
	# Backlog B27: the per-participant platform add-on, on top of the session
	# fee after discount — same line, same footer rule as a court booking.
	invoice.platform_fee = flt(row.get("platform_fee"), 2)
	session_total = flt(fee * (1 - flt(row.discount_percent) / 100.0), 2)
	invoice.total_amount = flt(session_total + invoice.platform_fee, 2)
	invoice.discount_amount = flt(invoice.subtotal - session_total, 2)
	# B29 ruling: VAT on the session share only — the add-on is outside it.
	breakdown = compute_vat_breakdown(
		session_total, invoice.vat_mode, invoice.vat_percent
	)
	invoice.vatable_amount = breakdown["vatable_amount"]
	invoice.vat_amount = breakdown["vat_amount"]


def _apply_participant_status(invoice, row, verified_by=None, verified_at=None):
	invoice.status = PARTICIPANT_STATUS_TO_INVOICE.get(row.payment_status, "Unpaid")
	if invoice.status == PAID:
		invoice.verified_by = verified_by or invoice.verified_by or frappe.session.user
		invoice.verified_at = verified_at or invoice.verified_at or clock.now_dt()


# ---------------------------------------------------------------------------
# doc_events (hooks.py) — CBT Court Booking
# ---------------------------------------------------------------------------


def on_booking_after_insert(doc, method=None):
	create_invoice_for_booking(doc)


def on_booking_update(doc, method=None):
	if doc.flags.in_insert:
		# The creation path just derived status/amounts; frappe fires
		# on_update during insert too.
		return
	# The refund reason, cause and store-credit flag travel on the booking's
	# flags (cancel_booking sets them before the save) — the hook receives THIS
	# document object, flags intact, so all three land in ONE write (B53).
	sync_invoice_for_booking(
		doc.name,
		refund_reason=doc.flags.refund_reason,
		refund_cause=doc.flags.get("refund_cause"),
		refund_as_credit=doc.flags.get("refund_as_credit"),
	)
