# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""CBT Ad — a paid banner on the marketplace home page (Backlog B52).

PLATFORM SCOPE, deliberately: there is no `company` link. `/find-court` is the
platform's own front door — the one surface every customer of every tenant passes
through — so the placement is the platform's to sell, and a tenant cannot create
one. Reasoning lives in docs/sections; this file is the rules.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
	clean_public_link,
)


class CBTAd(Document):
	def validate(self):
		# Same validator the company's public links use: this is a guest page and
		# a `javascript:` href would run in our own origin.
		self.link_url = clean_public_link(self.link_url, _("Link"))
		if not self.link_url:
			frappe.throw(_("A link is required."))
		if getdate(self.ends_on) < getdate(self.starts_on):
			frappe.throw(_("The end date cannot be before the start date."))
		self._require_public_image()

	def _require_public_image(self):
		"""A /private/files banner 404s for the guest the ad exists to reach.

		Section-22's rule: a file is RE-CREATED as public, never flipped, so this
		refuses rather than quietly rewriting somebody's attachment.
		"""
		if self.image and self.image.startswith("/private/"):
			frappe.throw(
				_("The banner must be a public file — a private one cannot be shown to visitors.")
			)
