# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Payment Proof — IMMUTABLE once uploaded (PLAN §4).

A deleted or edited proof un-holding a booking is a state nobody wants to
reason about, so:
- every save after insert is rejected here in validate;
- the review lifecycle (Pending → Accepted / Rejected) is driven exclusively
  by api/proofs.py via frappe.db.set_value under the booking row lock;
- creation happens ONLY through api/proofs.upload_proof (caps, locks and the
  verification deadline live there) — company roles hold no create DocPerm.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from court_booking_tech import clock


class CBTPaymentProof(Document):
	def before_insert(self):
		# A proof pays for EXACTLY ONE thing (section-10): a court booking, or
		# an open play participant. Neither leaves the row unscoped; both makes
		# "which clock governs this?" unanswerable — and open play is
		# deliberately clock-free (PLAN §8q).
		if bool(self.booking) == bool(self.open_play_session):
			frappe.throw(
				_(
					"A payment proof belongs to exactly one booking OR one open "
					"play session."
				)
			)
		# The parent chain is authoritative — whatever the caller sent for
		# company is overwritten (also neutralizes the desk default-company
		# pre-fill leak, S4 as-built 8).
		if self.booking:
			self.company = frappe.db.get_value(
				"CBT Court Booking", self.booking, "company"
			)
			if not self.company:
				frappe.throw(_("Booking {0} does not exist.").format(self.booking))
		else:
			if not self.participant_ref:
				frappe.throw(
					_("An open play proof must name the participant it pays for.")
				)
			self.company = frappe.db.get_value(
				"CBT Open Play Session", self.open_play_session, "company"
			)
			if not self.company:
				frappe.throw(
					_("Open play session {0} does not exist.").format(
						self.open_play_session
					)
				)
		self.uploaded_by = self.uploaded_by or frappe.session.user
		self.uploaded_at = self.uploaded_at or clock.now_dt()

	def validate(self):
		if not self.is_new():
			frappe.throw(
				_(
					"Payment proofs are immutable — accepting or rejecting them "
					"goes through the review actions."
				)
			)

	def on_trash(self):
		if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_("Payment proofs cannot be deleted."), frappe.PermissionError
			)
