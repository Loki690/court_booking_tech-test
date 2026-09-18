# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Court Booking — plain DocType with a GUARDED STATUS MACHINE (recorded
section-4 deviation from PLAN §4 "submittable": docstatus/amend adds friction
and no value to a record whose lifecycle is already a status field).

Invariants the rest of the app leans on:
- Scheduling fields (company, branch, court, customer, booking_date,
  start_time, number_of_slots, payment_method) are IMMUTABLE after insert —
  that is what makes the insert-only FOR-UPDATE overlap lock sufficient
  (reschedule = staff cancel + rebook, PLAN §5).
- IDENTITY (section-13, Backlog B1): a booking carries EITHER a `customer`
  account OR a free-text `customer_name` (+ optional `customer_phone`) — never
  neither. `customer` stays optional so a stranger paying cash at the desk gets
  an honest per-person receipt without being made to hand over an email. It
  stays IMMUTABLE, so a walk-in can never be retro-attached to an account
  (cancel and rebook) — a stated non-goal, not an oversight. The PORTAL can
  never mint one: reserve_booking always sets the session user, and
  _validate_customer_identity fences flags.customer_created against it.
- booking_status changes only along ALLOWED_TRANSITIONS (APIs + sweep);
  the field is read-only on the form.
- ATTENDANCE (section-16, PLAN §8s): `checked_in_at` empty is what makes a
  Confirmed booking eligible for no-show release. It is stamped by the Check in
  action, by the undo, and automatically at insert for an extension or a staff
  back-record of an already-started slot (_auto_check_in). The release itself
  never touches money — the invoice stays Paid & Verified, because the payment
  really happened and the slot is forfeit, not refunded.
- PRICE (section-14): a booking carries its OWN `rate_segments` snapshot,
  resolved once at insert through court_booking_tech.pricing. While segments
  exist they are the only money input — `hourly_rate` becomes their blended
  average and is DISPLAY ONLY. Segments empty means one flat rate, which is
  both every pre-section-14 booking and every deliberate staff override.
- Every time read goes through court_booking_tech.clock.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt, get_datetime, getdate

from court_booking_tech import clock, credits, payment_channels, platform_fees, pricing
from court_booking_tech.bans import ensure_customer_not_banned
from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
	resolve_advance_booking_days,
)
from court_booking_tech.slots import (
	_as_timedelta,
	_fmt,
	_overlaps,
	_slot_dt,
	get_slot_grid,
	is_reserved_hold_live,
)
from court_booking_tech.tenancy import require_company_access, require_company_bookable
from court_booking_tech.timeutil import END_OF_DAY

ACTIVE_STATUSES = ("Reserved", "Confirmed", "Extended")

# Section-4 machine, extended by section-5: Expired → Completed is the
# retro-confirm path (money really arrived; the desk let them play) and is
# additionally guarded in _validate_status_transition — a proof must be on
# file and the suspension gate re-runs.
#
# Section-16 adds No Show: a Confirmed booking nobody checked in for, released
# back onto the board by the sweep so the desk can resell the time. It leaves
# again in exactly two ways — Confirmed (the undo: they DID turn up, or the
# release was a mistake) or Cancelled (voiding a record whose slot has since
# been resold, which is the only correction left once undo would collide).
ALLOWED_TRANSITIONS = {
	"Reserved": {"Confirmed", "Cancelled", "Expired"},
	"Confirmed": {"Extended", "Completed", "Cancelled", "No Show"},
	"Extended": {"Cancelled"},
	"Completed": set(),
	"Cancelled": set(),
	"Expired": {"Completed"},
	"No Show": {"Confirmed", "Cancelled"},
}

IMMUTABLE_FIELDS = (
	"company",
	"branch",
	"court",
	"customer",
	"booking_date",
	"start_time",
	# Derived once at insert (24:00:00 for a midnight-ending slot) and never
	# re-derived on update — immutable so no API write can collapse it to 00:00.
	"end_time",
	"number_of_slots",
	"payment_method",
	"booking_group",
)

DEFAULT_EXPIRY_MINUTES = 30


def reservation_expiry_minutes(company) -> int:
	"""How long a Fund Transfer hold lives. Shared with api/portal.reserve_cart."""
	company_doc = (
		frappe.get_doc("CBT Company", company) if isinstance(company, str) else company
	)
	return company_doc.get_reservation_expiry_minutes() or DEFAULT_EXPIRY_MINUTES


class CBTCourtBooking(Document):
	def autoname(self):
		company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		self.name = make_autoname(f"BK-{company_code}-.YYYY.-.#####")

	def before_insert(self):
		# Naming (per-company series) runs BEFORE validate on insert — the
		# court→branch→company chain must be resolved here. customer_name is
		# NOT filled here: _validate_links (which applies fetch_from
		# server-side) already ran, and _validate_customer_identity owns the
		# authoritative derivation on both the insert and the update path.
		if not self.court:
			frappe.throw(_("Court is required."))
		chain = frappe.db.get_value(
			"CBT Court", self.court, ["branch", "company"], as_dict=True
		)
		if not chain:
			frappe.throw(_("Court {0} does not exist.").format(self.court))
		self.branch = self.branch or chain.branch
		self.company = self.company or chain.company
		self._validate_chain(chain)

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		chain = frappe.db.get_value(
			"CBT Court", self.court, ["branch", "company"], as_dict=True
		)
		if not chain:
			frappe.throw(_("Court {0} does not exist.").format(self.court))
		self._validate_chain(chain)
		# BEFORE the immutability check: it normalizes customer '' -> None, and
		# a stored NULL compared against an in-memory '' would otherwise throw
		# on every later save of a walk-in.
		self._validate_customer_identity()
		# ⚠ Same NULL-vs-'' trap as customer: normalize BEFORE the immutability check.
		self.booking_group = (self.booking_group or "").strip() or None

		if self.is_new():
			# Suspension gate ON: inserting IS booking-creating (PLAN §5).
			# Two actors, two gates (section-9): a portal customer has no CBT
			# Company User binding, so the staff gate would fail closed on the
			# marketplace's whole premise ("one account, book anywhere"). The
			# flag is server-set by api/portal.reserve_booking; customers hold
			# no create DocPerm here, so it cannot be forged over REST.
			if self.flags.customer_created:
				require_company_bookable(self.company)
				# Section-11: the company's own block-list. ONLINE bookings only
				# — staff may still book a banned customer standing at the desk
				# (that is a judgement call the front desk owns), so this sits
				# inside the customer branch rather than in the shared gate.
				ensure_customer_not_banned(self.company, self.customer)
			else:
				require_company_access(self.company)
			self._validate_slots_and_hours()
			self._compute_amounts()
			self._reject_past_for_customers()
			self._reject_beyond_horizon_for_customers()
			self._validate_no_overlap_locked()
			self._apply_credit()
			self._apply_payment_method()
			self._resolve_payment_channel()
			self._auto_check_in()
			self._canonicalize_end_times()
		else:
			# Self-cancel (section-9) is the one customer-driven update; its
			# ownership + "unpaid Reserved only" guards live in
			# api/portal.cancel_my_booking. Everything else below still runs.
			if not self.flags.customer_cancel:
				require_company_access(self.company, allow_suspended=True)
			self._validate_immutables()
			self._validate_status_transition()
			self._compute_amounts()
			self._resolve_payment_channel()
			self._canonicalize_end_times()

	def _canonicalize_end_times(self):
		# frappe writes a Time field with str(timedelta): a midnight end (24h) becomes
		# '1 day, 0:00:00' and MariaDB rejects the row. A reload hands the timedelta
		# back, so every save re-writes a midnight end as the '24:00:00' string.
		# Sub-24h ends are left as they are (str() already writes them correctly).
		def _canon(value):
			end = _as_timedelta(value)
			return _fmt(end) if end >= END_OF_DAY else value

		if self.end_time is not None:
			self.end_time = _canon(self.end_time)
		for segment in self.get("rate_segments") or []:
			if segment.end_time is not None:
				segment.end_time = _canon(segment.end_time)

	def _resolve_payment_channel(self):
		"""Backlog B29: the channel sits BESIDE payment_method and must match its
		kind. New row: the caller's choice or the company's default; a disabled
		channel is refused unless it is being CARRIED (a reschedule copy). Update:
		staff may correct it (the verify-proof ruling), to an enabled channel of
		the right kind; leaving it as stored is always allowed, even if that
		channel has since been disabled — history keeps its channel."""
		if self.is_new():
			allow_disabled = bool(self.flags.payment_channel_carried)
		else:
			stored = frappe.db.get_value("CBT Court Booking", self.name, "payment_channel")
			allow_disabled = (self.payment_channel or None) == (stored or None)
		self.payment_channel = payment_channels.resolve_channel(
			self.company,
			self.payment_method,
			self.payment_channel,
			allow_disabled=allow_disabled,
		)

	# ------------------------------------------------------------------
	# Identity (section-13)
	# ------------------------------------------------------------------

	def _validate_customer_identity(self):
		"""EITHER an account OR a walk-in name — never neither (Backlog B1).

		Runs on BOTH the insert and the update path, before the immutability
		and status logic. Four jobs:

		1. Normalize a blank `customer` to None so the column really holds
		   NULL. `_validate_immutables` compares raw values, so a stored NULL
		   read back against an in-memory '' would make every later save of a
		   walk-in throw "Customer cannot be changed".
		2. Fence the portal: flags.customer_created without a customer is
		   impossible through reserve_booking (it always sets the session
		   user), so reaching here means something is wrong — fail loud rather
		   than mint an anonymous booking on a customer-facing path.
		3. Derive customer_name from the account when there is one. fetch_from
		   already does this server-side (base_document._validate_links runs
		   before validate on both paths, and does NOT care that the field is
		   no longer hard read_only) — this is the belt-and-braces half, and it
		   is what keeps an account booking from ever lying about its name.
		4. Require a walk-in name when there is no account. mandatory_depends_on
		   is CLIENT-ONLY in frappe — there is no server-side evaluation of it
		   anywhere in the model layer — so this check is the real enforcement,
		   not a convenience.
		"""
		self.customer = self.customer or None
		self.customer_name = (self.customer_name or "").strip() or None
		self.customer_phone = (self.customer_phone or "").strip() or None

		if self.flags.customer_created and not self.customer:
			frappe.throw(
				_("An online booking must belong to a customer account."),
				frappe.ValidationError,
			)

		if self.customer:
			self.customer_name = frappe.db.get_value(
				"User", self.customer, "full_name"
			)
			# The phone is a WALK-IN field (hidden behind depends_on for an
			# account booking) — an account's number lives on its own profile.
			# Clearing it stops an API caller storing a value no screen shows.
			self.customer_phone = None
			return

		if not self.customer_name:
			frappe.throw(
				_(
					"Provide a customer account or a walk-in name — a booking "
					"has to say who it is for."
				)
			)

	# ------------------------------------------------------------------
	# Chain & immutability
	# ------------------------------------------------------------------

	def _validate_chain(self, chain):
		if self.branch != chain.branch:
			frappe.throw(
				_("Court {0} belongs to branch {1}, not {2}.").format(
					self.court, chain.branch, self.branch
				)
			)
		if self.company != chain.company:
			frappe.throw(
				_("Branch {0} belongs to company {1}, not {2}.").format(
					self.branch, chain.company, self.company
				)
			)

	def _validate_immutables(self):
		before = frappe.db.get_value(
			"CBT Court Booking", self.name, list(IMMUTABLE_FIELDS), as_dict=True
		)
		if not before:
			return
		for fieldname in IMMUTABLE_FIELDS:
			old, new = before.get(fieldname), self.get(fieldname)
			if fieldname in ("start_time", "end_time"):
				old = old and _as_timedelta(old)
				new = new and _as_timedelta(new)
			elif fieldname == "booking_date":
				old = old and getdate(old)
				new = new and getdate(new)
			elif fieldname == "number_of_slots":
				old, new = cint(old), cint(new)
			if old != new:
				frappe.throw(
					_(
						"{0} cannot be changed after the booking is created — "
						"cancel and rebook instead."
					).format(_(self.meta.get_label(fieldname)))
				)

	def _validate_status_transition(self):
		before = frappe.db.get_value(
			"CBT Court Booking", self.name, "booking_status"
		)
		if not before or before == self.booking_status:
			return
		allowed = ALLOWED_TRANSITIONS.get(before, set())
		if self.booking_status not in allowed:
			frappe.throw(
				_("A {0} booking cannot become {1}.").format(
					_(before), _(self.booking_status)
				)
			)
		if self.booking_status == "Confirmed":
			# Confirming is booking-creating in spirit: it consummates the
			# hold. Suspended companies must not confirm (PLAN §5).
			require_company_access(self.company)
			if before == "No Show":
				# Section-16 UNDO. This branch keys on the NEW status, so the
				# check belongs INSIDE it — an `elif before == "No Show"` beside
				# it is unreachable (the new status is Confirmed either way) and
				# would ship an undo with no double-booking guard at all.
				#
				# A No Show booking has been OFF the board, possibly for hours:
				# its slot is not merely free, the whole point of the feature is
				# that somebody may have bought it. So the insert-time guard has
				# to run again by hand — it fires on is_new() otherwise
				# (confirm_booking's dead-hold branch is the in-repo precedent).
				# It re-checks SLOT BLOCKS too, so an undo into a window blocked
				# since the release fails with "This time is blocked…" — correct,
				# and stated here because it does not read like a booking clash.
				self._validate_no_overlap_locked()
			self.confirmed_by = self.confirmed_by or frappe.session.user
			self.confirmed_at = self.confirmed_at or clock.now_dt()
		elif before == "Expired" and self.booking_status == "Completed":
			# Retro-confirm (section-5, PLAN §5a): only with a proof on file,
			# and it consummates revenue — the suspension gate re-runs.
			from court_booking_tech.verification import has_reviewable_proof

			if not has_reviewable_proof(self.name):
				frappe.throw(
					_(
						"An Expired booking can only be retro-confirmed when a "
						"payment proof is on file."
					)
				)
			require_company_access(self.company)
			self.confirmed_by = self.confirmed_by or frappe.session.user
			self.confirmed_at = self.confirmed_at or clock.now_dt()

	# ------------------------------------------------------------------
	# Insert pipeline
	# ------------------------------------------------------------------

	def _validate_slots_and_hours(self):
		if cint(self.number_of_slots) < 1:
			frappe.throw(_("Number of Slots must be at least 1."))
		branch_doc = frappe.get_doc("CBT Branch", self.branch)
		grid = get_slot_grid(branch_doc, self.booking_date)
		if not grid:
			frappe.throw(
				_("Branch {0} is closed on {1}.").format(
					self.branch, getdate(self.booking_date).strftime("%A")
				)
			)
		start = _as_timedelta(self.start_time)
		index = next(
			(i for i, slot in enumerate(grid) if slot["start_time"] == start),
			None,
		)
		if index is None:
			frappe.throw(
				_(
					"Start Time {0} is not on the slot grid for this branch "
					"(slots start every {1} minutes from {2})."
				).format(
					self.start_time,
					branch_doc.get_slot_duration_minutes() or 60,
					grid[0]["start_time"],
				)
			)
		last = index + cint(self.number_of_slots) - 1
		if last >= len(grid):
			frappe.throw(
				_(
					"{0} slot(s) from {1} would run past closing time — only "
					"{2} slot(s) remain in the day."
				).format(self.number_of_slots, self.start_time, len(grid) - index)
			)
		self._branch_doc = branch_doc
		self._grid_slice = grid[index : last + 1]
		self.end_time = self._grid_slice[-1]["end_time"]

	def _compute_amounts(self):
		"""Section-14 money rules. Three branches, ONE tail.

		1. INSERT, no override -> price each slot through the pricing seam and
		   store the resulting segments. A rule-less court yields [] and falls
		   straight through to the flat formula, exactly as before section-14.
		2. OVERRIDE -> segments stay EMPTY and the flat formula applies. An
		   override is either an explicit `flags.rate_override` (set by the
		   APIs when the caller supplied a rate) or an insert whose
		   `hourly_rate` differs from the court's base. The second test works
		   because `fetch_from` + `fetch_if_empty` have ALREADY filled an
		   omitted rate with the base by the time validate runs (_validate_links
		   runs first — S13 as-built 4), so "omitted" and "base" are the same
		   thing here and only a deliberately different number reads as intent.
		3. UPDATE -> a staff edit that CHANGES hourly_rate against the stored
		   value on a segmented booking is a deliberate switch to a flat price:
		   clear the segments. Anything else (a discount edit, a status flip
		   through confirm/cancel/extend, a plain re-save) keeps them.

		The tail is the important part: whenever segments exist the total comes
		from Σ(segment.amount) and the blended `hourly_rate` is OUTPUT ONLY.
		Re-deriving the total from the blend drifts — ₱500×1h + ₱400×2h = ₱1,300
		blends to 433.33, and 433.33 × 3 = ₱1,299.99. Every re-save would shave
		another centavo off a document someone has already been handed.

		Backlog B27 (2026-08-27): `total_amount` is what the CUSTOMER pays —
		the court (after discount) PLUS `platform_fee`, the per-booking add-on a
		Per Booking tenant's customers carry. The fee is fixed ONCE, at insert,
		through court_booking_tech.platform_fees (the same call the checkout
		quote makes), unless the caller says it is CARRIED from a booking being
		moved (`flags.platform_fee_carried`, set by reschedule_booking — the unit
		was already sold). Every later recompute keeps the stored fee: a
		discount edit or a status flip never re-reads the tiers.
		"""
		if self.is_new():
			# Billable time excludes turnover buffers between slots (as-built
			# decision): n × slot duration, NOT end − start.
			duration = self._grid_slice[0]["end_time"] - self._grid_slice[0]["start_time"]
			self.duration_hours = flt(
				cint(self.number_of_slots) * duration.total_seconds() / 3600.0, 2
			)
			if self.flags.rate_override or self._rate_overrides_court_base():
				self.set("rate_segments", [])
			else:
				self.set(
					"rate_segments",
					pricing.build_rate_segments(
						self.court, self.booking_date, self._grid_slice
					),
				)
		elif self.get("rate_segments"):
			stored_rate = frappe.db.get_value(
				"CBT Court Booking", self.name, "hourly_rate"
			)
			if flt(self.hourly_rate, 2) != flt(stored_rate, 2):
				# One-way door, by design: nothing ever rebuilds segments from
				# the court's rules afterwards. The staff decided this booking
				# has one price, and a later rule edit must not undo that.
				self.set("rate_segments", [])

		if self.get("rate_segments"):
			court_total = pricing.total_from_segments(
				self.rate_segments, self.discount_percent
			)
			self.hourly_rate = pricing.blended_rate(
				self.rate_segments, self.duration_hours
			)
		else:
			# Update path: the schedule is immutable, so duration_hours stays as
			# stored; only rate/discount edits change the total.
			court_total = flt(
				flt(self.hourly_rate)
				* flt(self.duration_hours)
				* (1 - flt(self.discount_percent) / 100.0),
				2,
			)

		if self.is_new():
			chained_to = self.flags.get("fee_chained_to")
			if chained_to:
				# Backlog B49: a CONTINUATION — the session's one fee sits on its
				# head; this row carries no unit and prints the head's fee as waived.
				head = frappe.db.get_value(
					"CBT Court Booking",
					chained_to,
					["platform_fee", "company"],
					as_dict=True,
				)
				if not head or head.company != self.company:
					frappe.throw(_("Continuation head {0} not found.").format(chained_to))
				self.platform_fee_seq, self.platform_fee = 0, 0.0
				self.fee_chained_to = chained_to
				self.platform_fee_waived = flt(head.platform_fee, 2)
			elif not self.flags.platform_fee_carried:
				self.platform_fee_seq, self.platform_fee = platform_fees.booking_fee(
					self.company, self.booking_date, self.payment_method
				)
		else:
			# PINNED from the stored row on every update (B27's ducky): the two
			# fields are read-only on the form, but read_only is a UI property —
			# a REST write from a seat with write on its own bookings could zero
			# the fee the tenant owes us, and the invoice sync would copy it.
			# B49's chain fields ride the same pin — which is also why the cancel
			# hand-over below writes with db.set_value, never save().
			stored = frappe.db.get_value(
				"CBT Court Booking",
				self.name,
				[
					"platform_fee",
					"platform_fee_seq",
					"credit_applied",
					"credit_document",
					"fee_chained_to",
					"platform_fee_waived",
				],
				as_dict=True,
			)
			if stored:
				self.platform_fee = stored.platform_fee
				self.platform_fee_seq = stored.platform_fee_seq
				self.fee_chained_to = stored.fee_chained_to
				self.platform_fee_waived = stored.platform_fee_waived
				# B39 rides the same pin, for the same reason: read_only is a UI
				# property and a re-save must never invent settled money.
				self.credit_applied = stored.credit_applied
				self.credit_document = stored.credit_document
		self.platform_fee = flt(self.platform_fee, 2)
		self.platform_fee_seq = cint(self.platform_fee_seq)
		self.platform_fee_waived = flt(self.get("platform_fee_waived"), 2)
		self.total_amount = flt(court_total + self.platform_fee, 2)

	def on_update(self):
		self._hand_fee_to_continuation()

	def _hand_fee_to_continuation(self):
		"""Backlog B49 (user ruling): cancelling an UNPAID head hands its exact
		fee and unit number to its continuation, so a chain cannot be booked and
		then trimmed into a free ride. Fires on the transition INTO Cancelled only
		(the sweep's Expired goes through db.set_value and expires the cart as
		one, by design), for a fee-carrying head, to a continuation that is still
		Reserved with no claiming proof, never across a closed month. Written with
		db.set_value because the update-path pin in _compute_amounts would revert
		a save() (the ducky's finding 1), then the invoice is re-synced by hand
		exactly as the sweep does. The rest of the chain follows its new head.
		"""
		before = self.get_doc_before_save()
		if not before or before.booking_status == self.booking_status:
			return
		if self.booking_status != "Cancelled":
			return
		if not cint(self.platform_fee_seq) or flt(self.platform_fee) <= 0:
			return
		if platform_fees.closed_month_key(self.booking_date):
			return
		rows = frappe.db.sql(
			"""
			SELECT name, total_amount
			FROM `tabCBT Court Booking`
			WHERE fee_chained_to = %s AND booking_status = 'Reserved'
			ORDER BY start_time ASC
			FOR UPDATE
			""",
			(self.name,),
			as_dict=True,
		)
		if not rows:
			return
		tail = rows[0]
		if frappe.db.exists(
			"CBT Payment Proof",
			{"booking": tail.name, "status": ("in", ("Pending", "Accepted"))},
		):
			return
		frappe.db.set_value(
			"CBT Court Booking",
			tail.name,
			{
				"platform_fee_seq": cint(self.platform_fee_seq),
				"platform_fee": flt(self.platform_fee, 2),
				"platform_fee_waived": 0,
				"fee_chained_to": None,
				"total_amount": flt(flt(tail.total_amount) + flt(self.platform_fee), 2),
			},
		)
		frappe.db.sql(
			"""
			UPDATE `tabCBT Court Booking` SET fee_chained_to = %s
			WHERE fee_chained_to = %s AND name != %s
			""",
			(tail.name, self.name, tail.name),
		)
		from court_booking_tech.billing import sync_invoice_for_booking

		sync_invoice_for_booking(tail.name)

	def _apply_credit(self):
		"""Backlog B39. AFTER the overlap lock (a booking that loses the race
		must drain nothing) and BEFORE the payment method (which reads the
		result to decide the status). See docs/sections/section-26.md."""
		if self.flags.get("credit_carried"):
			# A reschedule brings the settled credit across untouched — a move is
			# not a new sale, the same rule platform_fee follows.
			return
		credits.take_credit(self, bool(self.flags.get("apply_credit")))

	def _rate_overrides_court_base(self) -> bool:
		"""Did the CALLER choose this rate, rather than inherit the court's?

		Only meaningful on insert (see _compute_amounts). Note the deliberate
		blind spot recorded in the section: an override set to EXACTLY the base
		rate is indistinguishable from no override at all, so the APIs set
		flags.rate_override explicitly rather than relying on this.
		"""
		base = frappe.db.get_value("CBT Court", self.court, "hourly_rate")
		return flt(self.hourly_rate, 2) != flt(base, 2)

	def _reject_past_for_customers(self):
		# Staff may back-record walk-ins; the S9 portal booking API sets
		# flags.customer_created — customers cannot book the past.
		if not self.flags.customer_created:
			return
		if _slot_dt(self.booking_date, _as_timedelta(self.start_time)) < clock.now_dt():
			frappe.throw(_("This slot is already in the past."))

	def _reject_beyond_horizon_for_customers(self):
		"""The other end of the same fence as _reject_past_for_customers.

		The portal date strip stops at the company's horizon, but the strip is
		client code: /book?d=, a saved link or a hand-rolled POST to
		api.portal.reserve_booking would otherwise reserve a court in 2031 —
		free to make, never shown up for. Staff keep booking any date at the
		desk (blocked-out tournaments, a season's league night), so this rides
		the same flags.customer_created branch as the past-slot reject.

		Inclusive: horizon N means today .. today + N are all bookable, which
		is exactly what the picker offers.
		"""
		if not self.flags.customer_created:
			return
		horizon = resolve_advance_booking_days(
			frappe.db.get_value("CBT Company", self.company, "advance_booking_days")
		)
		last_date = clock.now_dt().date() + timedelta(days=horizon)
		if getdate(self.booking_date) > last_date:
			frappe.throw(
				_(
					"This facility takes online bookings up to {0} days ahead — "
					"please choose a date on or before {1}."
				).format(horizon, frappe.format(last_date, {"fieldtype": "Date"}))
			)

	def _auto_check_in(self):
		"""Stamp attendance at insert when the booking IS the person turning up
		(section-16). Insert path only — called from validate's is_new() branch.

		Two cases, and both exist to keep the no-show sweep off bookings that
		were never in doubt:

		(a) an EXTENSION (`extended_from`) — "one more hour" is asked for by
		    people already on the court;
		(b) a STAFF back-record of a slot that has already started (NOT
		    flags.customer_created, start in the past) — the player is standing
		    at the desk (S4 as-built 6). This is also what keeps a section-13
		    walk-in booked mid-session out of the release path, and it is what
		    makes the released slot RESELLABLE: the replacement walk-in is a
		    back-record, so it is born checked in and the next sweep leaves it
		    alone.

		A customer-created booking NEVER auto-checks: a customer cannot even
		book the past (_reject_past_for_customers), and case (b) is a statement
		about somebody physically at the counter.

		A RESCHEDULED booking does not INHERIT check-in — reschedule_booking
		copies an explicit field list and these are not on it, so a move is a
		new booking and a new attendance question. It can still auto-check
		through case (b) if the staff move it onto a slot already in progress,
		which is the same "they are here now" statement and is deliberate.
		"""
		if self.checked_in_at:
			return
		started = (
			_slot_dt(self.booking_date, _as_timedelta(self.start_time)) < clock.now_dt()
		)
		if self.extended_from or (not self.flags.customer_created and started):
			self.checked_in_at = clock.now_dt()
			self.checked_in_by = frappe.session.user

	def _validate_no_overlap_locked(self):
		"""Race-safe double-booking guard (PLAN §5).

		SELECT … FOR UPDATE serializes concurrent inserts on the same
		court+date inside the insert transaction (frappe runs validate in it;
		never commit mid-validate). The loser sees the winner's row and gets a
		clean error.

		ONE booking may be excused, via `flags.reschedule_of` (section-15): the
		booking this insert REPLACES, which api/bookings.reschedule_booking
		cancels later in the SAME transaction. Without it the two commonest desk
		moves — "same court, make it two hours" and "shift it 30 minutes" —
		collide with the very booking being moved. The licence is deliberately
		one NAMED row, not "skip the overlap check":
		- it cannot be forged from outside. `flags` is in frappe's
		  RESERVED_KEYWORDS (base_document.py), so both `update()` and `set()`
		  drop it — a REST payload or frappe.client.insert can never set it.
		- every OTHER booking on that court+date still collides normally, and
		  the slot-block loop below is untouched (a move onto a blocked window
		  is still refused).
		- reschedule_booking VERIFIES the original really reached Cancelled
		  before it returns, so the excused row can never survive the request.
		"""
		start = _as_timedelta(self.start_time)
		end = _as_timedelta(self.end_time)
		now = clock.now_dt()
		replaces = self.flags.get("reschedule_of")

		rows = frappe.db.sql(
			"""
			SELECT name, start_time, end_time, booking_status,
			       reservation_expires_at, verification_deadline_at
			FROM `tabCBT Court Booking`
			WHERE court = %s AND booking_date = %s
			  AND booking_status IN ('Reserved', 'Confirmed', 'Extended')
			FOR UPDATE
			""",
			(self.court, getdate(self.booking_date)),
			as_dict=True,
		)
		for row in rows:
			if row.name == self.name or row.name == replaces:
				continue
			if not is_reserved_hold_live(
				row.booking_status,
				row.reservation_expires_at,
				now,
				row.verification_deadline_at,
			):
				continue  # both clocks lapsed — slot is free
			if _overlaps(
				start, end, _as_timedelta(row.start_time), _as_timedelta(row.end_time)
			):
				frappe.throw(
					_("Slot already taken ({0} is booked {1}–{2}).").format(
						self.court, row.start_time, row.end_time
					)
				)

		blocks = frappe.db.sql(
			"""
			SELECT court, start_time, end_time, reason
			FROM `tabCBT Slot Block`
			WHERE branch = %s AND block_date = %s
			  AND (court = %s OR court IS NULL OR court = '')
			FOR UPDATE
			""",
			(self.branch, getdate(self.booking_date), self.court),
			as_dict=True,
		)
		for block in blocks:
			if _overlaps(
				start,
				end,
				_as_timedelta(block.start_time),
				_as_timedelta(block.end_time),
			):
				scope = _("this court") if block.court else _("the whole branch")
				frappe.throw(
					_("This time is blocked for {0} ({1}).").format(
						scope, _(block.reason or "Other")
					)
				)

	def _apply_payment_method(self):
		# Status on insert is SERVER-DERIVED from the payment method — seeds
		# and tests needing other statuses insert then transition legally.
		if self.payment_method not in ("Cash", "Free", "Fund Transfer"):
			frappe.throw(_("Payment Method is required."))
		# B39: credit that covers the whole price IS payment — a Fund Transfer
		# booking must not sit Reserved waiting for a receipt for ₱0.
		fully_prepaid = (
			flt(self.credit_applied) >= flt(self.total_amount) > 0
		)
		if self.payment_method in ("Cash", "Free") or fully_prepaid:
			self.booking_status = "Confirmed"
			# `or session.user` (section-15), mirroring the transition branches
			# in _validate_status_transition. It exists for exactly ONE caller:
			# api/bookings.reschedule_booking pre-sets the ORIGINAL booking's
			# verification stamps on the replacement, because the payment was
			# verified once, at the desk, by whoever took the cash — not by the
			# staff member who later moved the slot. Nothing else in the app
			# (no seed, patch or script) pre-sets these on insert, so every
			# other caller keeps its old behaviour byte-for-byte.
			self.confirmed_by = self.confirmed_by or frappe.session.user
			self.confirmed_at = self.confirmed_at or clock.now_dt()
			self.reservation_expires_at = None
		elif self.payment_method == "Fund Transfer":
			self.booking_status = "Reserved"
			now = clock.now_dt()
			minutes = reservation_expiry_minutes(self.company)
			# B35: reserve_cart injects ONE shared expiry for the whole cart.
			shared = self.flags.get("cart_expires_at")
			if shared and now < get_datetime(shared) <= now + timedelta(minutes=minutes):
				self.reservation_expires_at = get_datetime(shared)
			else:
				self.reservation_expires_at = now + timedelta(minutes=minutes)
		else:
			frappe.throw(_("Payment Method is required."))
