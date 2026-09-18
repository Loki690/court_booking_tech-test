"""
Court Booking Tech — Platform / company reports (section-11)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_reports

Every number here is HAND-COMPUTED and peso-pinned (house rule: a money report
asserted with "> 0" proves nothing). Each test module owns its own month so the
ground truth cannot be polluted by a sibling's fixtures — memberships own June
2027, bans own July, reports own AUGUST 2027, and the seeds live in January and
April.

August 2027 ground truth built by setUpClass:

  AYALA (Percentage, 10% commission)
    makati-court-a  06 Aug 10:00  Cash  ₱300           -> Paid & Verified
    makati-court-b  06 Aug 10:00  Cash  ₱300           -> Paid & Verified
    bgc-court-1     13 Aug 08:00  Cash  2 × ₱400 = ₱800 -> Paid & Verified
                                        (also the occupancy fixture)
    revenue 1400.00  amount due 140.00
    NOTE: the occupancy fixture is deliberately left IN the revenue month.
    It is real confirmed money and a facility's revenue report would include
    it — moving it somewhere convenient would be fitting the world to the
    test. The per-branch/day test below still pins makati's 06 Aug ₱600.

  QCSM (Subscription, ₱2999 flat)
    timog-court-1   06 Aug 10:00  Cash          ₱350 -> Paid & Verified
    timog-court-1   06 Aug 14:00  FT + proof    ₱350 -> Expired -> RETRO-CONFIRMED
                                                     (Completed => revenue, NOT evasion)
    timog-court-2   06 Aug 12:00  FT + proof    ₱350 -> Expired  (the evasion signal)
    revenue 700.00   amount due 2999.00 (flat, independent of revenue)
    expired-with-proof: 1 booking, ₱350.00

MONTH CLOSE (Backlog B21(b), 2026-08-27 — `TestMonthClose`): the SAME August
2027 ground truth, frozen into a `CBT Platform Month Close` and then contradicted
by a new paid booking in the closed month. The report must keep showing the
frozen figures while the live computation moves — that is the whole property
the row asked for. The close is deleted after every test, so the module's
ground truth stays live for every other class.
"""

from datetime import date, datetime
from unittest.mock import patch

import frappe
from frappe.client import get as client_get
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from court_booking_tech.api.bookings import confirm_booking
from court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close import (
	close_month,
	get_close_status,
)
from court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement import (
	cancel_statement,
	issue_statements,
	mark_paid,
)
from court_booking_tech.court_booking_tech.report.cbt_company_revenue import (
	cbt_company_revenue,
)
from court_booking_tech.court_booking_tech.report.cbt_occupancy import cbt_occupancy
from court_booking_tech.court_booking_tech.report.cbt_platform_revenue import (
	cbt_platform_revenue,
)
from court_booking_tech.seeds.seed_test_data import (
	PLATFORM_ADMIN_EMAIL,
	_attach_proof,
	seed_all,
)

CLOCK = "court_booking_tech.clock.now_dt"
# A pretend "now" AFTER August 2027 ended, and one INSIDE it.
AFTER_AUGUST = datetime(2027, 9, 15, 10, 0, 0)
DURING_AUGUST = datetime(2027, 8, 15, 10, 0, 0)

AYALA = "ayala-courts"
QCSM = "qc-smash"

MIA = "cust.mia@example.com"
PIA = "cust.pia@example.com"
STELLA = "staff.ayala@example.com"  # AYALA staff
QUINTIN = "admin.qcsm@example.com"  # QCSM admin

REVENUE_DATE = "2027-08-06"  # Friday
OCCUPANCY_DATE = "2027-08-13"  # Friday
MONTH, YEAR = 8, 2027

MAKATI_A = "AYALA-makati-court-a"  # ₱300/hr
MAKATI_B = "AYALA-makati-court-b"  # ₱300/hr
BGC_1 = "AYALA-bgc-court-1"  # ₱400/hr
QCSM_1 = "QCSM-timog-court-1"  # ₱350/hr
QCSM_2 = "QCSM-timog-court-2"  # ₱350/hr

_FIXTURES = []


def _book(court, date, start_time, payment_method="Cash", slots=1, customer=PIA):
	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": customer,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": slots,
			"payment_method": payment_method,
			# Pinned: these are revenue ground-truth fixtures and must not move
			# if the membership cast ever changes.
			"discount_percent": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	_FIXTURES.append(doc.name)
	return doc


class ReportTestCase(FrappeTestCase):
	"""Fixtures are built ONCE for the module and torn down at the end.

	Deliberately not per-test: every test reads the same month, so rebuilding
	would only add runtime and risk drift between the tests' expectations.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()
		frappe.set_user("Administrator")
		if _FIXTURES:
			return

		# --- AYALA: two confirmed cash bookings (₱300 each) -------------------
		_book(MAKATI_A, REVENUE_DATE, "10:00:00")
		_book(MAKATI_B, REVENUE_DATE, "10:00:00")
		# Occupancy fixture: 2 slot-hours on a 3-court branch.
		_book(BGC_1, OCCUPANCY_DATE, "08:00:00", slots=2)

		# --- QCSM: one confirmed cash booking (₱350) --------------------------
		_book(QCSM_1, REVENUE_DATE, "10:00:00")

		# --- QCSM: the evasion signal — Expired, but a proof was uploaded -----
		evaded = _book(QCSM_2, REVENUE_DATE, "12:00:00", payment_method="Fund Transfer")
		_attach_proof(evaded.name, "Customer", PIA)
		frappe.db.set_value(
			"CBT Court Booking", evaded.name, "booking_status", "Expired"
		)

		# --- QCSM: a RETRO-CONFIRMED booking — must leave the evasion column --
		# Same shape as the one above (expired with a proof) until staff confirm
		# the money really arrived; then it is Completed and counts as revenue.
		retro = _book(QCSM_1, REVENUE_DATE, "14:00:00", payment_method="Fund Transfer")
		_attach_proof(retro.name, "Customer", PIA)
		frappe.db.set_value(
			"CBT Court Booking", retro.name, "booking_status", "Expired"
		)
		confirm_booking(retro.name)  # -> Completed (PLAN §5a retro-confirm)
		cls._retro = retro.name
		cls._evaded = evaded.name

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in _FIXTURES:
			invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
			for proof in frappe.get_all(
				"CBT Payment Proof", filters={"booking": name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Payment Proof", proof, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
			if invoice:
				frappe.delete_doc(
					"CBT Booking Invoice", invoice, force=True,
					ignore_permissions=True, ignore_missing=True,
				)
		_FIXTURES.clear()
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _platform(self, filters=None):
		"""The report's rows. execute() returns (columns, data, message) since
		B21(b) — the message is the closed-month notice, None while live."""
		_columns, data, _message = cbt_platform_revenue.execute(
			filters or {"month": MONTH, "year": YEAR}
		)
		return data

	def _platform_row(self, company):
		data = self._platform()
		row = next((r for r in data if r["company"] == company), None)
		self.assertIsNotNone(row, f"{company} missing from platform revenue")
		return row


class TestPlatformRevenue(ReportTestCase):
	def test_percentage_company_revenue_and_commission(self):
		row = self._platform_row(AYALA)
		self.assertEqual(row["billing_mode"], "Percentage")
		# makati 300 + makati 300 + bgc (2 × 400) = 1400.
		self.assertEqual(row["confirmed_revenue"], 300.00 + 300.00 + 800.00)
		self.assertEqual(row["commission_percent"], 10.0)
		self.assertEqual(row["amount_due"], 140.00)  # 1400 × 10%

	def test_subscription_company_owes_a_flat_fee_regardless_of_revenue(self):
		row = self._platform_row(QCSM)
		self.assertEqual(row["billing_mode"], "Subscription")
		# 350 cash + 350 retro-confirmed (Completed counts as revenue).
		self.assertEqual(row["confirmed_revenue"], 700.00)
		self.assertEqual(row["amount_due"], 2999.00)

	def test_expired_with_proof_is_the_evasion_signal(self):
		"""A percentage-billed company could take a real transfer and never
		confirm it in-system; the booking dies quietly and the cut is never
		owed. This column is the fingerprint (PLAN §7)."""
		row = self._platform_row(QCSM)
		self.assertEqual(row["expired_with_proof_count"], 1)
		self.assertEqual(row["expired_with_proof_value"], 350.00)

	def test_retro_confirmed_booking_leaves_the_evasion_column(self):
		"""The other direction of the same rule: money that DID get confirmed
		late is revenue, not a red flag. Both directions matter — a column that
		flagged honest retro-confirms would be ignored within a week."""
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", self._retro, "booking_status"),
			"Completed",
		)
		row = self._platform_row(QCSM)
		# Exactly one expired-with-proof booking exists this month, and it is
		# the evaded one — not the retro-confirmed one.
		self.assertEqual(row["expired_with_proof_count"], 1)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", self._evaded, "booking_status"),
			"Expired",
		)

	def test_company_with_no_august_activity_reports_zero(self):
		row = self._platform_row("e2e-fast")
		self.assertEqual(row["confirmed_revenue"], 0.0)
		self.assertEqual(row["expired_with_proof_count"], 0)

	def test_every_company_is_listed_including_inactive_ones(self):
		"""Suspended/offboarded tenant data is retained (PLAN §8l) and a company
		suspended mid-month still owes for what it sold."""
		data = self._platform()
		listed = {row["company"] for row in data if not row.get("is_total_row")}
		self.assertTrue({AYALA, QCSM} <= listed)
		self.assertEqual(len(listed), frappe.db.count("CBT Company"))

	def test_total_row_sums_money_but_never_the_commission_rate(self):
		"""The total is built server-side because frappe's add_total_row sums
		EVERY numeric column — including the commission RATE, which rendered as
		a platform-wide '3.333%' that is not a real number anywhere."""
		data = self._platform()
		rows = [row for row in data if not row.get("is_total_row")]
		total = next(row for row in data if row.get("is_total_row"))

		self.assertIsNone(total["commission_percent"])
		self.assertIsNone(total["subscription_fee"])
		self.assertIsNone(total["billing_mode"])
		self.assertEqual(
			total["confirmed_revenue"], sum(r["confirmed_revenue"] for r in rows)
		)
		self.assertEqual(total["amount_due"], sum(r["amount_due"] for r in rows))
		# AYALA 1400 + QCSM 700; due = 140 (10% of AYALA) + 2999 (QCSM flat).
		self.assertEqual(total["confirmed_revenue"], 2100.00)
		self.assertEqual(total["amount_due"], 3139.00)
		self.assertEqual(total["expired_with_proof_count"], 1)

	def test_a_different_month_sees_none_of_these_fixtures(self):
		data = self._platform({"month": 9, "year": YEAR})
		for row in data:
			self.assertEqual(row["confirmed_revenue"], 0.0)
			self.assertEqual(row["expired_with_proof_count"], 0)

	def test_seeded_april_evasion_fixture_is_visible_in_its_own_month(self):
		"""The seeded demo fixture (QCSM, 2027-04-16) — proves the column works
		on data nobody built inside a test."""
		data = self._platform({"month": 4, "year": 2027})
		row = next(r for r in data if r["company"] == QCSM)
		self.assertEqual(row["expired_with_proof_count"], 1)
		self.assertEqual(row["expired_with_proof_value"], 350.00)

	def test_tenant_staff_cannot_run_the_platform_report(self):
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			cbt_platform_revenue.execute,
			{"month": MONTH, "year": YEAR},
		)


class TestMonthClose(ReportTestCase):
	"""Backlog B21(b): persist a monthly snapshot so a CLOSED month stops moving.

	Issuing stays manual (that is B21(a), the section-shaped half). What ships
	here is the freeze: a `CBT Platform Month Close` per month, rows copied from
	the report's own computation at close time, read back by the report instead
	of recomputed while the close exists. Delete the close = reopen the month.
	"""

	PERIOD = "2027-08"

	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(self._drop_close)

	def _drop_close(self):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Platform Month Close",
			self.PERIOD,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)

	def _close(self, when=AFTER_AUGUST, year=YEAR, month=MONTH):
		with patch(CLOCK, return_value=when):
			return close_month(year, month)

	def _row(self, doc, company):
		return next(row for row in doc.rows if row.company == company)

	def _drop_booking(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc(
			"CBT Court Booking", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		if name in _FIXTURES:
			_FIXTURES.remove(name)

	def test_closing_a_month_freezes_its_figures(self):
		"""THE property: after the close, a new paid booking in August changes
		the LIVE computation and not the report. Peso-pinned on both sides so a
		close that quietly recomputed would fail here, not pass by luck."""
		self.assertEqual(self._platform_row(AYALA)["confirmed_revenue"], 1400.00)

		result = self._close()
		self.assertEqual(result["name"], self.PERIOD)
		doc = frappe.get_doc("CBT Platform Month Close", self.PERIOD)
		self.assertEqual((doc.year, doc.month), (YEAR, MONTH))
		self.assertEqual(doc.closed_by, "Administrator")
		self.assertTrue(doc.closed_at)
		ayala = self._row(doc, AYALA)
		self.assertEqual(ayala.confirmed_revenue, 1400.00)
		self.assertEqual(ayala.amount_due, 140.00)
		self.assertEqual(ayala.billing_mode, "Percentage")
		qcsm = self._row(doc, QCSM)
		self.assertEqual(qcsm.confirmed_revenue, 700.00)
		self.assertEqual(qcsm.amount_due, 2999.00)
		self.assertEqual(qcsm.expired_with_proof_count, 1)
		self.assertEqual(qcsm.expired_with_proof_value, 350.00)
		self.assertEqual(len(doc.rows), frappe.db.count("CBT Company"))
		self.assertEqual(doc.total_confirmed_revenue, 2100.00)
		self.assertEqual(doc.total_amount_due, 3139.00)

		# The month moves underneath the close: ₱300 more of AYALA cash.
		late = _book(MAKATI_A, "2027-08-20", "10:00:00")
		self.addCleanup(self._drop_booking, late.name)
		live = cbt_platform_revenue.compute_rows(YEAR, MONTH)
		self.assertEqual(
			next(r for r in live if r["company"] == AYALA)["confirmed_revenue"], 1700.00
		)
		# ...and the report does NOT.
		_columns, data, message = cbt_platform_revenue.execute(
			{"month": MONTH, "year": YEAR}
		)
		row = next(r for r in data if r["company"] == AYALA)
		self.assertEqual(row["confirmed_revenue"], 1400.00)
		self.assertEqual(row["amount_due"], 140.00)
		self.assertIn("Closed", message)
		self.assertIn("Administrator", message)
		total = next(r for r in data if r.get("is_total_row"))
		self.assertEqual(total["confirmed_revenue"], 2100.00)
		self.assertEqual(total["amount_due"], 3139.00)

	def test_a_live_month_carries_no_message(self):
		_columns, _data, message = cbt_platform_revenue.execute(
			{"month": MONTH, "year": YEAR}
		)
		self.assertIsNone(message)
		self.assertEqual(get_close_status(YEAR, MONTH), {"closed": False})

	def test_a_month_cannot_close_before_it_ends(self):
		"""Freezing half a month would freeze a lie — and the refusal must name
		the first day it WOULD be allowed (the day after the last day), not
		the last day itself (ducky, 2026-08-27)."""
		from frappe.utils import formatdate

		with self.assertRaises(frappe.ValidationError) as caught:
			self._close(when=DURING_AUGUST)
		self.assertIn("2027-08", str(caught.exception))
		self.assertIn(formatdate("2027-09-01"), str(caught.exception))
		self.assertNotIn(formatdate("2027-08-31"), str(caught.exception))
		# A month that has not even started. Its own cleanup, so a regression
		# here can never leave December 2027 frozen on the site.
		self.addCleanup(
			frappe.delete_doc, "CBT Platform Month Close", "2027-12", force=True,
			ignore_permissions=True, ignore_missing=True,
		)
		with self.assertRaises(frappe.ValidationError):
			self._close(month=12)
		self.assertFalse(frappe.db.exists("CBT Platform Month Close", self.PERIOD))
		self.assertFalse(frappe.db.exists("CBT Platform Month Close", "2027-12"))

	def test_frozen_columns_are_the_reports_columns(self):
		"""One column list (the report's ROW_FIELDS) feeds the freeze; the
		child doctype must be able to hold every one of them, or a column added
		to the report would be frozen as NULL with nothing going red."""
		from court_booking_tech.court_booking_tech.report.cbt_platform_revenue.cbt_platform_revenue import (
			ROW_FIELDS,
		)

		meta = frappe.get_meta("CBT Platform Month Close Row")
		fields = {df.fieldname for df in meta.fields}
		self.assertTrue(set(ROW_FIELDS) <= fields, set(ROW_FIELDS) - fields)
		# ...and the report renders exactly those columns, so a closed month
		# shows nothing blank.
		columns = {c["fieldname"] for c in cbt_platform_revenue._columns()}
		self.assertEqual(columns, set(ROW_FIELDS))

	def test_a_month_closes_once(self):
		self._close()
		with self.assertRaises(frappe.ValidationError) as caught:
			self._close()
		self.assertIn("already closed", str(caught.exception))

	def test_a_close_is_frozen_until_deleted(self):
		"""Editing a figure on a close is refused; deleting the close reopens
		the month, and it can then be closed again."""
		self._close()
		doc = frappe.get_doc("CBT Platform Month Close", self.PERIOD)
		self._row(doc, AYALA).amount_due = 1.00
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save()
		self.assertIn("frozen", str(caught.exception))
		self.assertEqual(
			self._row(frappe.get_doc("CBT Platform Month Close", self.PERIOD), AYALA).amount_due,
			140.00,
		)

		self._drop_close()
		self.assertEqual(get_close_status(YEAR, MONTH), {"closed": False})
		_columns, _data, message = cbt_platform_revenue.execute(
			{"month": MONTH, "year": YEAR}
		)
		self.assertIsNone(message)
		self._close()  # re-closable after a reopen
		self.assertTrue(frappe.db.exists("CBT Platform Month Close", self.PERIOD))

	def test_close_status_names_the_close(self):
		self._close()
		status = get_close_status(YEAR, MONTH)
		self.assertTrue(status["closed"])
		self.assertEqual(status["name"], self.PERIOD)
		self.assertEqual(status["closed_by"], "Administrator")
		self.assertTrue(status["closed_at"])

	def test_only_platform_scope_may_close_read_or_ask(self):
		"""A tenant admin sees nothing of this — B21(a) is where tenants get a
		readable statement; a raw close of every tenant's figures is not it."""
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			self._close()
		self.assertRaises(frappe.PermissionError, get_close_status, YEAR, MONTH)
		self.assertRaises(
			frappe.PermissionError, frappe.get_list, "CBT Platform Month Close"
		)
		self.assertFalse(frappe.db.exists("CBT Platform Month Close", self.PERIOD))

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self._close()
		self.assertEqual(
			frappe.db.get_value("CBT Platform Month Close", self.PERIOD, "closed_by"),
			PLATFORM_ADMIN_EMAIL,
		)

		# The CHILD rows carry every tenant's figures and the child doctype has
		# no DocPerms of its own; frappe gates a child list on its PARENT's
		# read permission, and that is the one isolation property this doctype
		# creates — so it is pinned here rather than assumed (ducky 2026-08-27).
		frappe.set_user(QUINTIN)
		self.assertRaises(
			frappe.PermissionError,
			frappe.get_list,
			"CBT Platform Month Close Row",
			parent_doctype="CBT Platform Month Close",  # v16's child-table gate kwarg
			filters={"parent": self.PERIOD},
			fields=["company", "amount_due"],
		)


class TestPlatformStatements(ReportTestCase):
	"""Backlog B21(a) — the statement issued from the close (2026-08-27).

	Same August 2027 ground truth: AYALA owes ₱140 (10% of ₱1,400), QCSM owes
	its flat ₱2,999. Every test closes the month, issues, and tears BOTH down
	(statements first — the close refuses to go while a live one exists).
	"""

	PERIOD = "2027-08"
	STATEMENT = "CBT Platform Statement"

	def setUp(self):
		frappe.set_user("Administrator")
		# addCleanup is LIFO: statements are dropped BEFORE the close.
		self.addCleanup(self._drop_close)
		self.addCleanup(self._drop_statements)

	def _drop_statements(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all(self.STATEMENT, filters={"period": self.PERIOD}, pluck="name"):
			frappe.delete_doc(
				self.STATEMENT, name, force=True, ignore_permissions=True, ignore_missing=True
			)

	def _drop_close(self):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Platform Month Close",
			self.PERIOD,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)

	def _drop_booking(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc(
			"CBT Court Booking", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		if name in _FIXTURES:
			_FIXTURES.remove(name)

	def _close(self, when=AFTER_AUGUST):
		with patch(CLOCK, return_value=when):
			return close_month(YEAR, MONTH)

	def _issue(self, when=AFTER_AUGUST):
		with patch(CLOCK, return_value=when):
			return issue_statements(self.PERIOD)

	def _live(self, company):
		name = frappe.db.get_value(
			self.STATEMENT,
			{"period": self.PERIOD, "company": company, "status": ("in", ("Issued", "Paid"))},
			"name",
		)
		self.assertTrue(name, f"no live statement for {company}")
		return frappe.get_doc(self.STATEMENT, name)

	@staticmethod
	def _seq(name) -> int:
		return int(name.rsplit("-", 1)[1])

	def test_issue_copies_the_frozen_figures_per_tenant(self):
		"""One numbered statement per tenant, carrying the close's figures and
		nothing computed live; a second issue changes nothing."""
		self._close()
		result = self._issue()
		# Derived from the close, not hard-coded: the seeds carry a third tenant
		# (the E2E fixture company) that owes nothing in August 2027.
		owing = {
			row.company
			for row in frappe.get_doc("CBT Platform Month Close", self.PERIOD).rows
			if row.amount_due > 0
		}
		self.assertEqual(owing, {AYALA, QCSM}, owing)
		self.assertEqual(
			{frappe.db.get_value(self.STATEMENT, n, "company") for n in result["issued"]},
			owing,
		)
		self.assertEqual(result["already_issued"], [])
		self.assertNotIn(AYALA, result["nothing_due"])
		self.assertNotIn(QCSM, result["nothing_due"])

		ayala = self._live(AYALA)
		ayala_code = frappe.db.get_value("CBT Company", AYALA, "company_code")
		self.assertRegex(ayala.name, rf"^PST-{ayala_code}-2027-\d{{5}}$")
		self.assertEqual(ayala.company_name, frappe.db.get_value("CBT Company", AYALA, "company_name"))
		self.assertEqual(ayala.period, self.PERIOD)
		self.assertEqual(getdate(ayala.statement_date), date(2027, 9, 15))
		self.assertEqual(ayala.status, "Issued")
		self.assertEqual(ayala.issued_by, "Administrator")
		self.assertEqual(ayala.billing_mode, "Percentage")
		self.assertEqual(ayala.commission_percent, 10.0)
		self.assertEqual(ayala.confirmed_revenue, 1400.00)
		self.assertEqual(ayala.amount_due, 140.00)

		qcsm = self._live(QCSM)
		qcsm_code = frappe.db.get_value("CBT Company", QCSM, "company_code")
		self.assertRegex(qcsm.name, rf"^PST-{qcsm_code}-2027-\d{{5}}$")
		self.assertEqual(qcsm.billing_mode, "Subscription")
		self.assertEqual(qcsm.subscription_fee, 2999.00)
		self.assertEqual(qcsm.confirmed_revenue, 700.00)
		self.assertEqual(qcsm.amount_due, 2999.00)

		again = self._issue()
		self.assertEqual(again["issued"], [])
		self.assertEqual(set(again["already_issued"]), owing)
		self.assertEqual(frappe.db.count(self.STATEMENT, {"period": self.PERIOD}), len(owing))

	def test_issuing_needs_a_closed_month(self):
		"""A statement is a copy of a FROZEN figure — a live month has none."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._issue()
		self.assertIn("not a closed month", str(caught.exception))
		with self.assertRaises(frappe.ValidationError):
			issue_statements("")
		self.assertEqual(frappe.db.count(self.STATEMENT, {"period": self.PERIOD}), 0)

	def test_a_tenant_owing_nothing_gets_no_statement(self):
		self._close()
		row = frappe.db.get_value(
			"CBT Platform Month Close Row",
			{"parent": self.PERIOD, "company": AYALA},
			"name",
		)
		# Straight to the row (the close's own validate refuses edits) — the
		# point is what issue does with a zero, not how the zero got there.
		frappe.db.set_value("CBT Platform Month Close Row", row, "amount_due", 0)
		result = self._issue()
		self.assertIn(AYALA, result["nothing_due"])
		self.assertEqual(len(result["issued"]), 1, result)  # QCSM only
		self.assertEqual(self._live(QCSM).amount_due, 2999.00)
		self.assertFalse(
			frappe.db.exists(self.STATEMENT, {"period": self.PERIOD, "company": AYALA})
		)

	def test_a_statement_is_a_snapshot_and_blocks_a_reopen_while_live(self):
		"""The close cannot be deleted under a live statement; once those are
		cancelled the month reopens, moves, re-closes — and the NEW statement
		carries the new figure while the cancelled one keeps the old."""
		self._close()
		self._issue()
		first = self._live(AYALA)
		with self.assertRaises(frappe.ValidationError) as caught:
			frappe.delete_doc("CBT Platform Month Close", self.PERIOD, force=True)
		self.assertIn("cancel them before reopening", str(caught.exception))
		self.assertIn(first.name, str(caught.exception))
		self.assertTrue(frappe.db.exists("CBT Platform Month Close", self.PERIOD))

		cancel_statement(first.name, "issued before the late confirmation")
		cancel_statement(self._live(QCSM).name, "re-issue with the corrected month")
		frappe.delete_doc("CBT Platform Month Close", self.PERIOD, force=True)

		late = _book(MAKATI_A, "2027-08-21", "10:00:00")  # ₱300 more AYALA cash
		self.addCleanup(self._drop_booking, late.name)
		self._close()
		result = self._issue()
		self.assertEqual(len(result["issued"]), 2, result)
		second = self._live(AYALA)
		self.assertEqual(second.confirmed_revenue, 1700.00)
		self.assertEqual(second.amount_due, 170.00)
		self.assertEqual(self._seq(second.name), self._seq(first.name) + 1)
		first.reload()
		self.assertEqual(first.status, "Cancelled")
		self.assertEqual(first.confirmed_revenue, 1400.00)
		self.assertEqual(first.amount_due, 140.00)

	def test_mark_paid_records_the_payment(self):
		self._close()
		self._issue()
		qcsm = self._live(QCSM)
		with patch(CLOCK, return_value=AFTER_AUGUST):
			result = mark_paid(qcsm.name, "2027-09-10", "  GCASH-123 ")
		self.assertEqual(result["status"], "Paid")
		qcsm.reload()
		self.assertEqual(qcsm.status, "Paid")
		self.assertEqual(getdate(qcsm.paid_on), date(2027, 9, 10))
		self.assertEqual(qcsm.payment_reference, "GCASH-123")
		self.assertEqual(qcsm.paid_by, "Administrator")
		self.assertEqual(qcsm.paid_at, AFTER_AUGUST)
		# Frozen figures survive the status flip.
		self.assertEqual(qcsm.amount_due, 2999.00)

		with self.assertRaises(frappe.ValidationError) as caught:
			mark_paid(qcsm.name, "2027-09-11")
		self.assertIn("already paid", str(caught.exception))

		ayala = self._live(AYALA)
		with patch(CLOCK, return_value=AFTER_AUGUST):
			with self.assertRaises(frappe.ValidationError) as caught:
				mark_paid(ayala.name, "2027-09-16")  # the day after pretend-now
			self.assertIn("future", str(caught.exception))
			self.assertRaises(frappe.ValidationError, mark_paid, ayala.name, "")
		ayala.reload()
		self.assertEqual(ayala.status, "Issued")

	def test_cancel_retains_the_number_and_reissue_takes_the_next(self):
		"""PLAN §8e for the platform's own paper: cancelled is kept, its number
		is consumed, the re-issue is the next one — per tenant, so QCSM's
		numbering does not move when AYALA's does."""
		self._close()
		self._issue()
		ayala = self._live(AYALA)
		qcsm = self._live(QCSM)

		with self.assertRaises(frappe.ValidationError) as caught:
			cancel_statement(ayala.name, "   ")
		self.assertIn("reason", str(caught.exception))
		with patch(CLOCK, return_value=AFTER_AUGUST):
			cancel_statement(ayala.name, "wrong rate on file")
		ayala.reload()
		self.assertEqual(ayala.status, "Cancelled")
		self.assertEqual(ayala.cancel_reason, "wrong rate on file")
		self.assertEqual(ayala.cancelled_by, "Administrator")
		self.assertEqual(ayala.cancelled_at, AFTER_AUGUST)
		with self.assertRaises(frappe.ValidationError) as caught:
			cancel_statement(ayala.name, "again")
		self.assertIn("already cancelled", str(caught.exception))
		with self.assertRaises(frappe.ValidationError) as caught:
			mark_paid(ayala.name, "2027-09-10")
		self.assertIn("cancelled", str(caught.exception))

		result = self._issue()
		self.assertEqual(len(result["issued"]), 1, result)
		self.assertEqual(result["already_issued"], [QCSM])
		reissued = self._live(AYALA)
		self.assertNotEqual(reissued.name, ayala.name)
		self.assertEqual(self._seq(reissued.name), self._seq(ayala.name) + 1)
		self.assertTrue(frappe.db.exists(self.STATEMENT, ayala.name))  # retained
		self.assertEqual(self._live(QCSM).name, qcsm.name)  # untouched

		# A PAID statement can be cancelled too — cancel-and-reissue is the
		# only undo for a payment recorded against the wrong statement.
		with patch(CLOCK, return_value=AFTER_AUGUST):
			mark_paid(qcsm.name, "2027-09-02")
		cancel_statement(qcsm.name, "paid against the wrong month")
		self.assertEqual(frappe.db.get_value(self.STATEMENT, qcsm.name, "status"), "Cancelled")

	def test_a_statement_is_frozen_and_never_deleted_below_system_manager(self):
		self._close()
		self._issue()
		doc = self._live(AYALA)
		doc.amount_due = 1.00
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save()
		self.assertIn("frozen", str(caught.exception))
		self.assertEqual(frappe.db.get_value(self.STATEMENT, doc.name, "amount_due"), 140.00)

		by_hand = frappe.get_doc(
			{
				"doctype": self.STATEMENT,
				"company": AYALA,
				"period": self.PERIOD,
				"statement_date": "2027-09-15",
				"amount_due": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			by_hand.insert()
		self.assertIn("cannot be created by hand", str(caught.exception))

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		# ignore_permissions reaches the controller's own guard, which is the
		# thing under test (the DocPerm already withholds delete).
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(self.STATEMENT, doc.name, force=True, ignore_permissions=True)
		self.assertTrue(frappe.db.exists(self.STATEMENT, doc.name))

	def test_tenant_reads_own_statement_only_and_platform_writes(self):
		"""Platform writes, tenant reads — and only its own. Staff and customers
		read nothing (no DocPerm; leak vector 4 for the customer)."""
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self._close()
		result = self._issue()
		self.assertEqual(len(result["issued"]), 2)
		qcsm = self._live(QCSM)
		ayala = self._live(AYALA)
		self.assertEqual(qcsm.issued_by, PLATFORM_ADMIN_EMAIL)
		# The platform seat reads EVERY tenant's statement through the
		# permission stack (get_list, not a db read) — the one platform-side
		# read in this module that is not permission-free.
		self.assertEqual(
			{row.name for row in frappe.get_list(self.STATEMENT, limit_page_length=0)}
			& {qcsm.name, ayala.name},
			{qcsm.name, ayala.name},
		)

		frappe.set_user(QUINTIN)  # QCSM admin
		names = {row.name for row in frappe.get_list(self.STATEMENT, limit_page_length=0)}
		self.assertEqual(names, {qcsm.name})
		self.assertFalse(frappe.get_doc(self.STATEMENT, ayala.name).has_permission("read"))
		self.assertRaises(frappe.PermissionError, client_get, self.STATEMENT, ayala.name)
		mine = client_get(self.STATEMENT, qcsm.name)
		self.assertEqual(mine["amount_due"], 2999.00)
		self.assertRaises(frappe.PermissionError, issue_statements, self.PERIOD)
		self.assertRaises(frappe.PermissionError, mark_paid, qcsm.name, "2027-09-10")
		self.assertRaises(frappe.PermissionError, cancel_statement, qcsm.name, "no")
		self.assertEqual(frappe.db.get_value(self.STATEMENT, qcsm.name, "status"), "Issued")

		for user in (STELLA, "cust.carla@example.com"):
			with self.subTest(user=user):
				frappe.set_user(user)
				self.assertRaises(frappe.PermissionError, frappe.get_list, self.STATEMENT)
				self.assertRaises(frappe.PermissionError, client_get, self.STATEMENT, qcsm.name)

	def test_print_format_is_the_default_and_renders_the_frozen_figures(self):
		"""What the tenant prints: issuer from the platform settings, billed-to
		from the company, every figure from the statement, and the status
		visible in both states — B23's lesson, so the doctype names the format."""
		self.assertEqual(
			frappe.get_meta(self.STATEMENT).default_print_format, "CBT Platform Statement"
		)
		previous = frappe.db.get_single_value("CBT Platform Settings", "platform_legal_name")
		frappe.db.set_single_value("CBT Platform Settings", "platform_legal_name", "Byteunity Test Platform")
		self.addCleanup(
			frappe.db.set_single_value, "CBT Platform Settings", "platform_legal_name", previous
		)
		self._close()
		self._issue()
		qcsm = self._live(QCSM)
		registered = frappe.db.get_value("CBT Company", QCSM, "registered_name")

		html = frappe.get_print(self.STATEMENT, qcsm.name, "CBT Platform Statement")
		for needle in ("STATEMENT OF ACCOUNT", "Byteunity Test Platform", registered,
		               qcsm.name, "August 2027", "2,999.00", "UNPAID"):
			self.assertIn(needle, html, needle)
		self.assertNotIn("CANCELLED", html)

		with patch(CLOCK, return_value=AFTER_AUGUST):
			mark_paid(qcsm.name, "2027-09-10", "GCASH-123")
		html = frappe.get_print(self.STATEMENT, qcsm.name, "CBT Platform Statement")
		self.assertIn("PAID", html)
		self.assertIn("GCASH-123", html)
		self.assertNotIn("UNPAID", html)

		cancel_statement(qcsm.name, "paid against the wrong month")
		html = frappe.get_print(self.STATEMENT, qcsm.name, "CBT Platform Statement")
		self.assertIn("CANCELLED", html)
		self.assertIn("paid against the wrong month", html)
		# The paper that explains a mis-applied payment must still show the
		# payment (ducky, 2026-08-27: the first template hid it with the stamp).
		self.assertIn("GCASH-123", html)
		self.assertNotIn(">PAID<", html)

		# The PERCENTAGE branch — the minority mode, and the one whose printed
		# basis is an arithmetic claim to a paying tenant: revenue × rate = due.
		ayala = self._live(AYALA)
		html = frappe.get_print(self.STATEMENT, ayala.name, "CBT Platform Statement")
		for needle in ("Platform commission", "1,400.00", "10%", "140.00", "UNPAID"):
			self.assertIn(needle, html, needle)
		self.assertNotIn("subscription", html.lower())


class TestCompanyRevenue(ReportTestCase):
	def test_revenue_per_branch_and_day(self):
		_columns, data = cbt_company_revenue.execute(
			{"company": AYALA, "from_date": REVENUE_DATE, "to_date": REVENUE_DATE}
		)
		row = next(r for r in data if r["branch"] == "AYALA-makati")
		self.assertEqual(row["day"], REVENUE_DATE)
		self.assertEqual(row["bookings_count"], 2)
		self.assertEqual(row["bookings_revenue"], 600.00)
		self.assertEqual(row["open_play_revenue"], 0.0)
		self.assertEqual(row["total_revenue"], 600.00)

	def test_qcsm_revenue_counts_the_retro_confirmed_booking(self):
		_columns, data = cbt_company_revenue.execute(
			{"company": QCSM, "from_date": REVENUE_DATE, "to_date": REVENUE_DATE}
		)
		row = next(r for r in data if r["branch"] == "QCSM-timog")
		self.assertEqual(row["bookings_revenue"], 700.00)
		self.assertEqual(row["bookings_count"], 2)

	def test_expired_booking_earns_nothing(self):
		"""The evaded booking is ₱350 of court time that produced no revenue —
		which is exactly why the platform report flags it separately."""
		_columns, data = cbt_company_revenue.execute(
			{"company": QCSM, "from_date": REVENUE_DATE, "to_date": REVENUE_DATE}
		)
		total = sum(r["total_revenue"] for r in data)
		self.assertEqual(total, 700.00)  # not 1050 — the expired one is absent

	def test_tenant_user_is_forced_onto_their_own_company(self):
		"""Report filters are user input; honouring a hand-edited company would
		turn every report into a cross-tenant read."""
		frappe.set_user(QUINTIN)  # QCSM admin asking for AYALA
		_columns, data = cbt_company_revenue.execute(
			{"company": AYALA, "from_date": REVENUE_DATE, "to_date": REVENUE_DATE}
		)
		self.assertTrue(data)
		for row in data:
			self.assertTrue(row["branch"].startswith("QCSM-"), row)

	def test_staff_sees_only_their_own_company(self):
		frappe.set_user(STELLA)
		_columns, data = cbt_company_revenue.execute(
			{"from_date": REVENUE_DATE, "to_date": REVENUE_DATE}
		)
		for row in data:
			self.assertTrue(row["branch"].startswith("AYALA-"), row)

	def test_platform_scope_must_name_a_company(self):
		"""Fail-closed, exactly like the board's pending-payments endpoint: no
		filter must never silently mean 'every tenant'."""
		self.assertRaises(
			frappe.PermissionError,
			cbt_company_revenue.execute,
			{"from_date": REVENUE_DATE, "to_date": REVENUE_DATE},
		)

	def test_inverted_date_range_is_rejected(self):
		self.assertRaises(
			frappe.ValidationError,
			cbt_company_revenue.execute,
			{"company": AYALA, "from_date": REVENUE_DATE, "to_date": "2027-08-01"},
		)


class TestCartAttribution(ReportTestCase):
	"""Backlog B36: one document now bills a whole cart, so revenue is joined
	and summed PER BOOKING. October days of its own — the August ground truth
	above must not move."""

	D_ONE = "2027-10-08"
	D_TWO = "2027-10-09"

	def _cart_row(self, court, day, start, group, payment_method="Cash"):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": court,
				"customer": PIA,
				"booking_date": day,
				"start_time": start,
				"number_of_slots": 1,
				"payment_method": payment_method,
				"discount_percent": 0,
				"booking_group": group,
			}
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._drop, doc.name)
		return doc

	def _drop(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc(
			"CBT Court Booking", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

	def _company_rows(self, from_date, to_date):
		_columns, data = cbt_company_revenue.execute(
			{"company": AYALA, "from_date": from_date, "to_date": to_date}
		)
		return data

	def test_a_cart_across_two_dates_is_attributed_to_both(self):
		"""The join used to be `b.name = i.booking`, which for a group document
		put EVERY peso on the anchor row's date — and a cart straddling a month
		boundary would bill the wrong month's commission."""
		self._cart_row(MAKATI_A, self.D_ONE, "10:00:00", "CART-RPT-DATES")
		self._cart_row(MAKATI_A, self.D_TWO, "10:00:00", "CART-RPT-DATES")

		rows = self._company_rows(self.D_ONE, self.D_TWO)
		by_day = {row["day"]: row for row in rows if row["branch"] == "AYALA-makati"}
		self.assertEqual(set(by_day), {self.D_ONE, self.D_TWO}, by_day)
		for day in (self.D_ONE, self.D_TWO):
			self.assertEqual(by_day[day]["bookings_count"], 1, day)
			self.assertEqual(by_day[day]["bookings_revenue"], 300.00, day)

	def test_a_cancelled_cart_row_never_rides_a_later_payment(self):
		"""A customer drops one row while the cart is still unpaid, then pays
		for the rest. The dropped row keeps its billing_doc — without the
		booking_status filter it would be counted the moment that shared
		document flipped to Paid & Verified."""
		from court_booking_tech.api.bookings import cancel_booking

		kept = self._cart_row(
			MAKATI_A, self.D_ONE, "14:00:00", "CART-RPT-DROP",
			payment_method="Fund Transfer",
		)
		dropped = self._cart_row(
			MAKATI_B, self.D_ONE, "14:00:00", "CART-RPT-DROP",
			payment_method="Fund Transfer",
		)
		cancel_booking(dropped.name)
		confirm_booking(kept.name)

		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", kept.billing_doc, "status"),
			"Paid & Verified",
		)
		row = next(
			r
			for r in self._company_rows(self.D_ONE, self.D_ONE)
			if r["branch"] == "AYALA-makati"
		)
		self.assertEqual(row["bookings_count"], 1)
		self.assertEqual(row["bookings_revenue"], 300.00)


class TestOccupancy(ReportTestCase):
	def test_occupancy_against_hand_computed_capacity(self):
		"""BGC: 3 active courts × 16 one-hour slots (06:00–22:00) = 48 h of
		capacity. One 2-slot booking = 2 h booked = 4.17%."""
		_columns, data = cbt_occupancy.execute(
			{"company": AYALA, "from_date": OCCUPANCY_DATE, "to_date": OCCUPANCY_DATE}
		)
		row = next(r for r in data if r["branch"] == "AYALA-bgc")
		self.assertEqual(row["courts"], 3)
		self.assertEqual(row["available_hours"], 48.0)
		self.assertEqual(row["booked_hours"], 2.0)
		self.assertEqual(row["occupancy_percent"], 4.17)

	def test_branch_with_no_bookings_reports_zero_not_a_crash(self):
		_columns, data = cbt_occupancy.execute(
			{"company": AYALA, "from_date": OCCUPANCY_DATE, "to_date": OCCUPANCY_DATE}
		)
		row = next(r for r in data if r["branch"] == "AYALA-makati")
		self.assertEqual(row["available_hours"], 32.0)  # 2 courts × 16 h
		self.assertEqual(row["booked_hours"], 0.0)
		self.assertEqual(row["occupancy_percent"], 0.0)

	def test_closed_days_are_skipped_never_divided_by(self):
		"""QCSM-timog is closed on Sundays: zero capacity is not 0% occupancy,
		it is a day that should not appear at all."""
		sunday = "2027-08-08"
		_columns, data = cbt_occupancy.execute(
			{"company": QCSM, "from_date": sunday, "to_date": sunday}
		)
		branches = {row["branch"] for row in data}
		self.assertNotIn("QCSM-timog", branches)

	def test_reserved_holds_are_not_counted_as_utilisation(self):
		"""An unpaid hold is not utilisation — it becomes one when it confirms."""
		hold = _book(
			"AYALA-makati-court-a", OCCUPANCY_DATE, "09:00:00",
			payment_method="Fund Transfer",
		)
		self.addCleanup(self._drop, hold.name)
		_columns, data = cbt_occupancy.execute(
			{"company": AYALA, "from_date": OCCUPANCY_DATE, "to_date": OCCUPANCY_DATE}
		)
		row = next(r for r in data if r["branch"] == "AYALA-makati")
		self.assertEqual(row["booked_hours"], 0.0)

	def test_excessive_date_range_is_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			cbt_occupancy.execute,
			{"company": AYALA, "from_date": "2027-01-01", "to_date": "2027-12-31"},
		)

	def test_tenant_user_is_forced_onto_their_own_company(self):
		frappe.set_user(QUINTIN)
		_columns, data = cbt_occupancy.execute(
			{"company": AYALA, "from_date": OCCUPANCY_DATE, "to_date": OCCUPANCY_DATE}
		)
		for row in data:
			self.assertTrue(row["branch"].startswith("QCSM-"), row)

	def _drop(self, name):
		frappe.set_user("Administrator")
		invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
		frappe.delete_doc(
			"CBT Court Booking", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		if invoice:
			frappe.delete_doc(
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		if name in _FIXTURES:
			_FIXTURES.remove(name)


class TestGetMyCompany(FrappeTestCase):
	"""The whitelisted seam behind the reports' tenant company default
	(section-12 UAT finding: a reqd Link whose one pickable value is forced
	server-side anyway). Tests the ENDPOINT, not the tenancy helper, so the
	whitelisted surface is what stays pinned."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_bound_staff_get_their_company(self):
		from court_booking_tech.api.company_users import get_my_company

		frappe.set_user(STELLA)
		self.assertEqual(get_my_company(), AYALA)

	def test_platform_scope_gets_none(self):
		from court_booking_tech.api.company_users import get_my_company

		frappe.set_user("cbt.admin@example.com")
		self.assertIsNone(get_my_company())

	def test_unbound_customer_is_rejected(self):
		from court_booking_tech.api.company_users import get_my_company

		frappe.set_user("cust.carla@example.com")
		self.assertRaises(frappe.PermissionError, get_my_company)
