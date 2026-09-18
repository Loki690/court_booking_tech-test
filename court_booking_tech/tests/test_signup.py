# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Section-8 backend suite: the D9 signup wrapper (Turnstile, anti-enumeration,
rate limit), Platform Settings save-time secret validation, the customer
profile API's structural own-only guard, the default-role hook, and the
section-2 welcome-email carry-in.

Network purity (PLAN §8v): every Turnstile path patches
court_booking_tech.turnstile._siteverify — the suite never touches the wire.

Run: bench --site dev.localhost run-tests --module court_booking_tech.tests.test_signup
"""
import re
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_url, set_request

from court_booking_tech import notifications, throttle, turnstile
from court_booking_tech.api import signup as signup_api
from court_booking_tech.api.profile import get_my_profile, update_my_profile
from court_booking_tech.customer import ensure_customer_profile
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL


def _unique_email(prefix: str) -> str:
	return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"


def _make_website_user(email: str, first_name: str = "Testy") -> str:
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": "SignupCase",
			"enabled": 1,
			"user_type": "Website User",
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	return email


def _queued_for(recipient: str) -> list[dict]:
	return frappe.get_all(
		"Email Queue",
		filters=[["Email Queue Recipient", "recipient", "=", recipient]],
		fields=["name", "message", "reference_doctype", "reference_name"],
	)


def _email_queue_count(recipient: str) -> int:
	return len(_queued_for(recipient))


def _plain(message: str) -> str:
	"""Undo quoted-printable SOFT line breaks before searching a queued body.

	Email Queue stores the full MIME; QP soft-wraps long lines with "=" +
	newline, which can split a URL in half. Assert on the BODY, never the
	Subject header — it is RFC2047-encoded and `Email Queue` has no subject
	column at all (docs/README.md silent-failure classes)."""
	return re.sub(r"=\r?\n", "", message or "")


def _turnstile_off():
	doc = frappe.get_single("CBT Platform Settings")
	doc.enable_turnstile = 0
	doc.flags.skip_turnstile_validation = True
	doc.save(ignore_permissions=True)


class TestSignupWrapper(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def tearDown(self):
		self._set_turnstile(0)
		frappe.set_user("Administrator")
		super().tearDown()

	def _set_turnstile(self, enabled: int, secret: str = "test-secret"):
		doc = frappe.get_single("CBT Platform Settings")
		doc.enable_turnstile = enabled
		doc.turnstile_site_key = "test-site-key" if enabled else doc.turnstile_site_key
		if enabled:
			doc.turnstile_secret_key = secret
		doc.flags.skip_turnstile_validation = True
		doc.save(ignore_permissions=True)

	# -- seam registration -------------------------------------------------

	def test_override_seam_registered(self):
		"""The hooks override must cover the stock dotted path (api v1, v2
		AND cmd= all resolve through override_whitelisted_method)."""
		self.assertEqual(
			frappe.override_whitelisted_method("frappe.core.doctype.user.user.sign_up"),
			"court_booking_tech.api.signup.sign_up",
		)

	def test_wrapper_is_guest_post_only(self):
		"""methods=["POST"] hardening core lacks + allow_guest. Asserted on
		the exact registries the request handler consults."""
		fn = signup_api.sign_up
		self.assertIn(fn, frappe.whitelisted)
		self.assertIn(fn, frappe.guest_methods)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"])

	# -- turnstile enforcement ---------------------------------------------

	def test_tokenless_fails_closed_when_enabled(self):
		self._set_turnstile(1)
		with patch.object(turnstile, "_siteverify") as mock_sv:
			with self.assertRaises(frappe.ValidationError):
				signup_api.sign_up(_unique_email("tokenless"), "Tokenless Case")
		mock_sv.assert_not_called()  # no token -> no round trip, straight fail

	def test_rejected_token_fails_closed(self):
		self._set_turnstile(1)
		with patch.object(
			turnstile, "_siteverify", return_value={"success": False, "error-codes": ["invalid-input-response"]}
		):
			with self.assertRaises(frappe.ValidationError):
				signup_api.sign_up(_unique_email("rejected"), "Rejected Case", cf_turnstile_token="bad")

	def test_siteverify_outage_fails_closed(self):
		self._set_turnstile(1)
		with patch.object(turnstile, "_siteverify", side_effect=ConnectionError("down")):
			with self.assertRaises(frappe.ValidationError):
				signup_api.sign_up(_unique_email("outage"), "Outage Case", cf_turnstile_token="tok")

	def test_unreadable_secret_fails_closed(self):
		"""A raw decrypt error (mis-provisioned encryption_key) must map to
		the same friendly fail-closed message, never leak to the client."""
		self._set_turnstile(1)
		with patch.object(turnstile, "_get_secret", side_effect=Exception("decrypt boom")):
			with self.assertRaisesRegex(frappe.ValidationError, "temporarily unavailable"):
				signup_api.sign_up(
					_unique_email("nosecret"), "NoSecret Case", cf_turnstile_token="tok"
				)

	def test_valid_token_creates_customer(self):
		self._set_turnstile(1)
		email = _unique_email("happy")
		with patch.object(turnstile, "_siteverify", return_value={"success": True}):
			resp = signup_api.sign_up(email, "Happy SignupCase", cf_turnstile_token="tok")
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)

		user_name = frappe.db.get_value("User", {"email": email})
		self.assertTrue(user_name, "signup did not create the user")
		user = frappe.get_doc("User", user_name)
		self.assertEqual(user.user_type, "Website User")
		self.assertIn("CBT Customer", {r.role for r in user.roles})
		# Email Queue is the assertion surface — NO SMTP in dev (section-8).
		self.assertGreaterEqual(_email_queue_count(email), 1, "welcome mail not queued")

	def test_toggle_off_is_passthrough(self):
		self._set_turnstile(0)
		email = _unique_email("passthrough")
		with patch.object(turnstile, "_siteverify") as mock_sv:
			resp = signup_api.sign_up(email, "Passthrough Case")
		mock_sv.assert_not_called()
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)
		self.assertTrue(frappe.db.exists("User", {"email": email}))

	# -- anti-enumeration ---------------------------------------------------

	def test_existing_emails_get_byte_identical_generic_response(self):
		"""Enabled existing, disabled existing AND fresh signup all return the
		same constant — no oracle in any state, with the toggle in any state."""
		self._set_turnstile(0)
		enabled_email = _make_website_user(_unique_email("enabled"))
		disabled_email = _make_website_user(_unique_email("disabled"))
		frappe.db.set_value("User", {"email": disabled_email}, "enabled", 0)

		resp_enabled = signup_api.sign_up(enabled_email, "Enabled Again")
		resp_disabled = signup_api.sign_up(disabled_email, "Disabled Again")
		resp_fresh = signup_api.sign_up(_unique_email("fresh"), "Fresh Case")

		self.assertEqual(resp_enabled, resp_disabled)
		self.assertEqual(resp_enabled, resp_fresh)
		self.assertEqual(resp_enabled, signup_api.GENERIC_SUCCESS)
		# The existing call created no User — and since Backlog B22 it queues
		# exactly ONE mail (the account-exists notice), never a welcome mail.
		# Before B22 this line asserted 0, pinning the dead end the user hit.
		self.assertEqual(_email_queue_count(enabled_email), 1)

	# -- rate limit ---------------------------------------------------------

	def test_rate_limit_fires_at_limit_plus_one(self):
		self._set_turnstile(0)
		frappe.db.set_single_value(
			"CBT Platform Settings", "signup_ip_limit_per_hour", 3
		)
		self.addCleanup(
			frappe.db.set_single_value,
			"CBT Platform Settings",
			"signup_ip_limit_per_hour",
			25,
		)
		set_request(method="POST", path="/api/method/frappe.core.doctype.user.user.sign_up")
		# Unique per run: the limiter's redis window outlives the DB rollback.
		frappe.local.request_ip = f"rl-test-{uuid.uuid4().hex[:8]}"
		try:
			for _i in range(3):
				signup_api.sign_up(_unique_email("ratelimit"), "Rate LimitCase")
			with self.assertRaises(frappe.RateLimitExceededError):
				signup_api.sign_up(_unique_email("ratelimit"), "Rate LimitCase")
		finally:
			frappe.local.request = None
			frappe.local.request_ip = None


class TestExistingEmailMail(FrappeTestCase):
	"""Backlog B22 — the existing-address branch of sign_up.

	The response stays byte-identical (anti-enumeration, PLAN §2 D9); what
	changes is that the customer is now TOLD where to go: a plain "you already
	have an account" mail pointing at /login and Forgot Password, carrying NO
	token. The queue is the assertion surface (mute_emails, S8 as-built 2).
	"""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		_turnstile_off()

	def tearDown(self):
		frappe.set_user("Administrator")
		# The brake's buckets live in Redis and outlive the DB rollback — the
		# same dodge the IP-limit test documents. Unique addresses keep these
		# rows safe today; clearing makes the class safe for a fixed address too.
		throttle.clear_all_buckets()
		super().tearDown()

	def _existing(self, prefix: str) -> str:
		email = _make_website_user(_unique_email(prefix))
		self.assertEqual(_email_queue_count(email), 0, "fixture user must start mail-less")
		return email

	def test_existing_address_queues_exactly_one_account_exists_mail(self):
		email = self._existing("exists")
		# As the caller really is in production: a GUEST on /signup. A queue
		# insert that only works for Administrator would be swallowed by
		# notify_account_exists and reproduce the exact silent no-mail this fixes.
		frappe.set_user("Guest")
		resp = signup_api.sign_up(email, "Exists Again")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)
		rows = _queued_for(email)
		self.assertEqual(len(rows), 1, rows)
		self.assertIn("already have an account", _plain(rows[0].message))
		# No booking behind this mail — the no-reference queue sibling, not _queue.
		self.assertFalse(rows[0].reference_doctype)

	def test_second_hit_within_the_hour_queues_no_second_mail(self):
		"""The mail-amplification brake (ruled 2026-08-27): one account-exists
		mail per address per hour, whatever the caller's IP. The response stays
		the same on both hits — the brake is invisible from outside."""
		email = self._existing("repeat")
		first = signup_api.sign_up(email, "Repeat One")
		second = signup_api.sign_up(email, "Repeat Two")
		self.assertEqual(first, second)
		self.assertEqual(_email_queue_count(email), 1)

	def test_brake_fails_open_when_the_cache_is_down(self):
		"""A Redis fault must not cancel the mail — that would be the exact
		silent no-mail B22 exists to remove, now behind a misleading log line."""
		email = self._existing("cachedown")
		# Fault the brake's OWN seam, not frappe.cache globally — the signup
		# path reads the cache itself (settings, outgoing account) and a global
		# fault escapes from there, which is not what this row tests.
		with patch.object(notifications, "rate_key", side_effect=RuntimeError("brake down")):
			resp = signup_api.sign_up(email, "Cache Down")
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)
		self.assertEqual(_email_queue_count(email), 1)

	def test_failed_attempt_does_not_arm_the_brake(self):
		"""A queue failure must not suppress the retry that would have worked —
		the bucket is stamped only after a successful queue. MUTATION GUARD, not
		feature coverage: this row is green with no brake at all; it only goes
		red if someone arms the bucket BEFORE the queue. The row above is the
		one that fails without the brake."""
		email = self._existing("retry")
		with patch.object(
			notifications, "_queue_to_address", side_effect=RuntimeError("boom")
		):
			signup_api.sign_up(email, "Retry Boom")
		self.assertEqual(_email_queue_count(email), 0)
		signup_api.sign_up(email, "Retry Works")
		self.assertEqual(_email_queue_count(email), 1)

	def test_disabled_existing_address_gets_the_same_mail(self):
		"""Enabled and disabled existing accounts must be indistinguishable
		from the outside — same response AND the same single mail."""
		email = self._existing("disabled.exists")
		frappe.db.set_value("User", {"email": email}, "enabled", 0)
		signup_api.sign_up(email, "Disabled Again")
		rows = _queued_for(email)
		self.assertEqual(len(rows), 1, rows)
		self.assertIn("already have an account", _plain(rows[0].message))

	def test_fresh_address_gets_the_welcome_mail_not_the_account_exists_mail(self):
		email = _unique_email("fresh.mail")
		signup_api.sign_up(email, "Fresh Mail")
		rows = _queued_for(email)
		self.assertGreaterEqual(len(rows), 1, "welcome mail not queued")
		self.assertNotIn(
			"already have an account", " ".join(_plain(r.message) for r in rows)
		)

	def test_account_exists_mail_points_at_login_and_forgot_password_with_no_token(self):
		email = self._existing("notoken")
		user_name = frappe.db.get_value("User", {"email": email})
		key_before = frappe.db.get_value(
			"User", user_name, ["reset_password_key", "last_reset_password_key_generated_on"]
		)
		signup_api.sign_up(email, "No Token")
		body = _plain(_queued_for(email)[0].message)
		self.assertIn(get_url("/login"), body)
		self.assertIn(get_url("/login#forgot"), body)
		# Option (a) mints NOTHING: no reset link, no key in the body, and a
		# legitimate in-flight reset key on the User is left untouched.
		self.assertNotIn("/update-password", body)
		self.assertNotIn("key=", body)
		self.assertEqual(
			frappe.db.get_value(
				"User", user_name, ["reset_password_key", "last_reset_password_key_generated_on"]
			),
			key_before,
		)

	def test_account_exists_mail_renders_the_shipped_template_and_only_queues(self):
		"""Patch `frappe.sendmail` (not the queue helper) so the shipped Jinja
		actually renders — the B5 lesson: a test that patches the helper never
		exercises the template. And the mail must be QUEUED (now=False): a
		synchronous send would add latency to the existing-address branch
		alone — a timing oracle the byte-identical response exists to deny."""
		email = self._existing("rendered")
		with patch.object(frappe, "sendmail") as sendmail:
			resp = signup_api.sign_up(email, "Rendered Case")
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)
		sendmail.assert_called_once()
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], [email])
		self.assertFalse(kwargs.get("now"), "B22 mail must be queued, never sent now")
		self.assertNotIn("{{", kwargs["message"], "template placeholders unrendered")
		self.assertIn(get_url("/login#forgot"), kwargs["message"])
		self.assertIn("already have an account", kwargs["subject"] + kwargs["message"])

	def test_mail_failure_never_changes_the_response(self):
		"""The S9 failure model, applied here: a broken template must not turn
		the anti-enumeration branch into an error the fresh branch never
		raises — the swallow lives INSIDE notify (B5), and the failure is logged."""
		email = self._existing("boom")
		with patch.object(
			notifications, "_queue_to_address", side_effect=RuntimeError("boom")
		):
			resp = signup_api.sign_up(email, "Boom Case")
		self.assertEqual(resp, signup_api.GENERIC_SUCCESS)
		self.assertEqual(_email_queue_count(email), 0)
		# frappe.log_error(title=...) lands in Error Log's `method` column, and
		# _log puts the address in it — pin THIS test's row, not any earlier one.
		self.assertTrue(
			frappe.db.exists(
				"Error Log",
				{"method": ["like", f"CBT account-exists email failed ({email})%"]},
			),
			"mail failure was swallowed without an Error Log row",
		)


class TestPlatformSettingsTurnstileValidation(FrappeTestCase):
	def _fresh_off(self):
		doc = frappe.get_single("CBT Platform Settings")
		doc.enable_turnstile = 0
		doc.flags.skip_turnstile_validation = True
		doc.save(ignore_permissions=True)

	def _enabling_doc(self):
		doc = frappe.get_single("CBT Platform Settings")
		doc.enable_turnstile = 1
		doc.turnstile_site_key = "site-key"
		doc.turnstile_secret_key = "candidate-secret"
		return doc

	def tearDown(self):
		self._fresh_off()
		super().tearDown()

	def test_bad_secret_rejected_at_save(self):
		self._fresh_off()
		doc = self._enabling_doc()
		with patch.object(
			turnstile, "_siteverify", return_value={"success": False, "error-codes": ["invalid-input-secret"]}
		):
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)

	def test_unreachable_siteverify_rejected_at_save(self):
		self._fresh_off()
		doc = self._enabling_doc()
		with patch.object(turnstile, "_siteverify", side_effect=ConnectionError("down")):
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)

	def test_valid_secret_saves(self):
		"""A dummy probe against a VALID secret returns success=False with
		invalid-input-response — that must pass the save."""
		self._fresh_off()
		doc = self._enabling_doc()
		with patch.object(
			turnstile, "_siteverify", return_value={"success": False, "error-codes": ["invalid-input-response"]}
		):
			doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_single_value("CBT Platform Settings", "enable_turnstile"), 1
		)

	def test_site_key_required(self):
		self._fresh_off()
		doc = self._enabling_doc()
		doc.turnstile_site_key = ""
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)


class TestCustomerProfile(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.user_a = _make_website_user(_unique_email("profile.a"), "Ana")
		self.user_b = _make_website_user(_unique_email("profile.b"), "Ben")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_ensure_customer_profile_idempotent(self):
		first = ensure_customer_profile(self.user_a)
		second = ensure_customer_profile(self.user_a)
		self.assertEqual(first.name, second.name)
		self.assertEqual(
			frappe.db.count("CBT Customer Profile", {"user": self.user_a}), 1
		)

	def test_update_writes_own_row_only(self):
		"""The guard is structural: no target param exists, so A's write can
		only ever land on A's row."""
		ensure_customer_profile(self.user_b)
		frappe.set_user(self.user_a)
		result = update_my_profile(
			phone="0917-111-2222",
			home_latitude=14.6091,
			home_longitude=121.0223,
			address_text="Ortigas-ish",
		)
		self.assertEqual(result["phone"], "0917-111-2222")

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(
			frappe.db.get_value("CBT Customer Profile", {"user": self.user_a}, "phone"),
			"0917-111-2222",
		)
		self.assertFalse(
			frappe.db.get_value("CBT Customer Profile", {"user": self.user_b}, "phone")
		)

	def test_latlng_validation(self):
		frappe.set_user(self.user_a)
		with self.assertRaises(frappe.ValidationError):
			update_my_profile(home_latitude=91, home_longitude=121.0)
		with self.assertRaises(frappe.ValidationError):
			update_my_profile(home_latitude=14.6, home_longitude=181)
		with self.assertRaises(frappe.ValidationError):
			update_my_profile(home_latitude=14.6)  # half a pin is meaningless

	def test_guest_rejected(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_my_profile()
		with self.assertRaises(frappe.PermissionError):
			update_my_profile(phone="0917-000-0000")

	def test_get_branches_falls_back_to_session_pin(self):
		"""Section-8 extension: no explicit origin -> the session user's saved
		pin orders the marketplace listing (E2E 04 asserts the seeded order;
		this asserts the mechanism without seed coupling)."""
		from court_booking_tech.geo import get_branches

		ensure_customer_profile(self.user_a)
		frappe.set_user(self.user_a)
		update_my_profile(home_latitude=14.5507, home_longitude=121.0494)  # on BGC
		listing = get_branches()
		with_distance = [row for row in listing if row["distance_km"] is not None]
		self.assertTrue(with_distance, "session-pin fallback produced no distances")
		self.assertEqual(with_distance[0]["branch_slug"], "bgc")


class TestWelcomeEmailCarryIn(FrappeTestCase):
	def test_staff_creation_queues_welcome_mail(self):
		"""Section-2 as-built #4: with section-8's email plumbing, staff
		Users are created with send_welcome_email=1 and the invite lands in
		the Email Queue (still no SMTP — queue rows are the surface)."""
		from court_booking_tech.api.company_users import create_company_user

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		email = _unique_email("staff.welcome")
		create_company_user(
			email=email,
			full_name="Wella WelcomeStaff",
			company_role="Company Staff",
			company="ayala-courts",
		)
		self.assertGreaterEqual(_email_queue_count(email), 1, "invite mail not queued")
		# And the default-role hook must NOT have treated the bare staff
		# insert as a customer (cbt_skip_customer_role flag).
		roles = {r.role for r in frappe.get_doc("User", {"email": email}).roles}
		self.assertNotIn("CBT Customer", roles)
		self.assertIn("CBT Company Staff", roles)


class TestDefaultRoleHook(FrappeTestCase):
	def test_website_user_gets_customer_role(self):
		email = _make_website_user(_unique_email("hookrole"))
		roles = {r.role for r in frappe.get_doc("User", {"email": email}).roles}
		self.assertIn("CBT Customer", roles)

	def test_system_user_untouched(self):
		"""A REAL System User — one with a desk role; frappe flips role-less
		'System Users' to Website User in validate (set_system_user), which
		is exactly why the staff paths carry cbt_skip_customer_role."""
		email = _unique_email("sysuser")
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Sys",
				"last_name": "SignupCase",
				"enabled": 1,
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		user = frappe.get_doc("User", {"email": email})
		self.assertEqual(user.user_type, "System User")
		self.assertNotIn("CBT Customer", {r.role for r in user.roles})
