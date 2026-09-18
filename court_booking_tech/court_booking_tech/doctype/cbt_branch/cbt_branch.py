# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from court_booking_tech.court_booking_tech.doctype.cbt_business_hours.cbt_business_hours import (
	validate_business_hours,
)
from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
	SLUG_PATTERN,
)
from court_booking_tech.tenancy import (
	ensure_branch_management_allowed,
	reconcile_photos_on_parent_save,
)

WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_OPENING = "06:00:00"
DEFAULT_CLOSING = "22:00:00"


class CBTBranch(Document):
	def before_insert(self):
		# Naming happens BEFORE validate on insert, and fetch_from only runs
		# client-side — both name components must be resolved here or server
		# inserts (seeds, APIs) produce a broken document ID.
		if not self.company:
			frappe.throw(_("Company is required."))
		self._validate_slug()
		self.company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		if not self.company_code:
			frappe.throw(_("Company {0} does not exist.").format(self.company))

	def validate(self):
		if not self.company:
			# Leak vector 2: a blank company link is visible to every tenant.
			frappe.throw(_("Company is required."))
		self._validate_immutables()
		self._validate_slug()
		self._validate_slug_unique()
		self.company_code = frappe.db.get_value(
			"CBT Company", self.company, "company_code"
		)
		self._populate_default_hours()
		validate_business_hours(self.business_hours, label=_("Court Hours"))
		self._validate_coordinates()
		self._validate_layout()
		reconcile_photos_on_parent_save(self)
		ensure_branch_management_allowed(self.company)

	def _validate_immutables(self):
		if self.is_new():
			return
		before = frappe.db.get_value(
			"CBT Branch", self.name, ["slug", "company"], as_dict=True
		)
		if not before:
			return
		if before.slug != self.slug:
			frappe.throw(
				_(
					"The slug is part of this branch's document ID and printed deep "
					"links — it cannot be changed. Create a new branch instead."
				)
			)
		if before.company != self.company:
			frappe.throw(
				_(
					"Company cannot be changed after creation — it is part of the "
					"branch's document ID and tenancy scope."
				)
			)

	def _validate_slug(self):
		if not SLUG_PATTERN.match(self.slug or ""):
			frappe.throw(
				_(
					"Slug must be lowercase letters/digits separated by single hyphens "
					"(e.g. bgc, timog-hub)."
				)
			)

	def _validate_slug_unique(self):
		# On INSERT the name is already set (naming runs before validate) and
		# equals {company_code}-{slug} — excluding self.name would exclude the
		# duplicate itself and let the insert die as a raw DuplicateEntryError.
		filters = {"company": self.company, "slug": self.slug}
		if not self.is_new():
			filters["name"] = ("!=", self.name)
		duplicate = frappe.db.exists("CBT Branch", filters)
		if duplicate:
			frappe.throw(
				_("Branch slug {0} is already used by {1} in this company.").format(
					self.slug, duplicate
				)
			)

	def _populate_default_hours(self):
		if not self.is_new() or self.business_hours:
			return
		for day in WEEK:
			self.append(
				"business_hours",
				{
					"day": day,
					"is_open": 1,
					"opening_time": DEFAULT_OPENING,
					"closing_time": DEFAULT_CLOSING,
				},
			)

	def _validate_coordinates(self):
		# Truthiness on purpose: 0.0 means "no pin" (no PH branch sits at 0,0);
		# geo.get_branches sorts pin-less branches last on the same test.
		if bool(self.latitude) != bool(self.longitude):
			frappe.throw(
				_("Set both Latitude and Longitude (drop a pin), or leave both empty.")
			)
		if self.latitude and not -90 <= flt(self.latitude) <= 90:
			frappe.throw(_("Latitude must be between -90 and 90."))
		if self.longitude and not -180 <= flt(self.longitude) <= 180:
			frappe.throw(_("Longitude must be between -180 and 180."))

	def _validate_layout(self):
		rows, cols = cint(self.layout_rows), cint(self.layout_columns)
		if self.layout and (rows < 1 or cols < 1):
			frappe.throw(_("Layout Rows and Layout Columns must be at least 1."))
		seen_cells = set()
		seen_courts = set()
		for cell in self.layout or []:
			if not (1 <= cint(cell.row_index) <= rows and 1 <= cint(cell.col_index) <= cols):
				frappe.throw(
					_(
						"Layout cell ({0}, {1}) is outside the {2}×{3} floor plan."
					).format(cell.row_index, cell.col_index, rows, cols)
				)
			pos = (cint(cell.row_index), cint(cell.col_index))
			if pos in seen_cells:
				frappe.throw(
					_("Layout cell ({0}, {1}) is defined twice.").format(*pos)
				)
			seen_cells.add(pos)
			if not cell.court:
				continue  # empty cell = floor-plan gap
			if cell.court in seen_courts:
				frappe.throw(
					_("Court {0} appears more than once in the floor plan.").format(
						cell.court
					)
				)
			seen_courts.add(cell.court)
			court_branch = frappe.db.get_value("CBT Court", cell.court, "branch")
			if court_branch != self.name:
				frappe.throw(
					_("Court {0} belongs to branch {1}, not this branch.").format(
						cell.court, court_branch
					)
				)

	# ------------------------------------------------------------------
	# Fallback accessor (consumed by the section-4 slot engine). As-built:
	# the chain is branch -> platform default — CBT Company has no
	# slot-duration field (section-2 authoritative spec).
	# ------------------------------------------------------------------

	def get_slot_duration_minutes(self) -> int:
		return cint(self.slot_duration_minutes) or cint(
			frappe.db.get_single_value(
				"CBT Platform Settings", "default_slot_duration_minutes"
			)
		)
