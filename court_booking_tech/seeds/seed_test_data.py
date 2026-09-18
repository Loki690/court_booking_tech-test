"""
Court Booking Tech — Seed Test Data (Section 1 skeleton)

Portable, idempotent seeder. Auto-discovered by shared/scripts/oneshot_install.py
(convention: <app>.seeds.seed_test_data.seed_all) and re-run by snapshot_reset /
multisite_reset on every restore — it must stay idempotent and fast (<30s).

Usage:
    bench --site <site> execute court_booking_tech.seeds.seed_test_data.seed_all

NOT for production: refuses to run unless the site has developer_mode or
allow_tests set.
"""
import os

import frappe

from court_booking_tech.sso import CONF_KEY as GOOGLE_CONF_KEY
from court_booking_tech.sso import configure_google

PLATFORM_ADMIN_EMAIL = "cbt.admin@example.com"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# Every seeded company gets a 90-day booking window, NOT the 30-day platform
# default. Reason is test infrastructure, not product: the E2E suite books
# through the CUSTOMER path (api.portal.reserve_booking, which arms the
# advance-booking horizon) on a collision-free date ledger running today+30 ..
# today+62 — one date range per file so no two files fight over a slot. A
# 30-day fixture would fail six E2E files for a reason that has nothing to do
# with what they test. Real tenants start at the platform default; the desk
# field is where an operator widens it.
SEED_ADVANCE_BOOKING_DAYS = 90

# Section-2: two tenant companies (docs sections/section-2.md, Seeds delta)
COMPANIES = [
	{
		"slug": "ayala-courts",
		"company_code": "AYALA",
		"company_name": "Ayala Courts",
		"registered_name": "Ayala Courts Sports Corp.",
		# TIN renders on the section-6 billing statement — keep it seeded.
		"tin": "010-203-040-000",
		"vat_registration": "VAT",
		"allow_self_branch_management": 1,
		"office_days": WEEKDAYS,
		"office_open": "09:00:00",
		"office_close": "18:00:00",
		# Renders in the portal checkout "How to pay" box AND satisfies the
		# S11 go-live checklist — a seeded tenant must not contradict its own
		# gate (section-12 UAT finding: the box had never rendered anywhere).
		"payment_instructions": (
			"GCash 0917-000-1111 (Ayala Courts Sports Corp.) / "
			"BDO 0012-3456-7890"
		),
		"advance_booking_days": SEED_ADVANCE_BOOKING_DAYS,
	},
	{
		"slug": "qc-smash",
		"company_code": "QCSM",
		"company_name": "QC Smash",
		"registered_name": "QC Smash Badminton Center Inc.",
		"tin": "050-607-080-000",
		"vat_registration": "NON-VAT",
		"allow_self_branch_management": 0,
		"office_days": WEEKDAYS + ["Saturday"],
		"office_open": "10:00:00",
		"office_close": "17:00:00",
		"payment_instructions": (
			"GCash 0917-000-2222 (QC Smash) — send the screenshot to the desk"
		),
		"advance_booking_days": SEED_ADVANCE_BOOKING_DAYS,
	},
]

# Backlog B29: the seeded GCash rows' customer-facing details — the SAME
# numbers the payment_instructions above print, so the checkout's channel
# card and the free-text note never disagree. (company, label, fields).
PAYMENT_CHANNEL_DETAILS = [
	(
		"ayala-courts",
		"GCash",
		{
			"account_name": "Ayala Courts Sports Corp.",
			"account_number": "0917-000-1111",
			"instructions": "Send the exact amount and keep your GCash reference number — the desk checks it against your proof.",
		},
	),
	(
		"qc-smash",
		"GCash",
		{
			"account_name": "QC Smash",
			"account_number": "0917-000-2222",
			"instructions": "Send the screenshot to the desk.",
		},
	),
]

# 2026-09-04: the seeded GCash rows carry a sample QR so the checkout's QR tile
# and lightbox render from seed data (seeds/files/README.md).
GCASH_QR_FILE = "gcash_qr_sample.png"

# AYALA gets a BANK beside GCash — the split the user asked for ("which are
# cash, which are GCASH, which are BANK TRANSFER") needs all three ways on one
# seeded tenant. QCSM deliberately stays Cash + GCash (two ways).
EXTRA_PAYMENT_CHANNELS = [
	{
		"company": "ayala-courts",
		"label": "BDO",
		"kind": "Transfer",
		"mode_of_payment": "Bank Transfer",
		"account_label": "BDO Current Account",
		"account_name": "Ayala Courts Sports Corp.",
		"account_number": "0012-3456-7890",
		"sort_order": 3,
	},
]

# Section-7: E2E files 02/03 form-login as these users — the password matches
# e2e/helpers/auth.py DEFAULT_PASSWORD. Dev/staging only (seeds are guarded).
COMPANY_USER_PASSWORD = "P@ssw0rd@123"

# Distinctive full names — cross-app employee/user collision rule (root README).
# Backlog B43: e2e-fast carries seats too — section-27.
COMPANY_USERS = [
	("admin.ayala@example.com", "Alona", "AyalaAdmin", "ayala-courts", "Company Admin"),
	("staff.ayala@example.com", "Stella", "AyalaStaff", "ayala-courts", "Company Staff"),
	("admin.qcsm@example.com", "Quintin", "QcsmAdmin", "qc-smash", "Company Admin"),
	("staff.qcsm@example.com", "Samuel", "QcsmStaff", "qc-smash", "Company Staff"),
	("admin.e2ef@example.com", "Emil", "E2efAdmin", "e2e-fast", "Company Admin"),
	("staff.e2ef@example.com", "Elsa", "E2efStaff", "e2e-fast", "Company Staff"),
]

# Section-3: branches with REAL Metro-Manila coordinates (tests assert known
# Haversine distances between them — do not "fix" these numbers).
ALL_WEEK = WEEKDAYS + ["Saturday", "Sunday"]

BRANCHES = [
	{
		"company": "ayala-courts",
		"slug": "bgc",
		"branch_name": "BGC Courts",
		"address_text": "5th Ave cor 26th St, Bonifacio Global City, Taguig",
		"latitude": 14.5507,
		"longitude": 121.0494,
		"phone": "0917-100-0001",
	},
	{
		"company": "ayala-courts",
		"slug": "makati",
		"branch_name": "Makati Arena",
		"address_text": "Ayala Ave cor Paseo de Roxas, Makati",
		"latitude": 14.5547,
		"longitude": 121.0244,
		"phone": "0917-100-0002",
	},
	{
		"company": "qc-smash",
		"slug": "timog",
		"branch_name": "Timog Hub",
		"address_text": "Timog Ave cor Tomas Morato, Quezon City",
		"latitude": 14.6349,
		"longitude": 121.0388,
		"phone": "0917-200-0001",
		# Closed-day test case (section-3): Sundays CLOSED.
		"closed_days": ["Sunday"],
	},
	{
		"company": "qc-smash",
		"slug": "annex",
		"branch_name": "QC Annex",
		"address_text": "Annex Bldg, Kamuning, Quezon City",
		# NO coordinates on purpose — the sort-last (pin-less) test case.
		"phone": "0917-200-0002",
	},
]

# Backlog B49: the continue-on flag is OPT-IN (user ruling). The three BGC courts
# carry it as the demo — AYALA is Percentage-billed, so no fee changes anywhere
# in the suite; a Per Booking tenant's flagged courts are arranged per test.
CONTINUE_ON_COURTS = ("AYALA-bgc-court-1", "AYALA-bgc-court-2", "AYALA-bgc-court-3")

# (branch, court_name, court_type, hourly_rate)
COURTS = [
	("AYALA-bgc", "Court 1", "Pickleball", 400),
	("AYALA-bgc", "Court 2", "Pickleball", 450),
	("AYALA-bgc", "Court 3", "Pickleball", 500),
	("AYALA-makati", "Court A", "Badminton", 300),
	("AYALA-makati", "Court B", "Badminton", 300),
	("QCSM-timog", "Court 1", "Pickleball", 350),
	("QCSM-timog", "Court 2", "Pickleball", 350),
	("QCSM-timog", "Center Court", "Basketball", 600),
	("QCSM-annex", "Main Hall", "Multi-purpose", 250),
]

# BGC floor plan: 2×2 with one empty cell (3 courts + a gap).
#
# LOAD-BEARING, TWICE OVER: E2E file 06's `test_floor_plan_renders` asserts
# `repeat(2`, three court cards and exactly ONE `.cbt-floor-gap` on the DESK
# board for this branch. And its counterpart — `tests/test_board.py`'s
# `test_board_data_branch_without_layout` — asserts `layout["cells"] == []` on
# **AYALA-makati**, which makes that branch a load-bearing NO-layout fixture.
# Never give AYALA-makati a floor plan.
BGC_LAYOUT = {
	"branch": "AYALA-bgc",
	"rows": 2,
	"columns": 2,
	"cells": [
		(1, 1, "AYALA-bgc-court-1"),
		(1, 2, "AYALA-bgc-court-2"),
		(2, 1, "AYALA-bgc-court-3"),
	],
}

# Section-22: the floor plan the CUSTOMER sees, on /book.
#
# COMPANY CHOICE IS LOAD-BEARING and was made by sweeping every E2E file, not
# by preference. A layout changes a branch's DESK board DOM and media changes
# its /book DOM, and `qc-smash` is the ONLY seeded company with neither
# asserted anywhere in the suite: no E2E file loads a QCSM board (file 02's
# single QCSM board reference asserts HTTP 403 — payload-blind, as does
# tests/test_isolation.py) and no E2E file loads /book?c=qc-smash. AYALA-bgc,
# AYALA-makati and E2EF-main are all claimed board-side; ayala-courts and
# e2e-fast are claimed portal-side.
#
# SHAPE: 2×3 with THREE gaps — two courts flanking a walkway, the center court
# below. Deliberately a different shape from BGC's 2×2 so the section-22 sketch
# test cannot pass by accident against the wrong branch.
QCSM_LAYOUT = {
	"branch": "QCSM-timog",
	"rows": 2,
	"columns": 3,
	"cells": [
		(1, 1, "QCSM-timog-court-1"),
		(1, 3, "QCSM-timog-court-2"),
		(2, 2, "QCSM-timog-center-court"),
	],
}

# Section-29: the enticement fixture. The company gets a banner and photos; ONE
# of its two branches gets its own set, so the marketplace card can be seen to
# override — the other branch must fall back to the company's.
MEDIA_COMPANY = "qc-smash"
MEDIA_BRANCH = "QCSM-annex"
MEDIA_BANNER_FILE = "media_banner.jpg"
# (file, group_label, caption, court, sort_order) — only the file is read now.
COMPANY_MEDIA = [
	(
		"media_court_1.jpg",
		"Courts",
		"Court 1 — tournament-grade flooring",
		"QCSM-timog-court-1",
		10,
	),
	(
		"media_court_center.jpg",
		"Courts",
		"Center Court, seats 200",
		"QCSM-timog-center-court",
		20,
	),
	("media_courts_night.jpg", "Courts", "The hall under lights", None, 30),
	("media_lounge.jpg", "Amenities", "Air-conditioned lounge", None, 40),
	("media_showers.jpg", "Amenities", "Showers and lockers", None, 50),
	("media_league_night.jpg", "Ambience", "Friday night league", None, 40),
]

# Section-4: demo customer (distinctive full name — cross-app collision rule).
# Customers are Website Users with the CBT Customer role assigned directly —
# they have NO CBT Company User binding (that sync is for staff only).
CUSTOMER_EMAIL = "cust.carla@example.com"

# Section-8: portal customers with home pins. E2E file 04 asserts Pia's
# nearest-first order (bgc, makati, timog, annex) from her BGC-adjacent pin —
# pins are FORCE-restored every seed run so a moved pin can never leak into
# the next suite. The map-click E2E moves NOEL's pin, never Pia's. Carla
# keeps a profile WITHOUT a pin (the pin-less case).
PORTAL_CUSTOMERS = [
	("cust.pia@example.com", "Pia", "PortalCustomer", 14.5510, 121.0500),
	("cust.noel@example.com", "Noel", "NorthCustomer", 14.6500, 121.0300),
]

# Section-11: membership cast. DELIBERATELY new users rather than Pia/Noel
# (whom the section text named): E2E file 05 and tests/test_portal.py pin Pia's
# UNDISCOUNTED totals at AYALA (₱800 = 2 × ₱400), so making her a VIP would
# quietly break a green suite. Same lesson as S10 note 13 — a seed must not
# disturb an existing fixture.
MEMBER_CUSTOMERS = [
	("cust.mia@example.com", "Mia", "Memberly"),
	("cust.milo@example.com", "Milo", "Membrino"),
	# The commission-evasion fixture's customer (expired booking WITH a proof).
	("cust.evan@example.com", "Evan", "Evader"),
]

# (company, customer, tier, discount, start, end) — end None = lifetime.
MEMBERSHIPS = [
	("ayala-courts", "cust.mia@example.com", "VIP", 20, "2025-01-01", "2030-12-31"),
	("qc-smash", "cust.milo@example.com", "Standard", 10, "2025-01-01", "2030-12-31"),
	# Mia at a SECOND company, and long expired: proves both that memberships
	# are company-scoped and that a lapsed window grants nothing.
	("qc-smash", "cust.mia@example.com", "Standard", 10, "2024-01-01", "2024-06-30"),
]

# Section-11 platform billing config (drives CBT Platform Revenue's amount-due).
BILLING_MODES = {
	"ayala-courts": {"billing_mode": "Percentage", "commission_percent": 10},
	"qc-smash": {"billing_mode": "Subscription", "subscription_fee": 2999},
}

# Section-11 evasion fixture: an Expired booking that HAS a payment proof — the
# commission-evasion fingerprint the platform report surfaces.
# DATE CHOICE MATTERS: 2027-01-16 (the section draft's pick) is BLOCK_DATE_BRANCH,
# where the seeds already close the WHOLE QCSM-timog branch for a holiday
# 06:00–22:00 — inserting there throws "blocked for the whole branch" and the
# entire seed run dies. 2027-04-16 is a Friday (timog is open Mon–Sat) and is
# touched by no seed, test or E2E fixture.
EVASION_DATE = "2027-04-16"
EVASION_COURT = "QCSM-timog-court-2"

# Section-13: the walk-in fixture (Backlog B1) — a cash customer with NO account.
#
# DATE CHOICE (month-per-module discipline, S11 as-built 21). Date STRINGS are
# taken in Jan (main cast + branch closure + open play), Feb (test_booking /
# test_isolation / test_billing), Mar (test_portal / test_verification /
# test_notifications / test_open_play), Apr (evasion fixture), Jun
# (test_membership), Jul (test_bans), Aug (test_reports).
#
# **SEPTEMBER IS RESERVED-EMPTY AND MUST STAY THAT WAY.** A seeded booking there
# is invisible to a date-string grep but still breaks the suite:
# test_reports.test_a_different_month_sees_none_of_these_fixtures asserts that
# EVERY company's confirmed revenue is 0 for `{"month": 9}` — a month claimed as
# an INTEGER, which no `2027-09` search can find. This fixture was drafted on
# 2027-09-17 and failed exactly there. Months claimed by integer: 4, 8, 9.
# => October 2027 is clear on both axes. 2027-10-15 is a Friday, and
# AYALA-makati is open 06:00–22:00 every day, so 10:00 is on the grid.
#
# COURT: makati has Court A / Court B — there is no "court-1" (the section draft
# named one; it does not exist and booking it would kill the whole seed run).
# court-a's §5 proof fixture is on 2027-01-15, a different date, and the overlap
# lock is scoped WHERE court = %s AND booking_date = %s — no interaction.
WALKIN_DATE = "2027-10-15"
WALKIN_COURT = "AYALA-makati-court-a"
WALKIN_NAME = "Walk-in Wanda"
WALKIN_PHONE = "0917-000-1111"

# Section-10: open play players. Distinctive full names — the cross-app
# collision rule (root README) bites any lookup that matches on part of a name.
OPEN_PLAY_CUSTOMERS = [
	("op.ace@example.com", "Ace", "OpenPlayer"),
	("op.bea@example.com", "Bea", "OpenPlayer"),
	("op.caloy@example.com", "Caloy", "OpenPlayer"),
	("op.dina@example.com", "Dina", "OpenPlayer"),
	("op.elmo@example.com", "Elmo", "OpenPlayer"),
	("op.fely@example.com", "Fely", "OpenPlayer"),
	("op.gani@example.com", "Gani", "OpenPlayer"),
	("op.hazel@example.com", "Hazel", "OpenPlayer"),
]

# A fixed far-future SATURDAY, same idempotency reasoning as BOOKING_DATE.
# This session BLOCKS its three BGC courts, so the date must be clear of every
# booking fixture in the suite — not just the other seed dates. 2027-01-23 was
# the first pick and broke test_board's pending-payments fixtures, which book
# AYALA-bgc-court-1 that day.
OPEN_PLAY_DATE = "2027-01-30"
OPEN_PLAY_TITLE = "Saturday Open Play"
OPEN_PLAY_FEE = 150
OPEN_PLAY_COURTS = ("AYALA-bgc-court-1", "AYALA-bgc-court-2", "AYALA-bgc-court-3")

# Section-20 (Backlog B2): the open-play half of the walk-in story — a stranger
# with cash joins the session with no account at all.
#
# NO NEW DATE IS CLAIMED: this rides the session above, on 2027-01-30. That
# matters for the two-way month pre-check (S13 as-built 1b) — January is already
# the seeds' own month by date STRING, and no test claims month 1 as an INTEGER
# (the integer claims are 4, 8 and 9, all year-scoped to 2027). The one real
# consequence is that AYALA now books ₱150 more confirmed revenue in Jan 2027;
# every report fixture lives in August, and the "nothing here" control is
# month 9, so nothing moves today.
#
# NAME: two words on purpose. The board and TV shorten a queue chip to
# "First L." (`short_name`), so the drafted "Walk-in Wanda OP" would render as
# "Walk-in O." on the demo screen a prospect is shown.
OPEN_PLAY_WALKIN_NAME = "Wanda Walkin"
OPEN_PLAY_WALKIN_PHONE = "0917-000-2222"

# Section-4: bookings in every status. Fixed FAR-FUTURE dates (2027-01-15 is a
# Friday) — dates relative to the seed day are NOT idempotent, and a fixed
# future date can never be auto-expired/completed by the sweep between runs.
BOOKING_DATE = "2027-01-15"
BLOCK_DATE_BRANCH = "2027-01-16"  # Saturday — QCSM-timog is open (Sun closed)

# Backlog B43 (see _seed_customer_relationships). (court, start_time, customer)
# on BOOKING_DATE, inserted Free and immediately Cancelled. Courts and times are
# ones no other fixture or E2E row touches.
CUSTOMER_RELATIONSHIPS = [
	("E2EF-main-court-2", "03:00:00", "cust.carla@example.com"),
	("AYALA-makati-court-b", "07:00:00", "cust.pia@example.com"),
]

# Section-4 E2E determinism seed (consumed by E2E file 07 in section-9):
# 1-minute reservation expiry + round-the-clock hours. Branch slug "main" is
# deliberately OUTSIDE test_geo's SEEDED_SLUGS and pin-less, so it cannot
# disturb the geo ordering fixtures.
E2E_COMPANY = {
	"slug": "e2e-fast",
	"company_code": "E2EF",
	"company_name": "E2E Fast Courts",
	"registered_name": "E2E Fast Courts Testing Corp.",
	"vat_registration": "NON-VAT",
	"allow_self_branch_management": 0,
	"office_days": WEEKDAYS,
	"office_open": "00:00:00",
	"office_close": "23:59:00",
	"payment_instructions": "E2EF test rig — pay at the desk",
	"advance_booking_days": SEED_ADVANCE_BOOKING_DAYS,
}
E2E_BRANCH_NAME = "E2EF-main"

# Section-14: the rate-rules fixture court. Rules go on a court NOTHING else
# touches — never on a load-bearing seeded court, because half a dozen green
# files assert seeded totals to the peso (Pia's ₱800 at BGC, the §4 2027-01-15
# cast) and re-pricing any of them would break suites unrelated to pricing.
#
# NAME: "Court 2", NOT "Court 2 (Night Rate)". CBT Court is
# `autoname: format:{branch}-{court_slug}` and the slug is scrubbed from the
# name, so the descriptive version would produce `E2EF-main-court-2-night-rate`
# and every test row referring to `E2EF-main-court-2` would miss. The
# description field carries the human explanation instead.
#
# WINDOWS: the night rule is what E2E file 12 asserts (18:00 → ₱350, 17:00 →
# base ₱200). Its end is 23:59: since 2026-09-02 a 23:59 branch close means
# MIDNIGHT and the E2EF grid's last slot starts 23:00; a rule's end is
# EXCLUSIVE and a slot matches on its START (pricing.py MATCHING), so 23:59
# catches that slot. A rule end is NOT normalized to 24:00 the way a branch
# close is — 23:59 is simply the latest a Time field holds, the same value
# operators enter for "until midnight". The weekend rule is deliberately scoped to
# 05:00–07:00, hours NO test asserts: E2E dates are RELATIVE, so the weekday
# varies per run, and a weekend rule that outranked the night rule would change
# asserted prices on Saturday/Sunday runs. Weekend classification is proven
# backend-side on absolute October 2027 dates instead.
E2E_RATE_COURT_NAME = "Court 2"
E2E_RATE_COURT = f"{E2E_BRANCH_NAME}-court-2"
E2E_RATE_COURT_BASE = 200
E2E_RATE_RULES = [
	{
		"day_scope": "All Days",
		"start_time": "18:00:00",
		"end_time": "23:59:00",
		"hourly_rate": 350,
		"label": "Night rate",
	},
	{
		"day_scope": "Weekends",
		"start_time": "05:00:00",
		"end_time": "07:00:00",
		"hourly_rate": 250,
		"label": "Weekend early-bird",
	},
]

# Section-9 portal: the marketplace is the site's front door, and these are the
# only portal menu rows a court customer should see.
PORTAL_HOME = "find-court"
CBT_PORTAL_ROUTES = {"/find-court", "/my-bookings", "/my-profile"}


def _guard():
	if not (frappe.conf.get("developer_mode") or frappe.conf.get("allow_tests")):
		frappe.throw(
			"court_booking_tech seed_all refused: this site has neither "
			"developer_mode nor allow_tests set. Seeds are for dev/test sites only."
		)


def _seed_platform_admin():
	from frappe.utils.password import update_password

	print("\n=== CBT platform admin user ===")
	if frappe.db.exists("User", PLATFORM_ADMIN_EMAIL):
		user = frappe.get_doc("User", PLATFORM_ADMIN_EMAIL)
		print(f"  Already exists: {PLATFORM_ADMIN_EMAIL}")
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": PLATFORM_ADMIN_EMAIL,
				"first_name": "CBT",
				"last_name": "Platform Admin",
				"enabled": 1,
				# A desk seat from the first insert — before the role below
				# exists on the doc, frappe's set_system_user would otherwise
				# stamp it Website User (flipped back only by the role save).
				"user_type": "System User",
				"send_welcome_email": 0,
			}
		)
		# Not a customer — see customer.on_user_after_insert's flag note.
		user.flags.cbt_skip_customer_role = True
		user.insert(ignore_permissions=True)
		print(f"  Created: {PLATFORM_ADMIN_EMAIL}")

	# ONLY the CBT role — never System Manager. The tests that run as this seat
	# (section-27 onwards) are the proof that the platform needs neither
	# Administrator nor System Manager; a System Manager here would make every
	# one of them pass for the wrong reason.
	if "CBT Platform Admin" not in {r.role for r in user.roles}:
		user.append("roles", {"role": "CBT Platform Admin"})
		user.save(ignore_permissions=True)
		print("  Role added: CBT Platform Admin")
	else:
		print("  Role present: CBT Platform Admin")

	# Known password every run (idempotent), the same one the company users get
	# — the E2E lane logs this seat in through the real login form (section-27).
	update_password(PLATFORM_ADMIN_EMAIL, COMPANY_USER_PASSWORD)


def _seed_companies():
	print("\n=== CBT companies ===")
	for spec in COMPANIES:
		if frappe.db.exists("CBT Company", spec["slug"]):
			# Backfill fields added after an older snapshot was taken —
			# only when empty, never clobbering an operator's later edit.
			# advance_booking_days rides along: 0 IS its "unset" value (it
			# means "use the platform default"), so the same when-empty rule
			# applies and an operator's own number survives.
			backfilled = []
			for field in ("tin", "payment_instructions", "advance_booking_days"):
				if spec.get(field) and not frappe.db.get_value(
					"CBT Company", spec["slug"], field
				):
					frappe.db.set_value(
						"CBT Company", spec["slug"], field, spec[field]
					)
					backfilled.append(field)
			if backfilled:
				print(f"  Backfilled {', '.join(backfilled)}: {spec['slug']}")
			else:
				print(f"  Already exists: {spec['slug']}")
			continue
		doc = frappe.get_doc(
			{
				"doctype": "CBT Company",
				"slug": spec["slug"],
				"company_code": spec["company_code"],
				"company_name": spec["company_name"],
				"registered_name": spec["registered_name"],
				"tin": spec.get("tin"),
				"payment_instructions": spec.get("payment_instructions"),
				"advance_booking_days": spec.get("advance_booking_days") or 0,
				"vat_registration": spec["vat_registration"],
				"allow_self_branch_management": spec["allow_self_branch_management"],
				"status": "Active",
				"office_hours": [
					{
						"day": day,
						"is_open": 1,
						"opening_time": spec["office_open"],
						"closing_time": spec["office_close"],
					}
					for day in spec["office_days"]
				],
			}
		)
		doc.insert(ignore_permissions=True)
		# Link validation reads a per-doc cache; clear so same-run inserts
		# that link this company (company users below) see it.
		frappe.clear_document_cache("CBT Company", doc.name)
		print(f"  Created: {spec['slug']}")


def _seed_payment_channels():
	"""Backlog B29. Cash + GCash per company and for the platform (idempotent,
	only where a company has NONE), the seeded GCash rows given the account
	details the payment_instructions already print, AYALA a BDO bank beside
	GCash so its split has three ways — and every payment row that pre-dates
	channels stamped with its kind's default (an older snapshot)."""
	from court_booking_tech.payment_channels import ensure_all

	print("\n=== CBT payment channels (Backlog B29) ===")
	result = ensure_all()
	print(
		f"  Defaults created: {result['companies']} tenant rows, "
		f"{result['platform']} platform rows; backfilled {result['backfilled']} payment rows"
	)
	# When-empty backfill, same rule as _seed_companies: an operator's own
	# edit to a channel survives a re-seed.
	for company, label, values in PAYMENT_CHANNEL_DETAILS:
		name = frappe.db.get_value(
			"CBT Payment Channel", {"company": company, "label": label}, "name"
		)
		if not name:
			continue
		empty = {
			field: value
			for field, value in values.items()
			if not frappe.db.get_value("CBT Payment Channel", name, field)
		}
		if empty:
			frappe.db.set_value("CBT Payment Channel", name, empty)
			print(f"  Backfilled {', '.join(empty)}: {name}")
	for spec in EXTRA_PAYMENT_CHANNELS:
		if frappe.db.exists(
			"CBT Payment Channel", {"company": spec["company"], "label": spec["label"]}
		):
			print(f"  Already exists: {spec['company']} / {spec['label']}")
			continue
		doc = frappe.get_doc({"doctype": "CBT Payment Channel", "scope": "Tenant", **spec})
		doc.insert(ignore_permissions=True)
		print(f"  Created: {doc.name}")
	_seed_gcash_qr()


def _seed_content_hash(name: str) -> str:
	"""frappe's File.content_hash for a seed file: md5 of the bytes as stored.
	Measured 2026-09-04 — the PNG survives the EXIF strip byte-identical."""
	import hashlib

	return hashlib.md5(_seed_file_bytes(name), usedforsecurity=False).hexdigest()


def _seed_gcash_qr():
	"""ONE seed-owned public File, UNATTACHED, referenced by every SEEDED GCash
	row (the rows in PAYMENT_CHANNEL_DETAILS — never "every GCash on the site").
	Unattached on purpose: frappe deletes an attached File when its field is
	cleared, which would 404 the other tenant's tile.

	NOT pure when-empty, unlike the backfill above: a row already pointing at
	the seed's File gets its blob restored when snapshot_reset dropped it; a row
	pointing anywhere else is an operator's own QR and is left alone.

	The seed's own File is found by CONTENT HASH, not file_name: when an earlier
	run's blob survived a snapshot_reset on disk (the DB row did not), frappe
	suffixes the new File's url AND file_name ("gcash_qr_sampleb5f4d7.png"), and
	a name lookup then misses the seed's own file on every later run — the full
	suite found it on 2026-09-04 as a 0 != 1 in test_seed_data.
	"""
	own = frappe.db.get_value(
		"File",
		{"content_hash": _seed_content_hash(GCASH_QR_FILE), "is_private": 0},
		["name", "file_url"],
		as_dict=True,
	)
	for company, label, _values in PAYMENT_CHANNEL_DETAILS:
		row = frappe.db.get_value(
			"CBT Payment Channel",
			{"company": company, "label": label},
			["name", "qr_image"],
			as_dict=True,
		)
		if not row:
			continue
		if row.qr_image:
			if own and row.qr_image == own.file_url:
				restored = _restore_blob(row.qr_image, GCASH_QR_FILE)
				print(f"  QR exists: {row.name}" + (" (blob restored on disk)" if restored else ""))
			else:
				print(f"  QR is the operator's own, left alone: {row.name}")
			continue
		if not own:
			created = _public_file(GCASH_QR_FILE)
			own = frappe._dict(name=created.name, file_url=created.file_url)
		frappe.db.set_value("CBT Payment Channel", row.name, "qr_image", own.file_url)
		print(f"  QR set: {row.name} -> {own.file_url}")


def _seed_company_users():
	from frappe.utils.password import update_password

	print("\n=== CBT company users ===")
	for email, first_name, last_name, company, company_role in COMPANY_USERS:
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"last_name": last_name,
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
				}
			)
			# Staff, not customers — see customer.on_user_after_insert.
			user.flags.cbt_skip_customer_role = True
			user.insert(ignore_permissions=True)
			print(f"  Created user: {email}")
		else:
			print(f"  User exists: {email}")

		# Known password every run (idempotent) — E2E staff logins (section-7).
		update_password(email, COMPANY_USER_PASSWORD)

		# CBT roles come ONLY from the CBT Company User sync — this proves the
		# sync on every reset instead of masking a broken on_update.
		if frappe.db.exists("CBT Company User", {"user": email}):
			print(f"  Binding exists: {email}")
			continue
		frappe.get_doc(
			{
				"doctype": "CBT Company User",
				"user": email,
				"company": company,
				"company_role": company_role,
			}
		).insert(ignore_permissions=True)
		print(f"  Bound {email} -> {company} ({company_role})")


def _seed_branches():
	print("\n=== CBT branches ===")
	for spec in BRANCHES:
		code = frappe.db.get_value("CBT Company", spec["company"], "company_code")
		name = f"{code}-{spec['slug']}"
		if frappe.db.exists("CBT Branch", name):
			print(f"  Already exists: {name}")
			continue
		closed_days = set(spec.get("closed_days") or [])
		doc = frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": spec["company"],
				"slug": spec["slug"],
				"branch_name": spec["branch_name"],
				"address_text": spec["address_text"],
				"latitude": spec.get("latitude"),
				"longitude": spec.get("longitude"),
				"phone": spec.get("phone"),
				"is_active": 1,
				# Explicit rows only when a day deviates from the 06:00–22:00
				# default (otherwise the controller auto-populates all 7).
				"business_hours": [
					{
						"day": day,
						"is_open": 0 if day in closed_days else 1,
						"opening_time": None if day in closed_days else "06:00:00",
						"closing_time": None if day in closed_days else "22:00:00",
					}
					for day in ALL_WEEK
				]
				if closed_days
				else [],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Branch", doc.name)
		print(f"  Created: {doc.name}")


def _seed_courts():
	print("\n=== CBT courts ===")
	for branch, court_name, court_type, hourly_rate in COURTS:
		if frappe.db.exists("CBT Court", {"branch": branch, "court_name": court_name}):
			print(f"  Already exists: {branch} / {court_name}")
			continue
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court",
				"branch": branch,
				"court_name": court_name,
				"court_type": court_type,
				"hourly_rate": hourly_rate,
				"is_active": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Court", doc.name)
		print(f"  Created: {doc.name}")
	# Heal by CONTENT, not count: a restored snapshot carries the courts without
	# the flag (the field is new), so this runs on every seed.
	for name in CONTINUE_ON_COURTS:
		if not frappe.db.get_value("CBT Court", name, "allow_continuation"):
			frappe.db.set_value("CBT Court", name, "allow_continuation", 1)
			frappe.clear_document_cache("CBT Court", name)
			print(f"  Continue-on flag set: {name}")


def _seed_bgc_layout():
	print("\n=== BGC floor plan ===")
	doc = frappe.get_doc("CBT Branch", BGC_LAYOUT["branch"])
	if doc.layout:
		print("  Already set")
		return
	doc.layout_rows = BGC_LAYOUT["rows"]
	doc.layout_columns = BGC_LAYOUT["columns"]
	for row_index, col_index, court in BGC_LAYOUT["cells"]:
		doc.append(
			"layout",
			{"row_index": row_index, "col_index": col_index, "court": court},
		)
	doc.save(ignore_permissions=True)
	print(f"  Set {BGC_LAYOUT['rows']}×{BGC_LAYOUT['columns']} layout with one empty cell")


def _seed_qcsm_layout():
	"""Section-22: the floor plan behind the /book sketch. Same guard as
	_seed_bgc_layout — seed_all() is run TWICE by test_seed_data."""
	print("\n=== QCSM Timog floor plan ===")
	doc = frappe.get_doc("CBT Branch", QCSM_LAYOUT["branch"])
	if doc.layout:
		print("  Already set")
		return
	doc.layout_rows = QCSM_LAYOUT["rows"]
	doc.layout_columns = QCSM_LAYOUT["columns"]
	for row_index, col_index, court in QCSM_LAYOUT["cells"]:
		doc.append(
			"layout",
			{"row_index": row_index, "col_index": col_index, "court": court},
		)
	doc.save(ignore_permissions=True)
	print(
		f"  Set {QCSM_LAYOUT['rows']}×{QCSM_LAYOUT['columns']} layout "
		f"with {QCSM_LAYOUT['rows'] * QCSM_LAYOUT['columns'] - len(QCSM_LAYOUT['cells'])} walkway cells"
	)


def _seed_file_bytes(name: str) -> bytes:
	with open(os.path.join(os.path.dirname(__file__), "files", name), "rb") as f:
		return f.read()


def _public_file(name: str):
	"""A PUBLIC File doc for a gallery image (callers want both name and url).

	Public from birth on purpose. The portal that renders these is guest-facing,
	and File permission follows the attached document — so a private gallery
	image 403s every customer while looking fine to the staff who uploaded it.
	CBTCompanyMedia._publish_image is the backstop that repairs a private one;
	seeding through it would exercise the repair instead of the steady state.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": name,
			"content": _seed_file_bytes(name),
			"is_private": 0,
		}
	).insert(ignore_permissions=True)
	return doc


def _restore_blob(file_url: str, seed_name: str) -> bool:
	"""Put the bytes back under an existing File row whose blob is gone.

	`snapshot_reset` restores the DATABASE but never `sites/<site>/public/files`
	(docs README, product invariants) — so on a restored site every seeded File
	row can point at a blob that is no longer on disk. The idempotency guards
	below then say "Banner exists" / "Already exists" and skip, the desk form
	shows a filename, and `/book` serves a 500 for the banner and every gallery
	image: E2E file 18's decode assertion is the only thing that notices. Found
	2026-08-27 (Batch 4, B24) — file 18 was red on a bench where nothing that
	file covers had changed.

	Writes the seed's ORIGINAL bytes. The row's `file_size`/`content_hash` were
	recorded from the EXIF-stripped re-encode frappe made at upload time, so they
	may not match the restored blob — nothing reads them for these rows (the
	oversize test inflates `file_size` on rows of its own), and a decodable
	public image is the whole requirement here.

	True when a blob was written; False when there was nothing to do.
	"""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return False
	path = frappe.get_doc("File", name).get_full_path()
	if os.path.exists(path):
		return False
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as handle:
		handle.write(_seed_file_bytes(seed_name))
	return True


HOME_AD_LIVE = "Rally Sports — 20% off paddles"
HOME_AD_EXPIRED = "Summer League 2020 (ended)"


def _seed_home_ad():
	"""Backlog B52: the marketplace home's paid banner.

	EXACTLY ONE live row, deliberately. `/find-court` picks at random from
	whatever is live that day, so a second live seed would make the E2E render
	assertion flake on which one it drew. The second row is EXPIRED — it proves
	the date window is honoured and it can never be chosen.

	The dates are absolute and absurdly wide on purpose: a seed whose fixture
	depends on WHEN it runs is unsound, not flaky (the standing rule that cost a
	whole UAT seed on 2026-09-01).
	"""
	print("\n=== CBT home ad (Backlog B52) ===")
	rows = (
		(HOME_AD_LIVE, "2020-01-01", "2999-12-31", 0),
		(HOME_AD_EXPIRED, "2020-01-01", "2020-01-31", 10),
	)
	for title, starts_on, ends_on, sort_order in rows:
		if frappe.db.exists("CBT Ad", {"title": title}):
			print(f"  Already exists: {title}")
			continue
		image = _public_file(MEDIA_BANNER_FILE)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Ad",
				"title": title,
				"image": image.file_url,
				"link_url": "https://example.com/rally-sports",
				"placement": "Top",
				"starts_on": starts_on,
				"ends_on": ends_on,
				"is_active": 1,
				"sort_order": sort_order,
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(
			"File",
			image.name,
			{
				"attached_to_doctype": "CBT Ad",
				"attached_to_name": doc.name,
				"attached_to_field": "image",
			},
		)
		print(f"  Created: {doc.name} — {title} ({starts_on} .. {ends_on})")


def _seed_company_media():
	"""Section-22 (B13): banner + gallery for the /book enticement pass."""
	print("\n=== CBT company media (section-22) ===")

	banner_url = frappe.db.get_value("CBT Company", MEDIA_COMPANY, "banner")
	if banner_url and _restore_blob(banner_url, MEDIA_BANNER_FILE):
		print(f"  Banner blob restored on disk: {banner_url}")
	if not banner_url:
		banner = _public_file(MEDIA_BANNER_FILE)
		frappe.db.set_value("CBT Company", MEDIA_COMPANY, "banner", banner.file_url)
		frappe.db.set_value(
			"File",
			banner.name,
			{
				"attached_to_doctype": "CBT Company",
				"attached_to_name": MEDIA_COMPANY,
				"attached_to_field": "banner",
			},
		)
		print(f"  Banner: {MEDIA_COMPANY} -> {banner.file_url}")
	else:
		print(f"  Banner exists: {MEDIA_COMPANY}")

	# Photos now live ON the facility as CBT Media Item rows (section-29). The
	# branch gets its own set so the marketplace card can be seen to override.
	# The banner IS row 1 of the company's photos, never a fourth thing beside them.
	for parent_type, parent, names in (
		("CBT Company", MEDIA_COMPANY,
		 [MEDIA_BANNER_FILE] + [row[0] for row in COMPANY_MEDIA[:3]]),
		("CBT Branch", MEDIA_BRANCH, [row[0] for row in COMPANY_MEDIA[3:]]),
	):
		if frappe.db.exists(
			"CBT Media Item", {"parent": parent, "parenttype": parent_type}
		):
			print(f"  Already exists: photos on {parent}")
			continue
		for idx, file_name in enumerate(names, start=1):
			image = _public_file(file_name)
			frappe.get_doc(
				{
					"doctype": "CBT Media Item",
					"parent": parent,
					"parenttype": parent_type,
					"parentfield": "photos",
					"idx": idx,
					"image": image.file_url,
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value(
				"File",
				image.name,
				{"attached_to_doctype": parent_type, "attached_to_name": parent},
			)
		print(f"  Created: {len(names)} photos on {parent}")


def _seed_customer():
	print("\n=== CBT demo customer ===")
	if not frappe.db.exists("User", CUSTOMER_EMAIL):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": CUSTOMER_EMAIL,
				"first_name": "Carla",
				"last_name": "Courtside",
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		print(f"  Created customer: {CUSTOMER_EMAIL}")
	else:
		print(f"  Customer exists: {CUSTOMER_EMAIL}")

	user = frappe.get_doc("User", CUSTOMER_EMAIL)
	if "CBT Customer" not in {r.role for r in user.roles}:
		user.append("roles", {"role": "CBT Customer"})
		user.save(ignore_permissions=True)
		print("  Role added: CBT Customer")
	else:
		print("  Role present: CBT Customer")


def _seed_encryption_key():
	"""Password fields (the Turnstile secret) need conf.encryption_key. The
	snapshot's site_config has NONE, and frappe mints one lazily on the
	FIRST encrypt — if that first write lands after `bench serve` started,
	the server keeps its key-less conf and every decrypt fails until restart
	(observed as a deterministic fail-closed signup on freshly cloned worker
	sites). Minting at seed time makes the key part of provisioning, before
	any server starts."""
	from frappe.utils.password import get_encryption_key

	print("\n=== Site encryption key (section-8) ===")
	had_key = "encryption_key" in frappe.conf
	get_encryption_key()
	print("  Already present" if had_key else "  Minted into site_config.json")


def _seed_portal_settings():
	"""Stock signup assigns Portal Settings.default_role (PLAN §7); the
	User.after_insert hook is only the belt-and-suspenders layer."""
	print("\n=== Portal Settings (section-8) ===")
	if frappe.db.get_single_value("Portal Settings", "default_role") != "CBT Customer":
		frappe.db.set_single_value("Portal Settings", "default_role", "CBT Customer")
		print("  default_role -> CBT Customer")
	else:
		print("  default_role already CBT Customer")

	# PLAN §8u: the D9 wrapper delegates to core sign_up, which throws while
	# Website Settings.disable_signup is on — and ERPNext's installer turns
	# it ON. Dev sites re-open it here; prod is a section-12 checklist line.
	if frappe.db.get_single_value("Website Settings", "disable_signup"):
		frappe.db.set_single_value("Website Settings", "disable_signup", 0)
		print("  Website Settings.disable_signup -> 0 (ERPNext installer sets 1)")
	else:
		print("  Website Settings.disable_signup already 0")

	# Section-9: the marketplace front door is wired via the
	# get_website_user_home_page hook (guests/customers -> find-court; System
	# Users -> desk fallback). Website Settings.home_page must stay EMPTY — a
	# global value there would override the "me"->"desk" remap and send staff
	# to the portal after login. Clear a find-court value left by an earlier
	# (buggy) seed run.
	if frappe.db.get_single_value("Website Settings", "home_page") == PORTAL_HOME:
		frappe.db.set_single_value("Website Settings", "home_page", "")
		print("  Website Settings.home_page cleared (home is via hook, not a global)")
	else:
		print("  Website Settings.home_page not globally pinned (good)")

	# Portal menu: sync our hook rows in, and hide stock/ERPNext entries that
	# mean nothing to a court customer (idempotent; enabled flag only).
	settings = frappe.get_single("Portal Settings")
	settings.sync_menu()
	changed = 0
	for row in settings.menu:
		wanted = 1 if row.route in CBT_PORTAL_ROUTES else 0
		if int(row.enabled or 0) != wanted:
			row.enabled = wanted
			changed += 1
	if changed:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
	print(f"  Portal menu rows adjusted: {changed}")


def _seed_rate_limit_reset():
	"""Portal rate-limit counters live in REDIS, so they survive
	snapshot_reset (which only rolls back the DB). A stale bucket would fail
	the next E2E round with a spurious 'too many requests' — every seed run
	starts the suite from a clean window."""
	from court_booking_tech.throttle import clear_all_buckets

	print("\n=== Portal rate-limit buckets (section-9) ===")
	clear_all_buckets()
	print("  Cleared")


def _seed_mail_stub():
	"""Email Queue is the dev/E2E assertion surface (section-8): frappe
	resolves the outgoing account at QUEUE time and hard-fails when none is
	configured (EmailAccount.find_outgoing _raise_error) — zero accounts
	means zero queue rows, not queued-but-unsent. The site-config stub
	account (EmailAccount.find_default_outgoing fallback) fixes that with
	NO real SMTP. mute_emails then blocks the SEND leg entirely
	(EmailQueue.can_send_now) — without it the request-time after_commit
	q.send opens a real SMTP connection and a refused localhost:25 500s the
	signup response. Real SMTP stays a section-12 prod-checklist line."""
	from frappe.installer import update_site_config

	print("\n=== Dev mail stub (section-8, site config) ===")
	wanted = {
		"mail_server": "localhost",
		"mail_port": 25,
		"auto_email_id": "cbt-dev@example.com",
		"disable_mail_smtp_authentication": 1,
		"mute_emails": 1,
	}
	changed = []
	for key, value in wanted.items():
		if frappe.conf.get(key) != value:
			update_site_config(key, value)
			frappe.conf[key] = value  # this process reads conf too
			changed.append(key)
	if changed:
		print(f"  Set: {', '.join(changed)}")
	else:
		print("  Already configured")


def _seed_customer_profiles():
	from frappe.utils.password import update_password

	from court_booking_tech.customer import ensure_customer_profile

	print("\n=== CBT portal customers + profiles (section-8) ===")
	for email, first_name, last_name, lat, lng in PORTAL_CUSTOMERS:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"last_name": last_name,
					"enabled": 1,
					"user_type": "Website User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
			print(f"  Created customer: {email}")
		else:
			print(f"  Customer exists: {email}")

		# Known password every run — E2E portal logins (auth DEFAULT_PASSWORD).
		update_password(email, COMPANY_USER_PASSWORD)

		profile = ensure_customer_profile(email)
		# Force-restore the canonical pin (see PORTAL_CUSTOMERS note).
		if (profile.home_latitude, profile.home_longitude) != (lat, lng):
			frappe.db.set_value(
				"CBT Customer Profile",
				profile.name,
				{"home_latitude": lat, "home_longitude": lng},
			)
			print(f"  Pin set: {email} -> ({lat}, {lng})")
		else:
			print(f"  Pin already canonical: {email}")

	# The pin-less profile case. She owns every seeded booking, and until
	# 2026-09-04 was the one customer with NO password (portal login 401'd).
	ensure_customer_profile(CUSTOMER_EMAIL)
	update_password(CUSTOMER_EMAIL, COMPANY_USER_PASSWORD)
	print(f"  Profile ensured (no pin) + password: {CUSTOMER_EMAIL}")


def _seed_e2e_fast_company():
	print("\n=== CBT e2e-fast company (E2E determinism fixture) ===")
	spec = E2E_COMPANY
	if not frappe.db.exists("CBT Company", spec["slug"]):
		frappe.get_doc(
			{
				"doctype": "CBT Company",
				"slug": spec["slug"],
				"company_code": spec["company_code"],
				"company_name": spec["company_name"],
				"registered_name": spec["registered_name"],
				"vat_registration": spec["vat_registration"],
				"payment_instructions": spec.get("payment_instructions"),
				"advance_booking_days": spec.get("advance_booking_days") or 0,
				"allow_self_branch_management": spec["allow_self_branch_management"],
				"status": "Active",
				"reservation_expiry_minutes": 1,
				"office_hours": [
					{
						"day": day,
						"is_open": 1,
						"opening_time": spec["office_open"],
						"closing_time": spec["office_close"],
					}
					for day in spec["office_days"]
				],
			}
		).insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Company", spec["slug"])
		print(f"  Created: {spec['slug']} (expiry 1 min)")
	else:
		# Same when-empty backfill as _seed_companies (older snapshots predate
		# these fields; E2E file 07 books E2EF up to today+57, so the horizon
		# has to land on an already-created fixture too).
		backfilled = []
		for field in ("payment_instructions", "advance_booking_days"):
			if spec.get(field) and not frappe.db.get_value(
				"CBT Company", spec["slug"], field
			):
				frappe.db.set_value("CBT Company", spec["slug"], field, spec[field])
				backfilled.append(field)
		if backfilled:
			print(f"  Backfilled {', '.join(backfilled)}: {spec['slug']}")
		else:
			print(f"  Already exists: {spec['slug']}")

	if not frappe.db.exists("CBT Branch", E2E_BRANCH_NAME):
		frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": spec["slug"],
				"slug": "main",
				"branch_name": "E2E Fast Main",
				"address_text": "Test Grid, Nowhere",
				# NO pin on purpose — must not disturb geo ordering fixtures.
				"is_active": 1,
				# Round-the-clock court hours so E2E can book at any wall time.
				"business_hours": [
					{
						"day": day,
						"is_open": 1,
						"opening_time": "00:00:00",
						"closing_time": "23:59:00",
					}
					for day in ALL_WEEK
				],
			}
		).insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Branch", E2E_BRANCH_NAME)
		print(f"  Created: {E2E_BRANCH_NAME}")
	else:
		print(f"  Already exists: {E2E_BRANCH_NAME}")

	if not frappe.db.exists(
		"CBT Court", {"branch": E2E_BRANCH_NAME, "court_name": "Court 1"}
	):
		frappe.get_doc(
			{
				"doctype": "CBT Court",
				"branch": E2E_BRANCH_NAME,
				"court_name": "Court 1",
				"court_type": "Pickleball",
				"hourly_rate": 100,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_document_cache("CBT Court", f"{E2E_BRANCH_NAME}-court-1")
		print(f"  Created: {E2E_BRANCH_NAME}-court-1")
	else:
		print(f"  Already exists: {E2E_BRANCH_NAME}-court-1")

	_seed_rate_rule_court()


def _seed_rate_rule_court():
	"""Section-14: the ONLY seeded court that carries rate rules.

	Deliberately a NEW court rather than rules on an existing one — Pia's
	pinned ₱800 at BGC and the §4 2027-01-15 cast are asserted to the peso in
	half a dozen green files, and re-pricing any of them would break a suite
	that has nothing to do with this feature (S10 note 13, S11 as-built 21).

	Rules are re-applied when their CONTENT differs (2026-09-03 — the count
	alone could not heal a changed window), so a court left behind by a
	half-finished run or an older seed heals instead of failing the seed
	contract; a matching set is a no-op, pinned by the drift test.
	"""
	if not frappe.db.exists(
		"CBT Court", {"branch": E2E_BRANCH_NAME, "court_name": E2E_RATE_COURT_NAME}
	):
		frappe.get_doc(
			{
				"doctype": "CBT Court",
				"branch": E2E_BRANCH_NAME,
				"court_name": E2E_RATE_COURT_NAME,
				"court_type": "Pickleball",
				"hourly_rate": E2E_RATE_COURT_BASE,
				"is_active": 1,
				"description": (
					"Section-14 rate-rules fixture: ₱350 after 18:00 every day, "
					"₱250 on weekend mornings."
				),
			}
		).insert(ignore_permissions=True)
		print(f"  Created: {E2E_RATE_COURT}")
	else:
		print(f"  Already exists: {E2E_RATE_COURT}")

	court = frappe.get_doc("CBT Court", E2E_RATE_COURT)
	if _rate_rules_differ(court.rate_rules or [], E2E_RATE_RULES):
		court.set("rate_rules", [])
		for rule in E2E_RATE_RULES:
			court.append("rate_rules", rule)
		court.save(ignore_permissions=True)
		print(f"  Rate rules set: {len(E2E_RATE_RULES)} on {E2E_RATE_COURT}")
	else:
		print(f"  Rate rules already set on {E2E_RATE_COURT}")
	frappe.clear_document_cache("CBT Court", E2E_RATE_COURT)


def _rate_rules_differ(rows, expected) -> bool:
	"""Content, not count. Times compare as timedeltas (a Time column reads back
	as one), rates as floats, a blank label as None."""
	from frappe.utils import flt

	from court_booking_tech.timeutil import _as_timedelta

	def _key(rule):
		return (
			rule.get("day_scope"),
			_as_timedelta(rule.get("start_time")),
			_as_timedelta(rule.get("end_time")),
			flt(rule.get("hourly_rate")),
			rule.get("label") or None,
		)

	return [_key(rule) for rule in rows] != [_key(rule) for rule in expected]


def _insert_booking(court, start_time, payment_method, slots=1):
	# NO `hourly_rate` on purpose (section-14): an omitted rate is filled from
	# the court by fetch_from/fetch_if_empty and the controller then applies
	# that court's rate rules per slot. Passing one would make the booking a
	# deliberate FLAT OVERRIDE and skip the rules — invisible today because
	# every court seeded through here is flat-rate, so anyone adding a ruled
	# court to this path must decide which they meant.
	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": CUSTOMER_EMAIL,
			"booking_date": BOOKING_DATE,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": payment_method,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _seed_customer_relationships():
	"""Past, CANCELLED bookings that make a customer known to a company.

	Backlog B43 found this the moment the E2E suite stopped running as
	Administrator, and it is a FIXTURE gap, not a product one. B38 scopes the
	desk's customer picker by RELATIONSHIP (section-13): a seat is offered only
	customers its company has actually dealt with. Measured 2026-09-05:

	  * `e2e-fast` had NO customer relationships at all, so an E2E Fast staff
	    seat was offered NOBODY — three board rows had been quick-booking Carla
	    there for months, green, on an Administrator context that saw every
	    tenant;
	  * `cust.pia@example.com` had no booking and no membership anywhere, so the
	    "a non-member pays list price" row could not pick her from an Ayala seat
	    either.

	Both are now real customers of those companies, which is what those tests
	always assumed. CANCELLED and FREE on purpose: it occupies no slot, carries
	no money into any report, and cannot collide with a ledger day.
	"""
	print("\n=== CBT customer relationships (Backlog B43) ===")
	for court, start_time, customer in CUSTOMER_RELATIONSHIPS:
		if frappe.db.exists(
			"CBT Court Booking",
			{"court": court, "booking_date": BOOKING_DATE, "start_time": start_time},
		):
			print(f"  Already exists: {court} {start_time} ({customer})")
			continue
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": court,
				"customer": customer,
				"booking_date": BOOKING_DATE,
				"start_time": start_time,
				"number_of_slots": 1,
				"payment_method": "Free",
			}
		)
		doc.insert(ignore_permissions=True)
		# Straight to Cancelled through the same db flip the Expired fixture
		# uses — the API path is a refund gate, and there is nothing to refund.
		frappe.db.set_value(
			"CBT Court Booking", doc.name, "booking_status", "Cancelled"
		)
		print(f"  Related {customer} -> {court} ({doc.name}, Cancelled)")


def _seed_bookings():
	"""One booking per status (idempotency key: court + date + start_time).
	Statuses other than the insert-derived ones are reached through LEGAL
	transitions (or the sweep's own db flip for Expired)."""
	print("\n=== CBT court bookings ===")

	def exists(court, start_time):
		return frappe.db.exists(
			"CBT Court Booking",
			{"court": court, "booking_date": BOOKING_DATE, "start_time": start_time},
		)

	# Reserved (fund transfer). Deterministic far-future expiry — the insert
	# pipeline stamps now+30min, which would flake across suite runs.
	if not exists("AYALA-bgc-court-1", "10:00:00"):
		doc = _insert_booking("AYALA-bgc-court-1", "10:00:00", "Fund Transfer")
		frappe.db.set_value(
			"CBT Court Booking",
			doc.name,
			"reservation_expires_at",
			f"{BOOKING_DATE} 09:00:00",
		)
		print(f"  Reserved (FT): {doc.name}")
	else:
		print("  Reserved exists: AYALA-bgc-court-1 10:00")

	# Confirmed (cash walk-in — instant confirm).
	if not exists("AYALA-bgc-court-2", "10:00:00"):
		doc = _insert_booking("AYALA-bgc-court-2", "10:00:00", "Cash")
		print(f"  Confirmed (cash): {doc.name}")
	else:
		print("  Confirmed exists: AYALA-bgc-court-2 10:00")

	# Extended pair — created THROUGH the API (dogfoods extend_booking).
	if not exists("AYALA-bgc-court-3", "08:00:00"):
		from court_booking_tech.api.bookings import extend_booking

		original = _insert_booking("AYALA-bgc-court-3", "08:00:00", "Cash")
		extension = extend_booking(original.name, slots=1, payment_method="Cash")
		print(f"  Extended pair: {original.name} -> {extension}")
	else:
		print("  Extended pair exists: AYALA-bgc-court-3 08:00")

	# Expired (fund transfer whose base clock ran out — direct db flip, the
	# same write the sweep performs).
	if not exists("AYALA-bgc-court-1", "14:00:00"):
		doc = _insert_booking("AYALA-bgc-court-1", "14:00:00", "Fund Transfer")
		frappe.db.set_value(
			"CBT Court Booking",
			doc.name,
			{
				"booking_status": "Expired",
				"reservation_expires_at": "2026-01-01 12:00:00",
			},
		)
		print(f"  Expired (FT): {doc.name}")
	else:
		print("  Expired exists: AYALA-bgc-court-1 14:00")


def _proof_sample_bytes() -> bytes:
	with open(os.path.join(os.path.dirname(__file__), "files", "proof_sample.jpg"), "rb") as f:
		return f.read()


def _attach_proof(booking_name, source, uploaded_by, status="Pending", rejection_reason=None):
	"""Seed a proof directly (NOT via the upload API — seeds must not couple
	to caps/clock); same File-then-attach pattern as api/proofs.py."""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "proof_sample.jpg",
			"content": _proof_sample_bytes(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	proof = frappe.get_doc(
		{
			"doctype": "CBT Payment Proof",
			"booking": booking_name,
			"file": file_doc.file_url,
			"source": source,
			"uploaded_by": uploaded_by,
			"reference_no": "SEED-REF-001",
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value(
		"File",
		file_doc.name,
		{
			"attached_to_doctype": "CBT Payment Proof",
			"attached_to_name": proof.name,
			"attached_to_field": "file",
		},
	)
	if status != "Pending":
		# Same controller-driven write the review APIs perform.
		frappe.db.set_value(
			"CBT Payment Proof",
			proof.name,
			{
				"status": status,
				"rejected_by": "Administrator",
				"rejected_at": f"{BOOKING_DATE} 08:30:00",
				"rejection_reason": rejection_reason,
			},
		)
	return proof


def _seed_proof_bookings():
	"""Section-5 verification cast — on AYALA-makati courts ONLY: the bgc
	courts' 2027-01-15 slots are load-bearing fixtures for the S4
	availability/expiry tests (the bgc Reserved hold must stay proof-free)."""
	print("\n=== CBT payment-proof bookings (section-5) ===")

	def exists(court, start_time):
		return frappe.db.exists(
			"CBT Court Booking",
			{"court": court, "booking_date": BOOKING_DATE, "start_time": start_time},
		)

	# Reserved WITH a Pending proof: base clock fixed at 09:00, verification
	# deadline fixed at 12:00 — between those, the hold lives on the
	# verification clock alone (S7 panel + E2E consume this).
	if not exists("AYALA-makati-court-a", "10:00:00"):
		doc = _insert_booking("AYALA-makati-court-a", "10:00:00", "Fund Transfer")
		frappe.db.set_value(
			"CBT Court Booking",
			doc.name,
			{
				"reservation_expires_at": f"{BOOKING_DATE} 09:00:00",
				"verification_deadline_at": f"{BOOKING_DATE} 12:00:00",
			},
		)
		_attach_proof(doc.name, "Customer", CUSTOMER_EMAIL)
		print(f"  Reserved with Pending proof: {doc.name}")
	else:
		print("  Reserved-with-proof exists: AYALA-makati-court-a 10:00")

	# Rejected once (Unreadable): inside its fixed regrace window, deadline
	# cleared — the re-upload path restarts the walker from here.
	if not exists("AYALA-makati-court-b", "10:00:00"):
		doc = _insert_booking("AYALA-makati-court-b", "10:00:00", "Fund Transfer")
		frappe.db.set_value(
			"CBT Court Booking",
			doc.name,
			{
				"reservation_expires_at": f"{BOOKING_DATE} 11:00:00",
				"verification_deadline_at": None,
				"rejection_count": 1,
			},
		)
		_attach_proof(
			doc.name,
			"Customer",
			CUSTOMER_EMAIL,
			status="Rejected",
			rejection_reason="Unreadable",
		)
		print(f"  Rejected-once (regrace): {doc.name}")
	else:
		print("  Rejected-once exists: AYALA-makati-court-b 10:00")


def _seed_invoices():
	"""Section-6: billing documents in all three states across VAT (AYALA)
	and NON-VAT (QCSM). New bookings get their invoice from the after_insert
	hook; bookings restored from a pre-section-6 snapshot are BACKFILLED, and
	every seeded booking is re-synced so db-flipped statuses (the seeded
	Expired hold) land on the invoice too."""
	from court_booking_tech.billing import (
		create_invoice_for_booking,
		sync_invoice_for_booking,
	)

	print("\n=== CBT booking invoices (section-6) ===")

	# NON-VAT paid: one QCSM cash walk-in. Court-2 on 2027-01-15 10:00 is
	# collision-free (test_isolation uses this court on 2027-02-12; the
	# numbering tests assert regex + relative sequence only).
	if not frappe.db.exists(
		"CBT Court Booking",
		{
			"court": "QCSM-timog-court-2",
			"booking_date": BOOKING_DATE,
			"start_time": "10:00:00",
		},
	):
		doc = _insert_booking("QCSM-timog-court-2", "10:00:00", "Cash")
		print(f"  Confirmed (cash, NON-VAT): {doc.name}")
	else:
		print("  QCSM cash booking exists: QCSM-timog-court-2 10:00")

	# Backfill invoices for bookings that predate the section-6 hooks.
	for name in frappe.get_all(
		"CBT Court Booking",
		filters=[["billing_doc", "is", "not set"]],
		pluck="name",
	):
		create_invoice_for_booking(name)
		print(f"  Backfilled invoice: {name}")

	# Re-derive every seeded booking's invoice (Expired hold → Cancelled doc).
	for name in frappe.get_all(
		"CBT Court Booking", filters={"booking_date": BOOKING_DATE}, pluck="name"
	):
		sync_invoice_for_booking(name)

	# The staff's manual O.R. cross-reference on the AYALA paid invoice.
	paid_invoice = frappe.db.get_value(
		"CBT Court Booking",
		{
			"court": "AYALA-bgc-court-2",
			"booking_date": BOOKING_DATE,
			"start_time": "10:00:00",
		},
		"billing_doc",
	)
	if paid_invoice and not frappe.db.get_value(
		"CBT Booking Invoice", paid_invoice, "or_number"
	):
		frappe.db.set_value("CBT Booking Invoice", paid_invoice, "or_number", "OR-0001")
		print(f"  or_number OR-0001 on {paid_invoice}")
	else:
		print("  or_number already set (or no paid invoice)")


def _seed_blocks():
	print("\n=== CBT slot blocks ===")
	# Whole-branch closure (holiday) — court left EMPTY on purpose.
	if not frappe.db.exists(
		"CBT Slot Block",
		{"branch": "QCSM-timog", "block_date": BLOCK_DATE_BRANCH, "reason": "Holiday"},
	):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "QCSM-timog",
				"block_date": BLOCK_DATE_BRANCH,
				"start_time": "06:00:00",
				"end_time": "22:00:00",
				"reason": "Holiday",
				"notes": "Seeded whole-branch closure",
			}
		)
		doc.insert(ignore_permissions=True)
		print(f"  Whole-branch Holiday block: {doc.name}")
	else:
		print("  Whole-branch Holiday block exists")

	# Court-level maintenance block.
	if not frappe.db.exists(
		"CBT Slot Block",
		{
			"branch": "AYALA-bgc",
			"court": "AYALA-bgc-court-2",
			"block_date": BOOKING_DATE,
			"reason": "Maintenance",
		},
	):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "AYALA-bgc",
				"court": "AYALA-bgc-court-2",
				"block_date": BOOKING_DATE,
				"start_time": "18:00:00",
				"end_time": "20:00:00",
				"reason": "Maintenance",
				"notes": "Seeded court block",
			}
		)
		doc.insert(ignore_permissions=True)
		print(f"  Court Maintenance block: {doc.name}")
	else:
		print("  Court Maintenance block exists")


def _seed_billing_modes():
	"""Platform billing config (section-11). Written with set_value, not through
	the form: these are permlevel-1 platform levers, and _seed_companies skips
	companies that already exist — a snapshot taken before section-11 would
	otherwise never gain them."""
	print("\n=== CBT platform billing config (section-11) ===")
	for slug, config in BILLING_MODES.items():
		if not frappe.db.exists("CBT Company", slug):
			continue
		current = frappe.db.get_value(
			"CBT Company", slug, ["billing_mode", "commission_percent", "subscription_fee"],
			as_dict=True,
		)
		if current.billing_mode == config.get("billing_mode"):
			print(f"  Already set: {slug} ({current.billing_mode})")
			continue
		frappe.db.set_value("CBT Company", slug, config)
		print(f"  {slug} -> {config}")


def _seed_member_customers():
	from frappe.utils.password import update_password

	print("\n=== CBT membership customers (section-11) ===")
	for email, first, last in MEMBER_CUSTOMERS:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first,
					"last_name": last,
					"enabled": 1,
					"user_type": "Website User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
			print(f"  Created customer: {email}")
		else:
			print(f"  Customer exists: {email}")
		# Known password every run — E2E portal logins (auth DEFAULT_PASSWORD).
		update_password(email, COMPANY_USER_PASSWORD)

		user = frappe.get_doc("User", email)
		if "CBT Customer" not in {r.role for r in user.roles}:
			user.append("roles", {"role": "CBT Customer"})
			user.save(ignore_permissions=True)
			print(f"  Role added: CBT Customer ({email})")


def _seed_memberships():
	"""Idempotency key: (company, customer, start_date). The controller rejects
	overlapping windows, so a re-run must never insert a second row."""
	print("\n=== CBT memberships (section-11) ===")
	for company, customer, tier, discount, start_date, end_date in MEMBERSHIPS:
		if frappe.db.exists(
			"CBT Membership",
			{"company": company, "customer": customer, "start_date": start_date},
		):
			print(f"  Already exists: {customer} @ {company} ({start_date})")
			continue
		doc = frappe.get_doc(
			{
				"doctype": "CBT Membership",
				"company": company,
				"customer": customer,
				"tier": tier,
				"discount_percent": discount,
				"start_date": start_date,
				"end_date": end_date,
			}
		)
		doc.insert(ignore_permissions=True)
		print(f"  Created: {doc.name} ({tier} {discount}% — {customer} @ {company})")


def _seed_evasion_booking():
	"""Section-11: the commission-evasion audit fixture — an EXPIRED booking
	that carries a payment proof (money was claimed, the booking still died).

	Runs AFTER _seed_invoices because that function only re-syncs bookings on
	BOOKING_DATE; this one lives on its own date and owns its invoice flip.
	"""
	from court_booking_tech.billing import sync_invoice_for_booking

	print("\n=== CBT expired-with-proof booking (section-11 evasion fixture) ===")
	if frappe.db.exists(
		"CBT Court Booking",
		{"court": EVASION_COURT, "booking_date": EVASION_DATE, "start_time": "10:00:00"},
	):
		print(f"  Already exists: {EVASION_COURT} {EVASION_DATE} 10:00")
		return

	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": EVASION_COURT,
			"customer": "cust.evan@example.com",
			"booking_date": EVASION_DATE,
			"start_time": "10:00:00",
			"number_of_slots": 1,
			"payment_method": "Fund Transfer",
			# Explicit: Evan holds no membership, and pinning it keeps the
			# report's peso ground truth stable if that ever changes.
			"discount_percent": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	_attach_proof(doc.name, "Customer", "cust.evan@example.com")
	# The same write the sweep performs when both clocks lapse.
	frappe.db.set_value(
		"CBT Court Booking",
		doc.name,
		{
			"booking_status": "Expired",
			"reservation_expires_at": f"{EVASION_DATE} 09:00:00",
		},
	)
	sync_invoice_for_booking(doc.name)  # -> invoice Cancelled (PLAN §8e)
	print(f"  Expired-with-proof: {doc.name} ({EVASION_COURT} {EVASION_DATE})")


def _seed_walkin_booking():
	"""Section-13: the walk-in fixture — a cash customer with NO account.

	Runs AFTER _seed_invoices for the same reason as the evasion fixture: that
	function only re-syncs bookings on BOOKING_DATE, and this one lives on its
	own date. Cash means the insert pipeline confirms it outright, so its
	invoice is born Paid & Verified through the normal after_insert hook —
	nothing to flip by hand.

	Idempotency key: (court, booking_date, start_time), as everywhere else.
	"""
	print("\n=== CBT walk-in booking (section-13, Backlog B1) ===")
	if frappe.db.exists(
		"CBT Court Booking",
		{
			"court": WALKIN_COURT,
			"booking_date": WALKIN_DATE,
			"start_time": "10:00:00",
		},
	):
		print(f"  Already exists: {WALKIN_COURT} {WALKIN_DATE} 10:00")
		return

	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": WALKIN_COURT,
			# customer deliberately ABSENT — that is the whole point of the
			# fixture: prove the walk-in path end to end, receipt included.
			"customer_name": WALKIN_NAME,
			"customer_phone": WALKIN_PHONE,
			"booking_date": WALKIN_DATE,
			"start_time": "10:00:00",
			"number_of_slots": 1,
			"payment_method": "Cash",
		}
	)
	doc.insert(ignore_permissions=True)
	print(f"  Walk-in (cash, no account): {doc.name} — {WALKIN_NAME}")


def _seed_open_play_customers():
	print("\n=== CBT open play players (section-10) ===")
	for email, first, last in OPEN_PLAY_CUSTOMERS:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first,
					"last_name": last,
					"enabled": 1,
					"user_type": "Website User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
			print(f"  Created player: {email}")
		user = frappe.get_doc("User", email)
		if "CBT Customer" not in {r.role for r in user.roles}:
			user.append("roles", {"role": "CBT Customer"})
			user.save(ignore_permissions=True)


def _seed_open_play():
	"""One AYALA Saturday session, driven through the REAL engine APIs so the
	seeds exercise the same path staff use — a regression there breaks the seed
	run loudly instead of quietly shipping fake-shaped data.

	Payment mix mirrors a real desk: 4 cash, 1 free, 1 fund transfer confirmed,
	1 with a proof awaiting review, 1 still owing.
	"""
	print("\n=== CBT open play session (section-10) ===")
	existing = frappe.db.exists(
		"CBT Open Play Session",
		{
			"branch": "AYALA-bgc",
			"session_date": OPEN_PLAY_DATE,
			"title": OPEN_PLAY_TITLE,
		},
	)
	if existing:
		print(f"  Session exists: {existing}")
		return

	from court_booking_tech.api.open_play import (
		add_players,
		confirm_participant_payment,
		create_participant_proof,
		open_session,
	)

	doc = frappe.get_doc(
		{
			"doctype": "CBT Open Play Session",
			"branch": "AYALA-bgc",
			"title": OPEN_PLAY_TITLE,
			"session_date": OPEN_PLAY_DATE,
			"start_time": "09:00:00",
			"end_time": "12:00:00",
			"court_type": "Pickleball",
			"rotation_mode": "Timed",
			"rotation_minutes": 15,
			"entry_fee": OPEN_PLAY_FEE,
			"courts": [{"court": court} for court in OPEN_PLAY_COURTS],
		}
	)
	doc.insert(ignore_permissions=True)
	open_session(doc.name)
	print(f"  Created and opened: {doc.name}")

	emails = [row[0] for row in OPEN_PLAY_CUSTOMERS]
	add_players(
		doc.name,
		frappe.as_json(
			[{"customer": e, "payment_method": "Cash"} for e in emails[:4]]
		),
	)
	add_players(
		doc.name, frappe.as_json([{"customer": emails[4], "payment_method": "Free"}])
	)
	add_players(
		doc.name,
		frappe.as_json(
			[{"customer": e, "payment_method": "Fund Transfer"} for e in emails[5:8]]
		),
	)

	doc = frappe.get_doc("CBT Open Play Session", doc.name)
	unpaid = [row for row in doc.participants if row.payment_status == "Unpaid"]
	confirm_participant_payment(doc.name, unpaid[0].name)
	create_participant_proof(
		doc.name, unpaid[1].name, "proof_sample.jpg", _proof_sample_bytes()
	)
	revenue = frappe.db.get_value(
		"CBT Open Play Session", doc.name, "total_revenue"
	)
	print(f"  {len(emails)} players seeded; revenue {revenue}")


def _seed_open_play_walkin():
	"""Section-20 (Backlog B2): one cash walk-in in the seeded session.

	A FUNCTION OF ITS OWN, and that is the whole point. `_seed_open_play`
	early-returns as soon as its session exists — which on any snapshot-restored
	site it always does — so a walk-in appended inside that function would never
	run again after the snapshot was taken, and `test_seed_data` would fail on a
	participant that silently never appeared. Its idempotency key is therefore
	the walk-in ROW, not the session.
	"""
	print("\n=== CBT open play walk-in (section-20) ===")
	session = frappe.db.exists(
		"CBT Open Play Session",
		{
			"branch": "AYALA-bgc",
			"session_date": OPEN_PLAY_DATE,
			"title": OPEN_PLAY_TITLE,
		},
	)
	if not session:
		print("  No seeded session to join — skipped.")
		return

	doc = frappe.get_doc("CBT Open Play Session", session)
	if doc.status != "Open":
		# add_players requires an Open session. Without this guard a seeded
		# session that someone completed or cancelled would throw here, and
		# because seed_all() runs in every test class's setUpClass that turns a
		# fixture problem into the WHOLE SUITE erroring at class setup.
		print(f"  Session is {doc.status}, not Open — skipped.")
		return
	if any(
		not row.customer and row.customer_name == OPEN_PLAY_WALKIN_NAME
		for row in (doc.participants or [])
	):
		print(f"  Walk-in exists: {OPEN_PLAY_WALKIN_NAME}")
		return

	from court_booking_tech.api.open_play import add_players

	add_players(
		session,
		frappe.as_json(
			[
				{
					"customer_name": OPEN_PLAY_WALKIN_NAME,
					"customer_phone": OPEN_PLAY_WALKIN_PHONE,
					"payment_method": "Cash",
				}
			]
		),
	)
	print(f"  Walk-in joined (cash, no account): {OPEN_PLAY_WALKIN_NAME}")


def _seed_google_login():
	"""Backlog B51: the Google key from `cbt_google_login` in the site config —
	common_site_config.json on dev, which snapshot_reset never restores. No
	pair, no key: the login page simply omits the button."""
	print("\n=== Google login (Backlog B51) ===")
	pair = frappe.conf.get(GOOGLE_CONF_KEY) or {}
	if not (isinstance(pair, dict) and pair.get("client_id") and pair.get("client_secret")):
		print(f"  No {GOOGLE_CONF_KEY} in site config — skipped (docs/PRODUCTION_DEPLOYMENT.md §3.4)")
		return
	status = configure_google(pair["client_id"], pair["client_secret"])
	print(f"  Configured: enabled={status['enabled']} callback={status['callback']}")


def _seed_legal_pages():
	"""Create the legal pages if missing; never overwrites an edited live one."""
	from court_booking_tech.legal import push_legal_pages

	print("\n=== CBT legal pages ===")
	for route, action in push_legal_pages().items():
		print(f"  /{route}: {action}")


def seed_all():
	_guard()
	# The seed is the INSTALLER's act and must not depend on who called it.
	# `bench execute` already runs as Administrator; a test class's setUpClass
	# does not — it inherits whatever seat the previous module's last test
	# ended on, and since Batch 17 (2026-08-28) that is the platform seat, which
	# cannot `get_list("DocType")` for Portal Settings.sync_menu. Measured on
	# the full-suite gate: 4 setUpClass errors in test_slots / test_verification
	# after test_signup / test_tenant_core. Every per-module run was green.
	frappe.set_user("Administrator")
	_seed_platform_admin()
	_seed_companies()
	# A company: seats bind to it below (fresh-site order, section-2).
	_seed_e2e_fast_company()
	# Right after the companies: every booking seeded below resolves its
	# channel through the controller, and an older snapshot's rows are
	# backfilled here before anything reads them.
	_seed_payment_channels()
	_seed_company_users()
	_seed_branches()
	_seed_courts()
	_seed_bgc_layout()
	_seed_qcsm_layout()
	# After the courts: a court-scoped photo links one.
	_seed_company_media()
	_seed_customer()
	_seed_encryption_key()
	_seed_portal_settings()
	# After the encryption key (the client secret is a Password field).
	_seed_google_login()
	_seed_mail_stub()
	_seed_rate_limit_reset()
	_seed_customer_profiles()
	_seed_bookings()
	# After the bookings and BEFORE the customer-facing fixtures: B38 scopes the
	# desk picker by relationship, so a seat can only offer customers its company
	# has dealt with — these are the two the E2E suite always assumed.
	_seed_customer_relationships()
	_seed_proof_bookings()
	_seed_invoices()
	_seed_blocks()
	_seed_open_play_customers()
	_seed_open_play()
	_seed_open_play_walkin()
	# Section-11 — after _seed_invoices: the evasion booking lives on its own
	# date and syncs its own invoice, and memberships must exist before any
	# later fixture books as a member.
	_seed_billing_modes()
	_seed_member_customers()
	_seed_memberships()
	_seed_evasion_booking()
	_seed_walkin_booking()
	_seed_legal_pages()
	_seed_home_ad()
	frappe.db.commit()
	print("\ncourt_booking_tech seed_all complete.")
