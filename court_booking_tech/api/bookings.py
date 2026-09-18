# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Booking transitions (section-4) — staff endpoints. EVERY entry point calls the
tenancy choke point; confirm/extend/reschedule enforce the suspension gate (they
consummate/create bookings), cancel de-escalates and is allowed for suspended
companies. Row locks serialize against the per-minute expiry sweep and racing
extends (same discipline as the double-booking guard).

Section-15 adds reschedule_booking — the third "new linked booking" endpoint
after extend_booking. All three obey the same two rules: copy the section-13
identity fields explicitly, and NEVER copy `hourly_rate` off a booking that
carries rate segments (it is the blend, section-14 as-built 4).

Section-16 adds the attendance pair, check_in and undo_no_show. They sit on
opposite sides of the gate deliberately: check_in is allow_suspended (recording
who turned up is not booking-creating), undo_no_show is not (it re-occupies a
slot). Reschedule stays refused for a No Show booking — undo it first, or
cancel and rebook.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate

from court_booking_tech import clock, credits
from court_booking_tech.billing import PAID, require_refund
from court_booking_tech.membership import get_member_discount
from court_booking_tech.notifications import notify_booking_rescheduled
from court_booking_tech.slots import _as_timedelta, _fmt, _slot_dt, is_reserved_hold_live
from court_booking_tech.tenancy import require_company_access

ACTIVE_STATUSES = ("Reserved", "Confirmed", "Extended")

# Section-16: a released No Show is NOT active (that is the whole point — it
# holds no slot), but it must still be cancellable. Without it a release made
# in error whose slot has since been resold would have no correction path at
# all: the undo collides, and cancel would refuse. Cancelling one writes OFF
# money already recognised, which is the opposite direction of travel from
# cancelling a Confirmed booking — the desk UI says so before it asks.
CANCELLABLE_STATUSES = ACTIVE_STATUSES + ("No Show",)

CHECK_IN_STATUSES = ("Confirmed", "Extended")


def _locked_booking(name: str):
	"""Lock the booking row, then load it fresh (post-lock state)."""
	if not frappe.db.exists("CBT Court Booking", name):
		frappe.throw(_("Booking {0} not found.").format(name), frappe.DoesNotExistError)
	frappe.db.get_value("CBT Court Booking", name, "name", for_update=True)
	return frappe.get_doc("CBT Court Booking", name)


@frappe.whitelist(methods=["POST"])
def create_booking(
	court: str,
	booking_date: str,
	start_time: str,
	payment_method: str,
	customer: str | None = None,
	customer_name: str | None = None,
	customer_phone: str | None = None,
	number_of_slots=1,
	notes: str | None = None,
	discount_percent=None,
	hourly_rate=None,
	payment_channel: str | None = None,
	apply_credit=0,
) -> dict:
	"""Desk quick-book (section-7 board). Branch/company/rate are derived
	server-side from the court chain (S4 as-built 8 — never trust a client
	company); the full section-4 insert pipeline runs unchanged (suspension
	gate, business hours, FOR-UPDATE overlap lock, Cash/Free instant-confirm).

	DESK-side only: a portal customer fails require_company_access (fail-closed
	tenancy). The portal booking endpoint is section-9 work and must set
	flags.customer_created there.

	IDENTITY (section-13): pass EXACTLY ONE of `customer` (an account) or
	`customer_name` (a walk-in paying cash, optionally with `customer_phone`).
	Both is a contradiction the caller has to resolve — unlike the desk FORM,
	where a hand-typed name beside an account is silently corrected, an API
	caller has declared two conflicting intents and gets told so.

	NOTE the signature order: `payment_method` moved ahead of `customer` when
	customer became optional (a parameter without a default cannot follow one
	with a default). Every caller in the app passes these by keyword, and the
	board posts a kwargs dict over REST, so the move is source-compatible.

	`discount_percent` (section-11) is deliberately tri-state:
	  - OMITTED / empty  -> auto-fill from the customer's membership at this
	    company (the API stays correct for callers that know nothing about
	    memberships — seeds, scripts, future integrations);
	  - any value, INCLUDING 0 -> the caller means it; staff override wins and
	    survives revalidation (the booking's stored value is what re-computes).
	An untouched Percent field in a frappe dialog reads as 0, indistinguishable
	from a deliberate 0 — so the BOARD always sends what the staff can see on
	screen (WYSIWYG money), and shows the membership hint beside it.

	`hourly_rate` (section-14) is tri-state the same way:
	  - OMITTED / empty -> the court's rate rules price each slot (a flat-rate
	    court is unchanged: no rules means one rate, exactly as before);
	  - any value -> a deliberate staff override for THIS booking; the rules
	    are skipped and the booking carries no rate segments.
	The flag is set explicitly rather than inferred, because an override that
	happens to equal the court's base rate is otherwise indistinguishable from
	no override at all. Seeds and scripts that pass a rate therefore keep their
	old flat behaviour by definition.
	"""
	customer = (customer or "").strip() or None
	customer_name = (customer_name or "").strip() or None
	if customer and customer_name:
		frappe.throw(
			_(
				"Pass either a customer account or a walk-in name, not both — "
				"an account booking takes its name from the account."
			)
		)
	if not customer and not customer_name:
		frappe.throw(_("Provide a customer account or a walk-in name."))

	chain = frappe.db.get_value(
		"CBT Court", court, ["branch", "company", "hourly_rate"], as_dict=True
	)
	if not chain:
		frappe.throw(_("Court {0} does not exist.").format(court))
	require_company_access(chain.company)  # suspension gate ON (booking-creating)

	if discount_percent in (None, ""):
		# A walk-in holds no membership by construction — there is no account
		# to hold one against, so the tri-state default is a plain 0.
		discount_percent = (
			get_member_discount(chain.company, customer) if customer else 0
		)

	rate_override = hourly_rate not in (None, "")

	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"company": chain.company,
			"branch": chain.branch,
			"court": court,
			"customer": customer,
			"customer_name": customer_name,
			"customer_phone": customer_phone,
			"booking_date": booking_date,
			"start_time": start_time,
			"number_of_slots": cint(number_of_slots),
			"payment_method": payment_method,
			# Backlog B29: the desk's choice of channel; empty = the company's
			# first enabled channel of the method's kind (controller-resolved).
			"payment_channel": (payment_channel or "").strip() or None,
			# Left EMPTY on the default path so fetch_from fills the court's
			# base rate and the controller lets the rate rules price the slots.
			"hourly_rate": flt(hourly_rate) if rate_override else None,
			"discount_percent": flt(discount_percent),
			"notes": notes,
		}
	)
	if rate_override:
		doc.flags.rate_override = True
	# Backlog B39: spend the customer's store credit at this company.
	doc.flags.apply_credit = bool(cint(apply_credit))
	doc.insert()
	return {
		"name": doc.name,
		"booking_status": doc.booking_status,
		"credit_applied": flt(doc.credit_applied),
	}


@frappe.whitelist(methods=["POST"])
def create_desk_cart(
	items,
	payment_method: str,
	customer: str | None = None,
	customer_name: str | None = None,
	customer_phone: str | None = None,
	discount_percent=None,
	payment_channel: str | None = None,
	apply_credit=0,
	notes: str | None = None,
) -> dict:
	"""Backlog B46 — the desk books a CART: many courts, many dates, one payment.

	Until 2026-09-05 the front desk could express only "N consecutive slots on
	ONE court", as an integer, while the customer's own website had held a
	multi-court, multi-date, non-contiguous cart since B35. So a customer who
	booked two courts online got one payment, one countdown and ONE billing
	statement, and the same customer phoning the desk got two of each — the
	product's reconciliation story was available to strangers on the internet
	and not to the staff taking the money.

	THIS IS NOT `api.portal.reserve_cart` WITH A CUSTOMER FIELD, and it must
	never become one: `test_isolation.test_reserve_cart_takes_no_customer_
	parameter` pins that endpoint's signature precisely because a list-shaped
	payload is where a caller-named customer would look natural. The customer is
	the session user there, always. What the two DO share is every line of the
	arithmetic (`portal.cart_quote_core`), the normalizer (`_normalise_cart` —
	union the selected slots per court+date, split into contiguous runs) and the
	group stamp (`portal.mint_cart_group`).

	The desk's own three differences:
	- IDENTITY (section-13): EXACTLY ONE of `customer` (an account) or
	  `customer_name` (a walk-in). A walk-in MAY hold a cart — `booking_group`
	  and the one-invoice rule need no account (`billing._billable_rows` groups
	  on the stamp alone), and "three courts for the tournament, cash" is the
	  commonest desk cart there is.
	- PAYMENT METHOD: Cash and Free confirm instantly; Fund Transfer holds every
	  row on ONE shared clock, exactly as reserve_cart does.
	- MONEY: `discount_percent` is the staff override and is tri-state the same
	  way `create_booking` documents it.

	ONE TRANSACTION. A row that throws rolls the whole cart back, which is what
	makes "nothing in your cart was booked" true rather than hopeful.
	"""
	from court_booking_tech.api.portal import (
		_cart_setup,
		_insert_order,
		_reject_unavailable_runs,
		mint_cart_group,
	)
	from court_booking_tech.court_booking_tech.doctype.cbt_court_booking.cbt_court_booking import (
		reservation_expiry_minutes,
	)

	customer = (customer or "").strip() or None
	customer_name = (customer_name or "").strip() or None
	if customer and customer_name:
		frappe.throw(
			_(
				"Pass either a customer account or a walk-in name, not both — "
				"an account booking takes its name from the account."
			)
		)
	if not customer and not customer_name:
		frappe.throw(_("Provide a customer account or a walk-in name."))

	payment_method = (payment_method or "").strip()
	if payment_method not in ("Cash", "Fund Transfer", "Free"):
		frappe.throw(_("Payment Method is required."))

	company, branch, courts, runs = _cart_setup(
		items, require_active_company=False, payment_method=payment_method
	)
	# Suspension gate ON — inserting IS booking-creating (PLAN §5). The company
	# is derived from the COURTS, never taken from the client (S4 as-built 8).
	require_company_access(company.name)
	if not cint(branch.is_active):
		frappe.throw(_("This branch is not available."))
	# Name the slot that went, before anything is inserted. past_from_end=True:
	# this is the STAFF view, and staff may back-record the running hour.
	_reject_unavailable_runs(branch, runs, courts, past_from_end=True)

	if discount_percent in (None, ""):
		# A walk-in holds no membership by construction — no account to hold one.
		discount_percent = (
			get_member_discount(company.name, customer) if customer else 0
		)

	group = mint_cart_group(company, runs)
	# ONE pay-by clock for the whole cart, like reserve_cart. Cash and Free
	# confirm on insert and never read it.
	expires = clock.now_dt() + timedelta(minutes=reservation_expiry_minutes(company))

	# B49: heads before their continuations (the portal cart does the same).
	booked = [None] * len(runs)
	for index in _insert_order(runs, courts):
		run = runs[index]
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"company": company.name,
				"branch": branch.name,
				"court": run["court"],
				"customer": customer,
				"customer_name": customer_name,
				"customer_phone": customer_phone,
				"booking_date": run["booking_date"],
				"start_time": run["start_time"],
				"number_of_slots": run["number_of_slots"],
				"payment_method": payment_method,
				"payment_channel": (payment_channel or "").strip() or None,
				"discount_percent": flt(discount_percent),
				"booking_group": group,
				"notes": notes,
			}
		)
		doc.flags.cart_expires_at = expires
		# B39: ONE credit drains across the rows in insert order, one document
		# per row (credits.take_credit) — which is the walk credits.plan_spend
		# models so the quote on screen cannot overstate it.
		doc.flags.apply_credit = bool(cint(apply_credit))
		if run.get("chain_head") is not None:
			doc.flags.fee_chained_to = booked[run["chain_head"]].name
		doc.insert()
		booked[index] = doc

	first = booked[0]
	return {
		"booking_group": group,
		"bookings": [
			{
				"name": doc.name,
				"court_name": courts[doc.court].court_name,
				"booking_date": str(doc.booking_date),
				"start_time": _fmt(_as_timedelta(doc.start_time)),
				"end_time": _fmt(_as_timedelta(doc.end_time)),
				"total_amount": flt(doc.total_amount),
				"credit_applied": flt(doc.credit_applied),
			}
			for doc in booked
		],
		"count": len(booked),
		"booking_status": first.booking_status,
		"total_amount": flt(sum(flt(doc.total_amount) for doc in booked), 2),
		"credit_applied": flt(sum(flt(doc.credit_applied) for doc in booked), 2),
		"reservation_expires_at": first.reservation_expires_at,
	}


@frappe.whitelist(methods=["POST"])
def create_block(
	branch: str,
	block_date: str,
	start_time: str,
	end_time: str,
	reason: str,
	court: str | None = None,
	notes: str | None = None,
) -> dict:
	"""Board block dialog. Empty court = whole-branch closure (PLAN §8d).
	Blocking is housekeeping, not booking-creating — suspended companies may
	still manage their calendar (S4 as-built 5); the controller re-validates
	(court∈branch, window, Confirmed-overlap warning)."""
	company = frappe.db.get_value("CBT Branch", branch, "company")
	if not company:
		frappe.throw(_("Branch {0} does not exist.").format(branch))
	require_company_access(company, allow_suspended=True)

	doc = frappe.get_doc(
		{
			"doctype": "CBT Slot Block",
			"branch": branch,
			"company": company,
			"court": court or None,
			"block_date": block_date,
			"start_time": start_time,
			"end_time": end_time,
			"reason": reason,
			"notes": notes,
		}
	)
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def confirm_booking(
	name: str, payment_channel: str | None = None, suppress_confirm_mail: bool = False
) -> str:
	"""Reserved → Confirmed (payment verified at the desk), or the section-5
	retro-confirm: Expired with a proof on file → Completed (money really
	arrived; the desk let them play; finance confirmed later).

	Confirming IS the staff verifying payment, so any Pending proofs flip to
	Accepted in the same action — a Confirmed booking never carries Pending
	proofs (they would poison the holds cap and the expired-with-proof audit).

	`payment_channel` (Backlog B29, user ruling "customer pick, staff can edit
	based on the uploaded"): the channel the money ACTUALLY arrived through, if
	staff can see from the receipt that it differs from what the customer chose.
	Omitted = keep the booking's. The controller enforces kind / company /
	enabled, and the invoice sync copies it.
	"""
	from court_booking_tech.verification import accept_pending_proofs

	doc = _locked_booking(name)
	require_company_access(doc.company)  # suspension gate ON
	payment_channel = (payment_channel or "").strip() or None
	if payment_channel:
		doc.payment_channel = payment_channel
	if suppress_confirm_mail:
		# B35: a cart accept sends ONE mail instead (notify_cart_confirmed).
		doc.flags.suppress_confirm_mail = True

	if doc.booking_status == "Expired":
		# Retro-confirm — proof requirement + gate re-checked in the status
		# machine (_validate_status_transition); never re-occupies the slot
		# (Completed does not hold), so no overlap re-check is needed.
		accept_pending_proofs(doc.name)
		doc.booking_status = "Completed"
		doc.confirmed_by = frappe.session.user
		doc.confirmed_at = clock.now_dt()
		doc.save()
		return doc.name

	if doc.booking_status != "Reserved":
		frappe.throw(
			_("Only a Reserved booking can be confirmed (this one is {0}).").format(
				_(doc.booking_status)
			)
		)
	if not is_reserved_hold_live(
		doc.booking_status,
		doc.reservation_expires_at,
		clock.now_dt(),
		doc.verification_deadline_at,
	):
		# Both clocks ran out but the sweep hasn't flipped it yet. The money
		# may be real — allow the confirm, but ONLY if nobody else took the
		# slot in the meantime (dead holds read as free).
		doc._validate_no_overlap_locked()
	accept_pending_proofs(doc.name)
	doc.booking_status = "Confirmed"
	doc.confirmed_by = frappe.session.user
	doc.confirmed_at = clock.now_dt()
	doc.save()
	return doc.name


@frappe.whitelist(methods=["POST"])
def cancel_booking(
	name: str, reason: str | None = None, issue_credit=0, cause: str | None = None
) -> str | dict:
	"""Desk cancel: any cancellable status → Cancelled (frees the slot).

	Section-26: when the booking's document is PAID this is a REFUND — money
	out — so it is a Company Admin's action (platform scope passes) and it
	carries a reason, for every seat. An unpaid booking is still the front
	desk's to cancel; a reason given there is simply not needed. The customer
	self-cancel path (unpaid Reserved only) is the portal's (section-9).

	Backlog B39: `issue_credit` turns the refund into store credit at this
	company instead of money out of the drawer.

	B53: `cause` is "Customer request" or "Facility fault". A company on
	`Reschedule only` refuses the CASH path for a customer request — store
	credit and facility-fault cancellations both still go through.
	"""
	doc = _locked_booking(name)
	require_company_access(doc.company, allow_suspended=True)
	if doc.booking_status not in CANCELLABLE_STATUSES:
		frappe.throw(
			_("A {0} booking cannot be cancelled.").format(_(doc.booking_status))
		)
	issue_credit = cint(issue_credit)
	refunding = is_refund(doc)
	credit_reason = None
	if refunding:
		# Reason, cause and the store-credit flag ride the SAME save that cancels
		# the document — the hook hands billing this object, flags intact.
		credit_reason, refund_cause = require_refund(
			doc.company, reason, cause=cause, as_credit=bool(issue_credit)
		)
		doc.flags.refund_reason = credit_reason
		doc.flags.refund_cause = refund_cause
		doc.flags.refund_as_credit = issue_credit
	elif issue_credit:
		frappe.throw(_("There is nothing to credit on this booking."))
	doc.booking_status = "Cancelled"
	doc.save()

	# B39, in this order: credit this booking SPENT comes back first, then what
	# it PAID FOR may be re-issued as new credit. The reload picks up the
	# billing_doc a B36 carve-off may just have re-pointed.
	credits.restore_credit(doc.name)
	# ⚠ reload() rebuilds the doc from the DB and does NOT carry flags, so the
	# stripped reason is held in a local rather than read back off flags.
	doc.reload()
	credit = credits.issue_credit(doc, credit_reason) if issue_credit else None
	if credit:
		return {"name": doc.name, "credit": credit}
	return doc.name


def is_refund(booking) -> bool:
	"""True when cancelling `booking` reverses money already received — its
	billing document is Paid & Verified (the invoice, not the booking status,
	is the truth: a retro-confirm or a released no-show both keep it PAID)."""
	return bool(
		booking.billing_doc
		and frappe.db.get_value("CBT Booking Invoice", booking.billing_doc, "status") == PAID
	)


@frappe.whitelist(methods=["POST"])
def check_in(name: str) -> dict:
	"""Mark that the people on this booking actually turned up (section-16).

	ONE click at the board, and the only thing standing between a paid booking
	and automatic release. Deliberately NOT suspension-gated
	(allow_suspended=True): recording attendance is not booking-creating, and a
	company suspended for a billing dispute must not have its customers' courts
	released out from under them for want of a check-in.

	frappe.db.set_value rather than doc.save(): the booking's own validate would
	re-run the whole insert-time pipeline for two attendance stamps, and nothing
	derives from check-in except the sweep's own guard — so no doc_events are
	needed and none are wanted. It DOES bump `modified`, which is why both desk
	callers reload afterwards.

	Idempotent: checking in twice is what a busy desk does, and the second click
	must not overwrite who greeted them or when.
	"""
	doc = _locked_booking(name)
	require_company_access(doc.company, allow_suspended=True)

	if doc.checked_in_at:
		return {
			"name": doc.name,
			"checked_in_at": doc.checked_in_at,
			"checked_in_by": doc.checked_in_by,
			"already": True,
		}
	if doc.booking_status == "No Show":
		# Answering the question they are really asking, rather than "wrong
		# status": this booking has already been released and its slot may have
		# been resold, so getting it back is the undo, not a check-in.
		frappe.throw(
			_(
				"{0} was already released as a no-show — use Undo no-show to put "
				"it back on the board."
			).format(doc.name)
		)
	if doc.booking_status not in CHECK_IN_STATUSES:
		frappe.throw(
			_("Only a Confirmed or Extended booking can be checked in (this one is {0}).").format(
				_(doc.booking_status)
			)
		)

	now = clock.now_dt()
	frappe.db.set_value(
		"CBT Court Booking",
		doc.name,
		{"checked_in_at": now, "checked_in_by": frappe.session.user},
	)
	return {
		"name": doc.name,
		"checked_in_at": now,
		"checked_in_by": frappe.session.user,
		"already": False,
	}


@frappe.whitelist(methods=["POST"])
def undo_no_show(name: str) -> dict:
	"""Put a released booking back on the board (section-16).

	Suspension-gated like confirm, and for the same reason: this RE-OCCUPIES a
	slot, which is booking-creating.

	The overlap re-check lives in the status machine's Confirmed branch (see
	_validate_status_transition) — a No Show booking has been off the board,
	possibly for hours, and the whole point of releasing it was that somebody
	else could buy the time. If they did, this throws the ordinary
	"Slot already taken" and the booking STAYS No Show; the desk's remaining
	option is to cancel it. Slot blocks are re-checked too.

	Everything is set on the in-memory document and saved ONCE. Writing the
	attendance stamps with db.set_value first would leave a booking that is
	No Show AND checked in whenever the save then throws — and inside a backend
	test's assertRaises there is no request boundary to roll that back
	(section-15 as-built 2, the same class of trap).

	No email fires: notifications.CONFIRMING_TRANSITIONS covers the two
	transitions that mean "money verified", and this one means "we were wrong
	about the attendance" — the money never moved and the customer, who is
	standing there, does not need a receipt for it.
	"""
	doc = _locked_booking(name)
	require_company_access(doc.company)  # suspension gate ON (occupies a slot)
	if doc.booking_status != "No Show":
		frappe.throw(
			_("Only a No Show booking can be restored (this one is {0}).").format(
				_(doc.booking_status)
			)
		)

	# Undoing IS the statement that they showed up, so the booking leaves this
	# call ineligible for release — otherwise the next sweep would take it
	# straight back off the board.
	doc.checked_in_at = doc.checked_in_at or clock.now_dt()
	doc.checked_in_by = doc.checked_in_by or frappe.session.user
	doc.booking_status = "Confirmed"
	doc.save()
	return {
		"name": doc.name,
		"booking_status": doc.booking_status,
		"checked_in_at": doc.checked_in_at,
	}


@frappe.whitelist(methods=["POST"])
def extend_booking(
	name: str, slots=1, payment_method: str = "Cash", payment_channel: str | None = None
) -> str:
	"""Extend an ongoing Confirmed session: a NEW linked booking starting
	where the original ends (plus the branch turnover buffer, so it lands on
	the next grid slot); the original becomes Extended.

	The new booking goes through the FULL insert pipeline — overlap lock,
	business hours, suspension gate — so racing extends serialize and the
	loser gets the clean "slot taken" error.

	PRICING (section-14). The branch is "does the original carry rate
	segments?", and it is load-bearing in BOTH directions:

	- SEGMENTED original -> the extension must RE-RESOLVE at its own window, so
	  `hourly_rate` is left out of the payload entirely and fetch_from seeds the
	  court's base rate. Copying the original's `hourly_rate` here would copy
	  its BLENDED average (₱433.33 for a ₱500+₱400+₱400 session) — and because
	  fetch_if_empty never overwrites a value that is already set, that blend
	  would survive into validate, read as a staff override, and price the extra
	  hour at a rate that appears on no rule and on no statement line.
	- NO segments -> inherit the original's rate unchanged, flagged explicitly.
	  That covers three cases with one rule: a flat-rate court (every court
	  before this section — behaviour is byte-identical to before), a staff
	  override the desk agreed with the customer, and a legacy booking on a
	  court that has gained rules SINCE. The last one is deliberate price
	  continuity for a session already in progress, not an oversight.
	"""
	slots = cint(slots)
	if slots < 1:
		frappe.throw(_("Additional slots must be at least 1."))

	original = _locked_booking(name)
	require_company_access(original.company)  # suspension gate ON
	if original.booking_status != "Confirmed":
		frappe.throw(
			_("Only a Confirmed booking can be extended (this one is {0}).").format(
				_(original.booking_status)
			)
		)

	buffer_minutes = cint(
		frappe.db.get_value("CBT Branch", original.branch, "buffer_minutes")
	)
	extension_start = _as_timedelta(original.end_time) + timedelta(
		minutes=buffer_minutes
	)

	inherits_flat_rate = not original.get("rate_segments")

	payload = {
		"doctype": "CBT Court Booking",
		"company": original.company,
		"branch": original.branch,
		"court": original.court,
		"customer": original.customer,
		# Section-13: carry the walk-in identity explicitly. fetch_from only
		# fills customer_name FROM A USER, so without this an extension of a
		# walk-in would reach validate with neither identity and be refused
		# — "one more hour" is exactly the walk-in's commonest request.
		"customer_name": original.customer_name,
		"customer_phone": original.customer_phone,
		"booking_date": original.booking_date,
		"start_time": extension_start,
		"number_of_slots": slots,
		"payment_method": payment_method,
		# B29: an extension is its own payment (its own fee unit, B27), so it
		# takes its own channel — the desk's choice or the kind's default, never
		# a blind copy of the original's (a cash extension of a GCash booking).
		"payment_channel": (payment_channel or "").strip() or None,
		"discount_percent": original.discount_percent,
		"extended_from": original.name,
	}
	if inherits_flat_rate:
		payload["hourly_rate"] = original.hourly_rate

	extension = frappe.get_doc(payload)
	if inherits_flat_rate:
		# Explicit, never inferred: an override that happens to EQUAL the
		# court's base rate would otherwise look like "no override" and get
		# re-priced by the rules on a court that has since gained them.
		extension.flags.rate_override = True
	extension.insert()

	original.booking_status = "Extended"
	original.save()
	return extension.name


@frappe.whitelist(methods=["POST"])
def reschedule_booking(
	name: str,
	court: str | None = None,
	booking_date: str | None = None,
	start_time: str | None = None,
	number_of_slots=None,
) -> dict:
	"""Move a booking to a new court/date/time in ONE staff action, preserving
	payment truth (PLAN §8c) — replacing the cancel-then-rebook dance.

	STAFF ONLY (user decision, section-15). Portal customers keep self-cancel +
	rebook: an unpaid Reserved customer already has exactly that power, and once
	a proof exists it is a staff conversation anyway.

	The immutability doctrine is UNCHANGED (S4 as-built 3). This creates a NEW
	booking through the FULL insert pipeline and cancels the original — it never
	edits the original's scheduling fields, which is what keeps the insert-only
	FOR-UPDATE overlap lock sufficient.

	ORDER IS LOAD-BEARING: insert first, cancel last. Every step before the
	cancel can throw, so the destructive write happens only once the replacement
	really exists. That is what makes "occupied target -> the original is
	UNTOUCHED" true even in a backend test, where a throw inside assertRaises
	does not unwind the way a request boundary does.

	Omitted parameters default to the original's values, so "same slot, one hour
	longer" is `number_of_slots=2` alone. Moving across BRANCHES of the same
	company is allowed; moving across companies is not (the company field is
	immutable and both the booking series and the invoice series are per-company).
	"""
	original = _locked_booking(name)
	require_company_access(original.company)  # suspension gate ON (occupies a slot)

	# --- what cannot be moved -------------------------------------------------
	if original.booking_status == "Extended":
		frappe.throw(
			_(
				"This booking has an extension — cancel the pair and rebook "
				"instead. Moving it would leave the extension behind."
			)
		)
	if original.extended_from:
		# Note the asymmetry is only apparent: a Confirmed booking can never
		# have a LIVE extension child, because extend_booking flips the parent
		# to Extended in the same call. So the check above covers the parent
		# side and this one covers the child side.
		frappe.throw(
			_(
				"This booking extends {0} — cancel the pair and rebook instead. "
				"Moving it would detach it from the session it extends."
			).format(original.extended_from)
		)
	if original.booking_status not in ("Reserved", "Confirmed"):
		frappe.throw(
			_(
				"Only a Reserved or Confirmed booking can be rescheduled "
				"(this one is {0})."
			).format(_(original.booking_status))
		)
	# Backlog B49 (user ruling 2026-09-09): one hour of a continuous session
	# cannot move on its own — moving a continuation away would leave it
	# permanently fee-free, moving the head would orphan its waiver.
	continuation = frappe.db.get_value(
		"CBT Court Booking",
		{
			"fee_chained_to": original.name,
			"booking_status": ("not in", ("Cancelled", "Expired")),
		},
		"name",
	)
	if original.get("fee_chained_to") or continuation:
		frappe.throw(
			_(
				"This booking is part of a continuous session — its booking fee is "
				"shared with {0}. Cancel and rebook the session instead of moving "
				"one hour of it."
			).format(original.get("fee_chained_to") or continuation)
		)

	# --- the target -----------------------------------------------------------
	court = (court or "").strip() or original.court
	target_date = getdate(booking_date) if booking_date else getdate(original.booking_date)
	target_start = (
		_as_timedelta(start_time) if start_time else _as_timedelta(original.start_time)
	)
	target_slots = (
		cint(number_of_slots)
		if number_of_slots not in (None, "")
		else cint(original.number_of_slots)
	)

	chain = frappe.db.get_value("CBT Court", court, ["branch", "company"], as_dict=True)
	if not chain:
		frappe.throw(_("Court {0} does not exist.").format(court))
	# Same choke point create_booking uses, and for the same reason: a tenant
	# user aiming at a foreign court fails closed here with PermissionError
	# before the message below can confirm the court exists.
	require_company_access(chain.company)
	if chain.company != original.company:
		# Reachable only from platform scope, which passes the gate everywhere.
		frappe.throw(
			_("A booking can only be moved to another court of the same company.")
		)

	if (
		court == original.court
		and target_date == getdate(original.booking_date)
		and target_start == _as_timedelta(original.start_time)
		and target_slots == cint(original.number_of_slots)
	):
		frappe.throw(
			_(
				"This booking is already on that court, date and time — pick a "
				"different slot, or change how many slots it runs for."
			)
		)

	# --- Backlog B27: a fee unit cannot cross a CLOSED month ---------------------
	# The fee travels with the booking (a move is not a new sale), and every fee
	# figure is attributed by SERVICE month. While both months are live that is a
	# transfer — the source month loses the unit, the target gains it. Once either
	# month is closed its figures are frozen: moving out of a closed month would
	# leave the fee in the frozen source AND land it in the live target (billed
	# twice); moving into one would drop it from both. Refuse, rather than bill a
	# fee the customer paid once twice (the ducky's finding, 2026-08-27).
	if cint(original.platform_fee_seq) and target_date.replace(day=1) != getdate(
		original.booking_date
	).replace(day=1):
		from court_booking_tech.platform_fees import closed_month_key

		closed = closed_month_key(original.booking_date) or closed_month_key(target_date)
		if closed:
			frappe.throw(
				_(
					"This booking carries a platform booking fee and {0} is a closed "
					"month — it cannot be moved across months once either month is "
					"closed. Cancel and rebook instead."
				).format(closed)
			)

	# --- the replacement ------------------------------------------------------
	# PRICING (section-14 as-built 4, the trap that bit extend_booking). NEVER
	# copy `hourly_rate` off a SEGMENTED booking: there it is the BLENDED
	# average (₱433.33 for ₱500+₱400+₱400), and because fetch_if_empty never
	# overwrites a value that is already set, the blend would survive into
	# validate, read as a staff override, and price the new slot at a rate on no
	# rule and on no statement line. Omit the key entirely so the seam re-prices
	# at the NEW window. With no segments the original carried ONE flat rate —
	# a rule-less court, a legacy booking, or a staff override the desk already
	# agreed with the customer — so inherit it, flagged explicitly.
	inherits_flat_rate = not original.get("rate_segments")

	payload = {
		"doctype": "CBT Court Booking",
		"company": original.company,
		"branch": chain.branch,
		"court": court,
		"customer": original.customer,
		# Section-13 identity-field copy list: fetch_from fills customer_name
		# only FROM A USER, so a walk-in copy without these two dies on the
		# either-or validation in _validate_customer_identity.
		"customer_name": original.customer_name,
		"customer_phone": original.customer_phone,
		"booking_date": target_date,
		"start_time": target_start,
		"number_of_slots": target_slots,
		"payment_method": original.payment_method,
		"discount_percent": original.discount_percent,
		"notes": original.notes,
		# Carried so a customer who already burned their ONE proof-rejection
		# restart (PLAN §5a: a second rejection of any kind expires the hold)
		# cannot win a fresh one by being rescheduled.
		"rejection_count": cint(original.rejection_count),
		"rescheduled_from": original.name,
		# Backlog B27: a move is NOT a new sale. The platform fee and the unit
		# number travel with the booking; the cancelled original stops counting,
		# so the month's bracket count is unchanged by a reschedule.
		"platform_fee": flt(original.platform_fee),
		"platform_fee_seq": cint(original.platform_fee_seq),
		# Backlog B29: the money already moved through this channel — a move
		# does not change where it landed. Carried even if the channel has since
		# been disabled (history keeps its channel; the flag below says so).
		"payment_channel": original.payment_channel,
		# Backlog B39: store credit already settled part of this booking. A move
		# is not a new sale, so the settlement travels with it rather than being
		# drained a second time — the platform_fee rule, applied to credit.
		"credit_applied": flt(original.credit_applied),
		"credit_document": original.credit_document,
	}
	if inherits_flat_rate:
		payload["hourly_rate"] = original.hourly_rate

	new = frappe.get_doc(payload)
	new.flags.platform_fee_carried = True
	new.flags.payment_channel_carried = True
	new.flags.credit_carried = True
	if inherits_flat_rate:
		new.flags.rate_override = True
	# The overlap licence for the ONE row this insert replaces — see
	# _validate_no_overlap_locked. Without it "same court, one hour longer" and
	# "shift it 30 minutes" collide with the very booking being moved.
	new.flags.reschedule_of = original.name

	original_was_confirmed = original.booking_status == "Confirmed"
	if original_was_confirmed and original.payment_method in ("Cash", "Free"):
		# The insert itself lands Confirmed (_apply_payment_method), so the
		# ORIGINAL's stamps have to be on the doc BEFORE it runs. The payment
		# was verified once, at the desk, by whoever took the cash — not by the
		# staff member who moved the slot afterwards.
		new.confirmed_by = original.confirmed_by
		new.confirmed_at = original.confirmed_at

	# The FULL pipeline: chain guard, identity, hours/grid, suspension gate,
	# FOR-UPDATE overlap, payment-method status. A failure here throws with the
	# original untouched.
	new.insert()

	if original_was_confirmed and new.booking_status != "Confirmed":
		# Fund Transfer is born Reserved. Carry the verification forward in the
		# SAME call, stamps set BEFORE save so the status machine's
		# `or frappe.session.user` default never fires (S4).
		new.confirmed_by = original.confirmed_by
		new.confirmed_at = original.confirmed_at
		# Section-19 (B5): this save is a real Reserved->Confirmed transition, so
		# it USED to fire the booking-confirmed email — a mail saying "your
		# payment has been verified" for a payment verified days ago, sent
		# because the slot moved. The move mail below replaces it and says the
		# true thing. Set before save; flags die with the request, so a later
		# genuine confirm of a moved unpaid booking still mails normally.
		new.flags.suppress_confirm_mail = True
		new.booking_status = "Confirmed"
		new.save()
	elif not original_was_confirmed:
		# Reserved: the base clock restarted at insert (deliberate kindness —
		# the full window begins again), and the evidence follows the live
		# document.
		_carry_reservation_evidence(original, new)

	# --- release the original (LAST, and unconditional) -----------------------
	original.booking_status = "Cancelled"
	original.rescheduled_to = new.name
	original.save()  # on_update syncs its invoice to Cancelled (§8e: retained,
	# number consumed; the new booking's after_insert already minted the next).
	if (
		frappe.db.get_value("CBT Court Booking", original.name, "booking_status")
		!= "Cancelled"
	):
		# The overlap licence granted above is sound ONLY because the original
		# really leaves the board. Never return having created a second live
		# booking on the same slot.
		frappe.throw(_("Reschedule could not release the original booking."))

	# Section-19 (Backlog B5). LAST, and deliberately AFTER the verification
	# above: the mail describes a move that has fully happened. It cannot break
	# the move — notify_booking_rescheduled wraps, swallows and logs (S9 failure
	# model) — and it is silently skipped for a walk-in, who has no inbox.
	notify_booking_rescheduled(original, new)

	return {
		"name": new.name,
		"booking_status": new.booking_status,
		"rescheduled_from": original.name,
	}


def _carry_reservation_evidence(original, new):
	"""Move an unpaid hold's proof evidence onto its replacement (section-15).

	The board's proof badges, the pending-payments panel, the per-booking proof
	cap and the per-customer holds cap ALL filter on `booking`, so evidence left
	behind would make the new hold look unproven and the cancelled one look
	live. Both caps stay count-stable: one Reserved booking joins the Pending
	proofs before, one after.

	frappe.db.set_value, not doc.save: CBT Payment Proof rejects every non-new
	save in validate (immutable by design), so set_value is the only legal route
	— and it fires no doc_events, which is safe here because the DocType has
	none registered. `company` needs no update: the proof derives it from its
	booking at insert and this endpoint enforces the SAME company. Attached
	Files point at the PROOF, not the booking, so they follow for free.
	"""
	for proof in frappe.get_all(
		"CBT Payment Proof", filters={"booking": original.name}, pluck="name"
	):
		frappe.db.set_value(
			"CBT Payment Proof", proof, "booking", new.name, update_modified=False
		)

	if original.verification_deadline_at:
		# Re-capped at the NEW booking's end. The cap is the walker's hard rule
		# (S5) — a hold can never outlive the slot it holds — and a move onto an
		# EARLIER slot would otherwise carry a deadline past its own booking.
		capped = min(
			get_datetime(original.verification_deadline_at),
			_slot_dt(new.booking_date, _as_timedelta(new.end_time)),
		)
		# update_modified=False so the in-memory `new` can never go stale under
		# a later save (the branches are mutually exclusive today; a future
		# merge of them would otherwise TimestampMismatchError).
		frappe.db.set_value(
			"CBT Court Booking",
			new.name,
			"verification_deadline_at",
			capped,
			update_modified=False,
		)
		new.verification_deadline_at = capped
