# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
D9 signup wrapper (PLAN §2 D9) — the ONE leak-proof seam around frappe's
stock sign_up, registered via hooks.override_whitelisted_methods so it covers
api v1, api v2 AND `cmd=` form posts.

Layers, in order:
1. methods=["POST"] whitelist (hardening core lacks) + per-IP rate limit
   backstop (generous — PH carrier CGNAT, Turnstile is the real gate).
2. Turnstile verification when enabled (fail-closed; kill-switch in
   CBT Platform Settings).
3. Anti-enumeration: EVERY successful-looking outcome (new user, existing
   enabled, existing disabled) returns the same constant tuple — applies
   regardless of the Turnstile toggle. Stock sign_up's "Already Registered" /
   "Registered but disabled" replies are an email-enumeration oracle.
   The existing-address branch still TELLS the account holder what to do —
   by mail, never by response (Backlog B22, notifications.notify_account_exists).
4. Delegate to core by DIRECT import — never frappe.call, which would
   re-dispatch through the override and loop forever.
"""

import frappe
from frappe import _
from frappe.core.doctype.user.user import sign_up as core_sign_up
from frappe.rate_limiter import rate_limit

from court_booking_tech import notifications, turnstile

# The one success response. Matches core's email-sent reply so production
# behaviour is indistinguishable between new and existing accounts.
GENERIC_SUCCESS = (1, _("Please check your email for verification"))


def get_signup_ip_limit() -> int:
	from frappe.utils import cint

	return (
		cint(
			frappe.db.get_single_value(
				"CBT Platform Settings", "signup_ip_limit_per_hour"
			)
		)
		or 25
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=get_signup_ip_limit, seconds=60 * 60)
def sign_up(
	email: str,
	full_name: str,
	redirect_to: str | None = None,
	cf_turnstile_token: str | None = None,
):
	email = (email or "").strip()

	# Captcha BEFORE the existence check: bots must not enumerate without
	# solving the challenge first.
	if frappe.db.get_single_value("CBT Platform Settings", "enable_turnstile"):
		turnstile.verify_or_throw(cf_turnstile_token)

	# Same lookup core uses ({"email": ...}, not the docname) so the two can
	# never disagree about "existing".
	if frappe.db.exists("User", {"email": email}):
		# Backlog B22: the screen says "check your email" — so something has
		# to arrive. Queued, swallowed on failure, response unchanged.
		notifications.notify_account_exists(email)
		return GENERIC_SUCCESS

	core_sign_up(email, full_name, redirect_to or "")
	# Normalize the fresh-signup reply too: core returns (2, "ask your
	# administrator") when no sender is configured (dev), which would make
	# new-vs-existing distinguishable there.
	return GENERIC_SUCCESS
