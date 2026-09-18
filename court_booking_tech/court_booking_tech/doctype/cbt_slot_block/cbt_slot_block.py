# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Slot Block — takes court time off the grid (PLAN §8d). An EMPTY court
means the WHOLE BRANCH is closed for that window (holidays). Availability and
the booking overlap lock both honor blocks; creating a block over an existing
Confirmed booking WARNS (staff decide how to handle the guest) but does not
throw.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from court_booking_tech.slots import _as_timedelta, _fmt, _overlaps
from court_booking_tech.tenancy import require_company_access
from court_booking_tech.timeutil import END_OF_DAY


class CBTSlotBlock(Document):
	def before_insert(self):
		self._mirror_company()

	def validate(self):
		if not self.branch:
			frappe.throw(_("Branch is required."))
		self._mirror_company()
		if not self.company:
			# Leak vector 2: company drives every tenancy filter.
			frappe.throw(_("Company is required."))
		# Blocking is company housekeeping, not booking-creating — suspended
		# companies may still manage their calendar.
		require_company_access(self.company, allow_suspended=True)
		self._validate_court()
		self._validate_window()
		self._warn_confirmed_overlaps()

	def _mirror_company(self):
		if self.branch:
			self.company = frappe.db.get_value("CBT Branch", self.branch, "company")

	def _validate_court(self):
		if not self.court:
			return  # whole-branch closure
		court_branch = frappe.db.get_value("CBT Court", self.court, "branch")
		if court_branch != self.branch:
			frappe.throw(
				_("Court {0} belongs to branch {1}, not {2}.").format(
					self.court, court_branch, self.branch
				)
			)

	def _validate_window(self):
		start = _as_timedelta(self.start_time)
		end = _as_timedelta(self.end_time)
		if end <= start:
			frappe.throw(
				_("End Time must be after Start Time (same-day blocks only).")
			)
		# A block to midnight ends at 24:00; str(timedelta) would write
		# '1 day, 0:00:00' on the next save — keep the canonical '24:00:00'.
		if end >= END_OF_DAY:
			self.end_time = _fmt(end)

	def _warn_confirmed_overlaps(self):
		filters = {
			"branch": self.branch,
			"booking_date": self.block_date,
			"booking_status": ("in", ("Confirmed", "Extended")),
		}
		if self.court:
			filters["court"] = self.court
		start = _as_timedelta(self.start_time)
		end = _as_timedelta(self.end_time)
		clashing = [
			booking.name
			for booking in frappe.get_all(
				"CBT Court Booking",
				filters=filters,
				fields=["name", "start_time", "end_time"],
			)
			if _overlaps(
				start,
				end,
				_as_timedelta(booking.start_time),
				_as_timedelta(booking.end_time),
			)
		]
		if clashing:
			frappe.msgprint(
				_(
					"Warning: this block overlaps confirmed booking(s) {0}. "
					"They stay confirmed — contact the guests or cancel/rebook."
				).format(", ".join(clashing)),
				indicator="orange",
				title=_("Confirmed bookings in the blocked window"),
			)
