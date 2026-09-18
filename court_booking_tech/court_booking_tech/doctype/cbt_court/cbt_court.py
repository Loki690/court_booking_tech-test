# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from court_booking_tech.pricing import (
	DAY_NAMES,
	TIER_DAY_GROUP,
	TIER_SPECIFIC_DAY,
	rule_tier,
)
from court_booking_tech.timeutil import _as_timedelta, _overlaps


def scrub_court_slug(court_name: str) -> str:
	"""'Center Court!' -> 'center-court' (feeds the format autoname)."""
	slug = re.sub(r"[^a-z0-9]+", "-", (court_name or "").lower()).strip("-")
	return slug


class CBTCourt(Document):
	def before_insert(self):
		# Naming happens BEFORE validate on insert, and fetch_from only runs
		# client-side — both name components must be resolved here or server
		# inserts (seeds, APIs) produce a broken document ID.
		if not self.branch:
			frappe.throw(_("Branch is required."))
		self.company = frappe.db.get_value("CBT Branch", self.branch, "company")
		self.court_slug = scrub_court_slug(self.court_name)
		if not self.court_slug:
			frappe.throw(_("Court Name must contain at least one letter or digit."))
		if frappe.db.exists(
			"CBT Court", {"branch": self.branch, "court_slug": self.court_slug}
		):
			frappe.throw(
				_("A court named {0} already exists in branch {1}.").format(
					self.court_name, self.branch
				)
			)

	def validate(self):
		# Leak vector 2: company drives every tenancy filter — it must exist
		# and always mirror the branch.
		if not self.branch:
			frappe.throw(_("Branch is required."))
		if not self.is_new():
			before = frappe.db.get_value("CBT Court", self.name, "branch")
			if before and before != self.branch:
				frappe.throw(
					_(
						"Branch cannot be changed after creation — it is part of the "
						"court's document ID. Create a new court instead."
					)
				)
		branch_company = frappe.db.get_value("CBT Branch", self.branch, "company")
		if not self.company:
			self.company = branch_company
		if self.company != branch_company:
			frappe.throw(
				_("Company {0} does not match the branch's company {1}.").format(
					self.company, branch_company
				)
			)
		if flt(self.hourly_rate) <= 0:
			frappe.throw(_("Base Hourly Rate must be greater than zero."))
		self._validate_rate_rules()

	# ------------------------------------------------------------------
	# Rate rules (section-14, PLAN §8b)
	# ------------------------------------------------------------------

	def _validate_rate_rules(self):
		"""Well-formed windows, and no AMBIGUITY within a specificity tier.

		CROSS-tier overlap is legal and is the whole point — "₱300 all day, but
		₱500 on Friday nights" is two rules covering Friday 18:00, resolved by
		precedence (pricing.rule_tier). SAME-tier overlap has no such answer,
		so it is refused at save rather than resolved by row order at read.

		Buckets are keyed by (tier, scope-key): each weekday name is its own
		bucket, Weekdays and Weekends are separate buckets (they cover disjoint
		days, so they can never conflict with each other), and All Days is one.
		"""
		rows = self.get("rate_rules") or []
		if not rows:
			return

		buckets: dict[tuple, list] = {}
		for row in rows:
			# None-checks, NOT truthiness — a 00:00 window is timedelta(0)
			# (S4 as-built 9). reqd on the field only catches an empty STRING.
			if row.start_time is None or row.end_time is None:
				frappe.throw(
					_("Rate rule #{0}: both From and Until are required.").format(row.idx)
				)
			start, end = _as_timedelta(row.start_time), _as_timedelta(row.end_time)
			if end <= start:
				frappe.throw(
					_(
						"Rate rule #{0}: Until ({1}) must be later than From ({2}). "
						"A window that crosses midnight is not supported — add one "
						"rule per day instead."
					).format(row.idx, row.end_time, row.start_time)
				)
			if flt(row.hourly_rate) <= 0:
				frappe.throw(
					_("Rate rule #{0}: Hourly Rate must be greater than zero.").format(
						row.idx
					)
				)
			key = self._bucket_key(row)
			if key is None:
				frappe.throw(
					_("Rate rule #{0}: {1} is not a valid Applies On value.").format(
						row.idx, row.day_scope
					)
				)
			for other_idx, other_start, other_end in buckets.setdefault(key, []):
				if _overlaps(start, end, other_start, other_end):
					frappe.throw(
						_(
							"Rate rules #{0} and #{1} both cover {2} and overlap in "
							"time — a slot in the overlap would have two prices. "
							"Rules at DIFFERENT scopes may overlap (a specific day "
							"beats Weekdays/Weekends, which beats All Days)."
						).format(other_idx, row.idx, _(row.day_scope))
					)
			buckets[key].append((row.idx, start, end))

	@staticmethod
	def _bucket_key(row):
		"""Which rules can conflict with this one? None = unknown scope."""
		scope = row.day_scope
		if scope in DAY_NAMES:
			return (TIER_SPECIFIC_DAY, scope)
		if scope in ("Weekdays", "Weekends"):
			return (TIER_DAY_GROUP, scope)
		if scope == "All Days":
			# Any weekday resolves All Days to its own tier — index 0 is only a
			# probe to keep the key derivation going through the same helper.
			return (rule_tier(scope, 0), scope)
		return None
