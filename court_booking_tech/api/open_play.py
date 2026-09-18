# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Open Play engine (section-10, PLAN §4/§7/§8q) — the ONLY write path for
CBT Open Play Session's participants, queue and assignments.

Locking discipline: every entry point locks the session row first (same
FOR-UPDATE discipline as the booking guard and the expiry sweep), so a queue
drag-reorder racing a Game Done serializes instead of interleaving — the
section-10 gotcha that motivated the lock.

Gate map (two questions, two answers — PLAN §5's suspension doctrine):
- revenue-creating (open a session, add players, confirm a payment) →
  require_company_access(company), suspension gate ON;
- operating an existing session (start, rotate, backfill, remove, reorder,
  complete, cancel, read the board, receive a proof) → allow_suspended=True.

CLOCK-FREE (PLAN §8q): participants are physically present at a staffed
session, so their fund-transfer proofs are plain Pending-until-staff-confirm.
Nothing here writes a verification deadline, and the per-minute sweep only
reads `tabCBT Court Booking` — the exemption holds by construction.

Queue-position invariant: `queue_position` is meaningful for WAITING rows only
(1..n, no gaps, reindexed after every mutation). Playing / Done / Left rows
carry 0, and queue ORDER is driven by queue_position — never by row idx, which
stays at insertion order while players rotate to the back of the line.

IDENTITY (section-20, Backlog B2): a player may be a WALK-IN with no account,
so `customer` is no longer the rotation identity — `_player_key(queue_row)` is.
See that function; it is this module's load-bearing wall.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from court_booking_tech import clock, credits, payment_channels, platform_fees
from court_booking_tech.api.proofs import _validate_file
from court_booking_tech.billing import (
	PAID,
	create_invoice_for_participant,
	require_refund,
	sync_invoice_for_participant,
)
from court_booking_tech.court_booking_tech.doctype.cbt_open_play_session.cbt_open_play_session import (
	PLAYER_FIELDS,
	PLAYERS_PER_COURT,
)
from court_booking_tech.membership import has_active_membership
from court_booking_tech.slots import _as_timedelta, _fmt
from court_booking_tech.tenancy import get_session_company, require_company_access

# Sorting sentinel for "send to the back of the line" — reindexed away
# immediately, never persisted as a real position.
END_OF_QUEUE = 100000


# ---------------------------------------------------------------------------
# Locking & row helpers
# ---------------------------------------------------------------------------


def _locked_session(name: str):
	"""Lock the session row, then load it fresh (post-lock state)."""
	if not frappe.db.exists("CBT Open Play Session", name):
		frappe.throw(
			_("Open play session {0} not found.").format(name), frappe.DoesNotExistError
		)
	frappe.db.get_value("CBT Open Play Session", name, "name", for_update=True)
	return frappe.get_doc("CBT Open Play Session", name)


def _save(doc):
	"""Every engine write carries the flag the controller's ownership guards
	look for. Permissions stay ON (defense in depth) — the endpoint already
	passed the tenancy choke point."""
	doc.flags.via_open_play_engine = True
	doc.save()
	return doc


def _require_status(doc, *allowed):
	if doc.status not in allowed:
		frappe.throw(
			_("This action needs a {0} session — this one is {1}.").format(
				" / ".join(_(status) for status in allowed), _(doc.status)
			)
		)


def _waiting_rows(doc):
	return sorted(
		[row for row in (doc.queue or []) if row.status == "Waiting"],
		key=lambda row: cint(row.queue_position),
	)


def _reindex_waiting(doc):
	"""1..n over the waiting rows in their current order; everyone else 0."""
	for position, row in enumerate(_waiting_rows(doc), start=1):
		row.queue_position = position
	for row in doc.queue or []:
		if row.status != "Waiting":
			row.queue_position = 0


def _player_key(queue_row) -> str:
	"""The rotation identity of a QUEUE row (section-20, Backlog B2).

	An account player keeps their user id, so every assignment value written
	before this section — and every backend/E2E assertion built on emails —
	stays literally correct. A WALK-IN has no account, and `customer` NULL
	would alias every walk-in in the session into one identity: `_queue_row`
	would hand the first walk-in it met to whichever ✕ button was pressed. So
	they key on their own queue-row name, which is unique and stable, and which
	`CBT Open Play Queue.participant_ref` already precedents as a cross-table
	row reference.

	QUEUE ROWS ONLY. A participant row has a DIFFERENT name from its queue row,
	so passing one here would mint a key no assignment can ever match. The
	participants payload renders `customer_name` directly instead.

	The key is unique among LIVE rows, which is all the engine needs — it is NOT
	globally unique: a player who leaves and rejoins gets a fresh pair, so an
	account can own several rows and `_queue_row`'s status filter is what
	disambiguates them (the same rule that predates this section). Walk-ins are
	additionally unique by construction, one key per row.

	Fails loud on an unsaved row: `frappe.model.base_document._init_child` never
	assigns `name` before the parent is saved (it sets `__temporary_name`), so
	an appended walk-in row would key as None here — which is exactly the
	aliasing this function exists to prevent. Nothing may key a row that has not
	been persisted.
	"""
	key = queue_row.customer or queue_row.name
	if not key:
		frappe.throw(
			_("Cannot identify a player on an unsaved queue row."),
			frappe.ValidationError,
		)
	return key


def _queue_row(doc, key, *, status=None):
	for row in doc.queue or []:
		if _player_key(row) != key:
			continue
		if status is None and row.status != "Left":
			return row
		if status is not None and row.status == status:
			return row
	return None


def _player_display(doc, key) -> str:
	"""Human name for a player key — for messages that a person reads.

	Without this a walk-in's key is a row hash, so "abc123def is not playing on
	court 2" is what staff would be shown.
	"""
	row = _queue_row(doc, key) or _queue_row(doc, key, status="Left")
	return (row.customer_name if row else None) or key


def _participant_row(doc, participant: str):
	for row in doc.participants or []:
		if row.name == participant:
			return row
	frappe.throw(
		_("Participant {0} is not part of this session.").format(participant),
		frappe.DoesNotExistError,
	)


def _participant_for_queue_row(doc, queue_row):
	if not queue_row.participant_ref:
		return None
	for row in doc.participants or []:
		if row.name == queue_row.participant_ref:
			return row
	return None


def _active_assignment(doc, court: str):
	for row in doc.assignments or []:
		if row.court == court and cint(row.is_active):
			return row
	frappe.throw(_("No game is running on court {0}.").format(court))


def _assign_court(doc, court: str, rows, now):
	"""Seat up to four waiting players on a court and start its clock."""
	payload = {
		"court": court,
		"court_name": frappe.db.get_value("CBT Court", court, "court_name"),
		"started_at": now,
		"is_active": 1,
	}
	if doc.rotation_mode == "Timed":
		payload["ends_at"] = now + timedelta(minutes=cint(doc.rotation_minutes))
	for index, row in enumerate(rows[:PLAYERS_PER_COURT]):
		# The KEY, not the account: player_1..4 are Data since section-20.
		payload[PLAYER_FIELDS[index]] = _player_key(row)
		row.status = "Playing"
		row.queue_position = 0
	return doc.append("assignments", payload)


def _rotate_court(doc, court: str, now):
	"""Game over on `court`: its players go to the BACK of the queue with their
	games counter bumped, then the next four waiting players take the court.
	Shared by the manual Game Done button and the auto-rotate tick."""
	assignment = _active_assignment(doc, court)
	returned = 0
	for field in PLAYER_FIELDS:
		key = assignment.get(field)
		if not key:
			continue
		row = _queue_row(doc, key, status="Playing")
		if not row:
			continue
		row.status = "Waiting"
		row.games_played = cint(row.games_played) + 1
		# Keep player_1..player_4 order among the returning four.
		row.queue_position = END_OF_QUEUE + returned
		returned += 1
	assignment.is_active = 0
	_reindex_waiting(doc)

	next_group = _waiting_rows(doc)[:PLAYERS_PER_COURT]
	seated = []
	if len(next_group) == PLAYERS_PER_COURT:
		_assign_court(doc, court, next_group, now)
		seated = [_player_key(row) for row in next_group]
	_reindex_waiting(doc)
	return seated


def _validate_player_entry(entry) -> tuple[str | None, str | None, str | None]:
	"""EXACTLY ONE identity per add_players entry (section-20, Backlog B2).

	The API-payload rule, deliberately stricter than the stored-row rule the
	session controller enforces: a stored ACCOUNT row legitimately carries both
	`customer` and a fetch_from-derived `customer_name`, but a CALLER that sends
	both is contradicting itself and must be refused rather than silently
	resolved — the S13 `create_booking` doctrine.

	Returns (customer, customer_name, customer_phone), with the walk-in halves
	as None for an account entry so nothing downstream can store a phone number
	no screen ever shows.
	"""
	customer = (entry.get("customer") or "").strip()
	customer_name = (entry.get("customer_name") or "").strip()
	customer_phone = (entry.get("customer_phone") or "").strip()

	if customer and customer_name:
		frappe.throw(
			_(
				"A player is either an account or a walk-in name, never both — "
				"got customer {0} and name {1}."
			).format(customer, customer_name)
		)
	if not customer and not customer_name:
		frappe.throw(_("Every player needs a customer account or a walk-in name."))

	if customer:
		_validate_customer(customer)
		# The phone is a WALK-IN field (PLAN §8j, hidden behind depends_on for
		# an account row) — an account holder's number lives on their profile.
		return customer, None, None
	return None, customer_name, customer_phone or None


def _validate_customer(customer: str):
	"""Server-side re-check of the dialog's Link query — never trust the client
	about who counts as a customer (same doctrine as board.customer_query)."""
	if not customer:
		frappe.throw(_("Pick a customer for every row."))
	enabled = frappe.db.get_value("User", customer, "enabled")
	if enabled is None:
		frappe.throw(_("User {0} does not exist.").format(customer))
	if not cint(enabled):
		frappe.throw(_("User {0} is disabled.").format(customer))
	if "CBT Customer" not in frappe.get_roles(customer):
		frappe.throw(_("{0} is not a customer account.").format(customer))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def open_session(session: str) -> dict:
	"""Scheduled → Open: dedicate the courts by blocking them off the public
	grid for the session window (PLAN §4). The blocks carry a link back to this
	session so cancelling removes exactly its own — matching on the notes text
	would also delete a staff-made "Open Play" block."""
	doc = _locked_session(session)
	require_company_access(doc.company)  # revenue-creating
	_require_status(doc, "Scheduled")
	if not (doc.courts or []):
		frappe.throw(_("Add at least one dedicated court before opening the session."))

	doc.status = "Open"
	_save(doc)

	blocks = []
	for row in doc.courts:
		block = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": doc.branch,
				"court": row.court,
				"block_date": doc.session_date,
				"start_time": doc.start_time,
				"end_time": doc.end_time,
				"reason": "Open Play",
				"open_play_session": doc.name,
				"notes": _("Open Play — {0}").format(doc.name),
			}
		)
		block.insert()
		blocks.append(block.name)
	return {"session": doc.name, "status": doc.status, "blocks": blocks}


@frappe.whitelist(methods=["POST"])
def complete_session(session: str) -> dict:
	"""Open → Completed: end every game and close the queue. The slot blocks
	STAY — they are history for the day that was played, and they only ever
	covered session_date."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")

	doc.status = "Completed"
	for row in doc.assignments or []:
		row.is_active = 0
	for row in doc.queue or []:
		if row.status in ("Waiting", "Playing"):
			row.status = "Done"
			row.queue_position = 0
	_save(doc)
	return {"session": doc.name, "status": doc.status}


def _has_paid_document(doc) -> bool:
	"""Any participant whose billing document is Paid & Verified — the invoice
	is the truth, not the row's payment_status (section-26)."""
	names = [row.billing_doc for row in doc.participants or [] if row.billing_doc]
	if not names:
		return False
	return bool(
		frappe.get_all(
			"CBT Booking Invoice",
			filters={"name": ("in", names), "status": PAID},
			limit=1,
		)
	)


@frappe.whitelist(methods=["POST"])
def cancel_session(
	session: str,
	cancel_billing=0,
	reason: str | None = None,
	cause: str | None = None,
	issue_credit=0,
) -> dict:
	"""Cancel a Scheduled/Open session and free its courts.

	`cancel_billing` mirrors the solo app's two-option prompt: cancel the
	participants' billing documents too, or leave them standing (money already
	reconciled). Cancelled documents are RETAINED with their numbers consumed
	(PLAN §8e) — never deleted.

	Section-26: cancelling the billing of PAID players is a REFUND — Company
	Admin only, with a reason stamped on every paid document it cancels. A
	session with only unpaid players (or `cancel_billing=0`) is still the
	desk's to cancel.
	"""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)  # de-escalation
	_require_status(doc, "Scheduled", "Open")

	issue_credit = cint(issue_credit)
	if issue_credit and not cint(cancel_billing):
		# Crediting while the documents still stand would pay the player twice.
		frappe.throw(
			_("There is nothing to credit while the players' documents stand.")
		)

	refund_reason = None
	refund_cause = None
	if cint(cancel_billing) and _has_paid_document(doc):
		refund_reason, refund_cause = require_refund(
			doc.company,
			reason,
			what="session",
			cause=cause,
			as_credit=bool(issue_credit),
		)

	doc.status = "Cancelled"
	for row in doc.assignments or []:
		row.is_active = 0
	for row in doc.queue or []:
		if row.status in ("Waiting", "Playing"):
			row.status = "Left"
			row.queue_position = 0
	_save(doc)

	removed = frappe.get_all(
		"CBT Slot Block", filters={"open_play_session": doc.name}, pluck="name"
	)
	for name in removed:
		frappe.delete_doc("CBT Slot Block", name, ignore_permissions=True)

	cancelled_invoices = []
	credits_issued = []
	credit_skipped = []
	if cint(cancel_billing):
		for row in doc.participants or []:
			if not row.billing_doc:
				continue
			# Read BEFORE the sync — the sync is what flips it to Cancelled, and
			# only money actually RECEIVED may come back as credit (B53).
			was_paid = (
				frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "status")
				== PAID
			)
			sync_invoice_for_participant(
				row,
				status_override="Cancelled",
				refund_reason=refund_reason,
				refund_cause=refund_cause,
				refund_as_credit=issue_credit,
			)
			cancelled_invoices.append(row.billing_doc)
			if issue_credit and was_paid:
				minted = credits.issue_participant_credit(
					doc.company, row, refund_reason
				)
				if minted:
					credits_issued.append(minted)
				elif not row.customer:
					# A walk-in has no account to hold credit. Reported, never
					# fatal: the session must still cancel.
					credit_skipped.append(row.customer_name or _("Walk-in"))

	return {
		"session": doc.name,
		"status": doc.status,
		"blocks_removed": removed,
		"invoices_cancelled": cancelled_invoices,
		"credits_issued": credits_issued,
		"credit_skipped": credit_skipped,
	}


# ---------------------------------------------------------------------------
# Participants & payments
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def add_players(session: str, players) -> dict:
	"""Add one or more players: a participant row (money) plus a queue row
	(rotation), and a billing document each.

	Each entry carries EXACTLY ONE identity — `customer` (an account) or
	`customer_name` (+ optional `customer_phone`) for a walk-in paying at the
	desk (section-20, Backlog B2).

	Cash/Free are paid on the spot; Fund Transfer stays Unpaid until staff
	confirm it. The invoices are created AFTER the first save so their
	participant_ref points at a persisted child-row name.
	"""
	doc = _locked_session(session)
	require_company_access(doc.company)  # revenue-creating
	_require_status(doc, "Open")

	players = frappe.parse_json(players) or []
	if not players:
		frappe.throw(_("Add at least one player."))

	now = clock.now_dt()
	# Keys, not customers: a set comprehension over `row.customer` would
	# collapse every walk-in into a single None, which both aliases them AND
	# under-counts the capacity check below.
	live = {_player_key(row) for row in (doc.queue or []) if row.status != "Left"}
	cap = cint(doc.max_participants)
	if cap and len(live) + len(players) > cap:
		frappe.throw(
			_(
				"This session is capped at {0} participants — {1} are in and {2} "
				"more were requested."
			).format(cap, len(live), len(players))
		)

	default_method = "Free" if flt(doc.entry_fee) <= 0 else "Cash"
	pairs = []
	for entry in players:
		customer, walkin_name, walkin_phone = _validate_player_entry(entry)
		method = entry.get("payment_method") or default_method
		# Accounts only. Two same-named WALK-INS are legal — they are distinct
		# people with distinct rows and distinct keys, and the desk has no way
		# to tell "the other Wanda" apart from a duplicate submission anyway.
		# (Their keys are their row names, which do not exist until the save
		# below, so there is nothing to add to `live` for them either.)
		if customer:
			if customer in live:
				frappe.throw(_("{0} is already in this session.").format(customer))
			live.add(customer)
		if method not in ("Cash", "Fund Transfer", "Free"):
			frappe.throw(_("Invalid payment method: {0}").format(method))

		paid = method in ("Cash", "Free")
		full_name = (
			frappe.db.get_value("User", customer, "full_name")
			if customer
			else walkin_name
		)
		# Section-11: members pay the SESSION's member rate, not their booking
		# discount — open play is a flat per-head product the organiser prices
		# per session (PLAN §4), so the entitlement is "am I a member here?"
		# and the amount comes from the session. A Free entry is already ₱0;
		# discounting zero would just be noise on the statement.
		#
		# Section-20: a walk-in has no account, so there is no membership to
		# look up — the discount is a plain 0, never a query (S13 doctrine).
		member_discount = (
			flt(doc.member_discount_percent)
			if customer
			and method != "Free"
			and has_active_membership(doc.company, customer)
			else 0
		)
		participant = doc.append(
			"participants",
			{
				# `or None` so the column really holds NULL: an empty string
				# would survive /api/resource's no_nulls serialisation and make
				# a walk-in look like it carries an account.
				"customer": customer or None,
				"customer_name": full_name,
				"customer_phone": walkin_phone,
				# Free means free — never bill a zero-rate entry at the session fee.
				"fee": 0 if method == "Free" else flt(doc.entry_fee),
				"discount_percent": member_discount,
				# Backlog B27: a Per Booking company's flat per-participant add-on,
				# fixed here (like the booking's) and never re-read later.
				"platform_fee": platform_fees.open_play_fee(doc.company, method),
				"payment_method": method,
				# Backlog B29: the desk's channel, or the kind's default; Free
				# carries none. Same rule as a court booking.
				"payment_channel": payment_channels.resolve_channel(
					doc.company, method, entry.get("payment_channel")
				),
				"payment_status": "Paid" if paid else "Unpaid",
				"checked_in_at": now if paid else None,
			},
		)
		queue_row = doc.append(
			"queue",
			{
				"customer": customer or None,
				"customer_name": full_name,
				"queue_position": END_OF_QUEUE,
				"status": "Waiting",
				"games_played": 0,
				"joined_at": now,
			},
		)
		pairs.append((participant, queue_row))

	_reindex_waiting(doc)
	_save(doc)  # child rows get their real names here

	for participant, queue_row in pairs:
		queue_row.participant_ref = participant.name
		create_invoice_for_participant(doc, participant)
	_save(doc)

	return {
		"session": doc.name,
		"added": [participant.name for participant, _row in pairs],
		"current_participants": cint(doc.current_participants),
		"total_revenue": flt(doc.total_revenue),
	}


@frappe.whitelist(methods=["POST"])
def confirm_participant_payment(
	session: str, participant: str, payment_channel: str | None = None
) -> dict:
	"""Fund-transfer participant → Paid, accepting any proof on file and
	flipping the billing document. Allowed while the session is still Open AND
	after it completes — finance genuinely does confirm transfers the next
	working day. `payment_channel` (B29): staff's correction of where the
	money landed, validated like a booking's."""
	doc = _locked_session(session)
	require_company_access(doc.company)  # revenue-creating
	_require_status(doc, "Open", "Completed")
	row = _participant_row(doc, participant)
	if row.payment_status == "Paid":
		frappe.throw(
			_("{0} has already paid.").format(row.customer_name or row.customer)
		)
	payment_channel = (payment_channel or "").strip() or None
	if payment_channel:
		# The row's OWN channel stays legal after being disabled (the dialog
		# pre-selects it) — only a NEW choice must be enabled.
		row.payment_channel = payment_channels.resolve_channel(
			doc.company,
			row.payment_method,
			payment_channel,
			allow_disabled=(payment_channel == row.get("payment_channel")),
		)

	now = clock.now_dt()
	row.payment_status = "Paid"
	row.checked_in_at = row.checked_in_at or now
	_save(doc)

	for name in frappe.get_all(
		"CBT Payment Proof",
		filters={
			"open_play_session": doc.name,
			"participant_ref": row.name,
			"status": "Pending",
		},
		pluck="name",
	):
		frappe.db.set_value("CBT Payment Proof", name, "status", "Accepted")

	sync_invoice_for_participant(row, verified_by=frappe.session.user, verified_at=now)
	return {
		"session": doc.name,
		"participant": row.name,
		"payment_status": row.payment_status,
		"billing_doc": row.billing_doc,
		"total_revenue": flt(doc.total_revenue),
	}


@frappe.whitelist(methods=["POST"])
def upload_participant_proof(
	session: str,
	participant: str,
	reference_no: str | None = None,
	remarks: str | None = None,
	payment_channel: str | None = None,
):
	"""Multipart upload — the file arrives as request file "file". Staff-only
	(a customer session fails closed in require_company_access): open play
	proofs are handed over at the desk, not uploaded from the portal."""
	request_file = (getattr(frappe.request, "files", None) or {}).get("file")
	if request_file is None or not request_file.filename:
		frappe.throw(_("Attach the payment proof as a file named 'file'."))
	return create_participant_proof(
		session,
		participant,
		request_file.filename,
		request_file.stream.read(),
		reference_no,
		remarks,
		payment_channel=payment_channel,
	)


def create_participant_proof(
	session: str,
	participant: str,
	file_name: str,
	content: bytes,
	reference_no: str | None = None,
	remarks: str | None = None,
	payment_channel: str | None = None,
) -> dict:
	"""NO clocks and NO caps (PLAN §8q): there is no future slot to squat and
	no office-hours gap — the player is standing at the desk."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open", "Completed")
	row = _participant_row(doc, participant)
	if row.payment_status == "Paid":
		frappe.throw(
			_("{0} has already paid.").format(row.customer_name or row.customer)
		)
	# B29: the channel the receipt says; defaults to the participant's own,
	# which stays legal even if since disabled (same rule as create_proof).
	payment_channel = (payment_channel or "").strip() or None
	if payment_channel:
		payment_channel = payment_channels.resolve_channel(
			doc.company,
			"Fund Transfer",
			payment_channel,
			allow_disabled=(payment_channel == row.get("payment_channel")),
		)
	else:
		payment_channel = row.get("payment_channel") or None

	_validate_file(file_name, content)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)

	proof = frappe.get_doc(
		{
			"doctype": "CBT Payment Proof",
			"open_play_session": doc.name,
			"participant_ref": row.name,
			"file": file_doc.file_url,
			"source": "Staff",
			"reference_no": reference_no,
			"payment_channel": payment_channel,
			"remarks": remarks,
		}
	).insert(ignore_permissions=True)

	# Re-point the File at its proof (private-file permission checks follow the
	# attached document from here on).
	frappe.db.set_value(
		"File",
		file_doc.name,
		{
			"attached_to_doctype": "CBT Payment Proof",
			"attached_to_name": proof.name,
			"attached_to_field": "file",
		},
	)

	row.proof_ref = proof.name
	_save(doc)
	return {"session": doc.name, "participant": row.name, "proof": proof.name}


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def start_session(session: str) -> dict:
	"""Seat the first four players on each dedicated court, in court order."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")
	if any(cint(row.is_active) for row in (doc.assignments or [])):
		frappe.throw(_("This session has already started."))

	waiting = _waiting_rows(doc)
	if len(waiting) < PLAYERS_PER_COURT:
		frappe.throw(
			_("Need at least {0} players in the queue to start.").format(
				PLAYERS_PER_COURT
			)
		)

	now = clock.now_dt()
	seated = {}
	index = 0
	for court_row in doc.courts or []:
		group = waiting[index : index + PLAYERS_PER_COURT]
		if len(group) < PLAYERS_PER_COURT:
			break  # a court with fewer than four waiting players stays empty
		_assign_court(doc, court_row.court, group, now)
		seated[court_row.court] = [_player_key(row) for row in group]
		index += PLAYERS_PER_COURT

	_reindex_waiting(doc)
	_save(doc)
	return {"session": doc.name, "seated": seated, "server_now": str(now)}


@frappe.whitelist(methods=["POST"])
def game_done(session: str, court: str) -> dict:
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")
	now = clock.now_dt()
	seated = _rotate_court(doc, court, now)
	_save(doc)
	return {"session": doc.name, "court": court, "seated": seated}


@frappe.whitelist(methods=["POST"])
def auto_rotate_tick(session: str) -> dict:
	"""Rotate every court whose Timed game has run out. Driven by the OPEN
	BOARD, deliberately not by cron: a session rotating with nobody in the room
	is not a feature, and the board is the thing that knows staff are watching.
	Idempotent under the row lock."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	now = clock.now_dt()
	if (
		doc.status != "Open"
		or not cint(doc.auto_rotate)
		or doc.rotation_mode != "Timed"
	):
		return {"session": doc.name, "rotated": [], "server_now": str(now)}

	due = [
		row.court
		for row in (doc.assignments or [])
		if cint(row.is_active) and row.ends_at and get_datetime(row.ends_at) <= now
	]
	for court in due:
		_rotate_court(doc, court, now)
	if due:
		_save(doc)
	return {"session": doc.name, "rotated": due, "server_now": str(now)}


@frappe.whitelist(methods=["POST"])
def return_to_queue(session: str, court: str, player: str) -> dict:
	"""Court ✕ — the player sits out but stays in the session, and the court
	keeps a vacant slot for a backfill.

	`player` is a PLAYER KEY (section-20): an account id, or a walk-in's queue
	row name. It is what the board ships and echoes back.
	"""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")

	assignment = _active_assignment(doc, court)
	field = next(
		(f for f in PLAYER_FIELDS if assignment.get(f) == player), None
	)
	if not field:
		frappe.throw(
			_("{0} is not playing on court {1}.").format(
				_player_display(doc, player), court
			)
		)
	assignment.set(field, None)

	row = _queue_row(doc, player, status="Playing")
	if row:
		row.status = "Waiting"
		row.queue_position = END_OF_QUEUE
	_reindex_waiting(doc)
	_save(doc)
	return {"session": doc.name, "court": court, "player": player}


@frappe.whitelist(methods=["POST"])
def backfill(session: str, court: str, player: str) -> dict:
	"""Fill a court's vacant slot from the waiting queue (`player` = a key)."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")

	assignment = _active_assignment(doc, court)
	field = next((f for f in PLAYER_FIELDS if not assignment.get(f)), None)
	if not field:
		frappe.throw(_("Court {0} is already full.").format(court))

	row = _queue_row(doc, player, status="Waiting")
	if not row:
		frappe.throw(
			_("{0} is not waiting in the queue.").format(_player_display(doc, player))
		)

	assignment.set(field, player)
	row.status = "Playing"
	row.queue_position = 0
	_reindex_waiting(doc)
	_save(doc)
	return {"session": doc.name, "court": court, "player": player}


@frappe.whitelist(methods=["POST"])
def remove_from_session(session: str, player: str) -> dict:
	"""Queue ✕ — the player (by KEY) leaves for good. Court ✕ and queue ✕ are
	different actions on purpose (solo-app UX): take them off the court first.

	An unpaid leaver's billing document is cancelled (retained, number
	consumed) — an eternally Unpaid statement for someone who never played is
	not paper anyone wants to explain.
	"""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")

	row = _queue_row(doc, player)
	if not row:
		frappe.throw(
			_("{0} is not in this session.").format(_player_display(doc, player))
		)
	if row.status == "Playing":
		frappe.throw(
			_("Take {0} off the court first, then remove them from the queue.").format(
				row.customer_name or player
			)
		)

	row.status = "Left"
	row.queue_position = 0
	participant = _participant_for_queue_row(doc, row)
	_reindex_waiting(doc)
	_save(doc)

	if participant and participant.payment_status == "Unpaid" and participant.billing_doc:
		sync_invoice_for_participant(participant, status_override="Cancelled")

	return {
		"session": doc.name,
		"player": player,
		"current_participants": cint(doc.current_participants),
	}


@frappe.whitelist(methods=["POST"])
def reorder_queue(session: str, ordered) -> dict:
	"""Persist a drag-reorder. The payload must be EXACTLY the current waiting
	set: a board that has drifted (a game finished mid-drag) must lose, not
	silently reshuffle a queue it no longer describes."""
	doc = _locked_session(session)
	require_company_access(doc.company, allow_suspended=True)
	_require_status(doc, "Open")

	ordered = frappe.parse_json(ordered) or []
	waiting = _waiting_rows(doc)
	if len(ordered) != len(waiting) or {row.name for row in waiting} != set(ordered):
		frappe.throw(
			_(
				"The queue changed while you were reordering it — refresh the "
				"board and try again."
			)
		)

	position = {name: index for index, name in enumerate(ordered)}
	for row in waiting:
		row.queue_position = position[row.name] + 1
	_save(doc)
	return {"session": doc.name, "queue": ordered}


# ---------------------------------------------------------------------------
# Board payload
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_open_play_board(session: str | None = None, company: str | None = None) -> dict:
	"""Today's session list for the scoped company, plus the full state of one
	session when asked (deep link `?session=OPS-…` works on any date).

	Platform scope must pass `company` — require_company_access fails closed on
	an empty one, exactly like get_pending_payments.
	"""
	company = company or get_session_company()
	require_company_access(company, allow_suspended=True)

	now = clock.now_dt()
	sessions = frappe.get_all(
		"CBT Open Play Session",
		filters={"company": company, "session_date": now.date()},
		fields=[
			"name",
			"title",
			"branch",
			"session_date",
			"start_time",
			"end_time",
			"status",
			"rotation_mode",
			"entry_fee",
			"current_participants",
			"total_revenue",
		],
		order_by="start_time asc",
	)
	for row in sessions:
		row["start_time"] = _fmt(_as_timedelta(row["start_time"]))
		row["end_time"] = _fmt(_as_timedelta(row["end_time"]))
		row["branch_name"] = frappe.db.get_value(
			"CBT Branch", row["branch"], "branch_name"
		)

	return {
		"company": company,
		"today": str(now.date()),
		"sessions": sessions,
		"session": _session_state(session) if session else None,
		"server_now": str(now),
	}


def _session_state(name: str) -> dict:
	doc = frappe.get_doc("CBT Open Play Session", name)
	require_company_access(doc.company, allow_suspended=True)

	# Keyed by PLAYER KEY and built from the QUEUE ONLY (section-20). A
	# participant row has a different `name` from its queue row, so seeding this
	# from both tables — which is what the pre-B2 code did, harmlessly, when the
	# key was the shared `customer` — would now mint keys no assignment can
	# match. Every seated player is a queue row by construction.
	display = {
		_player_key(row): (row.customer_name or row.customer or _player_key(row))
		for row in (doc.queue or [])
	}
	# Key -> account, so the payload can keep saying who has an account without
	# the board having to guess from the key's shape.
	account_of = {_player_key(row): row.customer for row in (doc.queue or [])}

	invoice_status = {}
	invoice_names = [row.billing_doc for row in (doc.participants or []) if row.billing_doc]
	if invoice_names:
		invoice_status = {
			row.name: row.status
			for row in frappe.get_all(
				"CBT Booking Invoice",
				filters={"name": ("in", invoice_names)},
				fields=["name", "status"],
			)
		}

	branch = frappe.get_doc("CBT Branch", doc.branch)
	session_courts = [row.court for row in (doc.courts or [])]

	return {
		"name": doc.name,
		"title": doc.title,
		"company": doc.company,
		"branch": doc.branch,
		"branch_name": branch.branch_name,
		"session_date": str(doc.session_date),
		"start_time": _fmt(_as_timedelta(doc.start_time)),
		"end_time": _fmt(_as_timedelta(doc.end_time)),
		"status": doc.status,
		"court_type": doc.court_type,
		"rotation_mode": doc.rotation_mode,
		"rotation_minutes": cint(doc.rotation_minutes),
		"rally_points": cint(doc.rally_points),
		"auto_rotate": cint(doc.auto_rotate),
		"entry_fee": flt(doc.entry_fee),
		# Section-20: the add-players dialog states the per-head price BEFORE
		# staff commit to it, and open play prices membership from the SESSION
		# (S11) rather than from the member's booking discount — so the dialog
		# needs this number, not the membership's own.
		"member_discount_percent": flt(doc.member_discount_percent),
		"max_participants": cint(doc.max_participants),
		"current_participants": cint(doc.current_participants),
		"total_revenue": flt(doc.total_revenue),
		"courts": [
			{
				"court": row.court,
				"court_name": row.court_name
				or frappe.db.get_value("CBT Court", row.court, "court_name"),
			}
			for row in (doc.courts or [])
		],
		"queue": [
			{
				"name": row.name,
				# `player_key` is what every rotation action must echo back;
				# `customer` stays the ACCOUNT (null for a walk-in) so the board
				# can still say who has one — and so the ban/portal boundaries
				# keep reading the way they always did.
				"player_key": _player_key(row),
				"customer": row.customer,
				"customer_name": row.customer_name or row.customer or _player_key(row),
				"queue_position": cint(row.queue_position),
				"status": row.status,
				"games_played": cint(row.games_played),
				"participant_ref": row.participant_ref,
			}
			for row in sorted(
				(doc.queue or []),
				key=lambda row: (row.status != "Waiting", cint(row.queue_position)),
			)
		],
		"participants": [
			{
				"name": row.name,
				"customer": row.customer,
				# Straight off the row — a participant is addressed by its own
				# row name everywhere (confirm payment, proofs, invoices), so it
				# never needs a player key and must never be given one.
				# `customer_phone` is deliberately ABSENT: TV mode renders this
				# same payload on an unattended screen (PLAN §8j).
				"customer_name": row.customer_name or row.customer,
				"fee": flt(row.fee),
				"discount_percent": flt(row.discount_percent),
				"payment_method": row.payment_method,
				# B29: the Mark Paid dialog pre-selects the channel on record.
				"payment_channel": row.get("payment_channel"),
				"payment_status": row.payment_status,
				"proof_ref": row.proof_ref,
				"billing_doc": row.billing_doc,
				"invoice_status": invoice_status.get(row.billing_doc),
			}
			for row in (doc.participants or [])
		],
		"assignments": [
			{
				"court": row.court,
				"court_name": row.court_name,
				"players": [
					{
						# `key` is the stored player_N value and the thing the
						# board must send back; `customer` is the account behind
						# it, or None for a walk-in. File 08 asserts on
						# `customer`, and account keys ARE user ids, so both
						# stayed true across the B2 migration.
						"key": row.get(field),
						"customer": account_of.get(row.get(field)),
						"name": display.get(row.get(field)) or row.get(field),
					}
					for field in PLAYER_FIELDS
					if row.get(field)
				],
				"vacancies": sum(1 for field in PLAYER_FIELDS if not row.get(field)),
				"started_at": row.started_at,
				"ends_at": row.ends_at,
			}
			for row in (doc.assignments or [])
			if cint(row.is_active)
		],
		"layout": {
			"rows": cint(branch.layout_rows),
			"columns": cint(branch.layout_columns),
			"cells": [
				{
					"row_index": cint(cell.row_index),
					"col_index": cint(cell.col_index),
					"court": cell.court,
					"in_session": cell.court in session_courts,
				}
				for cell in (branch.layout or [])
			],
		},
	}
