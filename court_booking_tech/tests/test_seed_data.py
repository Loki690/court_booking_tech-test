"""
Court Booking Tech — Seed Data Smoke Test

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_seed_data

Catches broken link references, missing required fields, stale Select options
and import errors in seconds instead of a 10+ minute full reset.
"""
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech import legal
from court_booking_tech.seeds.seed_test_data import (
	COMPANIES,
	COMPANY_USERS,
	COMPANY_USER_PASSWORD,
	CUSTOMER_EMAIL,
	GCASH_QR_FILE,
	PAYMENT_CHANNEL_DETAILS,
	E2E_COMPANY,
	E2E_RATE_COURT,
	E2E_RATE_COURT_BASE,
	E2E_RATE_RULES,
	EVASION_COURT,
	EVASION_DATE,
	OPEN_PLAY_DATE,
	OPEN_PLAY_TITLE,
	OPEN_PLAY_WALKIN_NAME,
	OPEN_PLAY_WALKIN_PHONE,
	PLATFORM_ADMIN_EMAIL,
	WALKIN_COURT,
	WALKIN_DATE,
	WALKIN_NAME,
	WALKIN_PHONE,
	_seed_content_hash,
	seed_all,
)
from court_booking_tech.timeutil import _as_timedelta

CBT_ROLES = [
	"CBT Platform Admin",
	"CBT Company Admin",
	"CBT Company Staff",
	"CBT Customer",
]

# fieldname -> default, per the authoritative table in docs section-1
PLATFORM_SETTINGS_DEFAULTS = {
	"default_slot_duration_minutes": "60",
	"default_advance_booking_days": "30",
	"default_reservation_expiry_minutes": "30",
	"default_verification_hold_hours": "4",
	"default_vat_percent": "12",
	"default_billing_mode": "Subscription",
	"default_subscription_fee": "0",
	"default_commission_percent": "0",
	"proof_max_mb": "10",
	"media_max_mb": "10",
	"rejection_reupload_minutes": "120",
	"max_pending_proofs_per_booking": "3",
	# Backlog B35: this counts SLOTS now, not bookings — 5 -> 8 with the unit
	# change. api/proofs.DEFAULT_MAX_HOLDS_PER_CUSTOMER must match, or a site
	# whose Singles row predates the field silently runs the old number.
	"max_active_proof_holds_per_customer": "8",
	"signup_ip_limit_per_hour": "25",
	# Backlog B35: bounds one cart checkout — the insert transaction, and how
	# many slots one customer can hold unpaid at once (a cart shares one
	# pay-by clock, so it is one hold, not N).
	"max_cart_items": "8",
}


class TestSeedData(FrappeTestCase):

	def test_seed_all_no_errors(self):
		"""seed_all() runs without exceptions (and is idempotent — run twice)."""
		seed_all()
		seed_all()

	def test_every_company_a_user_binds_to_is_seeded_before_the_users(self):
		"""A SOURCE-ORDER pin, not a behaviour test. The first full reset for this
		app (2026-09-15) died on `Could not find Company: e2e-fast`: B43 bound
		seats to a company seeded 15 steps after `_seed_company_users`, and every
		snapshot reset hid it (the restored DB already held the fixture). This
		reads seed_all's text; a seeder called via a helper slips past it. The
		proof of fresh-site order is a full_reset, never this test."""
		import inspect

		source = inspect.getsource(seed_all)
		creators = {spec["slug"]: "_seed_companies()" for spec in COMPANIES}
		creators[E2E_COMPANY["slug"]] = "_seed_e2e_fast_company()"
		users_at = source.index("_seed_company_users()")
		for _email, _first, _last, company, _role in COMPANY_USERS:
			self.assertIn(company, creators, f"{company}: no seeder creates it")
			self.assertLess(
				source.index(creators[company]),
				users_at,
				f"{creators[company]} must run before _seed_company_users() for {company}",
			)

	def test_platform_admin_user_seeded(self):
		"""The S1 platform-admin user exists, is a DESK seat, carries the CBT
		Platform Admin role and NOTHING that would let it pass for the wrong
		reason (System Manager), and can log in with the shared seeded password
		(section-27: the E2E lane logs it in through the real form)."""
		from frappe.utils.password import check_password

		seed_all()
		self.assertTrue(frappe.db.exists("User", PLATFORM_ADMIN_EMAIL))
		roles = frappe.get_roles(PLATFORM_ADMIN_EMAIL)
		self.assertIn("CBT Platform Admin", roles)
		self.assertNotIn("System Manager", roles)
		self.assertEqual(
			frappe.db.get_value("User", PLATFORM_ADMIN_EMAIL, "user_type"), "System User"
		)
		self.assertEqual(frappe.db.get_value("Role", "CBT Platform Admin", "desk_access"), 1)
		# check_password RAISES on a mismatch; the equality is the identity check.
		self.assertEqual(check_password(PLATFORM_ADMIN_EMAIL, COMPANY_USER_PASSWORD), PLATFORM_ADMIN_EMAIL)
		# The same literal lives in e2e/helpers/auth.py DEFAULT_PASSWORD with
		# nothing else tying the two runtimes together — pin it here.
		self.assertEqual(COMPANY_USER_PASSWORD, "P@ssw0rd@123")

	def test_cbt_roles_exist(self):
		"""All four fixture roles exist; CBT Customer is a website (non-desk) role."""
		for role in CBT_ROLES:
			self.assertTrue(frappe.db.exists("Role", role), f"missing role: {role}")
		self.assertEqual(
			frappe.db.get_value("Role", "CBT Customer", "desk_access"), 0
		)
		for role in CBT_ROLES[:3]:
			self.assertEqual(
				frappe.db.get_value("Role", role, "desk_access"), 1, role
			)

	def test_google_login_key_follows_the_site_config(self):
		"""Backlog B51: no `cbt_google_login` pair in the site config -> no ENABLED
		key (the login page simply omits the button); a pair -> an enabled key.
		This dev bench carries no pair by default, so the first branch is the
		one that runs here."""
		from court_booking_tech.sso import CONF_KEY, KEY_NAME

		seed_all()
		pair = frappe.conf.get(CONF_KEY) or {}
		enabled = frappe.utils.cint(
			frappe.db.get_value("Social Login Key", KEY_NAME, "enable_social_login")
		)
		if pair.get("client_id") and pair.get("client_secret"):
			self.assertEqual(enabled, 1)
		else:
			self.assertEqual(enabled, 0)

	def test_platform_settings_schema(self):
		"""CBT Platform Settings carries the authoritative fields + defaults,
		is a Single, and is readable/writable ONLY by System Manager +
		CBT Platform Admin."""
		meta = frappe.get_meta("CBT Platform Settings")
		self.assertTrue(meta.issingle)

		for fieldname, default in PLATFORM_SETTINGS_DEFAULTS.items():
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, f"missing field: {fieldname}")
			self.assertEqual(
				str(field.default), default, f"wrong default on {fieldname}"
			)

		for fieldname, fieldtype in [
			("enable_turnstile", "Check"),
			("turnstile_site_key", "Data"),
			("turnstile_secret_key", "Password"),
			# Backlog B21(a) — the issuer printed on CBT Platform Statement.
			# No defaults on purpose: a blank falls back to the app name.
			("platform_legal_name", "Data"),
			("platform_tin", "Data"),
			("platform_address", "Small Text"),
		]:
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, f"missing field: {fieldname}")
			self.assertEqual(field.fieldtype, fieldtype, fieldname)

		perm_roles = {p.role for p in meta.permissions}
		self.assertEqual(perm_roles, {"System Manager", "CBT Platform Admin"})

		# The single loads without error (what the desk form does server-side)
		frappe.get_doc("CBT Platform Settings")

	def test_seeded_invoice_states(self):
		"""Section-6 seeds contract: every seeded booking carries an invoice,
		and the cast covers all three states across VAT (AYALA) and NON-VAT
		(QCSM). Asserted HERE because this class never runs a sweep — sweep
		tests elsewhere legitimately expire the seeded Reserved holds."""
		seed_all()

		def invoice_of(court, start_time):
			name = frappe.db.get_value(
				"CBT Court Booking",
				{"court": court, "booking_date": "2027-01-15", "start_time": start_time},
				"billing_doc",
			)
			self.assertTrue(name, f"no invoice on {court} {start_time}")
			return frappe.get_doc("CBT Booking Invoice", name)

		orphans = frappe.get_all(
			"CBT Court Booking",
			filters=[["billing_doc", "is", "not set"]],
			pluck="name",
		)
		self.assertFalse(orphans, f"bookings without invoices: {orphans}")

		paid = invoice_of("AYALA-bgc-court-2", "10:00:00")
		self.assertEqual(paid.status, "Paid & Verified")
		self.assertEqual(paid.or_number, "OR-0001")
		self.assertEqual(paid.vat_mode, "VAT")

		self.assertEqual(invoice_of("AYALA-bgc-court-1", "10:00:00").status, "Unpaid")
		self.assertEqual(
			invoice_of("AYALA-bgc-court-1", "14:00:00").status, "Cancelled"
		)

		qcsm = invoice_of("QCSM-timog-court-2", "10:00:00")
		self.assertEqual(qcsm.status, "Paid & Verified")
		self.assertEqual(qcsm.vat_mode, "NON-VAT")
		# S2 as-built 9: NON-VAT snapshots 0 via the accessor (raw field = 12).
		self.assertFalse(qcsm.vat_percent)
		self.assertFalse(qcsm.vatable_amount)
		self.assertFalse(qcsm.vat_amount)

	def test_seeded_open_play_session(self):
		"""Section-10 seeds contract: one AYALA Saturday session, already Open,
		its three courts blocked, eight players in a realistic payment mix, and
		a billing document per participant.

		Section-20 adds a NINTH: a cash walk-in with no account at all.
		"""
		seed_all()

		name = frappe.db.get_value(
			"CBT Open Play Session",
			{
				"branch": "AYALA-bgc",
				"session_date": OPEN_PLAY_DATE,
				"title": OPEN_PLAY_TITLE,
			},
		)
		self.assertTrue(name, "open play session not seeded")
		doc = frappe.get_doc("CBT Open Play Session", name)
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.company, "ayala-courts")
		self.assertEqual(len(doc.courts), 3)
		self.assertEqual(len(doc.participants), 9)
		# current_participants counts non-Left QUEUE rows, so the walk-in moves
		# this too — the walk-in gets a queue row like everyone else.
		self.assertEqual(doc.current_participants, 9)

		# 4 cash + 1 free + 1 confirmed transfer + 1 cash walk-in = 900
		# collected; two still owe.
		self.assertEqual(flt(doc.total_revenue), 900.0)
		statuses = [row.payment_status for row in doc.participants]
		self.assertEqual(statuses.count("Paid"), 7)
		self.assertEqual(statuses.count("Unpaid"), 2)

		for row in doc.participants:
			self.assertTrue(row.billing_doc, f"{row.customer} has no billing document")
			self.assertEqual(
				frappe.db.get_value(
					"CBT Booking Invoice", row.billing_doc, "participant_ref"
				),
				row.name,
			)

		blocks = frappe.get_all(
			"CBT Slot Block", filters={"open_play_session": name}, pluck="court"
		)
		self.assertEqual(len(blocks), 3)

		# One proof awaiting review — booking-less, so both clocks are
		# structurally out of reach (PLAN §8q).
		proofs = frappe.get_all(
			"CBT Payment Proof",
			filters={"open_play_session": name},
			fields=["status", "booking", "participant_ref"],
		)
		self.assertEqual(len(proofs), 1)
		self.assertEqual(proofs[0].status, "Pending")
		self.assertFalse(proofs[0].booking)
		self.assertTrue(proofs[0].participant_ref)

	def test_seeded_open_play_walkin(self):
		"""Section-20 seeds contract (Backlog B2): the ninth participant has NO
		account, and their own name is what the statement carries."""
		seed_all()

		name = frappe.db.get_value(
			"CBT Open Play Session",
			{
				"branch": "AYALA-bgc",
				"session_date": OPEN_PLAY_DATE,
				"title": OPEN_PLAY_TITLE,
			},
		)
		doc = frappe.get_doc("CBT Open Play Session", name)

		walkins = [row for row in doc.participants if not row.customer]
		self.assertEqual(len(walkins), 1, "expected exactly one seeded walk-in")
		walkin = walkins[0]
		self.assertIsNone(walkin.customer)
		self.assertEqual(walkin.customer_name, OPEN_PLAY_WALKIN_NAME)
		self.assertEqual(walkin.customer_phone, OPEN_PLAY_WALKIN_PHONE)
		self.assertEqual(walkin.payment_status, "Paid")
		self.assertEqual(flt(walkin.discount_percent), 0.0)

		invoice = frappe.get_doc("CBT Booking Invoice", walkin.billing_doc)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.customer_name, OPEN_PLAY_WALKIN_NAME)
		self.assertFalse(invoice.customer)

		# They are in the rotation like anyone else, keyed on their own queue
		# row rather than on an account they do not have.
		queue_row = next(
			row
			for row in doc.queue
			if not row.customer and row.customer_name == OPEN_PLAY_WALKIN_NAME
		)
		self.assertEqual(queue_row.status, "Waiting")
		self.assertEqual(queue_row.participant_ref, walkin.name)

	def test_seeded_platform_billing_config(self):
		"""Section-11 seeds contract: both billing modes are represented, so
		the platform revenue report has a percentage tenant AND a subscription
		tenant to compute against."""
		seed_all()

		ayala = frappe.db.get_value(
			"CBT Company", "ayala-courts",
			["billing_mode", "commission_percent"], as_dict=True,
		)
		self.assertEqual(ayala.billing_mode, "Percentage")
		self.assertEqual(flt(ayala.commission_percent), 10.0)

		qcsm = frappe.db.get_value(
			"CBT Company", "qc-smash",
			["billing_mode", "subscription_fee"], as_dict=True,
		)
		self.assertEqual(qcsm.billing_mode, "Subscription")
		self.assertEqual(flt(qcsm.subscription_fee), 2999.0)

	def test_seeded_companies_carry_payment_instructions(self):
		"""Section-12 seeds contract (UAT finding): the portal checkout's
		"How to pay" box renders only when the company has payment
		instructions — with none seeded, that surface had never rendered
		anywhere, and a seeded tenant contradicted its own go-live checklist."""
		seed_all()

		for slug in ("ayala-courts", "qc-smash", "e2e-fast"):
			value = frappe.db.get_value(
				"CBT Company", slug, "payment_instructions"
			)
			self.assertTrue(
				(value or "").strip(),
				f"{slug}: seeded company has no payment_instructions",
			)

	def test_seeded_memberships(self):
		"""Section-11 seeds contract: an active VIP, an active Standard at a
		DIFFERENT company, and an expired row — the three shapes the discount
		engine must tell apart. Deliberately NOT on Pia/Noel, whose
		undiscounted totals are pinned by file 05 and test_portal."""
		seed_all()

		mia_ayala = frappe.db.get_value(
			"CBT Membership",
			{"company": "ayala-courts", "customer": "cust.mia@example.com"},
			["tier", "discount_percent", "end_date"],
			as_dict=True,
		)
		self.assertEqual(mia_ayala.tier, "VIP")
		self.assertEqual(flt(mia_ayala.discount_percent), 20.0)

		milo = frappe.db.get_value(
			"CBT Membership",
			{"company": "qc-smash", "customer": "cust.milo@example.com"},
			["tier", "discount_percent"],
			as_dict=True,
		)
		self.assertEqual(milo.tier, "Standard")
		self.assertEqual(flt(milo.discount_percent), 10.0)

		# The expired row: same customer, second company, window closed in 2024.
		self.assertTrue(
			frappe.db.exists(
				"CBT Membership",
				{
					"company": "qc-smash",
					"customer": "cust.mia@example.com",
					"end_date": "2024-06-30",
				},
			)
		)

		# The membership cast must never carry a portal-fixture customer.
		for pinned in ("cust.pia@example.com", "cust.noel@example.com"):
			self.assertFalse(
				frappe.db.exists("CBT Membership", {"customer": pinned}),
				f"{pinned}'s totals are pinned by the portal suite — no membership",
			)

	def test_seeded_expired_with_proof_booking(self):
		"""Section-11 seeds contract: the commission-evasion demo fixture — an
		Expired booking that carries a proof, on a date clear of every other
		fixture (2027-01-16 collides with the seeded whole-branch holiday
		block and would abort the seed run)."""
		seed_all()

		name = frappe.db.get_value(
			"CBT Court Booking",
			{
				"court": EVASION_COURT,
				"booking_date": EVASION_DATE,
				"start_time": "10:00:00",
			},
			"name",
		)
		self.assertTrue(name, "evasion fixture not seeded")
		booking = frappe.get_doc("CBT Court Booking", name)
		self.assertEqual(booking.booking_status, "Expired")
		self.assertEqual(booking.company, "qc-smash")
		self.assertTrue(
			frappe.db.exists("CBT Payment Proof", {"booking": name}),
			"the evasion signal requires a proof on the expired booking",
		)
		# Expired => the billing document is Cancelled, number retained.
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", booking.billing_doc, "status"),
			"Cancelled",
		)

	def test_seeded_membership_customers_can_log_in(self):
		"""E2E logs in as Mia — the password must be set every run, like the
		other portal fixtures."""
		seed_all()
		for email in (
			"cust.mia@example.com",
			"cust.milo@example.com",
			"cust.evan@example.com",
		):
			self.assertTrue(frappe.db.exists("User", email), email)
			self.assertIn(
				"CBT Customer",
				{r.role for r in frappe.get_doc("User", email).roles},
				email,
			)

	def test_walkin_booking_seeded_without_an_account(self):
		"""Section-13 seed contract: the walk-in fixture is the ONLY seeded
		booking with no customer, and its billing document names the person who
		actually stood at the desk."""
		seed_all()
		name = frappe.db.get_value(
			"CBT Court Booking",
			{
				"court": WALKIN_COURT,
				"booking_date": WALKIN_DATE,
				"start_time": "10:00:00",
			},
			"name",
		)
		self.assertTrue(name, "section-13 walk-in booking not seeded")

		doc = frappe.get_doc("CBT Court Booking", name)
		# NULL, not '' — the immutability guard compares raw values.
		self.assertIsNone(
			frappe.db.get_value("CBT Court Booking", name, "customer")
		)
		self.assertEqual(doc.customer_name, WALKIN_NAME)
		self.assertEqual(doc.customer_phone, WALKIN_PHONE)
		self.assertEqual(doc.booking_status, "Confirmed")  # Cash confirms on insert

		self.assertTrue(doc.billing_doc, "walk-in booking has no billing document")
		invoice = frappe.get_doc("CBT Booking Invoice", doc.billing_doc)
		self.assertEqual(invoice.customer_name, WALKIN_NAME)
		self.assertFalse(invoice.customer)
		self.assertEqual(invoice.status, "Paid & Verified")

	def test_rate_rule_court_seeded_with_exactly_its_two_rules(self):
		"""Section-14 seed contract. E2E file 12 asserts ₱350 at 18:00 and the
		base ₱200 at 17:00 on this court, so the exact windows are the fixture
		— not an illustration."""
		seed_all()
		self.assertTrue(
			frappe.db.exists("CBT Court", E2E_RATE_COURT),
			f"section-14 rate-rule court {E2E_RATE_COURT} not seeded",
		)
		court = frappe.get_doc("CBT Court", E2E_RATE_COURT)
		self.assertEqual(flt(court.hourly_rate), flt(E2E_RATE_COURT_BASE))
		self.assertEqual(len(court.rate_rules), len(E2E_RATE_RULES))
		for row, expected in zip(court.rate_rules, E2E_RATE_RULES):
			self.assertEqual(row.day_scope, expected["day_scope"])
			# Compare as timedeltas, never as str(): a Time field comes back as
			# a timedelta whose str() drops the leading zero ("5:00:00"), so a
			# string compare fails on every window before 10:00 while the stored
			# value is perfectly correct.
			self.assertEqual(
				_as_timedelta(row.start_time), _as_timedelta(expected["start_time"])
			)
			self.assertEqual(
				_as_timedelta(row.end_time), _as_timedelta(expected["end_time"])
			)
			self.assertEqual(flt(row.hourly_rate), flt(expected["hourly_rate"]))
			self.assertEqual(row.label, expected["label"])

	def test_rate_rules_live_on_exactly_one_seeded_court(self):
		"""The load-bearing half of the fixture. Pia's ₱800 at BGC and the §4
		2027-01-15 cast are asserted to the peso across half a dozen files, so
		a rule reaching any OTHER seeded court would break suites that have
		nothing to do with pricing (S10 note 13, S11 as-built 21)."""
		seed_all()
		ruled = {
			row.parent
			for row in frappe.get_all(
				"CBT Court Rate Rule",
				filters={"parenttype": "CBT Court"},
				fields=["parent"],
			)
		}
		self.assertEqual(ruled, {E2E_RATE_COURT})

	def test_rate_rule_seed_heals_a_drifted_window_and_is_idempotent(self):
		"""2026-09-03: the seed re-applies rules when their CONTENT differs, not
		only their count — a changed window must heal an existing bench — and a
		matching set must be a no-op, or the seed would rewrite the court on
		every run and clobber a deliberate local edit."""
		from court_booking_tech.seeds.seed_test_data import _seed_rate_rule_court

		seed_all()
		court = frappe.get_doc("CBT Court", E2E_RATE_COURT)
		night = court.rate_rules[0]
		# Drift: knock the night rule's end back to its pre-2026-09-03 value.
		frappe.db.set_value("CBT Court Rate Rule", night.name, "end_time", "23:00:00")
		frappe.clear_document_cache("CBT Court", E2E_RATE_COURT)
		_seed_rate_rule_court()
		healed = frappe.get_doc("CBT Court", E2E_RATE_COURT)
		self.assertEqual(
			_as_timedelta(healed.rate_rules[0].end_time),
			_as_timedelta(E2E_RATE_RULES[0]["end_time"]),
		)
		# Idempotent: a second run with nothing to heal writes nothing.
		before = frappe.db.get_value("CBT Court", E2E_RATE_COURT, "modified")
		_seed_rate_rule_court()
		after = frappe.db.get_value("CBT Court", E2E_RATE_COURT, "modified")
		self.assertEqual(before, after)

	def test_seeded_gcash_channels_carry_one_public_qr_and_a_note(self):
		"""2026-09-04: the checkout's QR tile renders from seed data, so the
		mobile lane can photograph it. Exactly ONE seed-owned File (a count, not
		a shared-url check — frappe's content-hash dedup would make that pass
		even if the seed created a File per run), referenced by every seeded
		GCash row, its blob on disk (snapshot_reset never restores public/files),
		and a channel-level note on each.

		Identified by CONTENT HASH, not file_name: a blob that survived a
		snapshot_reset on disk makes frappe suffix the next File's name
		(section-22 as-built 21), and a name lookup then reads 0 forever."""
		seed_all()
		files = frappe.get_all(
			"File",
			filters={"content_hash": _seed_content_hash(GCASH_QR_FILE), "is_private": 0},
			fields=["name", "file_url"],
		)
		self.assertEqual(len(files), 1, files)
		self.assertTrue(files[0].file_url.startswith("/files/gcash_qr_sample"), files)
		on_disk = frappe.get_doc("File", files[0].name).get_full_path()
		for company, label, _values in PAYMENT_CHANNEL_DETAILS:
			row = frappe.db.get_value(
				"CBT Payment Channel",
				{"company": company, "label": label},
				["name", "qr_image", "instructions"],
				as_dict=True,
			)
			self.assertTrue(row, (company, label))
			self.assertEqual(row.qr_image, files[0].file_url, row)
			self.assertTrue(row.qr_image.startswith("/files/"), row)
			self.assertTrue(os.path.exists(on_disk), (row, on_disk))
			self.assertTrue((row.instructions or "").strip(), row)

	def test_the_seeded_booking_owner_can_log_in(self):
		"""Carla owns every seeded booking yet had no password until 2026-09-04
		(the mobile lane's /my-bookings row got HTTP 401 on both engines). An
		enabled Website User AND the shared seeded password — check_password
		alone would pass on a disabled user."""
		from frappe.utils.password import check_password

		seed_all()
		user = frappe.db.get_value("User", CUSTOMER_EMAIL, ["enabled", "user_type"], as_dict=True)
		self.assertEqual((user.enabled, user.user_type), (1, "Website User"), user)
		self.assertEqual(check_password(CUSTOMER_EMAIL, COMPANY_USER_PASSWORD), CUSTOMER_EMAIL)


class TestLegalPagesAreNeverClobbered(FrappeTestCase):
	"""The live legal pages are written BY HAND in the browser.

	User ruling 2026-09-11: *"if those pages already exist. DO NOT OVERWRITE
	THEM."* They were built as `Web Page` rows precisely so the wording changes
	without a deploy, so `legal/*.html` is a FIRST-INSTALL source, not the master
	of a live page. These tests are the wall around that.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		seed_all()

	def test_an_existing_page_is_kept_not_rewritten(self):
		"""The default path a deploy would ever take: create missing, keep the rest."""
		route = "privacy-policy"
		name = frappe.db.get_value("Web Page", {"route": route})
		self.assertTrue(name, "the seed should have installed it")

		frappe.db.set_value("Web Page", name, "main_section_html", "<p>HAND EDITED</p>")
		result = legal.push_legal_pages()
		self.assertEqual(result[route], "kept", result)
		self.assertEqual(
			frappe.db.get_value("Web Page", name, "main_section_html"), "<p>HAND EDITED</p>"
		)
		# Put the repo copy back so no later test reads the stub.
		legal.push_legal_pages(force=1)

	def test_force_is_refused_on_a_live_site_without_an_explicit_acknowledgement(self):
		"""`force=1` alone must not be enough anywhere that is not a test site.

		A future session reading "force overwrites from the repo" would run it on
		production and destroy hand-written legal copy. `allow_tests` is patched
		off here because that is the only thing standing between the two worlds.
		"""
		with patch.dict(frappe.conf, {"allow_tests": 0}):
			with self.assertRaises(frappe.PermissionError) as caught:
				legal.push_legal_pages(force=1)
			self.assertIn("maintained in the browser", str(caught.exception))

			# …and the deliberate second flag still gets through.
			result = legal.push_legal_pages(force=1, overwrite_live=1)
			self.assertTrue(set(result.values()) <= {"created", "overwritten"}, result)

	def test_no_migrate_hook_installs_them(self):
		"""A deploy must never touch a page the user wrote by hand.

		Measured 2026-09-11: our app registers NOTHING on after_migrate. If that
		ever changes, a production deploy starts rewriting legal copy silently.
		"""
		hooks = frappe.get_hooks("after_migrate") or []
		ours = [h for h in hooks if "court_booking_tech" in h]
		self.assertEqual(ours, [], f"court_booking_tech now runs on migrate: {ours}")
