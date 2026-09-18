# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Customer transactional email (section-9, PLAN §7; section-19 adds the third,
Backlog B22 the fourth).

Four load-bearing messages, all rendered from shipped Email Template fixtures.
The wording is REPO-OWNED: `fixtures/email_template.json` is force-imported on
every `bench migrate` (`sync_fixtures` → `import_doc` → `import_file_by_path(
force=True)`), so a template reworded in the desk is overwritten by the next
migrate. Reword it in the fixture file, not the desk (ruled 2026-08-27, B22 —
this docstring used to promise the opposite):

- **CBT Proof Rejected** — PLAN §5a's rejection-restart mechanic is only
  defensible if the customer is TOLD (this is why the section-5 stub existed).
- **CBT Booking Confirmed** — the receipt half of the money path.
- **CBT Booking Rescheduled** — section-19 (Backlog B5). A staff move used to
  tell the customer NOTHING: a cash move mails nothing (the replacement is born
  Confirmed and on_booking_update returns on flags.in_insert), an unpaid move
  mails nothing, and the paid Fund-Transfer carry queued the *confirmed*
  template — which says confirmed, not moved. So the customer saw a Cancelled
  booking and a new one in their own history, with no explanation, which reads
  as a mistake by the facility.
- **CBT Account Already Exists** — Backlog B22. Signing up with an address
  that already holds an account returns the same generic "check your email"
  reply as a fresh signup (anti-enumeration, PLAN §2 D9) — and used to send
  NOTHING, so the customer waited for a mail that never came. This one points
  them at /login and Forgot Password and carries NO token: nothing to expire,
  no credential minted, no in-flight reset key overwritten. The ONLY message
  here with no booking behind it — see `_queue_to_address`.

Delivery model (dev/E2E): the seeds' site-config mail stub queues rows and
`mute_emails` blocks the send leg, so `tabEmail Queue` IS the assertion surface
(section-8 as-built 2). Real SMTP is a section-12 prod-checklist line.

Failure model: mail NEVER breaks the transaction that triggered it. A broken
template or mail config must not stop a staff member from rejecting junk or
confirming a payment — every entry point is wrapped, logged, and swallowed.
"""

import frappe
from frappe.utils import flt, fmt_money, format_datetime, get_url

from court_booking_tech.timeutil import _fmt_time
from court_booking_tech.throttle import WINDOW_SECONDS, rate_key

PROOF_REJECTED_TEMPLATE = "CBT Proof Rejected"
BOOKING_CONFIRMED_TEMPLATE = "CBT Booking Confirmed"
RESCHEDULED_TEMPLATE = "CBT Booking Rescheduled"
ACCOUNT_EXISTS_TEMPLATE = "CBT Account Already Exists"
# B35: one confirmation for a cart, naming every booking it covered.
CART_CONFIRMED_TEMPLATE = "CBT Cart Confirmed"


def _when(doc) -> str:
	"""The one human rendering of a booking's slot, so the "was" and the "now"
	lines of a move can never be formatted two different ways."""
	return "{0} {1}–{2}".format(
		format_datetime(doc.booking_date, "d MMM yyyy"),
		_fmt_time(doc.start_time),
		_fmt_time(doc.end_time),
	)


def _booking_context(doc) -> dict:
	company_name = (
		frappe.db.get_value("CBT Company", doc.company, "company_name") or doc.company
	)
	return {
		"booking": doc.name,
		"customer_name": doc.customer_name or doc.customer,
		"company_name": company_name,
		"branch_name": frappe.db.get_value("CBT Branch", doc.branch, "branch_name")
		or doc.branch,
		"court_name": frappe.db.get_value("CBT Court", doc.court, "court_name")
		or doc.court,
		"when": _when(doc),
		"amount": fmt_money(flt(doc.total_amount), currency="PHP").replace("₱", "").strip(),
		"booking_url": get_url(f"/my-bookings/{doc.name}"),
		"billing_url": get_url(f"/billing-statement?booking={doc.name}"),
	}


def _queue(template_name: str, doc, context: dict):
	"""Render the template and hand it to the Email Queue.

	`sendmail` resolves the outgoing account at QUEUE time — with none
	configured it raises, which is why dev sites carry the seeded stub
	(section-8 as-built 2). Callers are already wrapped; this stays strict so
	a missing template is visible in the Error Log rather than silent.
	"""
	template = frappe.get_doc("Email Template", template_name)
	frappe.sendmail(
		recipients=[doc.customer],
		subject=frappe.render_template(template.subject, context),
		message=frappe.render_template(template.response_, context),
		reference_doctype="CBT Court Booking",
		reference_name=doc.name,
		now=False,
	)


def _queue_to_address(template_name: str, email: str, context: dict):
	"""The no-reference sibling of `_queue` (Backlog B22).

	`_queue` is booking-shaped — it mails `doc.customer` and stamps the booking
	as the reference — and it is deliberately NOT bent to fit a mail that has no
	document behind it: the account-exists notice goes to a raw address that may
	belong to no tenant at all. Same strictness as `_queue`, for the same reason:
	a missing template raises here and surfaces in the Error Log via the caller.
	Queued (`now=False`), never sent inline — see `notify_account_exists`.
	"""
	template = frappe.get_doc("Email Template", template_name)
	frappe.sendmail(
		recipients=[email],
		subject=frappe.render_template(template.subject, context),
		message=frappe.render_template(template.response_, context),
		now=False,
	)


def _log(title: str, ref: str):
	# S8 as-built 11a: log_error WITHOUT an explicit message renders
	# get_traceback(with_context=True) — source lines per frame, ~26s under a
	# deep stack. Always pass the plain traceback in hot/except paths.
	# `ref` is whatever identifies the failed mail — a booking name, or for the
	# account-exists notice (B22) the address itself.
	frappe.log_error(message=frappe.get_traceback(), title=f"{title} ({ref})")


def _has_mailable_customer(doc) -> bool:
	"""Walk-ins (section-13) have no account and therefore no inbox.

	Silent skip, NOT a logged error — nothing is wrong: the customer was
	standing at the desk with a printed statement. The guard lives in the two
	notify functions rather than in _queue on purpose: _queue stays strict so a
	missing or renamed Email Template still surfaces in the Error Log. Folding
	this into _queue would return before `frappe.get_doc("Email Template", …)`
	and blind a cash-heavy branch to a broken template.

	frappe would in fact swallow recipients=[None] on its own (Email Queue
	filters falsy recipients and returns before resolving a sender), but
	relying on that leaves the business rule invisible and undefended.
	"""
	return bool(doc.customer)


def notify_proof_rejected(
	booking: str, reason: str = "", outcome: str = "", regrace_until=None
):
	"""Queue the proof-rejected email (called by api/proofs.reject_proofs).

	`outcome` is the caller's own verdict string ("Regrace" / "Expired") — the
	template branches on it rather than re-deriving the §5a rules here, so the
	mail can never disagree with what the booking actually did.
	"""
	try:
		doc = frappe.get_doc("CBT Court Booking", booking)
		if not _has_mailable_customer(doc):
			return
		context = _booking_context(doc)
		context.update(
			{
				"reason": reason,
				"outcome": outcome,
				"regrace_until": format_datetime(regrace_until)
				if regrace_until
				else "",
			}
		)
		_queue(PROOF_REJECTED_TEMPLATE, doc, context)
	except Exception:
		_log("CBT proof-rejected email failed", booking)


def notify_booking_confirmed(doc):
	try:
		if not _has_mailable_customer(doc):
			return
		_queue(BOOKING_CONFIRMED_TEMPLATE, doc, _booking_context(doc))
	except Exception:
		_log("CBT booking-confirmed email failed", doc.name)


def notify_cart_confirmed(doc, rows):
	"""One confirmation for a cart. `rows` are the bookings actually confirmed."""
	try:
		if not _has_mailable_customer(doc):
			return
		company_name = (
			frappe.db.get_value("CBT Company", doc.company, "company_name") or doc.company
		)
		total = sum(flt(row.total_amount) for row in rows)
		_queue(
			CART_CONFIRMED_TEMPLATE,
			doc,
			{
				"customer_name": doc.customer_name or doc.customer,
				"company_name": company_name,
				"branch_name": frappe.db.get_value("CBT Branch", doc.branch, "branch_name")
				or doc.branch,
				"count": len(rows),
				"rows": [
					{
						"booking": row.name,
						"court_name": frappe.db.get_value(
							"CBT Court", row.court, "court_name"
						)
						or row.court,
						"when": _when(row),
					}
					for row in rows
				],
				"amount": fmt_money(total, currency="PHP").replace("₱", "").strip(),
				"bookings_url": get_url("/my-bookings"),
			},
		)
	except Exception:
		_log("CBT cart-confirmed email failed", doc.name)


def notify_booking_rescheduled(original, new):
	"""Tell the customer their booking MOVED (section-19, Backlog B5).

	Called by api/bookings.reschedule_booking AFTER it has verified the original
	really reached Cancelled — a mail describing a move that then throws is a lie
	in an inbox.

	The money clocks are branched here rather than in the template because the
	two Reserved deadlines mean OPPOSITE things and the wording has to follow:
	`verification_deadline_at` is the STAFF's verify-by clock (the customer has
	already paid and is waiting), while `reservation_expires_at` is the
	customer's own pay-by clock. Handing both to one `pay_by` placeholder would
	tell a customer who has already uploaded a proof to go and pay — the same
	class of misleading mail this whole section exists to delete. (The detail
	page has always drawn this distinction; cbt-booking-detail.html.)

	A moved booking that is still Reserved must carry SOME deadline: a mail that
	announces a move and omits the clock invites exactly the missed payment it
	exists to prevent.
	"""
	try:
		if not _has_mailable_customer(new):
			# Walk-in (section-13): no account, no inbox. Silent skip — the guard
			# lives here and NEVER in _queue, which stays strict so a missing or
			# renamed template still surfaces in the Error Log (S13 as-built 6).
			return

		context = _booking_context(new)
		pay_by = ""
		verify_by = ""
		if new.booking_status == "Reserved":
			# One query, and the `elif` is gated on `not awaiting` rather than
			# just falling through: a Pending proof with no deadline is
			# unreachable today (create_proof arms the clock on the first one),
			# but if it ever happened, falling through would tell a customer who
			# HAS paid that they have not. Failing into the quiet "your payment
			# carries over" branch is wrong-but-harmless; the other way round is
			# the mail this section exists to delete.
			awaiting = _has_pending_proof(new.name)
			if awaiting and new.verification_deadline_at:
				verify_by = format_datetime(new.verification_deadline_at)
			elif not awaiting and new.reservation_expires_at:
				pay_by = format_datetime(new.reservation_expires_at)

		context.update(
			{
				# The ref the customer's PREVIOUS email carried — without it they
				# cannot match this message to anything they already have.
				"old_booking": original.name,
				"old_when": _when(original),
				"old_court_name": frappe.db.get_value(
					"CBT Court", original.court, "court_name"
				)
				or original.court,
				# Emitted only for a cross-branch move, and decided on the branch
				# IDs — two branches of one company may legitimately share a
				# display name, and comparing the resolved names would then drop
				# the one line that tells the customer to drive somewhere else.
				"old_branch_name": (
					frappe.db.get_value("CBT Branch", original.branch, "branch_name")
					or original.branch
				)
				if original.branch != new.branch
				else "",
				"booking_status": new.booking_status,
				"pay_by": pay_by,
				"verify_by": verify_by,
			}
		)
		_queue(RESCHEDULED_TEMPLATE, new, context)
	except Exception:
		# The S9 failure model: mail NEVER breaks the transaction that triggered
		# it. A broken template must not stop staff moving a booking a customer
		# is standing at the desk asking about.
		_log("CBT booking-rescheduled email failed", new.name)


def notify_account_exists(email: str):
	"""Tell someone who signed up with an address that already holds an account
	where to go next (Backlog B22; called by api/signup.sign_up on its
	existing-user branch, BEFORE the generic reply is returned).

	The HTTP response is untouched — it must stay byte-identical to the fresh
	signup's (PLAN §2 D9). The mail is QUEUED, not sent inline: core's password
	reset uses `now=True`, but a synchronous send on this branch alone would add
	latency the fresh branch does not have — a timing oracle for "does this
	address exist" that the identical response exists to deny.

	The swallow lives HERE, not in the caller (the B5 lesson): a broken template
	must never turn the existing-address branch into an error the fresh branch
	does not raise.

	ONE mail per address per CLOCK hour (ruled 2026-08-27) — the bucket is
	`throttle.rate_key`'s calendar-hour key, so a hit at 10:59 and one at 11:00
	both send; the worst case is two per address per rolling hour, which is the
	brake's intent, not a rolling limiter. Before B22 this branch sent nothing;
	after it, every hit on a known address mails its owner, and the only other
	brake is the per-IP signup limit — rotating IPs would turn /signup into a
	mailbomb for any address an attacker knows. The bucket lives under the
	`cbt-rl:` prefix: Redis, the app's clock seam, cleared by the seeds like
	every other bucket. Two rules: the brake FAILS OPEN (a cache fault must not
	cancel the mail — that would recreate the exact silent no-mail B22 fixes),
	and it is armed only AFTER a successful queue, so a failed attempt never
	suppresses the retry that would have worked.
	"""
	bucket = None
	try:
		bucket = frappe.cache.make_key(rate_key("account-exists", email.lower()))
		if frappe.cache.get(bucket):
			return
	except Exception:
		_log("CBT account-exists brake unavailable, mailing anyway", email)
		bucket = None
	try:
		_queue_to_address(
			ACCOUNT_EXISTS_TEMPLATE,
			email,
			{
				"email": email,
				"login_url": get_url("/login"),
				"forgot_url": get_url("/login#forgot"),
			},
		)
	except Exception:
		_log("CBT account-exists email failed", email)
		return
	if bucket:
		try:
			frappe.cache.setex(bucket, WINDOW_SECONDS, 1)
		except Exception:
			_log("CBT account-exists brake not armed", email)


def _has_pending_proof(booking: str) -> bool:
	return bool(
		frappe.db.exists("CBT Payment Proof", {"booking": booking, "status": "Pending"})
	)


# ---------------------------------------------------------------------------
# doc_events (hooks.py) — CBT Court Booking
# ---------------------------------------------------------------------------

# The two transitions that mean "money verified" (S4/S5 status machine):
# Reserved → Confirmed (desk or proof acceptance) and Expired → Completed
# (retro-confirm).
CONFIRMING_TRANSITIONS = {
	("Reserved", "Confirmed"),
	("Expired", "Completed"),
}


def on_booking_update(doc, method=None):
	if doc.flags.suppress_confirm_mail:
		# Section-19. reschedule_booking carries an already-verified payment onto
		# the replacement with an explicit Reserved->Confirmed save. That
		# transition is real, but the customer is about to receive the "moved"
		# mail, which says everything this one would AND explains why — so the
		# confirmed mail here would be a duplicate that contradicts it in tone.
		#
		# Scoped by construction: flags are server-settable only ("flags" is in
		# frappe's RESERVED_KEYWORDS, so update()/set() drop it — the S15
		# doctrine) and they die with the request. A LATER, genuine
		# Reserved->Confirmed of a moved unpaid booking therefore still mails.
		return
	if doc.flags.in_insert:
		# A cash/free walk-in is born Confirmed at the desk, where the customer
		# is standing with a printed statement in hand. Emailing them a
		# "payment verified" notice adds nothing and would spam every seeded
		# booking on install (as-built decision, section-9).
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	if (before.booking_status, doc.booking_status) in CONFIRMING_TRANSITIONS:
		notify_booking_confirmed(doc)
