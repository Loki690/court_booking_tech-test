# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Court Board APIs (section-7) — the staff daily screen's data plane.

Every endpoint passes the tenancy choke point and ships `server_now` in its
payload: countdowns are ALWAYS computed from server timestamps (client clocks
drift — solo-app lesson 3). Viewing uses allow_suspended=True (suspension
blocks NEW bookings, not operations on the existing book — same posture as
slots.get_availability).

These endpoints are DESK-side: a portal customer fails require_company_access
(fail-closed, no company binding). The portal gets its own guest-safe/customer
APIs in section-9.
"""

from collections import Counter
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from court_booking_tech import billing, clock, payment_channels, pricing
from court_booking_tech.billing import PAID, refund_admin_sentence
from court_booking_tech.slots import (
	_as_timedelta,
	_availability,
	_branch_doc,
	_fmt,
	_slot_dt,
)
from court_booking_tech.tenancy import (
	get_session_company,
	has_platform_scope,
	is_company_admin,
	require_company_access,
)

ACTIVE_STATUSES = ("Reserved", "Confirmed", "Extended")


def _pending_proof_counts(booking_names: list[str]) -> Counter:
	if not booking_names:
		return Counter()
	rows = frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": ("in", booking_names), "status": "Pending"},
		fields=["booking"],
	)
	return Counter(row.booking for row in rows)


@frappe.whitelist(methods=["GET"])
def get_board_data(branch: str, date: str) -> dict:
	"""The board's one-shot payload: staff availability enriched with the hold
	clocks + proof badges per booked slot, the branch floor plan, and
	server_now for countdown math."""
	branch_doc = _branch_doc(branch)
	require_company_access(branch_doc.company, allow_suspended=True)

	now = clock.now_dt()
	# The release countdowns and whether the No-shows chip can mean anything.
	# It used to drive the availability rule too; section-18 (Backlog B7) dropped
	# that gate — a STAFF board always shows the running hour as bookable, knob
	# or no knob — but this read stays, because the other two uses are real.
	# frappe.db.get_value, NEVER get_cached_value: the knob is written by
	# db.set_value in test fixtures and can be written by raw SQL in support, and
	# a cached read would leave every web worker serving a stale answer out of
	# shared redis long after the flip.
	release_minutes = cint(
		frappe.db.get_value(
			"CBT Company", branch_doc.company, "no_show_release_minutes"
		)
	)
	data = _availability(
		branch_doc, date, include_customer=True, past_from_end=True
	)

	bookings = frappe.get_all(
		"CBT Court Booking",
		filters={
			"branch": branch_doc.name,
			"booking_date": date,
			"booking_status": ("in", ACTIVE_STATUSES),
		},
		fields=[
			"name",
			"reservation_expires_at",
			"verification_deadline_at",
			"payment_method",
			"total_amount",
			"rejection_count",
			"booking_status",
			"checked_in_at",
			"start_time",
			"end_time",
		],
	)
	pending = _pending_proof_counts([b.name for b in bookings])
	extras = {
		b.name: {
			"reservation_expires_at": b.reservation_expires_at,
			"verification_deadline_at": b.verification_deadline_at,
			"payment_method": b.payment_method,
			"total_amount": b.total_amount,
			"rejection_count": cint(b.rejection_count),
			"pending_proof_count": pending.get(b.name, 0),
			"checked_in_at": b.checked_in_at,
			"release_at": _release_at(b, date, release_minutes, now),
		}
		for b in bookings
	}
	for court_row in data["courts"]:
		for slot in court_row["slots"]:
			if slot.get("booking") in extras:
				slot.update(extras[slot["booking"]])

	data["layout"] = {
		"rows": cint(branch_doc.layout_rows),
		"columns": cint(branch_doc.layout_columns),
		"cells": [
			{
				"row_index": cint(cell.row_index),
				"col_index": cint(cell.col_index),
				"court": cell.court,
			}
			for cell in (branch_doc.layout or [])
		],
	}
	data["company"] = branch_doc.company
	data["no_show_release_minutes"] = release_minutes
	# Section-16: released bookings hold no slot, so they render nowhere on the
	# grid — without this list a no-show would vanish from the desk's world the
	# moment it happened, and the undo would be unreachable.
	data["no_shows"] = _no_shows(branch_doc.name, date)
	data["server_now"] = str(clock.now_dt())
	return data


def _release_at(booking, date, release_minutes: int, now):
	"""When this booking gets released if nobody checks in — or None.

	Shipped ONLY while the session is actually running and still unattended, so
	the board shows a countdown exactly when staff can act on it. Sending it for
	every Confirmed booking of the day would put "10h 24m" on a 20:00 booking at
	09:30, which is noise, and noise is how the countdown that matters gets
	ignored. A server-computed absolute timestamp, never a duration: the board's
	countdowns all tick off the clock offset it captures from server_now
	(S7 doctrine — client clocks drift).
	"""
	if not release_minutes or booking.checked_in_at:
		return None
	if booking.booking_status != "Confirmed":
		return None
	start = _slot_dt(date, _as_timedelta(booking.start_time))
	end = _slot_dt(date, _as_timedelta(booking.end_time))
	if not (start <= now < end):
		return None
	return start + timedelta(minutes=release_minutes)


def _no_shows(branch: str, date) -> list:
	"""Today's released bookings for this branch — the No-shows chip's payload."""
	rows = frappe.get_all(
		"CBT Court Booking",
		filters={
			"branch": branch,
			"booking_date": date,
			"booking_status": "No Show",
		},
		fields=[
			"name",
			"court",
			"customer",
			"customer_name",
			"start_time",
			"end_time",
			"total_amount",
		],
		order_by="start_time",
	)
	court_names = {
		c.name: c.court_name
		for c in frappe.get_all(
			"CBT Court",
			filters={"name": ("in", [r.court for r in rows] or [""])},
			fields=["name", "court_name"],
		)
	}
	return [
		{
			"name": row.name,
			"court": row.court,
			"court_name": court_names.get(row.court, row.court),
			"customer": row.customer,
			"customer_name": row.customer_name,
			"start_time": _fmt(_as_timedelta(row.start_time)),
			"end_time": _fmt(_as_timedelta(row.end_time)),
			"total_amount": row.total_amount,
		}
		for row in rows
	]


@frappe.whitelist(methods=["GET"])
def get_pending_payments(company: str | None = None) -> dict:
	"""Every Reserved booking of the company across dates, soonest effective
	deadline first. Lapsed-but-unswept holds are INCLUDED (countdown reads
	"due"; staff can still confirm/reject — S5 dead-hold semantics). Platform
	scope must pass `company` (the board sends the selected branch's company);
	require_company_access fails closed on an empty one."""
	company = company or get_session_company()
	require_company_access(company, allow_suspended=True)

	bookings = frappe.get_all(
		"CBT Court Booking",
		filters={"company": company, "booking_status": "Reserved"},
		fields=[
			"name",
			"customer",
			"customer_name",
			"branch",
			"court",
			"booking_date",
			"start_time",
			"end_time",
			"total_amount",
			"payment_method",
			"reservation_expires_at",
			"verification_deadline_at",
			"rejection_count",
		],
	)

	names = [b.name for b in bookings]
	pending = _pending_proof_counts(names)
	latest_proof: dict[str, dict] = {}
	if names:
		for proof in frappe.get_all(
			"CBT Payment Proof",
			filters={"booking": ("in", names)},
			fields=["booking", "file", "status", "reference_no", "uploaded_at", "source"],
			order_by="creation desc",
		):
			latest_proof.setdefault(
				proof.booking,
				{
					"file_url": proof.file,
					"is_pdf": (proof.file or "").lower().endswith(".pdf"),
					"status": proof.status,
					"reference_no": proof.reference_no,
					"uploaded_at": proof.uploaded_at,
					"source": proof.source,
				},
			)

	court_names = {
		c.name: c.court_name
		for c in frappe.get_all(
			"CBT Court",
			filters={"name": ("in", [b.court for b in bookings] or [""])},
			fields=["name", "court_name"],
		)
	}
	branch_names = {
		b.name: b.branch_name
		for b in frappe.get_all(
			"CBT Branch",
			filters={"name": ("in", [b.branch for b in bookings] or [""])},
			fields=["name", "branch_name"],
		)
	}

	items = []
	for b in bookings:
		effective = b.verification_deadline_at or b.reservation_expires_at
		items.append(
			{
				"name": b.name,
				"customer": b.customer,
				"customer_name": b.customer_name,
				"branch": b.branch,
				"branch_name": branch_names.get(b.branch, b.branch),
				"court": b.court,
				"court_name": court_names.get(b.court, b.court),
				"booking_date": str(b.booking_date),
				"start_time": _fmt(_as_timedelta(b.start_time)),
				"end_time": _fmt(_as_timedelta(b.end_time)),
				"total_amount": b.total_amount,
				"payment_method": b.payment_method,
				"reservation_expires_at": b.reservation_expires_at,
				"verification_deadline_at": b.verification_deadline_at,
				"effective_deadline": effective,
				"rejection_count": cint(b.rejection_count),
				"pending_proof_count": pending.get(b.name, 0),
				"latest_proof": latest_proof.get(b.name),
			}
		)
	# Soonest deadline first; a Reserved row with NO clock at all (seed/staff
	# anomaly, treated as live by the hold check) sorts last.
	items.sort(
		key=lambda item: (
			item["effective_deadline"] is None,
			get_datetime(item["effective_deadline"]) if item["effective_deadline"] else None,
		)
	)

	return {"company": company, "items": items, "server_now": str(clock.now_dt())}


@frappe.whitelist(methods=["GET"])
def get_booking_detail(booking: str) -> dict:
	"""Verification/details dialog payload — the booking, ALL its proofs
	(fresh at open time), and its invoice status."""
	if not frappe.db.exists("CBT Court Booking", booking):
		frappe.throw(_("Booking {0} not found.").format(booking), frappe.DoesNotExistError)
	doc = frappe.get_doc("CBT Court Booking", booking)
	require_company_access(doc.company, allow_suspended=True)

	proofs = frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": doc.name},
		fields=[
			"name",
			"file",
			"status",
			"source",
			"reference_no",
			"payment_channel",
			"remarks",
			"uploaded_at",
			"uploaded_by",
			"rejection_reason",
		],
		order_by="creation asc",
	)
	channel_labels = {
		row.name: row.label
		for row in payment_channels.list_channels(
			doc.company, enabled_only=False, fields=("name", "label")
		)
	}
	for proof in proofs:
		proof["is_pdf"] = (proof.file or "").lower().endswith(".pdf")
		proof["payment_channel_label"] = channel_labels.get(proof.payment_channel)

	invoice_status = (
		frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status")
		if doc.billing_doc
		else None
	)
	is_refund = invoice_status == PAID
	admin = is_company_admin()
	# B53: a facility-fault cancellation is the desk's when the company says so,
	# so a paid booking can be cancellable by staff for THAT cause alone.
	may_fault = admin or (
		billing.facility_fault_cancel_by(doc.company) == billing.STAFF_AND_ABOVE
	)
	can_cancel = (not is_refund) or admin or may_fault
	policy = billing.refund_policy(doc.company)
	causes = [billing.CAUSE_CUSTOMER] if admin else []
	if may_fault:
		causes.append(billing.CAUSE_FACILITY)
	# The dialog renders the answer; it never guesses from the role list. The
	# server re-checks every one of these — `cause_options` is a courtesy, and
	# `test_refunds` posts a cause the UI never offered.
	if not can_cancel:
		refusal = billing.refund_admin_sentence()
	elif is_refund and policy == billing.RESCHEDULE_ONLY and not admin and not causes:
		refusal = billing.no_cash_refund_sentence(doc.company)
	else:
		refusal = None

	return {
		"name": doc.name,
		"company": doc.company,
		"branch": doc.branch,
		"court": doc.court,
		"court_name": frappe.db.get_value("CBT Court", doc.court, "court_name"),
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		# Section-13 / PLAN §8j: staff see the customer's name and phone. This
		# endpoint is staff-gated by require_company_access above — the phone
		# must NOT be added to any portal or guest-facing payload.
		"customer_phone": doc.customer_phone,
		"booking_date": str(doc.booking_date),
		"start_time": _fmt(_as_timedelta(doc.start_time)),
		"end_time": _fmt(_as_timedelta(doc.end_time)),
		"number_of_slots": cint(doc.number_of_slots),
		"hourly_rate": doc.hourly_rate,
		# Section-14: on a booking priced by rate rules `hourly_rate` above is
		# the BLENDED average — a number matching no rule and no statement
		# line. Ship the segments so the dialog can show what was really
		# charged instead of asking staff to explain an average.
		"rate_segments": pricing.segments_payload(doc.get("rate_segments")),
		"discount_percent": doc.discount_percent,
		"total_amount": doc.total_amount,
		# Backlog B27: the fee this booking CARRIES. The reschedule dialog sends
		# it back to get_quote so a move prices the court at the new slot while
		# keeping the fee the customer already paid.
		"platform_fee": flt(doc.platform_fee),
		# Backlog B39: what store credit already settled on this booking.
		"credit_applied": flt(doc.credit_applied),
		"credit_document": doc.credit_document,
		"booking_status": doc.booking_status,
		"payment_method": doc.payment_method,
		# Backlog B29: the channel on record and the company's enabled channels
		# of the SAME kind, so the verify dialog can offer the correction the
		# ruling allows ("staff can edit based on the uploaded").
		"payment_channel": doc.payment_channel,
		"payment_channel_label": channel_labels.get(doc.payment_channel),
		"payment_channels": payment_channels.list_channels(
			doc.company, kind=payment_channels.channel_kind(doc.payment_method)
		)
		if payment_channels.channel_kind(doc.payment_method)
		else [],
		"reservation_expires_at": doc.reservation_expires_at,
		"verification_deadline_at": doc.verification_deadline_at,
		"rejection_count": cint(doc.rejection_count),
		# Section-16: the dialog decides between "Check in" and nothing from
		# these two, and shows who greeted them once it is done.
		"checked_in_at": doc.checked_in_at,
		"checked_in_by": doc.checked_in_by,
		"extended_from": doc.extended_from,
		# Section-15: both halves of a reschedule pair. The dialog needs
		# `extended_from` to HIDE the Reschedule action (moving an extension
		# would detach it from the session it extends — the API refuses too),
		# and the two links below so a moved booking explains itself instead of
		# showing staff a bare Cancelled row with no story.
		"rescheduled_from": doc.rescheduled_from,
		"rescheduled_to": doc.rescheduled_to,
		"billing_doc": doc.billing_doc,
		"invoice_status": invoice_status,
		# Section-26: the SERVER says whether a cancel is a refund and whether
		# this seat may do it — the dialog renders the answer instead of
		# guessing from the booking status and the role list (ducky, 2026-08-27).
		"is_refund": is_refund,
		"can_cancel": can_cancel,
		"cancel_refusal": refusal,
		# B53: the company's cash policy and the causes THIS seat may declare.
		"refund_policy": policy,
		"cause_options": causes,
		"cash_refund_note": (
			billing.no_cash_refund_sentence(doc.company)
			if is_refund and policy == billing.RESCHEDULE_ONLY
			else None
		),
		"notes": doc.notes,
		"proofs": proofs,
		"server_now": str(clock.now_dt()),
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def customer_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the quick-book dialog and the membership form: enabled
	users holding the CBT Customer role who have DEALT WITH THIS COMPANY
	(Backlog B38, section-13 addendum). The pool was cross-company until
	2026-09-05; PLAN §1's "the customer pool is cross-company" governs the
	MARKETPLACE the customer sees, not the list a tenant's staff can read."""
	if not has_platform_scope() and not frappe.db.exists(
		"CBT Company User", {"user": frappe.session.user}
	):
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	company = get_session_company()
	params = {"txt": f"%{txt}%", "page_len": page_len, "start": start}
	relationship = ""
	if company is not None:
		params["company"] = company
		relationship = """
		  AND (
		       EXISTS (SELECT 1 FROM `tabCBT Court Booking` b
		                WHERE b.customer = u.name AND b.company = %(company)s)
		    OR EXISTS (SELECT 1 FROM `tabCBT Membership` m
		                WHERE m.customer = u.name AND m.company = %(company)s)
		    OR EXISTS (SELECT 1 FROM `tabCBT Open Play Participant` p
		                JOIN `tabCBT Open Play Session` s ON s.name = p.parent
		                WHERE p.parenttype = 'CBT Open Play Session'
		                  AND p.customer = u.name AND s.company = %(company)s)
		    OR EXISTS (SELECT 1 FROM `tabCBT Payment Proof` pp
		                WHERE pp.uploaded_by = u.name AND pp.company = %(company)s)
		  )
		"""
	return frappe.db.sql(
		f"""
		SELECT u.name, u.full_name
		FROM `tabUser` u
		JOIN `tabHas Role` r ON r.parent = u.name AND r.parenttype = 'User'
		WHERE r.role = 'CBT Customer' AND u.enabled = 1
		  AND u.name NOT IN ('Administrator', 'Guest')
		  AND (u.name LIKE %(txt)s OR u.full_name LIKE %(txt)s)
		  {relationship}
		ORDER BY u.full_name
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		params,
	)


@frappe.whitelist(methods=["GET"])
def get_desk_cart_quote(
	items,
	customer: str | None = None,
	discount_percent=None,
	payment_method: str = "Cash",
) -> dict:
	"""Backlog B46 — what a DESK cart costs, before the operator says it out loud.

	The staff twin of `api.portal.get_cart_quote`, and it shares that endpoint's
	arithmetic exactly (`portal.cart_quote_core`): the cart is normalised into
	runs, priced run by run through the same seam the booking controller uses,
	the fee ordinal advances inside the basket, and the VAT footer is computed
	ONCE on the summed court share.

	THREE things differ, and each is why this is a second endpoint rather than a
	parameter on the first:

	- **the gate.** `get_cart_quote` is guest-open. This one arms
	  `require_company_access` on the cart's own company — derived server-side
	  from the courts, never from the client — because it takes staff params.
	- **the identity.** A desk cart can be for a named account or for a walk-in
	  with no account at all. `customer` decides whose store credit is planned.
	- **the payment method.** Cash and Free exist only at the desk, and Free
	  carries no platform fee.

	`discount_percent` is WYSIWYG money (S18/B4): what the operator can SEE is
	what is priced, INCLUDING a deliberate 0. Omitted means "resolve the
	customer's membership" — the same tri-state `create_booking` documents.
	"""
	from court_booking_tech.api.portal import _cart_setup, cart_quote_core

	payment_method = (payment_method or "Cash").strip() or "Cash"
	customer = (customer or "").strip() or None
	if discount_percent in (None, ""):
		discount_percent = None
	else:
		try:
			discount_percent = flt(float(discount_percent))
		except (TypeError, ValueError):
			# flt("abc") is 0.0, which would sail straight through the range
			# check below as a free cart — the class of silent wrong number
			# get_quote's own normalizer exists to refuse.
			frappe.throw(_("Discount must be a number."))
		if discount_percent < 0 or discount_percent > 100:
			frappe.throw(_("Discount must be between 0 and 100."))

	company, branch, courts, runs = _cart_setup(
		items, require_active_company=False, payment_method=payment_method
	)
	# Quoting is a READ, so a suspended company may still be priced — the same
	# posture as get_board_data. The INSERT re-arms the suspension gate.
	require_company_access(company.name, allow_suspended=True)
	quote = cart_quote_core(
		company,
		branch,
		courts,
		runs,
		customer=customer,
		discount_percent=discount_percent,
		payment_method=payment_method,
	)
	quote["payment_method"] = payment_method
	quote["company"] = company.name
	return quote
