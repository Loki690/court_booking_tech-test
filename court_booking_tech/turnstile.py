# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Cloudflare Turnstile verification (PLAN §2 D9).

_siteverify is the ONLY network seam — backend tests patch exactly that
function and never touch the wire (PLAN §8v). Everything here is fail-closed:
a missing token, a rejected token, or a siteverify outage all block manual
signup with the same friendly message (social login keeps working, so an
outage is degraded-not-dead; the Platform Settings toggle is the kill-switch).
"""

import requests

import frappe
from frappe import _

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SITEVERIFY_TIMEOUT = 5  # seconds — signup must fail fast, not hang

# One message for every failure mode: token missing, token rejected, outage.
# Distinct messages would leak which layer tripped.
FAIL_CLOSED_MSG = _(
	"Signup is temporarily unavailable. Please sign in with Google, "
	"or try again in a few minutes."
)


def _siteverify(secret: str, token: str, remoteip: str | None = None) -> dict:
	"""POST to Cloudflare siteverify. Raises on network trouble; returns the
	decoded JSON verdict otherwise. The single patch-point for tests."""
	data = {"secret": secret, "response": token}
	if remoteip:
		data["remoteip"] = remoteip
	resp = requests.post(SITEVERIFY_URL, data=data, timeout=SITEVERIFY_TIMEOUT)
	resp.raise_for_status()
	return resp.json()


def _get_secret() -> str:
	from frappe.utils.password import get_decrypted_password

	return (
		get_decrypted_password(
			"CBT Platform Settings",
			"CBT Platform Settings",
			"turnstile_secret_key",
			raise_exception=False,
		)
		or ""
	)


def verify_or_throw(token: str | None):
	"""Enforce Turnstile on a manual-signup request (caller has already
	checked that enable_turnstile is ON). Fail-closed on every path."""
	if not token:
		frappe.throw(FAIL_CLOSED_MSG, title=_("Verification Required"))

	try:
		secret = _get_secret()
	except Exception:
		# e.g. "Encryption key is invalid" on a mis-provisioned site — a raw
		# decrypt error must never reach the signup client. File logger:
		# the throw below rolls back any Error Log row.
		frappe.logger("court_booking_tech").error(
			"Turnstile secret unreadable", exc_info=True
		)
		secret = None
	if not secret:
		# Toggle ON with no/unreadable secret is a misconfiguration — fail
		# closed; the save-time validation exists to make this unreachable.
		frappe.throw(FAIL_CLOSED_MSG, title=_("Verification Required"))

	remoteip = getattr(frappe.local, "request_ip", None)
	try:
		try:
			verdict = _siteverify(secret, token, remoteip)
		except Exception:
			# One retry: cold DNS/TLS blips fail the FIRST outbound call of a
			# fresh container in ~2s. If the first attempt actually consumed
			# the token, the retry returns a clean verdict error (handled
			# below), never a crash.
			verdict = _siteverify(secret, token, remoteip)
	except Exception:
		# File logger FIRST: the frappe.throw below rolls the transaction
		# back, deleting any Error Log row with it — the file line is the
		# only evidence that survives. Explicit plain traceback on the DB
		# attempt: log_error without a message renders with_context=True
		# (source per frame) — measured ~26s under a deep stack.
		frappe.logger("court_booking_tech").error(
			"Turnstile siteverify unreachable", exc_info=True
		)
		frappe.log_error(
			title="Turnstile siteverify unreachable", message=frappe.get_traceback()
		)
		frappe.throw(FAIL_CLOSED_MSG, title=_("Verification Required"))

	if not verdict.get("success"):
		frappe.throw(FAIL_CLOSED_MSG, title=_("Verification Required"))


def validate_secret(secret: str):
	"""Save-time round-trip (CBT Platform Settings.validate): a bad secret
	must fail loudly at SAVE, not at the first customer signup.

	A dummy token against a VALID secret yields success=False with
	error-codes like invalid-input-response — that proves the secret works.
	invalid-input-secret / missing-input-secret prove it does not.
	"""
	if not secret:
		frappe.throw(_("Turnstile Secret Key is required to enable Turnstile."))
	try:
		verdict = _siteverify(secret, "cbt-secret-validation-probe")
	except Exception:
		frappe.throw(
			_(
				"Could not reach Cloudflare to validate the Turnstile secret. "
				"Check connectivity and save again."
			)
		)
	bad_secret = {"invalid-input-secret", "missing-input-secret"}
	if bad_secret & set(verdict.get("error-codes") or []):
		frappe.throw(
			_(
				"Cloudflare rejected the Turnstile Secret Key "
				"(invalid-input-secret). Fix the key before enabling Turnstile."
			)
		)
