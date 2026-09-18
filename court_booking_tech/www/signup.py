# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/signup — the D9 manual-registration page (PLAN §7).

Hosts the Turnstile widget and posts to the wrapped sign_up endpoint. Social
login (D8) is the promoted primary CTA whenever a provider is configured.
The stock login-page signup panel is replaced by a link-card to THIS page
(hooks.signup_form_template) because frappe's login.js posts token-less.
"""

import frappe
from frappe.utils.oauth import get_oauth2_authorize_url

no_cache = 1


def provider_logins() -> list:
	"""Every enabled provider, sending a NEW customer to the marketplace.

	Backlog B51: this used to send them to `/me`, frappe's account page — the
	one screen on the site that is not ours.
	"""
	return [
		{
			"name": provider.name,
			"provider_name": provider.provider_name,
			"auth_url": get_oauth2_authorize_url(provider.name, "/find-court"),
		}
		for provider in frappe.get_all(
			"Social Login Key",
			filters={"enable_social_login": 1},
			fields=["name", "provider_name"],
			order_by="name",
		)
	]


def get_context(context):
	if frappe.session.user != "Guest":
		frappe.local.flags.redirect_location = "/me"
		raise frappe.Redirect

	settings = frappe.get_cached_doc("CBT Platform Settings")
	context.enable_turnstile = bool(settings.enable_turnstile)
	# Site key only — the secret never reaches any page context.
	context.turnstile_site_key = settings.turnstile_site_key or ""

	context.provider_logins = provider_logins()
	return context
