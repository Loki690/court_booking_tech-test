"""
Court Booking Tech — the time language (Backlog B45)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_timeutil

The user's question, 2026-09-05: *"DIDN'T I SAY THE FORMATTING SHOULD BE
REUSABLE BEFORE"* and *"WHY NO UNIT TEST SPECIFIC TO THIS?"*

`timeutil.label_short` has carried the ruled Philippine convention since
2026-09-04 (section-6 as-built 15) and was covered only INDIRECTLY, through
`test_billing`'s printed statement. So the rule itself — 6 AM, 6:30 AM, 12 MN,
12 NN — could be changed without a single red line, which is how the desk came
to speak 24-hour at sixteen render sites while the print spoke twelve.

This file is the rule's OWN test. Its table is also the table
`e2e/tests/test_time_language.py` drives through the browser, so the Python and
the JavaScript cannot drift apart: change one and the other goes red.

`_fmt_time`'s 24-hour form is pinned here too, DELIBERATELY — section-6 as-built
15 ruled the transactional emails stay 24-hour, and a rule with no test is the
next thing to be "tidied up".
"""

from datetime import date, time, timedelta

from frappe.tests.utils import FrappeTestCase

from court_booking_tech.timeutil import (
	END_OF_DAY,
	_as_timedelta,
	_fmt,
	_fmt_time,
	_overlaps,
	closing_boundary,
	label_date,
	label_short,
)

# THE RULE, as a table. Every row is the user's own convention, and every
# consumer of it — the statement, the emails' companion label, the desk board,
# the portal grid — must agree with this list exactly.
#
# Both ends of the day land on the midnight branch on purpose: an opening
# 00:00:00 and a closing 24:00:00 both reduce to hour 0, and both mean midnight.
SHORT_LABELS = [
	("00:00:00", "12 MN"),
	("00:30:00", "12:30 AM"),
	("06:00:00", "6 AM"),
	("06:30:00", "6:30 AM"),
	("09:00:00", "9 AM"),
	("11:59:00", "11:59 AM"),
	("12:00:00", "12 NN"),
	("12:30:00", "12:30 PM"),
	("13:00:00", "1 PM"),
	("13:45:00", "1:45 PM"),
	("22:00:00", "10 PM"),
	("23:59:00", "11:59 PM"),
	# A 23:59 closing means MIDNIGHT, and the last slot's end_time is stored as
	# the string "24:00:00" (S4 as-built 9 / 2026-09-02). It must read 12 MN,
	# never 12 NN — the `% 24` in label_short is the whole reason for this row,
	# and www/cbt-my-bookings.html shipped a fork WITHOUT it, printing a
	# midnight-ending booking as "12:00 PM" until B45.
	("24:00:00", "12 MN"),
]


class TestTimeLabels(FrappeTestCase):
	def test_label_short_speaks_the_ruled_convention(self):
		for value, expected in SHORT_LABELS:
			self.assertEqual(label_short(value), expected, value)

	def test_label_short_takes_every_shape_a_time_field_arrives_in(self):
		"""A Time field reaches callers as a timedelta, a datetime.time or a
		string depending on the path (doc field, db row, JSON payload). The one
		that has bitten twice is timedelta(0): FALSY, and perfectly valid."""
		self.assertEqual(label_short(timedelta(0)), "12 MN")
		self.assertEqual(label_short(time(6, 0)), "6 AM")
		self.assertEqual(label_short(time(18, 30)), "6:30 PM")
		self.assertEqual(label_short(timedelta(hours=13)), "1 PM")
		self.assertEqual(label_short("6:30:00"), "6:30 AM")
		# frappe renders a Time with str(timedelta), which drops the leading
		# zero — "7:30:00", not "07:30:00" (data.py format_timedelta).
		self.assertEqual(label_short("7:30:00"), "7:30 AM")
		self.assertEqual(label_short("07:30"), "7:30 AM")

	def test_midnight_and_noon_are_never_am_or_pm(self):
		"""MN and NN are the pair that makes each other legible — "12 AM" and
		"12 PM" are the two labels people actually misread, which is why the
		user ruled both."""
		self.assertNotIn("AM", label_short("00:00:00"))
		self.assertNotIn("PM", label_short("12:00:00"))
		self.assertEqual(label_short("00:00:00"), "12 MN")
		self.assertEqual(label_short("12:00:00"), "12 NN")

	def test_label_date_spells_the_month_out(self):
		"""User ruling 2026-09-04: month-first and named, so a date can never be
		read day-first or month-first by mistake. 09-05-2026 is exactly the
		ambiguity this removes — it is what the desk's Date CONTROL still shows."""
		self.assertEqual(label_date("2026-09-03"), "Sep-03-2026")
		self.assertEqual(label_date(date(2027, 1, 15)), "Jan-15-2027")
		self.assertEqual(label_date("2026-12-31"), "Dec-31-2026")

	def test_the_emails_keep_24_hour_time(self):
		"""Section-6 as-built 15 ruled this: label_short took over the STATEMENT,
		and _fmt_time stayed for the transactional emails. Pinned so the next
		sweep does not "finish the job" nobody asked for."""
		self.assertEqual(_fmt_time("06:00:00"), "06:00")
		self.assertEqual(_fmt_time("13:00:00"), "13:00")
		self.assertEqual(_fmt_time(timedelta(0)), "00:00")
		# No `% 24` here, and that is correct: an email line reads
		# "22:00-24:00", which is unambiguous in 24-hour form.
		self.assertEqual(_fmt_time("24:00:00"), "24:00")


class TestTimePrimitives(FrappeTestCase):
	def test_closing_boundary_reads_2359_as_midnight(self):
		self.assertEqual(closing_boundary("23:59:00"), END_OF_DAY)
		self.assertEqual(closing_boundary("23:59:59"), END_OF_DAY)
		self.assertEqual(closing_boundary("22:00:00"), timedelta(hours=22))
		self.assertEqual(closing_boundary(timedelta(0)), timedelta(0))

	def test_fmt_round_trips_a_midnight_end(self):
		"""_fmt is what writes the "24:00:00" string the booking controller
		re-stamps on every save; str(timedelta) would write "1 day, 0:00:00"
		and MariaDB rejects it (1292)."""
		self.assertEqual(_fmt(END_OF_DAY), "24:00:00")
		self.assertEqual(_fmt(timedelta(hours=6, minutes=30)), "06:30:00")
		self.assertEqual(_as_timedelta(_fmt(END_OF_DAY)), END_OF_DAY)

	def test_overlaps_is_half_open(self):
		"""[a_start, a_end) x [b_start, b_end): two slots that merely TOUCH do
		not overlap, or every consecutive booking would collide."""
		hour = timedelta(hours=1)
		self.assertFalse(_overlaps(timedelta(hours=6), timedelta(hours=7),
		                          timedelta(hours=7), timedelta(hours=8)))
		self.assertTrue(_overlaps(timedelta(hours=6), timedelta(hours=8),
		                         timedelta(hours=7), timedelta(hours=7) + hour))
		# A midnight end is end > start, which is what keeps the last slot of
		# the day overlappable at all.
		self.assertTrue(_overlaps(timedelta(hours=23), END_OF_DAY,
		                         timedelta(hours=23, minutes=30), END_OF_DAY))
