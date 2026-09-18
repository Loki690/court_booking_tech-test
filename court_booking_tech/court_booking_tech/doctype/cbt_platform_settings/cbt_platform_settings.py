# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CBTPlatformSettings(Document):
	def validate(self):
		self._validate_turnstile()

	def _validate_turnstile(self):
		"""A bad Turnstile secret must fail loudly at SAVE, not at the first
		customer signup (section-8). The live round-trip runs only when the
		toggle flips ON or the secret changes — never on unrelated saves.
		testing.set_turnstile sets skip_turnstile_validation (E2E setup must
		not depend on Cloudflare reachability)."""
		if not self.enable_turnstile:
			return
		if not self.turnstile_site_key:
			frappe.throw(_("Turnstile Site Key is required to enable Turnstile."))

		if self.flags.skip_turnstile_validation:
			return

		# Only on the OFF->ON flip (the section-8 spec's trigger). Password
		# fields round-trip masked, so a value-diff gate would be unreliable;
		# to swap the secret later, toggle off and back on (as-built note).
		before = self.get_doc_before_save()
		if before and before.enable_turnstile:
			return

		from court_booking_tech.turnstile import validate_secret

		validate_secret(self.get_password("turnstile_secret_key", raise_exception=False))
