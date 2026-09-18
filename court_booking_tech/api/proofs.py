# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Payment-proof APIs (section-5, PLAN §5a) — the ONLY creation/review path for
CBT Payment Proof (the DocType itself is immutable and company roles hold no
create perm, so caps, locks and the verification deadline cannot be bypassed
from the desk).

Locking discipline: every entry locks the booking row first (same FOR-UPDATE
discipline as the double-booking guard and the sweep), so upload races the
sweep deterministically and multiple uploads serialize.

Actor model: the booking's customer uploads from the portal (S9); anyone else
must pass the tenancy choke point and uploads as Staff. Review actions are
staff-only — a customer session fails closed in require_company_access.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint

from court_booking_tech import clock
from court_booking_tech.api.bookings import _locked_booking, confirm_booking
from court_booking_tech.billing import sync_invoice_for_booking
from court_booking_tech.notifications import notify_cart_confirmed, notify_proof_rejected
from court_booking_tech.payment_channels import resolve_channel
from court_booking_tech.slots import _as_timedelta, _slot_dt, is_reserved_hold_live
from court_booking_tech.tenancy import require_company_access
from court_booking_tech.throttle import enforce_user_rate_limit
from court_booking_tech.verification import (
	compute_verification_deadline,
	get_pending_proofs,
	normalize_email_for_cap,
	reject_pending_proofs,
)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}

REJECT_REASONS = (
	"Invalid / suspected fake",
	"Unreadable",
	"Wrong amount",
	"Wrong reference",
)
FATAL_REASON = "Invalid / suspected fake"

DEFAULT_MAX_MB = 10
DEFAULT_MAX_PENDING_PER_BOOKING = 3
# ⚠ SLOTS, not bookings (B35). Must match the JSON default and the patch.
DEFAULT_MAX_HOLDS_PER_CUSTOMER = 8
DEFAULT_REUPLOAD_MINUTES = 120
DEFAULT_PROOF_UPLOADS_PER_HOUR = 10

EXPIRED_MSG = "This booking's reservation has expired — please contact the branch."


def _setting(fieldname: str, default: int) -> int:
	# get_single_value yields 0/None on a never-saved Singles record — every
	# knob needs its hard default (S3 as-built 1).
	return cint(frappe.db.get_single_value("CBT Platform Settings", fieldname)) or default


@frappe.whitelist(methods=["POST"])
def upload_proof(
	booking: str,
	reference_no: str | None = None,
	remarks: str | None = None,
	payment_channel: str | None = None,
):
	"""Multipart upload endpoint — the file arrives as request file "file".
	The portal (S9) and E2E post FormData here; backend tests call
	create_proof directly with bytes."""
	request_file = (getattr(frappe.request, "files", None) or {}).get("file")
	if request_file is None or not request_file.filename:
		frappe.throw(_("Attach the payment proof as a file named 'file'."))
	return create_proof(
		booking,
		request_file.filename,
		request_file.stream.read(),
		reference_no,
		remarks,
		payment_channel=payment_channel,
	)


def create_proof(
	booking: str,
	file_name: str,
	content: bytes,
	reference_no: str | None = None,
	remarks: str | None = None,
	payment_channel: str | None = None,
) -> dict:
	doc = _locked_booking(booking)
	now = clock.now_dt()

	# Backlog B29: the channel the uploader SAYS the money went through. Named
	# → must be one of this company's transfer channels (a customer picks from
	# the enabled list — or re-submits the booking's OWN channel, which stays
	# legal even if the platform disabled it after the hold was taken: a live
	# hold must never be left with no way to upload; ducky finding 1); omitted
	# → the booking's own. It is recorded on the proof for staff to read beside
	# the receipt; the BOOKING's channel changes only when staff confirm (the
	# ruling: customer picks, staff edit).
	payment_channel = (payment_channel or "").strip() or None
	if payment_channel:
		payment_channel = resolve_channel(
			doc.company,
			"Fund Transfer",
			payment_channel,
			allow_disabled=(payment_channel == doc.payment_channel),
		)
	else:
		payment_channel = doc.payment_channel or None

	# Actor: the booking's own customer, or (tenancy-checked) company staff.
	# Proof upload is NOT booking-creating, so suspended companies still
	# receive proofs for existing holds — confirm keeps the suspension gate.
	if frappe.session.user == doc.customer:
		source = "Customer"
		# PLAN §8k. Customer path ONLY: the desk's "sent via Messenger" upload
		# is a staff workflow and throttling it would punish the branch for
		# its customers' behaviour (as-built decision, section-9).
		enforce_user_rate_limit(
			"proof-upload",
			_setting("proof_upload_limit_per_hour", DEFAULT_PROOF_UPLOADS_PER_HOUR),
		)
	else:
		require_company_access(doc.company, allow_suspended=True)
		source = "Staff"

	if doc.booking_status != "Reserved":
		frappe.throw(_(EXPIRED_MSG))
	if not is_reserved_hold_live(
		doc.booking_status, doc.reservation_expires_at, now, doc.verification_deadline_at
	):
		# Upload-vs-sweep race, resolved inside the lock: the hold is dead —
		# make the sweep's write right here so a dead hold can never gain a
		# deadline, then fail with the same clean message. (set_value fires no
		# doc_events — flip the billing doc too, S6.)
		frappe.db.set_value("CBT Court Booking", doc.name, "booking_status", "Expired")
		sync_invoice_for_booking(doc.name)
		frappe.throw(_(EXPIRED_MSG))

	_validate_file(file_name, content)

	targets = group_targets(doc)

	# ⚠ Caps read the WHOLE group, never one nominated row: a dead anchor never
	# gets a proof, so its count stays empty and the deadline re-arms forever.
	pending_before = []
	for target in targets:
		pending_before.extend(get_pending_proofs(target.name))
	max_pending = _setting("max_pending_proofs_per_booking", DEFAULT_MAX_PENDING_PER_BOOKING)
	if len(pending_before) >= max_pending * len(targets):
		frappe.throw(
			_(
				"This booking already has {0} proofs awaiting review — "
				"please wait for the branch to review them."
			).format(len(pending_before))
		)
	if not pending_before:
		_check_customer_holds_cap(doc)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)

	# One proof per live row, all naming the same file. ⚠ No db.commit() here.
	proofs = []
	for target in targets:
		proofs.append(
			frappe.get_doc(
				{
					"doctype": "CBT Payment Proof",
					"booking": target.name,
					"file": file_doc.file_url,
					"source": source,
					"reference_no": reference_no,
					"payment_channel": payment_channel,
					"remarks": remarks,
				}
			).insert(ignore_permissions=True)
		)

	mine = next(
		(p for p, target in zip(proofs, targets) if target.name == doc.name), proofs[0]
	)

	# ⚠ A group's proofs SHARE one File, attached to the caller's own proof.
	frappe.db.set_value(
		"File",
		file_doc.name,
		{
			"attached_to_doctype": "CBT Payment Proof",
			"attached_to_name": mine.name,
			"attached_to_field": "file",
		},
	)

	# FIRST Pending proof arms the clock; later uploads never move it (PLAN §5a).
	# ⚠ One walk, but capped at each row's OWN end.
	deadline = doc.verification_deadline_at
	if not pending_before:
		walked = compute_verification_deadline(doc.company, now)
		for target in targets:
			capped = min(
				walked, _slot_dt(target.booking_date, _as_timedelta(target.end_time))
			)
			frappe.db.set_value(
				"CBT Court Booking", target.name, "verification_deadline_at", capped
			)
			if target.name == doc.name:
				deadline = capped

	return {
		"proof": mine.name,
		"verification_deadline_at": deadline,
		# Present only for a cart, so nothing single-booking reads it by accident.
		"group_proofs": [p.name for p in proofs] if doc.booking_group else None,
	}


def _validate_file(file_name: str, content: bytes):
	extension = file_name.rsplit(".", 1)[-1].lower() if "." in (file_name or "") else ""
	if extension not in ALLOWED_EXTENSIONS:
		frappe.throw(
			_("Only JPG, PNG or PDF payment proofs are accepted (got {0}).").format(
				file_name or _("no file name")
			)
		)
	max_mb = _setting("proof_max_mb", DEFAULT_MAX_MB)
	if not content:
		frappe.throw(_("The uploaded file is empty."))
	if len(content) > max_mb * 1024 * 1024:
		frappe.throw(
			_("The proof file is too large — the limit is {0} MB.").format(max_mb)
		)


def group_targets(doc) -> list:
	"""The live rows one payment answers for. Name order: two concurrent group
	actions then take row locks in the same order."""
	return group_rows(doc, live_only=True)


def group_rows(doc, live_only: bool = False) -> list:
	"""The cart's rows. live_only=True is the proof fan-out set (Reserved only).

	⚠ A REVIEW action needs live_only=False, or a skipped row is never reported.
	"""
	if not doc.booking_group:
		return [doc]
	filters = {
		"booking_group": doc.booking_group,
		"customer": doc.customer,
	}
	if live_only:
		filters["booking_status"] = "Reserved"
	rows = frappe.get_all(
		"CBT Court Booking",
		filters=filters,
		fields=[
			"name",
			"company",
			"booking_date",
			"end_time",
			"number_of_slots",
			"booking_status",
		],
		order_by="name asc",
	)
	# The clicked row must be in its own set; if its status has moved under us
	# the caller's own guards have already thrown.
	return rows or [doc]


def _group_slots(doc) -> int:
	"""Slots this upload puts on the clock — the group's sum, or just its own."""
	if not doc.booking_group:
		return cint(doc.number_of_slots)
	return (
		cint(
			frappe.db.sql(
				"""
				SELECT SUM(number_of_slots)
				FROM `tabCBT Court Booking`
				WHERE booking_group = %s AND customer = %s
				  AND booking_status = 'Reserved'
				""",
				(doc.booking_group, doc.customer),
			)[0][0]
		)
		or cint(doc.number_of_slots)
	)


def _check_customer_holds_cap(doc):
	"""Cross-company cap on concurrent unverified proof-holds, keyed by
	normalized email so plus-addressing / Gmail dots cannot pierce it.

	⚠ COUNTS SLOTS, not bookings (B35). A four-slot booking is four holds.
	The check is `held + arriving > cap`, so the size of the incoming cart counts.

	WALK-INS ARE EXEMPT, and this is a tenancy fix, not a convenience
	(section-13). The query below is deliberately cross-company — the cap
	exists to stop ONE person squatting slots at many facilities — but
	normalize_email_for_cap(None) returns "", so without this guard every
	customer-less booking on the platform would collapse into a single cap
	key: five walk-in holds at OTHER tenants would refuse the sixth here, and
	the error message would disclose a count of another company's rows. The
	converse leak is impossible — a real email can never normalize to "".
	Walk-in proofs are staff-created at the desk anyway; the abuse this
	defends against is online squatting.
	"""
	if not doc.customer:
		return
	cap = _setting("max_active_proof_holds_per_customer", DEFAULT_MAX_HOLDS_PER_CUSTOMER)
	cap_key = normalize_email_for_cap(doc.customer)
	# ⚠ number_of_slots joins the DISTINCT tuple, not a SQL SUM(): the join
	# yields one row per pending proof. Dedup first, sum in Python.
	group = doc.booking_group or None
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT b.name, b.customer, b.number_of_slots, b.booking_group
		FROM `tabCBT Court Booking` b
		JOIN `tabCBT Payment Proof` p
		  ON p.booking = b.name AND p.status = 'Pending'
		WHERE b.booking_status = 'Reserved' AND b.name != %s
		""",
		(doc.name,),
		as_dict=True,
	)
	held = sum(
		cint(row.number_of_slots)
		for row in rows
		if normalize_email_for_cap(row.customer) == cap_key
		and not (group and row.booking_group == group)
	)
	arriving = _group_slots(doc)
	if held + arriving > cap:
		frappe.throw(
			_(
				"You already have {0} slots awaiting payment verification, and "
				"this one needs {1} — the limit is {2}. Please wait for those "
				"before reserving more."
			).format(held, arriving, cap)
		)


@frappe.whitelist(methods=["POST"])
def accept_proofs(booking: str, payment_channel: str | None = None) -> str:
	"""One staff action = verified + confirmed (PLAN §5a): all Pending
	proofs → Accepted, then confirm_booking (which owns the suspension gate,
	the stale-hold overlap re-check, and the Expired retro-confirm path).
	`payment_channel` (B29) is the staff's correction of where the money
	landed — passed straight through to confirm_booking."""
	doc = _locked_booking(booking)
	require_company_access(doc.company, allow_suspended=True)  # confirm re-gates
	if not get_pending_proofs(doc.name):
		frappe.throw(_("This booking has no pending proofs to accept."))

	if not doc.booking_group:
		return confirm_booking(doc.name, payment_channel=payment_channel)

	# B35: confirm what is confirmable, report the rest. group_rows (not
	# group_targets) — a filtered-out row is a row the desk is never told about.
	confirmed, skipped = [], []
	for row in group_rows(doc):
		if row.booking_status != "Reserved":
			skipped.append({"booking": row.name, "status": row.booking_status})
			continue
		# ⚠ Savepoint per row: confirm_booking's overlap re-check is not
		# pre-checkable, and uncaught it rolls back rows already confirmed.
		# ⚠ No db.commit() in this loop.
		savepoint = f"cbt_group_accept_{len(confirmed) + len(skipped)}"
		frappe.db.savepoint(savepoint)
		try:
			confirm_booking(
				row.name, payment_channel=payment_channel, suppress_confirm_mail=True
			)
			confirmed.append(row.name)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			skipped.append(
				{
					"booking": row.name,
					"status": frappe.db.get_value(
						"CBT Court Booking", row.name, "booking_status"
					),
				}
			)

	if confirmed:
		# ONE mail for one payment, naming only the rows actually confirmed.
		notify_cart_confirmed(
			doc,
			[frappe.get_doc("CBT Court Booking", name) for name in confirmed],
		)

	if skipped:
		# ⚠ Return shape unchanged — the desk calls this by dotted path.
		frappe.msgprint(
			_("Confirmed {0} of {1} bookings in this cart. Could not confirm: {2}.").format(
				len(confirmed),
				len(confirmed) + len(skipped),
				", ".join(f"{row['booking']} ({_(row['status'])})" for row in skipped),
			),
			title=_("Part of this cart could not be confirmed"),
			indicator="orange",
		)
	return doc.name


@frappe.whitelist(methods=["POST"])
def reject_proofs(booking: str, reason: str) -> dict:
	"""Booking-LEVEL review event (PLAN §5a): ALL Pending proofs flip to
	Rejected at once (frees the per-booking cap slots), rejection_count
	increments, and the reason decides the booking's fate."""
	if reason not in REJECT_REASONS:
		frappe.throw(_("Invalid rejection reason: {0}").format(reason))

	doc = _locked_booking(booking)
	require_company_access(doc.company, allow_suspended=True)  # de-escalation
	if doc.booking_status != "Reserved":
		frappe.throw(
			_(
				"Only a Reserved booking's proofs can be rejected "
				"(this one is {0})."
			).format(_(doc.booking_status))
		)
	if not get_pending_proofs(doc.name):
		frappe.throw(_("This booking has no pending proofs to reject."))

	now = clock.now_dt()
	minutes = _setting("rejection_reupload_minutes", DEFAULT_REUPLOAD_MINUTES)

	# ⚠ B35: ONE reason, but the outcome is PER ROW — hold liveness differs
	# across a mixed-date cart and a dead hold must never be regraced.
	# Defaults below are overwritten in the loop; they only avoid an unbound local.
	new_count = cint(doc.rejection_count) + 1
	outcome = "Expired"
	regrace_until = None

	rejected, per_row = [], []
	for row in group_rows(doc):
		row_doc = doc if row.name == doc.name else frappe.get_doc("CBT Court Booking", row.name)
		if row_doc.booking_status != "Reserved":
			# Skipped, not thrown on — else a partly-dead cart is un-rejectable.
			per_row.append({"booking": row_doc.name, "outcome": "Skipped"})
			continue
		hold_live = is_reserved_hold_live(
			row_doc.booking_status,
			row_doc.reservation_expires_at,
			now,
			row_doc.verification_deadline_at,
		)
		rejected.extend(reject_pending_proofs(row_doc.name, reason, now))
		row_count = cint(row_doc.rejection_count) + 1
		updates = {"rejection_count": row_count, "verification_deadline_at": None}
		if reason == FATAL_REASON or row_count >= 2 or not hold_live:
			# Junk gets zero restarts; a second rejection of ANY kind expires;
			# a dead hold is never resurrected.
			updates["booking_status"] = "Expired"
			row_outcome = "Expired"
		else:
			# Regrace is still capped by this row's own clock, not the group's.
			updates["reservation_expires_at"] = now + timedelta(minutes=minutes)
			row_outcome = "Regrace"
		frappe.db.set_value("CBT Court Booking", row_doc.name, updates)
		if row_outcome == "Expired":
			# set_value fires no doc_events — flip the billing doc here (S6).
			sync_invoice_for_booking(row_doc.name)
		per_row.append({"booking": row_doc.name, "outcome": row_outcome})
		if row_doc.name == doc.name:
			new_count = row_count
			outcome = row_outcome
			regrace_until = updates.get("reservation_expires_at")

	# PLAN §7: the restart-once mechanic needs the customer told. ONE mail per group.
	notify_proof_rejected(
		doc.name,
		reason=reason,
		outcome=outcome,
		regrace_until=regrace_until,
	)
	return {
		"booking": doc.name,
		"outcome": outcome,
		"rejected_proofs": rejected,
		"rejection_count": new_count,
		"reservation_expires_at": regrace_until,
		"group_outcomes": per_row if doc.booking_group else None,
	}


# ---------------------------------------------------------------------------
# The transaction reference (Backlog B40) — section-5 as-built 12.
# ---------------------------------------------------------------------------


def _group_booking_names(doc) -> list:
	# ⚠ .name ONLY: group_rows returns the Document for a lone booking and
	# thin _dict rows for a cart (api/proofs.py group_rows).
	return [row.name for row in group_rows(doc)]


def _reference_targets(booking_name: str) -> list:
	"""The proofs of ONE row that a corrected reference lands on: its Pending
	ones, else its newest Accepted. Rejected receipts are dead evidence."""
	pending = frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": booking_name, "status": "Pending"},
		pluck="name",
		order_by="creation asc",
	)
	if pending:
		return pending
	return frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": booking_name, "status": "Accepted"},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)


def _reference_duplicates(company: str, reference_no, exclude_bookings: list) -> list:
	"""Other proofs at THIS company carrying the same reference. Advisory only
	— the ruling is warn, never block."""
	reference_no = (reference_no or "").strip()
	# ⚠ A blank reference is not a match, it is the installed base: every proof
	# the desk uploaded before B40 has one. Guarded HERE, not only in callers.
	if not reference_no:
		return []
	return frappe.db.sql(
		"""
		SELECT p.name AS proof, p.status AS proof_status, p.uploaded_at,
		       p.booking, p.open_play_session,
		       b.booking_date, b.total_amount, b.booking_status, c.court_name
		FROM `tabCBT Payment Proof` p
		LEFT JOIN `tabCBT Court Booking` b ON b.name = p.booking
		LEFT JOIN `tabCBT Court` c ON c.name = b.court
		WHERE p.company = %(company)s AND p.reference_no = %(reference_no)s
		  AND (p.booking IS NULL OR p.booking NOT IN %(exclude)s)
		ORDER BY p.uploaded_at DESC
		LIMIT 20
		""",
		{
			"company": company,
			"reference_no": reference_no,
			# The sentinel keeps the IN list non-empty (invalid SQL otherwise) and
			# names no booking; an open-play proof survives the NULL branch above.
			"exclude": tuple(exclude_bookings) + ("__cbt_none__",),
		},
		as_dict=True,
	)


@frappe.whitelist()
def find_reference_duplicates(booking: str, reference_no: str | None = None) -> list:
	"""Has this receipt already been used here? Staff-gated: the payload names
	another customer's date and amount, so the CALLER is checked, not just the
	matches (ducky STOP, 2026-09-05)."""
	company = frappe.db.get_value("CBT Court Booking", booking, "company")
	if not company:
		frappe.throw(_("Booking {0} not found.").format(booking), frappe.DoesNotExistError)
	require_company_access(company, allow_suspended=True)
	doc = frappe.get_doc("CBT Court Booking", booking)
	return _reference_duplicates(company, reference_no, _group_booking_names(doc))


@frappe.whitelist(methods=["POST"])
def set_proof_reference(booking: str, reference_no: str | None = None) -> dict:
	"""Staff type or correct the transaction reference at review (B40).

	allow_suspended, like reject_proofs: correcting a reference de-escalates
	nothing and a suspended tenant still has receipts to reconcile.
	"""
	doc = _locked_booking(booking)
	require_company_access(doc.company, allow_suspended=True)
	reference_no = (reference_no or "").strip() or None

	names = _group_booking_names(doc)
	updated = []
	for name in names:
		for proof in _reference_targets(name):
			frappe.db.set_value(
				"CBT Payment Proof",
				proof,
				{"reference_no": reference_no, "reference_set_by": frappe.session.user},
			)
			updated.append(proof)

	return {
		"booking": doc.name,
		"reference_no": reference_no,
		"updated": updated,
		"duplicates": _reference_duplicates(doc.company, reference_no, names),
	}
