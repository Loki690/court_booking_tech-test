"""
Court Booking Tech — Google login (Backlog B51, 2026-09-09)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_sso

What the app OWNS is pinned here: the one writer of the `google` Social Login
Key (idempotent, preset URLs, refuses to enable without both credentials, and
`sign_ups = Allow` so a first Google login is never refused as a signup), the
authorize URL frappe builds from it (Google's host, the site's own callback,
the userinfo scopes, `redirect_to` behind the single-use state token frappe
>= 16.29 keeps server-side), /signup's provider list
pointing at the marketplace, and — through frappe's own `update_oauth_user`,
with a Google userinfo payload and no network — that a first Google login
creates a WEBSITE USER carrying CBT Customer whose home page is /find-court.
The round trip to Google itself is external and is proved by hand
(docs/PRODUCTION_DEPLOYMENT.md §3.6); nothing here contacts it.

Every key and user created here is deleted in addCleanup — FrappeTestCase has
no per-test savepoint on this bench.
"""

from urllib.parse import parse_qs, urlparse

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.oauth import (
	OAUTH_LOGIN_FLOW_CACHE_PREFIX,
	get_oauth2_authorize_url,
	update_oauth_user,
)

from court_booking_tech import sso
from court_booking_tech.customer import CUSTOMER_ROLE, website_user_home_page
from court_booking_tech.seeds.seed_test_data import seed_all
from court_booking_tech.www.signup import provider_logins

CALLBACK = "/api/method/frappe.integrations.oauth2_logins.login_via_google"
CLIENT = "test-client.apps.googleusercontent.com"
SECRET = "GOCSPX-test-secret"
GOOGLE_USER = "b51.google.newcomer@example.com"


class SsoTestCase(FrappeTestCase):
	"""Shared fixtures; holds NO test methods (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(self._drop_key)

	def tearDown(self):
		frappe.set_user("Administrator")

	@staticmethod
	def _drop_key():
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"Social Login Key", sso.KEY_NAME, force=True, ignore_permissions=True, ignore_missing=True
		)


class TestGoogleLoginKey(SsoTestCase):
	def test_configure_creates_an_enabled_key_from_frappes_preset(self):
		status = sso.configure_google(CLIENT, SECRET)
		doc = frappe.get_doc("Social Login Key", sso.KEY_NAME)
		self.assertEqual(doc.provider_name, "Google")
		self.assertEqual(doc.social_login_provider, "Google")
		self.assertEqual(doc.authorize_url, "https://accounts.google.com/o/oauth2/auth")
		self.assertEqual(doc.access_token_url, "https://accounts.google.com/o/oauth2/token")
		self.assertEqual(doc.redirect_url, CALLBACK)
		self.assertEqual(doc.base_url, "https://www.googleapis.com")
		self.assertEqual(doc.enable_social_login, 1)
		self.assertEqual(doc.sign_ups, "Allow")
		self.assertEqual(doc.client_id, CLIENT)
		self.assertEqual(doc.get_password("client_secret"), SECRET)
		self.assertTrue(status["configured"] and status["enabled"])
		self.assertTrue(status["callback"].endswith(CALLBACK), status)

	def test_configure_is_idempotent_and_updates_in_place(self):
		sso.configure_google(CLIENT, SECRET)
		sso.configure_google("second-client", "second-secret")
		self.assertEqual(frappe.db.count("Social Login Key", {"provider_name": "Google"}), 1)
		doc = frappe.get_doc("Social Login Key", sso.KEY_NAME)
		self.assertEqual(doc.client_id, "second-client")
		self.assertEqual(doc.get_password("client_secret"), "second-secret")
		self.assertEqual(doc.enable_social_login, 1)

	def test_enabling_without_both_credentials_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			sso.configure_google("", "")
		with self.assertRaises(frappe.ValidationError):
			sso.configure_google(CLIENT, "")
		self.assertFalse(frappe.db.exists("Social Login Key", sso.KEY_NAME))

	def test_disable_keeps_the_key_and_takes_the_button_away(self):
		sso.configure_google(CLIENT, SECRET)
		status = sso.configure_google(enable=0)
		self.assertTrue(status["configured"])
		self.assertFalse(status["enabled"])
		# frappe's login page lists ENABLED keys only (www/login.py).
		self.assertEqual(
			frappe.get_all("Social Login Key", filters={"enable_social_login": 1}, pluck="name"),
			[],
		)
		self.assertEqual(provider_logins(), [])

	def test_status_without_a_key(self):
		self.assertEqual(
			sso.google_login_status(),
			{"configured": False, "enabled": False, "client_id": None, "callback": None},
		)


class TestGoogleLoginFlow(SsoTestCase):
	def _state(self, url: str) -> dict:
		"""frappe >= 16.29 keeps redirect_to in the cache behind a single-use token."""
		token = parse_qs(urlparse(url).query)["state"][0]
		return {"redirect_to": frappe.cache.get_value(f"{OAUTH_LOGIN_FLOW_CACHE_PREFIX}:{token}")}

	def test_the_authorize_url_carries_the_callback_scopes_and_redirect(self):
		sso.configure_google(CLIENT, SECRET)
		url = get_oauth2_authorize_url(sso.KEY_NAME, "/find-court")
		parsed = urlparse(url)
		query = parse_qs(parsed.query)
		self.assertEqual(parsed.netloc, "accounts.google.com")
		self.assertEqual(parsed.path, "/o/oauth2/auth")
		self.assertEqual(query["client_id"][0], CLIENT)
		self.assertTrue(query["redirect_uri"][0].endswith(CALLBACK), query["redirect_uri"])
		self.assertIn("userinfo.email", query["scope"][0])
		self.assertIn("userinfo.profile", query["scope"][0])
		self.assertEqual(query["response_type"][0], "code")
		self.assertEqual(self._state(url)["redirect_to"], "/find-court")

	def test_signup_offers_google_to_the_marketplace_not_frappes_account_page(self):
		sso.configure_google(CLIENT, SECRET)
		rows = provider_logins()
		self.assertEqual([row["provider_name"] for row in rows], ["Google"])
		self.assertEqual(self._state(rows[0]["auth_url"])["redirect_to"], "/find-court")

	def test_a_first_google_login_makes_a_customer_whose_home_is_the_marketplace(self):
		"""frappe's own user-creation path with a Google userinfo payload — the
		ducky's finding 13: assert the Website User and the role, never assume."""
		sso.configure_google(CLIENT, SECRET)
		self.addCleanup(
			frappe.delete_doc,
			"User",
			GOOGLE_USER,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
		update_oauth_user(
			GOOGLE_USER,
			{
				"id": "1234567890",
				"email": GOOGLE_USER,
				"verified_email": True,
				"given_name": "Gina",
				"family_name": "Googler",
				"picture": "https://example.com/gina.png",
			},
			"google",
		)
		user = frappe.get_doc("User", GOOGLE_USER)
		self.assertEqual(user.user_type, "Website User")
		self.assertIn(CUSTOMER_ROLE, [row.role for row in user.roles])
		self.assertEqual(user.get_social_login_userid("google"), "1234567890")
		self.assertEqual(user.first_name, "Gina")
		self.assertEqual(website_user_home_page(GOOGLE_USER), "find-court")
