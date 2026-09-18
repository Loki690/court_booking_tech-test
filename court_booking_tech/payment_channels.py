# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Payment CHANNELS (Backlog B29, Batch 13, 2026-08-27) — where the money actually
landed: Cash at the desk, GCash, this bank or that bank.

The user's rulings, verbatim where it matters:

- *"BY DEFAULT WE HAVE CASH, GCASH THEN THEY CAN ADD 1 to many BANKS … SYSTEM
  ADMIN OR US WILL CONFIGURE PER COMPANY TENANT … HAVE OPTION TO DISABLE CASH,
  GCASH SINCE WE WILL LET THEM DECIDE THEIR MODE OF PAYMENT BUT WE WILL BE THE
  ONE TO SET IT UP."* → `CBT Payment Channel` rows, PER TENANT, seeded Cash +
  GCash on company creation, written by the PLATFORM and read by the tenant
  (the B21 permission shape), each one disable-able.
- Disabling a channel with history HIDES it from every new-payment picker and
  keeps it on every old row (§8e retention); a hard delete is refused while any
  payment references it.
- The CUSTOMER picks the channel at checkout; STAFF can correct it when they
  verify the proof ("customer pick, staff can edit based on the uploaded").
- The platform's own statement (B21) records the channel the tenant paid US
  through, so our reconciliation matches theirs.
- A channel is the tenant's ERPNext **Mode of Payment** (docs/collection_flow_v1.md
  line 58: MOP "Cash" hits Cash on Hand, MOP "GCash" hits the e-wallet account),
  so it carries `mode_of_payment` + `account_label` for the Tenant Ledger, and
  the customer-facing account details (name / number / QR) so "where do I pay"
  and "which income was this" are ONE record.

THE TRAP THE ROW NAMED, and the design that avoids it: `payment_method`
(Cash / Fund Transfer / Free) is the STATE MACHINE's input — Cash and Free
confirm instantly, Fund Transfer opens the proof hold. It is NOT replaced. A
channel sits BESIDE it and must be of the matching KIND: every GCash and every
bank is a *Transfer* channel, Cash is the *Cash* channel, Free carries none.
`resolve_channel` is the one place that rule lives; every writer (the booking
controller, open play, proofs, the statement) calls it.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

DOCTYPE = "CBT Payment Channel"

CASH = "Cash"
TRANSFER = "Transfer"
KINDS = (CASH, TRANSFER)

TENANT = "Tenant"
PLATFORM = "Platform"

# payment_method -> channel kind. Free is deliberately absent: a Free booking
# moved no money and carries no channel.
KIND_FOR_METHOD = {"Cash": CASH, "Fund Transfer": TRANSFER}

# What a tenant (and the platform) starts with, in the order the pickers show
# them: (label, kind, account_label for the ledger).
DEFAULT_CHANNELS = (
	("Cash", CASH, "Cash on Hand"),
	("GCash", TRANSFER, "Cash in E-Wallet - GCash"),
)

# The fields a GUEST checkout may see. Nothing else on the row leaves the desk —
# the ledger labels and the enabled flag are the platform's business.
PUBLIC_FIELDS = (
	"name",
	"label",
	"kind",
	"account_name",
	"account_number",
	"qr_image",
	"instructions",
)

# Every Link that points at a channel — the retention guard reads this list, so
# a new consumer of channels is one line here, not a forgotten delete path.
REFERENCING = (
	("CBT Court Booking", "payment_channel"),
	("CBT Booking Invoice", "payment_channel"),
	("CBT Payment Proof", "payment_channel"),
	("CBT Open Play Participant", "payment_channel"),
	("CBT Platform Statement", "payment_channel"),
)


def channel_kind(payment_method: str | None) -> str | None:
	"""The channel kind a payment method needs — None for Free (and for nothing)."""
	return KIND_FOR_METHOD.get(payment_method or "")


def list_channels(
	company: str | None = None,
	*,
	kind: str | None = None,
	enabled_only: bool = True,
	scope: str = TENANT,
	fields=None,
) -> list[dict]:
	"""Channels in picker order (sort_order, then label), permission-free.

	Permission-free ON PURPOSE: the pickers that call this run for a portal
	customer (no DocPerm at all — leak vector 4) and for a guest checkout. What
	they may SEE is decided by `fields`; `public_channels` passes PUBLIC_FIELDS.
	"""
	filters = {"scope": scope}
	if scope == TENANT:
		filters["company"] = company
	if kind:
		filters["kind"] = kind
	if enabled_only:
		filters["enabled"] = 1
	return frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=list(fields or ("name", "label", "kind", "enabled", "mode_of_payment", "account_label")),
		order_by="sort_order asc, label asc",
	)


def public_channels(company: str) -> list[dict]:
	"""The ENABLED transfer channels a customer may pay through, with the
	details they are shown — the checkout, the booking page, the proof picker."""
	return list_channels(company, kind=TRANSFER, fields=PUBLIC_FIELDS)


def channel_detail(name: str | None) -> dict | None:
	"""One channel's PUBLIC fields (None for no channel) — what a booking page
	prints beside "How to pay" for the channel the customer chose."""
	if not name:
		return None
	row = frappe.db.get_value(DOCTYPE, name, list(PUBLIC_FIELDS), as_dict=True)
	return dict(row) if row else None


def default_channel(company: str, payment_method: str, scope: str = TENANT) -> str | None:
	kind = channel_kind(payment_method)
	if not kind:
		return None
	rows = list_channels(company, kind=kind, scope=scope, fields=("name",))
	return rows[0].name if rows else None


def resolve_channel(
	company: str | None,
	payment_method: str,
	channel: str | None = None,
	*,
	scope: str = TENANT,
	allow_disabled: bool = False,
) -> str | None:
	"""The ONE rule every writer goes through. Returns the channel name to store.

	- Free → no channel (a caller that names one is told so).
	- No channel named → the company's first enabled channel of the method's
	  kind; NONE enabled → refuse, because the tenant (through the platform) has
	  switched that way of paying off. That is the user's ruling ("let them
	  decide their mode of payment"), not a gap: a tenant with every transfer
	  channel disabled takes no online bookings until one is enabled.
	- A named channel must exist, belong to THIS company (a tenant's channel can
	  never be stamped on another tenant's booking), match the KIND, and be
	  enabled — unless `allow_disabled`, which is for a row that ALREADY carries
	  it (a reschedule copy, an unchanged save): history keeps its channel.
	"""
	channel = (channel or "").strip() or None
	kind = channel_kind(payment_method)
	if not kind:
		if channel:
			frappe.throw(_("A Free payment carries no payment channel."))
		return None
	if not channel:
		chosen = default_channel(company, payment_method, scope=scope)
		if not chosen:
			frappe.throw(
				_(
					"No enabled {0} payment channel is set up for this company — the "
					"platform has to enable one before a {1} payment can be taken."
				).format(_(kind).lower(), _(payment_method))
			)
		return chosen
	row = frappe.db.get_value(
		DOCTYPE, channel, ["company", "scope", "kind", "enabled", "label"], as_dict=True
	)
	if not row:
		frappe.throw(_("Payment channel {0} does not exist.").format(channel))
	if row.scope != scope or (scope == TENANT and row.company != company):
		# Same sentence for "another tenant's" and "does not exist": a caller
		# probing channel names must not learn which ones are real.
		frappe.throw(_("Payment channel {0} does not exist.").format(channel))
	if row.kind != kind:
		frappe.throw(
			_("{0} is a {1} channel — a {2} payment cannot go through it.").format(
				row.label, _(row.kind), _(payment_method)
			)
		)
	if not cint(row.enabled) and not allow_disabled:
		frappe.throw(_("{0} is disabled — pick another payment channel.").format(row.label))
	return channel


def resolve_platform_channel(channel: str | None, *, required: bool = False) -> str | None:
	"""The channel the TENANT paid the PLATFORM through (statement mark-paid).
	Any enabled platform-scope channel, cash or transfer — a tenant may well
	hand over cash. Optional unless `required`."""
	channel = (channel or "").strip() or None
	if not channel:
		if required:
			frappe.throw(_("Pick the payment channel the payment arrived through."))
		return None
	row = frappe.db.get_value(DOCTYPE, channel, ["scope", "enabled", "label"], as_dict=True)
	if not row or row.scope != PLATFORM:
		frappe.throw(_("Payment channel {0} does not exist.").format(channel))
	if not cint(row.enabled):
		frappe.throw(_("{0} is disabled — pick another payment channel.").format(row.label))
	return channel


# ---------------------------------------------------------------------------
# Defaults & backfill — seeds, the company's after_insert and the migrate patch
# all call these; they are idempotent.
# ---------------------------------------------------------------------------


def ensure_default_channels(company: str | None, scope: str = TENANT) -> list[str]:
	"""Cash + GCash for a company (or the platform) that has NO channels yet.

	Only when the list is EMPTY: a tenant that had GCash disabled on purpose
	must not have it re-created by the next migrate."""
	filters = {"scope": scope}
	if scope == TENANT:
		filters["company"] = company
	if frappe.db.exists(DOCTYPE, filters):
		return []
	created = []
	for index, (label, kind, account_label) in enumerate(DEFAULT_CHANNELS):
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"scope": scope,
				"company": company if scope == TENANT else None,
				"label": label,
				"kind": kind,
				"mode_of_payment": label,
				"account_label": account_label,
				"enabled": 1,
				"sort_order": index + 1,
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return created


def ensure_all() -> dict:
	"""Every company gets its defaults, the platform gets its own, and rows that
	pre-date channels are stamped with the default of their kind. Idempotent —
	the migrate patch and seed_all both run it."""
	created = {"companies": 0, "platform": 0}
	for company in frappe.get_all("CBT Company", pluck="name"):
		created["companies"] += len(ensure_default_channels(company))
	created["platform"] += len(ensure_default_channels(None, scope=PLATFORM))
	created["backfilled"] = backfill_missing_channels()
	created["cancelled_stamped"] = backfill_cancelled_at()
	return created


def backfill_cancelled_at() -> int:
	"""A PAID document cancelled BEFORE this batch carries no `cancelled_at`,
	and both channel reports read that stamp for the refund leg — without it
	the money it refunded would show as collected, forever (ducky finding 2).
	The invoice's `modified` is the honest stamp: a Cancelled document is
	frozen except for its O.R. number, so its last save IS the cancel sync."""
	rows = frappe.get_all(
		"CBT Booking Invoice",
		filters={
			"status": "Cancelled",
			"verified_at": ("is", "set"),
			"cancelled_at": ("is", "not set"),
		},
		fields=["name", "modified"],
	)
	for row in rows:
		frappe.db.set_value(
			"CBT Booking Invoice", row.name, "cancelled_at", row.modified, update_modified=False
		)
	return len(rows)


def backfill_missing_channels() -> int:
	"""Stamp the kind's default on payment rows that carry no channel.

	Bookings and participants first (their invoices copy them), then invoices
	and proofs from their parent. Free rows stay empty. Counts rows touched."""
	touched = 0
	defaults = {}

	def default_for(company, method):
		key = (company, method)
		if key not in defaults:
			defaults[key] = default_channel(company, method)
		return defaults[key]

	for row in frappe.get_all(
		"CBT Court Booking",
		filters={"payment_channel": ("is", "not set"), "payment_method": ("in", list(KIND_FOR_METHOD))},
		fields=["name", "company", "payment_method"],
	):
		channel = default_for(row.company, row.payment_method)
		if channel:
			frappe.db.set_value(
				"CBT Court Booking", row.name, "payment_channel", channel, update_modified=False
			)
			touched += 1

	for row in frappe.db.sql(
		"""
		SELECT p.name, s.company, p.payment_method
		FROM `tabCBT Open Play Participant` p
		JOIN `tabCBT Open Play Session` s ON s.name = p.parent
		WHERE p.parenttype = 'CBT Open Play Session'
		  AND (p.payment_channel IS NULL OR p.payment_channel = '')
		  AND p.payment_method IN ('Cash', 'Fund Transfer')
		""",
		as_dict=True,
	):
		channel = default_for(row.company, row.payment_method)
		if channel:
			frappe.db.set_value(
				"CBT Open Play Participant", row.name, "payment_channel", channel, update_modified=False
			)
			touched += 1

	# Invoices copy their parent's channel; proofs copy their booking's.
	frappe.db.sql(
		"""
		UPDATE `tabCBT Booking Invoice` i
		JOIN `tabCBT Court Booking` b ON b.name = i.booking
		SET i.payment_channel = b.payment_channel
		WHERE (i.payment_channel IS NULL OR i.payment_channel = '')
		  AND b.payment_channel IS NOT NULL AND b.payment_channel != ''
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabCBT Booking Invoice` i
		JOIN `tabCBT Open Play Participant` p ON p.name = i.participant_ref
		SET i.payment_channel = p.payment_channel
		WHERE (i.payment_channel IS NULL OR i.payment_channel = '')
		  AND p.payment_channel IS NOT NULL AND p.payment_channel != ''
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabCBT Payment Proof` pr
		JOIN `tabCBT Court Booking` b ON b.name = pr.booking
		SET pr.payment_channel = b.payment_channel
		WHERE (pr.payment_channel IS NULL OR pr.payment_channel = '')
		  AND b.payment_channel IS NOT NULL AND b.payment_channel != ''
		"""
	)
	return touched


def references(channel: str) -> list[tuple[str, int]]:
	"""(doctype, count) for every payment row that names this channel."""
	out = []
	for doctype, fieldname in REFERENCING:
		count = frappe.db.count(doctype, {fieldname: channel})
		if count:
			out.append((doctype, count))
	return out


def slug_for_name(label: str) -> str:
	return re.sub(r"[^A-Z0-9]+", "-", (label or "").upper()).strip("-") or "CHANNEL"
