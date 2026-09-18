# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Slot engine (section-4, PLAN §5).

get_slot_grid builds a branch's bookable grid for one date from its court
hours × effective slot duration (branch → platform, S3 as-built: the chain
bottoms out at 0 on a never-saved Singles — DEFAULT_SLOT_MINUTES guards it).
Availability classifies every court × slot as available / booked / blocked /
past. Two whitelisted views: the staff one (customer names, company-scoped via
the tenancy choke point) and the guest-safe portal one (section-9 reuses it)
which never exposes customer identity.

There is a SECOND difference between those two views, and it is not about
identity: THE STAFF PAST-LINE IS THE SLOT'S END, THE PORTAL'S IS ITS START.
So the desk can still sell the hour that is running right now — a walk-in who
turns up mid-session — while the portal cannot offer it, because
_reject_past_for_customers refuses a customer booking of a started slot and a
grid must never offer what the server will not take. See _availability's
`past_from_end`.

Section-16 introduced that split but gated it on `no_show_release_minutes > 0`,
because the no-show feature needs it (a released booking is by definition in
progress) and that section shipped default-OFF. Section-18 (Backlog B7) DROPPED
the gate: `create_booking` has always accepted a staff back-record of a started
slot (S4 as-built 6), so the gated version left the desk surface refusing what
the API accepts, and pushed staff to the DocType form — where Backlog B3 then
bit them. The relaxation is now knob-independent and unconditional for both
staff callers.

Time fields are `timedelta` server-side — _as_timedelta is the ONE
normalization helper; the booking controller reuses it (classic-bug gotcha).
Section-14 moved it (with _fmt and _overlaps) into court_booking_tech.timeutil
so the pricing seam can share it without an import cycle; they are RE-EXPORTED
here, so every pre-section-14 `from ...slots import _as_timedelta` still works.
"""

from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate

from court_booking_tech import clock, pricing
from court_booking_tech.tenancy import require_company_access
from court_booking_tech.timeutil import (  # noqa: F401  (re-exported)
	_as_timedelta,
	_fmt,
	_overlaps,
	closing_boundary,
)

DEFAULT_SLOT_MINUTES = 60

# Statuses that occupy a slot. Reserved only counts while unexpired (checked
# against the clock, so a base-clock-expired hold frees the slot even before
# the sweep flips it to Expired).
ACTIVE_STATUSES = ("Reserved", "Confirmed", "Extended")


def _slot_dt(date, td: timedelta) -> datetime:
	return datetime.combine(getdate(date), time.min) + td


def _branch_doc(branch):
	if isinstance(branch, str):
		return frappe.get_doc("CBT Branch", branch)
	return branch


def is_reserved_hold_live(
	status, reservation_expires_at, now=None, verification_deadline_at=None
) -> bool:
	"""Does this booking still occupy its slot right now?

	Confirmed/Extended always do; Reserved holds live while EITHER clock is
	running: the base clock (reservation_expires_at) or — once a payment
	proof armed it (section-5) — the verification clock
	(verification_deadline_at, already END-capped by its setter). Without the
	second check a proof-holding booking would read as free the moment its
	base clock lapsed and the slot could be double-booked.
	A Reserved row with NO expiry set (seed/staff anomaly) is treated as live —
	conservative for double-booking.
	"""
	if status not in ACTIVE_STATUSES:
		return False
	if status != "Reserved" or not reservation_expires_at:
		return True
	now = now or clock.now_dt()
	if get_datetime(reservation_expires_at) > now:
		return True
	return bool(
		verification_deadline_at and get_datetime(verification_deadline_at) > now
	)


def get_slot_grid(branch, date) -> list[dict]:
	"""Bookable slots for one branch × date: [{'start_time': td, 'end_time': td}].

	Closed day (or no hours row) -> []. The last slot ENDS at or before
	closing; buffer_minutes is dead turnover time BETWEEN slots. A 23:59
	closing means MIDNIGHT (timeutil.closing_boundary): the grid runs to 24:00
	and the last slot's end_time is stored as 24:00:00 — never 00:00, which
	would read as end < start and disarm _overlaps.
	"""
	branch = _branch_doc(branch)
	day = getdate(date).strftime("%A")
	row = next((r for r in branch.business_hours or [] if r.day == day), None)
	# None-checks, NOT truthiness: a midnight opening is timedelta(0) — falsy
	# but very much open (the e2e-fast fixture runs 00:00–23:59).
	if (
		not row
		or not cint(row.is_open)
		or row.opening_time is None
		or row.closing_time is None
	):
		return []

	duration = timedelta(
		minutes=branch.get_slot_duration_minutes() or DEFAULT_SLOT_MINUTES
	)
	step = duration + timedelta(minutes=cint(branch.buffer_minutes))
	opening = _as_timedelta(row.opening_time)
	closing = closing_boundary(row.closing_time)

	slots = []
	cursor = opening
	while cursor + duration <= closing:
		slots.append({"start_time": cursor, "end_time": cursor + duration})
		cursor += step
	return slots


def _availability(branch, date, include_customer: bool, past_from_end: bool = False) -> dict:
	"""Availability core: per active court × slot classification.

	`past_from_end` moves the "past" line from the slot's START to its END, so a
	slot that is RUNNING RIGHT NOW reads as available instead of past.

	It is passed by the STAFF callers, unconditionally (section-18, Backlog B7):
	- STAFF may legitimately book a started slot — `_reject_past_for_customers`
	  fires on flags.customer_created alone (S4 as-built 6), which is how a
	  walk-in who turns up mid-hour gets booked at all. While this was gated on
	  a knob the board's click handler (quick-book only for
	  `status === "available"`) refused what the API accepts.
	- The PORTAL must not: offering a customer a slot the controller then
	  refuses is a grid that lies. get_public_availability never passes it, and
	  the DEFAULT below is what guarantees that.

	The parameter survives rather than being inlined precisely because that
	default is load-bearing: the difference between the two views must stay one
	visible argument at the two call sites, not a condition buried in here.

	It also matters less than it looks. The set of slots this flips is exactly
	`start < now <= end` — one chip per court per day, the hour containing now
	— not the row of greyed-out hours before it, which have ENDED and stay past.

	NOTE this is the only thing other than customer identity that differs
	between the staff and public views — see the module docstring.
	"""
	branch = _branch_doc(branch)
	date = getdate(date)
	grid = get_slot_grid(branch, date)
	now = clock.now_dt()

	courts = frappe.get_all(
		"CBT Court",
		filters={"branch": branch.name, "is_active": 1},
		fields=["name", "court_name", "court_type", "hourly_rate"],
		order_by="name",
	)
	# Section-14: every court's rate rules in ONE query for the whole branch.
	# Resolving per slot would turn a single board load into hundreds of round
	# trips (courts × slots), and per court is still one query per card.
	rules_by_court = pricing.get_rate_rules_map([court.name for court in courts])

	bookings_by_court = {}
	for booking in frappe.get_all(
		"CBT Court Booking",
		filters={
			"branch": branch.name,
			"booking_date": date,
			"booking_status": ("in", ACTIVE_STATUSES),
		},
		fields=[
			"name",
			"court",
			"start_time",
			"end_time",
			"booking_status",
			"reservation_expires_at",
			"verification_deadline_at",
			"customer_name",
		],
	):
		if not is_reserved_hold_live(
			booking.booking_status,
			booking.reservation_expires_at,
			now,
			booking.verification_deadline_at,
		):
			continue  # expired-Reserved: slot is free before the sweep runs
		bookings_by_court.setdefault(booking.court, []).append(booking)

	court_blocks, branch_blocks = {}, []
	for block in frappe.get_all(
		"CBT Slot Block",
		filters={"branch": branch.name, "block_date": date},
		fields=["court", "start_time", "end_time", "reason"],
	):
		if block.court:
			court_blocks.setdefault(block.court, []).append(block)
		else:
			branch_blocks.append(block)  # empty court = whole-branch closure

	court_rows = []
	for court in courts:
		court_pricing = {
			"court": court.name,
			"base_rate": court.hourly_rate,
			"rules": rules_by_court.get(court.name, []),
		}
		slots = []
		for slot in grid:
			start, end = slot["start_time"], slot["end_time"]
			entry = {
				"start_time": _fmt(start),
				"end_time": _fmt(end),
				"status": "available",
				# Section-14: what this slot costs per hour. Price is not
				# identity — it goes to the guest-safe portal view too (the
				# whole point is that a customer can see the night rate BEFORE
				# committing), so the leak-vector rules are untouched.
				"rate": pricing.resolve_slot_rate(court_pricing, date, start),
			}
			booking = next(
				(
					b
					for b in bookings_by_court.get(court.name, ())
					if _overlaps(
						start,
						end,
						_as_timedelta(b.start_time),
						_as_timedelta(b.end_time),
					)
				),
				None,
			)
			block = next(
				(
					b
					for b in branch_blocks
					+ court_blocks.get(court.name, [])
					if _overlaps(
						start,
						end,
						_as_timedelta(b.start_time),
						_as_timedelta(b.end_time),
					)
				),
				None,
			)
			if booking:
				entry["status"] = "booked"
				entry["booking_status"] = booking.booking_status
				if booking.booking_status == "Reserved":
					# When the slot frees if nobody pays: the LATER of the two
					# clocks, because the hold lives while EITHER runs. A bare
					# timestamp — no identity — so the portal can show
					# "held, free in 12:04" to guests (section-9).
					entry["hold_expires_at"] = _later(
						booking.reservation_expires_at,
						booking.verification_deadline_at,
					)
				if include_customer:
					entry["booking"] = booking.name
					entry["customer_name"] = booking.customer_name
			elif block:
				entry["status"] = "blocked"
				entry["reason"] = block.reason
			elif _slot_dt(date, end if past_from_end else start) < now:
				entry["status"] = "past"
			slots.append(entry)
		court_rows.append(
			{
				"court": court.name,
				"court_name": court.court_name,
				"court_type": court.court_type,
				"hourly_rate": court.hourly_rate,
				# Section-14: lets both cards render "from ₱200/hr" instead of
				# stating a base rate that half the day does not charge.
				# Derived from the RULES, not from comparing slot rates — a
				# court whose rules all happen to equal its base would read as
				# flat and start lying again the day one of them changes.
				"has_rate_rules": bool(rules_by_court.get(court.name)),
				"slots": slots,
			}
		)

	return {
		"branch": branch.name,
		"branch_name": branch.branch_name,
		"date": str(date),
		"slot_duration_minutes": branch.get_slot_duration_minutes()
		or DEFAULT_SLOT_MINUTES,
		"buffer_minutes": cint(branch.buffer_minutes),
		# Countdowns tick from the SERVER's clock offset — client clocks drift
		# (PLAN §11.3, same discipline as the section-7 board).
		"server_now": now,
		"courts": court_rows,
	}


def _later(*values):
	"""The latest non-null datetime of the lot (None if all are empty)."""
	stamps = [get_datetime(value) for value in values if value]
	return max(stamps) if stamps else None


@frappe.whitelist(methods=["GET"])
def get_availability(branch: str, date: str) -> dict:
	"""Staff availability (includes customer names). Company-scoped through
	the tenancy choke point; viewing a suspended company's board is allowed —
	suspension blocks NEW bookings, not operations on the existing book.

	`past_from_end=True` unconditionally (section-18, Backlog B7): this is a
	STAFF view, and staff may book the hour that is running.
	"""
	branch_doc = _branch_doc(branch)
	require_company_access(branch_doc.company, allow_suspended=True)
	return _availability(
		branch_doc,
		date,
		include_customer=True,
		past_from_end=True,
	)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_public_availability(branch: str, date: str) -> dict:
	"""Guest-safe availability for the portal (section-9): booked slots carry
	NO customer identity and no booking numbers. Suspended companies and
	inactive branches are hidden from the portal, so they 404 here."""
	if not frappe.db.exists("CBT Branch", branch):
		frappe.throw(_("Branch not found."), frappe.DoesNotExistError)
	branch_doc = _branch_doc(branch)
	company_status = frappe.db.get_value(
		"CBT Company", branch_doc.company, "status"
	)
	if not cint(branch_doc.is_active) or company_status != "Active":
		frappe.throw(
			_("This facility is not accepting bookings."), frappe.ValidationError
		)
	return _availability(branch_doc, date, include_customer=False)
