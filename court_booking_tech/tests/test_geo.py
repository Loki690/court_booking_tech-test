"""
Court Booking Tech — Geo Engine (section-3)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_geo

Haversine math against known Metro-Manila distances, the _is_open weekly-hours
helper with FIXED datetimes (wall-clock-independent — honors the seeded
Timog-Sunday-CLOSED case), and the guest-safe get_branches listing API:
ordering, filters, suspension, and the exact-payload leak assertion.
"""

from datetime import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.geo import (
	BRANCH_PAYLOAD_KEYS,
	DAY_ORDER,
	_is_open,
	branch_status,
	get_branches,
	haversine_km,
	hours_summary,
	opens_label,
)
from court_booking_tech.seeds.seed_test_data import seed_all

BGC = (14.5507, 121.0494)
MAKATI = (14.5547, 121.0244)
TIMOG = (14.6349, 121.0388)
# A point just off the BGC branch — expected order: bgc, makati, timog, annex.
BGC_ADJACENT = (14.5510, 121.0490)

SEEDED_SLUGS = ["bgc", "makati", "timog", "annex"]

TIMOG_HOURS = [
	{"day": day, "is_open": 0 if day == "Sunday" else 1,
	 "opening_time": None if day == "Sunday" else "06:00:00",
	 "closing_time": None if day == "Sunday" else "22:00:00"}
	for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
]

# Fixed 2026 dates: 2026-07-26 is a Sunday, 2026-07-27 a Monday.
SUNDAY_NOON = datetime(2026, 7, 26, 12, 0)
MONDAY_NOON = datetime(2026, 7, 27, 12, 0)
MONDAY_LATE = datetime(2026, 7, 27, 23, 30)
MONDAY_OPENING = datetime(2026, 7, 27, 6, 0)
MONDAY_CLOSING = datetime(2026, 7, 27, 22, 0)


def _seeded(results):
	"""The seeded branches, in result order (ignores test-created strays)."""
	return [row for row in results if row["branch_slug"] in SEEDED_SLUGS]


class TestGeo(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- haversine -------------------------------------------------------

	def test_haversine_known_distances(self):
		self.assertAlmostEqual(haversine_km(*BGC, *MAKATI), 2.7, delta=0.2)
		self.assertAlmostEqual(haversine_km(*BGC, *TIMOG), 9.4, delta=0.5)

	def test_haversine_zero_distance(self):
		self.assertEqual(haversine_km(*BGC, *BGC), 0.0)

	# --- _is_open (fixed datetimes — never wall-clock) --------------------

	def test_is_open_closed_day(self):
		self.assertFalse(_is_open(TIMOG_HOURS, SUNDAY_NOON))

	def test_is_open_open_window(self):
		self.assertTrue(_is_open(TIMOG_HOURS, MONDAY_NOON))

	def test_is_open_outside_window(self):
		self.assertFalse(_is_open(TIMOG_HOURS, MONDAY_LATE))

	def test_is_open_boundaries(self):
		# Half-open [opening, closing): open AT opening, closed AT closing.
		self.assertTrue(_is_open(TIMOG_HOURS, MONDAY_OPENING))
		self.assertFalse(_is_open(TIMOG_HOURS, MONDAY_CLOSING))

	def test_is_open_empty_table(self):
		self.assertFalse(_is_open([], MONDAY_NOON))
		self.assertFalse(_is_open(None, MONDAY_NOON))

	def test_is_open_midnight_opening(self):
		# Regression (section-4): DB rows carry Time fields as timedelta —
		# a 00:00 opening is timedelta(0), falsy but OPEN. Truthiness checks
		# here read midnight-opening branches as closed.
		from datetime import timedelta as td

		rows = [
			{
				"day": "Monday",
				"is_open": 1,
				"opening_time": td(0),
				"closing_time": td(hours=23, minutes=59),
			}
		]
		self.assertTrue(_is_open(rows, MONDAY_NOON))
		self.assertTrue(_is_open(rows, datetime(2026, 7, 27, 0, 0)))

	# --- get_branches ----------------------------------------------------

	def test_ordering_nearest_first_pinless_last(self):
		rows = _seeded(get_branches(lat=BGC_ADJACENT[0], lng=BGC_ADJACENT[1]))
		self.assertEqual([row["branch_slug"] for row in rows], SEEDED_SLUGS)
		distances = [row["distance_km"] for row in rows]
		self.assertIsNone(distances[-1])  # annex has no pin
		self.assertEqual(distances[:3], sorted(distances[:3]))
		self.assertLess(distances[0], 0.5)  # origin is basically at BGC

	def test_no_origin_alphabetical(self):
		rows = _seeded(get_branches())
		self.assertEqual(
			[row["branch_name"] for row in rows],
			["BGC Courts", "Makati Arena", "QC Annex", "Timog Hub"],
		)
		self.assertTrue(all(row["distance_km"] is None for row in rows))

	def test_court_type_filter(self):
		rows = _seeded(get_branches(court_type="Basketball"))
		self.assertEqual([row["branch_slug"] for row in rows], ["timog"])
		rows = _seeded(get_branches(court_type="Pickleball"))
		self.assertEqual(
			sorted(row["branch_slug"] for row in rows), ["bgc", "timog"]
		)

	def test_price_from_and_types(self):
		by_slug = {row["branch_slug"]: row for row in _seeded(get_branches())}
		self.assertEqual(by_slug["bgc"]["price_from"], 400)
		self.assertEqual(by_slug["timog"]["price_from"], 350)
		self.assertEqual(by_slug["timog"]["court_types"], ["Basketball", "Pickleball"])
		self.assertEqual(by_slug["annex"]["court_types"], ["Multi-purpose"])

	def test_suspended_company_excluded(self):
		frappe.db.set_value("CBT Company", "qc-smash", "status", "Suspended")
		self.addCleanup(
			frappe.db.set_value, "CBT Company", "qc-smash", "status", "Active"
		)
		slugs = [row["branch_slug"] for row in _seeded(get_branches())]
		self.assertEqual(slugs, ["bgc", "makati"])

	def test_inactive_branch_excluded(self):
		frappe.db.set_value("CBT Branch", "AYALA-makati", "is_active", 0)
		self.addCleanup(
			frappe.db.set_value, "CBT Branch", "AYALA-makati", "is_active", 1
		)
		slugs = [row["branch_slug"] for row in _seeded(get_branches())]
		self.assertNotIn("makati", slugs)

	def test_guest_payload_is_leak_free(self):
		frappe.set_user("Guest")
		rows = get_branches(lat=BGC_ADJACENT[0], lng=BGC_ADJACENT[1])
		self.assertTrue(rows)
		for row in rows:
			self.assertEqual(set(row.keys()), set(BRANCH_PAYLOAD_KEYS))
			for value in row.values():
				if isinstance(value, str):
					self.assertNotIn(
						"@", value, f"email-like value leaked to guests: {value}"
					)

	def test_the_seeded_cards_carry_the_ruled_strings(self):
		"""Backlog B48 — the VALUES, because the key-set test above compares the
		payload to the constant that builds it and cannot fail on a blank."""
		# `main` is the slug of BOTH chan-vat and e2e-fast — a slug-only key
		# returned whichever sorted last and asserted the other one's hours.
		by_slug = {(row["company_slug"], row["branch_slug"]): row for row in get_branches()}
		self.assertEqual(by_slug[("ayala-courts", "bgc")]["hours_summary"], "Daily 6 AM – 10 PM")
		self.assertEqual(by_slug[("ayala-courts", "makati")]["hours_summary"], "Daily 6 AM – 10 PM")
		self.assertEqual(
			by_slug[("qc-smash", "timog")]["hours_summary"], "Mon–Sat 6 AM – 10 PM · Sun closed"
		)
		self.assertEqual(by_slug[("e2e-fast", "main")]["hours_summary"], "24/7")
		for row in by_slug.values():
			self.assertIn("status_label", row)
			# Live clock here, so only the PAIR is pinned: open <=> "Book now".
			self.assertEqual(row["open_now"], row["status_label"] == "Book now", row)
			if not row["open_now"]:
				self.assertTrue(row["status_label"].startswith("Opens "), row)


# ---- Backlog B48: the card's strings, on fixed rows and fixed clocks --------

def _week(windows):
	"""Seven rows from {day: ("HH:MM:SS", "HH:MM:SS") | None}."""
	rows = []
	for day in DAY_ORDER:
		window = windows.get(day)
		rows.append(
			{
				"day": day,
				"is_open": 1 if window else 0,
				"opening_time": window[0] if window else None,
				"closing_time": window[1] if window else None,
			}
		)
	return rows


WEEKDAYS_ = DAY_ORDER[:5]
TIMOG_WEEK = _week({**{d: ("06:00:00", "22:00:00") for d in DAY_ORDER[:6]}, "Sunday": None})

# 2026-07-27 is a Monday; 2026-08-01 a Saturday; 2026-07-26 a Sunday.
MONDAY_0530 = datetime(2026, 7, 27, 5, 30)
MONDAY_2300 = datetime(2026, 7, 27, 23, 0)
SATURDAY_2300 = datetime(2026, 8, 1, 23, 0)
LAST_MINUTE = datetime(2026, 7, 27, 23, 59, 30)


class TestHoursSummary(FrappeTestCase):
	"""The week in one line — the user's own examples, 2026-09-09."""

	def test_a_00_00_to_23_59_week_is_24_7(self):
		rows = _week({d: ("00:00:00", "23:59:00") for d in DAY_ORDER})
		self.assertEqual(hours_summary(rows), "24/7")

	def test_the_same_hours_every_day_read_daily(self):
		rows = _week({d: ("06:00:00", "22:00:00") for d in DAY_ORDER})
		self.assertEqual(hours_summary(rows), "Daily 6 AM – 10 PM")

	def test_consecutive_days_group_and_the_week_wraps(self):
		"""The user's example: Sun–Thu 8am–9pm, Fri–Sat 8am–12MN, with the
		midnight close stored as 11:59 PM. Sunday joins Monday's group across
		the wrap, and 23:59 prints 12 MN."""
		rows = _week(
			{
				**{d: ("08:00:00", "21:00:00") for d in WEEKDAYS_[:4]},
				"Friday": ("08:00:00", "23:59:00"),
				"Saturday": ("08:00:00", "23:59:00"),
				"Sunday": ("08:00:00", "21:00:00"),
			}
		)
		self.assertEqual(
			hours_summary(rows), "Sun–Thu 8 AM – 9 PM · Fri–Sat 8 AM – 12 MN"
		)

	def test_a_closed_day_is_named(self):
		self.assertEqual(hours_summary(TIMOG_WEEK), "Mon–Sat 6 AM – 10 PM · Sun closed")

	def test_a_lone_day_prints_alone_and_noon_is_nn(self):
		rows = _week(
			{
				**{d: ("06:00:00", "22:00:00") for d in WEEKDAYS_},
				"Saturday": ("08:00:00", "12:00:00"),
				"Sunday": None,
			}
		)
		self.assertEqual(
			hours_summary(rows), "Mon–Fri 6 AM – 10 PM · Sat 8 AM – 12 NN · Sun closed"
		)

	def test_half_hours_keep_their_minutes(self):
		rows = _week({d: ("06:30:00", "21:30:00") for d in DAY_ORDER})
		self.assertEqual(hours_summary(rows), "Daily 6:30 AM – 9:30 PM")

	def test_no_rows_and_all_closed_read_closed(self):
		self.assertEqual(hours_summary([]), "Closed")
		self.assertEqual(hours_summary(None), "Closed")
		self.assertEqual(hours_summary(_week({})), "Closed")


class TestBranchStatus(FrappeTestCase):
	"""`Book now` / `Opens …`, on fixed clocks — never the wall clock."""

	def test_open_now_reads_book_now(self):
		status = branch_status(TIMOG_WEEK, MONDAY_NOON)
		self.assertTrue(status["open_now"])
		self.assertEqual(status["status_label"], "Book now")
		self.assertEqual(status["hours_summary"], "Mon–Sat 6 AM – 10 PM · Sun closed")

	def test_before_opening_today(self):
		self.assertEqual(opens_label(TIMOG_WEEK, MONDAY_0530), "Opens 6 AM")
		self.assertEqual(branch_status(TIMOG_WEEK, MONDAY_0530)["status_label"], "Opens 6 AM")

	def test_after_closing_reads_tomorrow(self):
		self.assertEqual(opens_label(TIMOG_WEEK, MONDAY_2300), "Opens tomorrow 6 AM")

	def test_a_closed_day_names_the_next_open_day(self):
		# Saturday night: Sunday is closed, so Monday is named.
		self.assertEqual(opens_label(TIMOG_WEEK, SATURDAY_2300), "Opens Mon 6 AM")
		# Sunday noon: tomorrow is Monday.
		self.assertEqual(opens_label(TIMOG_WEEK, SUNDAY_NOON), "Opens tomorrow 6 AM")
		self.assertFalse(branch_status(TIMOG_WEEK, SUNDAY_NOON)["open_now"])

	def test_a_week_with_no_open_day_reads_closed(self):
		self.assertEqual(opens_label([], MONDAY_NOON), "Closed")
		self.assertEqual(branch_status(None, MONDAY_NOON)["status_label"], "Closed")

	def test_the_last_minute_before_a_midnight_close_is_still_open(self):
		"""Section-4's accepted edge, closed: a 23:59 closing is midnight for
		the badge exactly as it is for the grid."""
		rows = _week({d: ("00:00:00", "23:59:00") for d in DAY_ORDER})
		self.assertTrue(_is_open(rows, LAST_MINUTE))
		self.assertEqual(branch_status(rows, LAST_MINUTE)["status_label"], "Book now")
