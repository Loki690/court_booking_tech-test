# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Scheduled tasks (section-4). expire_reservations runs every minute (hooks.py
cron) — it NEVER commits itself: the scheduler wraps jobs in a transaction,
and backend tests call it directly under FrappeTestCase savepoints.

E2E never waits on cron: run_expiry_sweep() is the deterministic trigger,
hard-gated on allow_tests (consumed by E2E file 07 in section-9, smoke-tested
by the section-4 E2E file).
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from court_booking_tech import clock, credits
from court_booking_tech.billing import sync_invoice_for_booking


def _has_pending_proof(booking: str) -> bool:
	"""A Pending proof hands the booking to the verification clock (section-5).
	The table_exists guard stays: on a fresh install the scheduler can fire
	between app install and migrate creating the table."""
	if not frappe.db.table_exists("CBT Payment Proof"):
		return False
	return bool(
		frappe.db.exists(
			"CBT Payment Proof", {"booking": booking, "status": "Pending"}
		)
	)


def expire_reservations():
	"""The per-minute sweep — both clocks (PLAN §5/§5a).

	1. Reserved → Expired, inside a row-locked read (same discipline as the
	   double-booking guard), when its governing clock has run out:
	   - a Pending proof exists → the VERIFICATION clock governs: expire only
	     past verification_deadline_at — the base clock is ignored;
	   - no Pending proof → the BASE clock governs (reservation_expires_at);
	   - either way, past the booking END the hold expires unconditionally
	     (the slot was consumed — hard cap, PLAN §5a; also bounds a regrace
	     granted shortly before the booking end).
	2. No-show release (section-16, PLAN §8s, opt-in per company): a Confirmed
	   booking nobody checked in for goes back on sale X minutes after it was
	   due to start — see _release_no_shows for the safety rail that makes
	   switching the knob on a live facility safe.
	3. Post-end tidy: Confirmed bookings past their end datetime → Completed.
	"""
	now = clock.now_dt()

	expired_rows = frappe.db.sql(
		"""
		SELECT name, reservation_expires_at, verification_deadline_at,
		       TIMESTAMP(booking_date, end_time) AS end_dt
		FROM `tabCBT Court Booking`
		WHERE booking_status = 'Reserved'
		  AND (
		    (reservation_expires_at IS NOT NULL AND reservation_expires_at < %s)
		    OR (verification_deadline_at IS NOT NULL AND verification_deadline_at < %s)
		    OR TIMESTAMP(booking_date, end_time) < %s
		  )
		FOR UPDATE
		""",
		(now, now, now),
		as_dict=True,
	)
	expired = []
	for row in expired_rows:
		past_end = get_datetime(row.end_dt) < now
		if _has_pending_proof(row.name):
			deadline_lapsed = (
				row.verification_deadline_at
				and get_datetime(row.verification_deadline_at) < now
			)
			if not (past_end or deadline_lapsed):
				continue  # verification clock still running
		else:
			base_lapsed = (
				row.reservation_expires_at
				and get_datetime(row.reservation_expires_at) < now
			)
			if not (past_end or base_lapsed):
				continue  # regrace window still open
		frappe.db.set_value(
			"CBT Court Booking", row.name, "booking_status", "Expired"
		)
		# set_value fires no doc_events — flip the billing doc here (S6).
		sync_invoice_for_booking(row.name)
		# B39: a hold that lapses must hand the customer's store credit back.
		# Only Cancelled and Expired restore — a NO SHOW forfeits.
		credits.restore_credit(row.name)
		expired.append(row.name)

	no_show = _release_no_shows(now)

	completed_rows = frappe.db.sql(
		"""
		SELECT name
		FROM `tabCBT Court Booking`
		WHERE booking_status = 'Confirmed'
		  AND TIMESTAMP(booking_date, end_time) < %s
		FOR UPDATE
		""",
		(now,),
		as_dict=True,
	)
	for row in completed_rows:
		frappe.db.set_value(
			"CBT Court Booking", row.name, "booking_status", "Completed"
		)
		# Confirmed → Completed keeps the invoice Paid & Verified; the sync
		# is called for the one-flip-per-booking consistency guarantee.
		sync_invoice_for_booking(row.name)

	return {
		"expired": expired,
		"no_show": no_show,
		"completed": [row.name for row in completed_rows],
	}


def _release_no_shows(now) -> list:
	"""Step 2 of the sweep — free a paid slot nobody turned up for (§8s).

	Runs BETWEEN the expiry loop and the post-end tidy, and the ordering is
	load-bearing in one direction only: the two steps are disjoint by
	construction, because this one requires `now <= end_dt` and the tidy
	requires `end_dt < now`.

	THE `end_dt` BOUND IS THE SAFETY RAIL, not an optimisation. It is what makes
	switching the knob on a live facility safe: a company with years of
	un-checked-in Confirmed history has all of it past its end, so every one of
	those rows falls through to the tidy as Completed and NOTHING is rewritten.
	The single edge — enabling the knob mid-afternoon releases a currently
	RUNNING unattended booking — is real, documented, and undoable from the
	board.

	Both bounds are in SQL rather than in the loop for a second reason: they cap
	what FOR UPDATE locks to the handful of bookings actually in progress. Left
	to Python this would lock every future Confirmed booking of every opted-in
	tenant once a minute — thousands of rows on a facility taking bookings 90
	days out, against exactly the rows the desk is editing.

	The per-row savepoint is the third: the scheduler wraps this whole job in
	ONE transaction, so an exception here does not merely skip a booking, it
	rolls back the expiry flips already made in the same run — every minute,
	for every tenant. sync_invoice_for_booking loads and saves another document
	and is the live throw surface, so one bad row becomes a logged skip instead
	of a platform-wide stall.
	"""
	# Raw SQL, not frappe.get_all: this runs under whatever session called the
	# sweep (the scheduler's Administrator in production, a tenant staff user in
	# an isolation test), and a permission-scoped knob map would silently narrow
	# to one company — a sweep that quietly does less is worse than one that
	# fails. The rest of this module is raw SQL for the same reason.
	knobs = {
		row.name: cint(row.no_show_release_minutes)
		for row in frappe.db.sql(
			"""
			SELECT name, no_show_release_minutes
			FROM `tabCBT Company`
			WHERE no_show_release_minutes > 0
			""",
			as_dict=True,
		)
	}
	if not knobs:
		# The DEFAULT state of every site: the feature ships off, so the sweep
		# must not pay for it — and `company IN ()` is a MariaDB syntax error,
		# which inside the scheduler's single transaction would take the expiry
		# clock down with it.
		return []

	rows = frappe.db.sql(
		"""
		SELECT name, company, TIMESTAMP(booking_date, start_time) AS start_dt
		FROM `tabCBT Court Booking`
		WHERE booking_status = 'Confirmed'
		  AND checked_in_at IS NULL
		  AND company IN %(companies)s
		  AND TIMESTAMP(booking_date, start_time) < %(now)s
		  AND TIMESTAMP(booking_date, end_time) >= %(now)s
		FOR UPDATE
		""",
		{"companies": tuple(knobs), "now": now},
		as_dict=True,
	)

	released = []
	for row in rows:
		grace = knobs.get(row.company, 0)
		if not grace:
			continue
		if get_datetime(row.start_dt) + timedelta(minutes=grace) >= now:
			continue  # still inside the grace window — they may yet walk in
		savepoint = f"cbt_no_show_{len(released)}"
		frappe.db.savepoint(savepoint)
		try:
			frappe.db.set_value(
				"CBT Court Booking", row.name, "booking_status", "No Show"
			)
			# set_value fires no doc_events — flip the billing doc here (S6).
			# The status maps to the SAME "Paid & Verified" the booking already
			# had (the payment was real; the slot is forfeit, not refunded), and
			# the call is made anyway for the one-flip-per-booking guarantee.
			sync_invoice_for_booking(row.name)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"CBT no-show release failed ({row.name})",
			)
			continue
		released.append(row.name)
	return released


@frappe.whitelist(methods=["POST"])
def run_expiry_sweep():
	"""Deterministic E2E trigger — runs the sweep on demand instead of
	waiting for cron. Hard-gated: test/dev sites only."""
	if not frappe.conf.get("allow_tests"):
		frappe.throw(
			_("run_expiry_sweep is available only on test sites (allow_tests)."),
			frappe.PermissionError,
		)
	return expire_reservations()
