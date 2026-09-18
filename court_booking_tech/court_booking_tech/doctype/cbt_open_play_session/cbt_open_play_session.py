# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Open Play Session (section-10) — plain DocType with a GUARDED STATUS
MACHINE. Same recorded deviation from PLAN §4's "submittable" as CBT Court
Booking: the lifecycle is already a status field, so docstatus/amend would add
friction and no value.

Invariants the engine (api/open_play.py) and the board lean on:

- The schedule (company, branch, date, window) and the dedicated court list are
  FROZEN once the session leaves Scheduled — those courts are already blocked
  off the public grid and participants have paid. Rotation knobs and capacity
  stay tunable mid-session on purpose: staff really do shorten rotations when a
  session runs long.
- participants / queue / assignments are ENGINE-OWNED. Every mutation goes
  through api/open_play.py under a document lock, which sets
  flags.via_open_play_engine; a hand save that touches them is rejected.
- Child rows are mutated IN PLACE, never rebuilt. CBT Booking Invoice and CBT
  Payment Proof rows carry participant_ref = a participant row's `name`;
  clearing and re-appending a table mints new row names and orphans that
  billing history.
- Open play payments are CLOCK-FREE (PLAN §8q). Nothing here ever writes a
  verification deadline, and the per-minute sweep only ever reads
  `tabCBT Court Booking` — the exemption holds by construction, not convention.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt, get_datetime, getdate

from court_booking_tech.slots import _as_timedelta
from court_booking_tech.tenancy import require_company_access

LAYOUT_FIELDTYPES = {"Section Break", "Column Break", "Tab Break"}

ALLOWED_TRANSITIONS = {
	"Scheduled": {"Open", "Cancelled"},
	"Open": {"Completed", "Cancelled"},
	"Completed": set(),
	"Cancelled": set(),
}

FROZEN_AFTER_SCHEDULED = (
	"company",
	"branch",
	"session_date",
	"start_time",
	"end_time",
	"entry_fee",
)

ENGINE_TABLES = ("participants", "queue", "assignments")

PLAYER_FIELDS = ("player_1", "player_2", "player_3", "player_4")

PLAYERS_PER_COURT = len(PLAYER_FIELDS)


class CBTOpenPlaySession(Document):
	def autoname(self):
		# PLAN/section-10 spell this as `format:OPS-{company_code}-{#####}`, but
		# a format: rule can only read the doc's OWN fields and company_code
		# lives on CBT Company — so it names in python, exactly like the BK-
		# and INV- series.
		company_code = frappe.db.get_value("CBT Company", self.company, "company_code")
		self.name = make_autoname(f"OPS-{company_code}-.#####")

	def before_insert(self):
		# Naming runs before validate on insert, so the company must resolve here.
		self._mirror_company()

	def validate(self):
		if not self.branch:
			frappe.throw(_("Branch is required."))
		self._mirror_company()
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		self._validate_window()
		self._validate_rotation()
		self._validate_courts()
		self._validate_player_identity()

		if self.is_new():
			# Suspension gate ON: scheduling a session is revenue-creating.
			require_company_access(self.company)
			if self.status != "Scheduled":
				frappe.throw(_("A new session always starts as Scheduled."))
		else:
			require_company_access(self.company, allow_suspended=True)
			self._validate_status_transition()
			if not self.flags.via_open_play_engine:
				self._reject_frozen_edits()
				self._reject_engine_table_edits()

		self._recompute_counters()

	def on_trash(self):
		if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_(
					"Open play sessions are never deleted — cancel the session "
					"instead (its billing documents are retained)."
				),
				frappe.PermissionError,
			)

	# ------------------------------------------------------------------
	# Chain & schedule
	# ------------------------------------------------------------------

	def _mirror_company(self):
		if self.branch:
			self.company = frappe.db.get_value("CBT Branch", self.branch, "company")

	def _validate_window(self):
		start = _as_timedelta(self.start_time)
		end = _as_timedelta(self.end_time)
		if end <= start:
			frappe.throw(_("End Time must be after Start Time (same-day sessions only)."))

	def _validate_rotation(self):
		if self.rotation_mode == "Timed" and cint(self.rotation_minutes) < 1:
			frappe.throw(_("Rotation Minutes must be at least 1 for a Timed session."))
		if self.rotation_mode == "Rally" and cint(self.rally_points) < 1:
			frappe.throw(_("Rally Points must be at least 1 for a Rally session."))
		if cint(self.max_participants) < 0:
			frappe.throw(_("Max Participants cannot be negative (0 means unlimited)."))
		if flt(self.entry_fee) < 0:
			frappe.throw(_("Entry Fee cannot be negative."))

	def _validate_courts(self):
		seen = set()
		for row in self.courts or []:
			chain = frappe.db.get_value(
				"CBT Court", row.court, ["branch", "is_active", "court_name"], as_dict=True
			)
			if not chain:
				frappe.throw(_("Court {0} does not exist.").format(row.court))
			if chain.branch != self.branch:
				frappe.throw(
					_("Court {0} belongs to branch {1}, not {2}.").format(
						row.court, chain.branch, self.branch
					)
				)
			if not chain.is_active:
				frappe.throw(_("Court {0} is not active.").format(row.court))
			if row.court in seen:
				frappe.throw(_("Court {0} is listed twice.").format(row.court))
			seen.add(row.court)
			# fetch_from only runs client-side — server inserts (engine, seeds)
			# need the display name filled here.
			row.court_name = chain.court_name

	def _validate_player_identity(self):
		"""Every participant and queue row names SOMEBODY (section-20, B2).

		The stored-row rule is AT LEAST ONE identity, deliberately weaker than
		the exactly-one rule `api/open_play._validate_player_entry` applies to
		an incoming payload: an account row legitimately carries both halves,
		because `fetch_from` derives `customer_name` from the account
		server-side (`base_document._validate_links` → `set_fetch_from_value`).
		An "exactly one" rule here would reject every row already in the
		database.

		READ-ONLY BY DESIGN — it must not write a stripped value back.
		`_reject_engine_table_edits` fingerprints the in-memory rows against the
		stored ones inside this same `validate()`, so normalising whitespace
		here would make an innocent hand-save of an untouched session throw
		"Participants are managed by the Open Play Board".

		`mandatory_depends_on` in the child JSONs is CLIENT-ONLY (S13 as-built
		2) — this is the enforcement.
		"""
		for fieldname in ("participants", "queue"):
			label = self.meta.get_field(fieldname).label or fieldname
			for row in self.get(fieldname) or []:
				if row.customer or (row.customer_name or "").strip():
					continue
				frappe.throw(
					_(
						"Row {0} of {1} has no identity — give it a customer "
						"account or a walk-in name."
					).format(row.idx, _(label))
				)

	# ------------------------------------------------------------------
	# Status machine & engine ownership
	# ------------------------------------------------------------------

	def _validate_status_transition(self):
		before = frappe.db.get_value(self.doctype, self.name, "status")
		if not before or before == self.status:
			return
		if self.status not in ALLOWED_TRANSITIONS.get(before, set()):
			frappe.throw(
				_("A {0} session cannot become {1}.").format(_(before), _(self.status))
			)
		if not self.flags.via_open_play_engine:
			frappe.throw(
				_("Use the Open Play Board actions to open, complete or cancel a session.")
			)

	def _reject_frozen_edits(self):
		if frappe.db.get_value(self.doctype, self.name, "status") == "Scheduled":
			return
		before = frappe.db.get_value(
			self.doctype, self.name, list(FROZEN_AFTER_SCHEDULED), as_dict=True
		)
		if not before:
			return
		for fieldname in FROZEN_AFTER_SCHEDULED:
			df = self.meta.get_field(fieldname)
			if not _values_equal(df.fieldtype, before.get(fieldname), self.get(fieldname)):
				frappe.throw(
					_(
						"{0} cannot be changed once the session has opened — those "
						"courts are already blocked and participants have paid."
					).format(_(df.label or fieldname))
				)
		stored_courts = [
			row.court
			for row in frappe.get_all(
				"CBT Open Play Court",
				filters={"parent": self.name, "parenttype": self.doctype},
				fields=["court"],
				order_by="idx asc",
			)
		]
		if stored_courts != [row.court for row in (self.courts or [])]:
			frappe.throw(
				_(
					"The dedicated courts cannot be changed once the session has "
					"opened — cancel this session and create a new one."
				)
			)

	def _reject_engine_table_edits(self):
		for fieldname in ENGINE_TABLES:
			if self._table_fingerprint(fieldname) != self._stored_table_fingerprint(fieldname):
				label = self.meta.get_field(fieldname).label or fieldname
				frappe.throw(
					_(
						"{0} are managed by the Open Play Board and cannot be "
						"edited by hand."
					).format(_(label))
				)

	def _scalar_fields(self, child_doctype):
		return [
			df
			for df in frappe.get_meta(child_doctype).fields
			if df.fieldtype not in LAYOUT_FIELDTYPES and df.fieldtype != "Table"
		]

	def _table_fingerprint(self, fieldname):
		child_doctype = self.meta.get_field(fieldname).options
		fields = self._scalar_fields(child_doctype)
		return [
			tuple(
				[row.name] + [_norm(df.fieldtype, row.get(df.fieldname)) for df in fields]
			)
			for row in (self.get(fieldname) or [])
		]

	def _stored_table_fingerprint(self, fieldname):
		child_doctype = self.meta.get_field(fieldname).options
		fields = self._scalar_fields(child_doctype)
		rows = frappe.get_all(
			child_doctype,
			filters={
				"parent": self.name,
				"parenttype": self.doctype,
				"parentfield": fieldname,
			},
			fields=["name"] + [df.fieldname for df in fields],
			order_by="idx asc",
		)
		return [
			tuple(
				[row.name] + [_norm(df.fieldtype, row.get(df.fieldname)) for df in fields]
			)
			for row in rows
		]

	# ------------------------------------------------------------------
	# Derived counters
	# ------------------------------------------------------------------

	def _recompute_counters(self):
		"""current_participants counts who is still IN the session; revenue
		counts every participant who has paid — a player who paid and then left
		took their money with them, and dropping that from the total would make
		the board disagree with the billing documents."""
		self.current_participants = sum(
			1 for row in (self.queue or []) if row.status != "Left"
		)
		self.total_revenue = flt(
			sum(
				flt(row.fee) * (1 - flt(row.discount_percent) / 100.0)
				for row in (self.participants or [])
				if row.payment_status == "Paid"
			),
			2,
		)


def _values_equal(fieldtype, old, new) -> bool:
	return _norm(fieldtype, old) == _norm(fieldtype, new)


def _norm(fieldtype, value):
	"""Normalize before comparing: frappe stores an empty numeric as NULL or 0
	depending on the write path, so a raw != would false-trip the guards (the
	lesson the billing-document edit guard already learned)."""
	if fieldtype in ("Currency", "Float", "Percent"):
		return flt(value, 2)
	if fieldtype in ("Int", "Check"):
		return cint(value)
	if fieldtype == "Date":
		return str(getdate(value)) if value else ""
	if fieldtype == "Datetime":
		return str(get_datetime(value)) if value else ""
	if fieldtype == "Time":
		return str(_as_timedelta(value)) if value else ""
	return value or ""
