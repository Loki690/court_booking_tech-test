# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Google login (Backlog B51, 2026-09-09) — the ONE writer of the `google` Social
Login Key.

frappe does the OAuth itself (`frappe.integrations.oauth2_logins.login_via_google`
and `frappe.utils.oauth`): the login page shows the button the moment an ENABLED
key carries a client id and a decryptable secret, the callback is built from the
SITE's own URL, and a first Google login creates a Website User carrying Portal
Settings' default role. This module only puts that key in place, the same way on
every site — dev from `cbt_google_login` in common_site_config.json through the
seed, test and prod through `bench execute`. Operator guide:
docs/PRODUCTION_DEPLOYMENT.md §3.
"""

import frappe
from frappe import _
from frappe.utils import cint

PROVIDER = "Google"
# SocialLoginKey.autoname = frappe.scrub(provider_name).
KEY_NAME = "google"
# NOT `google_login`: that name is frappe's own credential override
# (frappe.utils.oauth.get_oauth_keys) and would feed the OAuth flow behind the
# desk's back.
CONF_KEY = "cbt_google_login"


def configure_google(client_id: str = "", client_secret: str = "", enable=1) -> dict:
	"""Create or update the Google key from frappe's own preset. Idempotent.

	bench --site <site> execute court_booking_tech.sso.configure_google \\
	    --kwargs '{"client_id": "...", "client_secret": "..."}'
	"""
	enable = cint(enable)
	client_id = (client_id or "").strip()
	client_secret = (client_secret or "").strip()
	if enable and not (client_id and client_secret):
		frappe.throw(
			_("Google login needs both a client id and a client secret before it can be enabled.")
		)

	if frappe.db.exists("Social Login Key", KEY_NAME):
		doc = frappe.get_doc("Social Login Key", KEY_NAME)
	else:
		doc = frappe.new_doc("Social Login Key")
		doc.social_login_provider = PROVIDER
		# The preset: provider name, Google's authorize/token/userinfo URLs,
		# the login_via_google callback and the icon.
		doc.get_social_login_provider(PROVIDER, initialize=True)
	if client_id:
		doc.client_id = client_id
	if client_secret:
		doc.client_secret = client_secret
	doc.enable_social_login = enable
	# A first Google login IS a signup — pinned on the key so a later global
	# "disable signup" cannot refuse it silently.
	doc.sign_ups = "Allow"
	doc.save(ignore_permissions=True)
	return google_login_status()


def google_login_status() -> dict:
	"""bench --site <site> execute court_booking_tech.sso.google_login_status"""
	row = frappe.db.get_value(
		"Social Login Key",
		KEY_NAME,
		["enable_social_login", "client_id", "redirect_url"],
		as_dict=True,
	)
	if not row:
		return {"configured": False, "enabled": False, "client_id": None, "callback": None}
	return {
		"configured": True,
		"enabled": bool(cint(row.enable_social_login)),
		"client_id": row.client_id,
		"callback": frappe.utils.get_url(row.redirect_url),
	}
