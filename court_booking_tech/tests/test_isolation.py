"""
Court Booking Tech — Tenant Isolation Matrix (section-2 core)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_isolation

Proves the four leak-vector defenses (PLAN §3): list views AND whitelisted
endpoints are company-scoped, a null company cannot slip through, tenant
doctypes never reach global search, and CBT Customer holds zero DocPerms
(no if_owner anywhere).
"""

import frappe
from frappe.client import get as client_get
from frappe.client import get_list as client_get_list
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"
ALONA = "admin.ayala@example.com"
STELLA = "staff.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"

TENANT_DOCTYPES = [
	"CBT Company",
	"CBT Company User",
	"CBT Branch",
	"CBT Court",
	"CBT Court Booking",
	"CBT Slot Block",
	"CBT Payment Proof",
	"CBT Booking Invoice",
	"CBT Open Play Session",
	"CBT Membership",
	"CBT Customer Ban",
	# This list is the ONLY thing standing between a new tenant doctype and
	# global search, and it is hand-maintained — a doctype left out of it does
	# not turn anything red, it just quietly ships outside the leak-vector-1
	# guard below. Any doctype added to tenancy.py belongs here.
	# Backlog B21(a). The ONE platform-billing document a tenant may read —
	# scoped to their own company like everything else here.
	"CBT Platform Statement",
	# Backlog B29. Platform-written, tenant-read; a Platform-scope row has no
	# company and is therefore invisible to every tenant seat.
	"CBT Payment Channel",
	# Backlog B39. Store credit is spendable only where it was issued.
	"CBT Customer Credit",
]


class TestTenantIsolation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- list views -----------------------------------------------------

	def test_staff_company_list_scoped_to_own_company(self):
		frappe.set_user(STELLA)
		names = {row.name for row in frappe.get_list("CBT Company", limit_page_length=0)}
		self.assertEqual(names, {AYALA})

	def test_admin_company_user_list_scoped_to_own_company(self):
		frappe.set_user(ALONA)
		users = {
			row.user
			for row in frappe.get_list(
				"CBT Company User", fields=["user"], limit_page_length=0
			)
		}
		self.assertTrue(users)
		self.assertTrue(all(u.endswith("ayala@example.com") for u in users), users)

	def test_staff_has_no_read_on_company_user(self):
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError, frappe.get_list, "CBT Company User"
		)

	def test_platform_admin_sees_all_companies(self):
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		names = {row.name for row in frappe.get_list("CBT Company", limit_page_length=0)}
		self.assertTrue({AYALA, QCSM} <= names)

	def test_staff_branch_and_court_lists_scoped(self):
		frappe.set_user(STELLA)
		branches = {row.name for row in frappe.get_list("CBT Branch", limit_page_length=0)}
		self.assertTrue(branches)
		self.assertTrue(all(name.startswith("AYALA-") for name in branches), branches)
		courts = {row.name for row in frappe.get_list("CBT Court", limit_page_length=0)}
		self.assertTrue(courts)
		self.assertTrue(all(name.startswith("AYALA-") for name in courts), courts)

	def test_staff_booking_and_block_lists_scoped(self):
		# Seeds provide AYALA bookings/blocks AND a QCSM block — Stella must
		# see only her company's rows (section-4 doctypes join the matrix).
		frappe.set_user(STELLA)
		bookings = {
			row.company
			for row in frappe.get_list(
				"CBT Court Booking", fields=["company"], limit_page_length=0
			)
		}
		self.assertEqual(bookings, {AYALA})
		blocks = {
			row.company
			for row in frappe.get_list(
				"CBT Slot Block", fields=["company"], limit_page_length=0
			)
		}
		self.assertEqual(blocks, {AYALA})

	def test_staff_invoice_list_scoped_and_cross_read_denied(self):
		# Seeds provide AYALA invoices AND a QCSM (NON-VAT) invoice — the
		# section-6 doctype joins the matrix.
		qcsm_invoice = frappe.db.get_value(
			"CBT Booking Invoice", {"company": QCSM}, "name"
		)
		self.assertTrue(qcsm_invoice)

		frappe.set_user(STELLA)
		companies = {
			row.company
			for row in frappe.get_list(
				"CBT Booking Invoice", fields=["company"], limit_page_length=0
			)
		}
		self.assertEqual(companies, {AYALA})
		self.assertFalse(
			frappe.get_doc("CBT Booking Invoice", qcsm_invoice).has_permission("read")
		)

	# --- document reads -------------------------------------------------

	def test_staff_cannot_read_other_company_doc(self):
		frappe.set_user(STELLA)
		doc = frappe.get_doc("CBT Company", QCSM)
		self.assertFalse(doc.has_permission("read"))

	def test_staff_cannot_read_other_company_branch_or_court(self):
		frappe.set_user(STELLA)
		self.assertFalse(
			frappe.get_doc("CBT Branch", "QCSM-timog").has_permission("read")
		)
		self.assertFalse(
			frappe.get_doc("CBT Court", "QCSM-timog-court-1").has_permission("read")
		)

	# --- whitelisted endpoints (leak vector 3) ---------------------------

	def test_endpoint_get_list_scoped(self):
		frappe.set_user(STELLA)
		rows = client_get_list("CBT Company", fields=["name"], limit_page_length=0)
		self.assertEqual({row["name"] for row in rows}, {AYALA})

	def test_endpoint_get_other_company_denied(self):
		frappe.set_user(STELLA)
		self.assertRaises(frappe.PermissionError, client_get, "CBT Company", QCSM)

	def test_endpoint_branch_scoped_and_denied(self):
		frappe.set_user(STELLA)
		rows = client_get_list("CBT Branch", fields=["name"], limit_page_length=0)
		self.assertTrue(rows)
		self.assertTrue(all(row["name"].startswith("AYALA-") for row in rows), rows)
		self.assertRaises(
			frappe.PermissionError, client_get, "CBT Branch", "QCSM-timog"
		)

	def test_cross_company_booking_and_block_denied(self):
		# One QCSM booking created platform-side; the QCSM block is seeded.
		booking = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": "QCSM-timog-court-2",
				"customer": "cust.carla@example.com",
				"booking_date": "2027-02-12",
				"start_time": "10:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		booking.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Court Booking",
			booking.name,
			force=True,
			ignore_permissions=True,
		)
		qcsm_block = frappe.db.get_value(
			"CBT Slot Block", {"company": QCSM}, "name"
		)
		self.assertTrue(qcsm_block)

		frappe.set_user(STELLA)
		self.assertFalse(
			frappe.get_doc("CBT Court Booking", booking.name).has_permission("read")
		)
		self.assertFalse(
			frappe.get_doc("CBT Slot Block", qcsm_block).has_permission("read")
		)
		self.assertRaises(
			frappe.PermissionError, client_get, "CBT Court Booking", booking.name
		)

	# --- board endpoints (section-7, leak vector 3) -----------------------

	def test_board_endpoints_cross_company_denied(self):
		"""Every section-7 board endpoint joins the matrix: as AYALA staff,
		QCSM data is unreachable through all five."""
		from court_booking_tech.api.board import (
			get_board_data,
			get_booking_detail,
			get_pending_payments,
		)
		from court_booking_tech.api.bookings import (
			check_in,
			create_block,
			create_booking,
			reschedule_booking,
			undo_no_show,
		)

		qcsm_booking = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": "QCSM-timog-court-2",
				"customer": "cust.carla@example.com",
				"booking_date": "2027-02-13",
				"start_time": "10:00:00",
				"number_of_slots": 1,
				"payment_method": "Fund Transfer",
			}
		)
		qcsm_booking.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Court Booking",
			qcsm_booking.name,
			force=True,
			ignore_permissions=True,
		)

		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError, get_board_data, "QCSM-timog", "2027-02-13"
		)
		self.assertRaises(frappe.PermissionError, get_pending_payments, QCSM)
		self.assertRaises(
			frappe.PermissionError, get_booking_detail, qcsm_booking.name
		)
		self.assertRaises(
			frappe.PermissionError,
			create_booking,
			court="QCSM-timog-court-1",
			booking_date="2027-02-13",
			start_time="12:00:00",
			customer="cust.carla@example.com",
			payment_method="Cash",
		)
		self.assertRaises(
			frappe.PermissionError,
			create_block,
			branch="QCSM-timog",
			block_date="2027-02-13",
			start_time="12:00:00",
			end_time="13:00:00",
			reason="Maintenance",
		)
		# Section-15: moving a booking is booking-creating, so it joins the
		# matrix at BOTH ends — the booking being moved and the court it is
		# moved onto each pass through the tenancy choke point.
		self.assertRaises(
			frappe.PermissionError, reschedule_booking, qcsm_booking.name
		)
		# Section-16: both attendance endpoints join the matrix. They sit on
		# opposite sides of the SUSPENSION gate on purpose (check_in allows a
		# suspended company, undo_no_show does not) — but neither relaxes the
		# TENANT gate, which is what this row proves.
		self.assertRaises(frappe.PermissionError, check_in, qcsm_booking.name)
		self.assertRaises(frappe.PermissionError, undo_no_show, qcsm_booking.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		ayala_booking = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": "AYALA-makati-court-a",
				"customer": "cust.carla@example.com",
				"booking_date": "2027-02-13",
				"start_time": "10:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		ayala_booking.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Court Booking",
			ayala_booking.name,
			force=True,
			ignore_permissions=True,
		)
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			reschedule_booking,
			ayala_booking.name,
			court="QCSM-timog-court-1",
		)

	def test_customer_query_denied_for_customers(self):
		"""The quick-book customer search is staff/platform-only — a portal
		customer must not enumerate other customers' emails.

		Section-18 (Backlog B3) pointed the desk booking FORM's Customer link at
		this same endpoint, so this row now guards two surfaces. No new row was
		needed for B3: the form consumes an already-pinned query.
		"""
		from court_booking_tech.api.board import customer_query

		frappe.set_user("cust.carla@example.com")
		self.assertRaises(
			frappe.PermissionError,
			customer_query,
			"User",
			"",
			"name",
			0,
			20,
			{},
		)

	# --- get_quote's staff-gated params (section-18, Backlog B4) -----------

	def test_get_quote_staff_params_are_refused_to_guests_and_customers(self):
		"""B4 made a deliberately GUEST-SAFE endpoint permission-sensitive, and
		this is the row that justifies the gate existing.

		Without it, `customer` is a membership-tier oracle: any logged-in stranger
		could ask "what discount does this email get at that facility?" and read
		another customer's commercial relationship out of a public price quote.
		Both params are checked, and BOTH actors — because the two fail closed by
		different routes (Guest has no binding; a customer has no binding either,
		which is the whole fail-closed doctrine of the tenancy spine).
		"""
		from court_booking_tech.api.portal import get_quote

		court = "AYALA-makati-court-a"
		for user in ("Guest", "cust.carla@example.com"):
			for kwargs in (
				{"customer": "cust.mia@example.com"},
				{"discount_percent": 50},
				# 0 is a REAL value, so it must arm the gate too — a truthiness
				# check here would leave the cheapest probe wide open.
				{"discount_percent": 0},
				# Backlog B15 (2026-08-27): the third staff param. Ungated, a
				# customer could quote — and then argue for — any rate at all.
				{"hourly_rate": 1},
				{"hourly_rate": 0},
				# Backlog B27: the fourth — a customer must not be able to quote
				# their own booking with the platform fee waived.
				{"platform_fee": 15},
				{"platform_fee": 0},
			):
				with self.subTest(user=user, kwargs=kwargs):
					frappe.set_user(user)
					self.assertRaises(
						frappe.PermissionError, get_quote, court, 1, **kwargs
					)

	def test_get_quote_staff_params_are_refused_across_tenants(self):
		"""AYALA staff may not price QCSM's court with the staff params, even
		though the bare quote is public information."""
		from court_booking_tech.api.portal import get_quote

		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			get_quote,
			"QCSM-timog-court-1",
			1,
			customer="cust.milo@example.com",
		)
		# B15's and B27's params take the same door.
		self.assertRaises(
			frappe.PermissionError, get_quote, "QCSM-timog-court-1", 1, hourly_rate=250
		)
		self.assertRaises(
			frappe.PermissionError, get_quote, "QCSM-timog-court-1", 1, platform_fee=0
		)
		# The gate runs BEFORE the suspension/Active check on purpose, so a
		# cross-tenant caller learns "not permitted" and never "that facility is
		# suspended" — the bare call below proves the public half still answers.
		self.assertTrue(get_quote("QCSM-timog-court-1", 1)["total_amount"])

	def test_get_quote_stays_guest_open_without_the_staff_params(self):
		"""The additive contract. B4 must not have quietly closed the endpoint
		the whole portal checkout is built on."""
		from court_booking_tech.api.portal import get_quote

		frappe.set_user("Guest")
		quote = get_quote("AYALA-makati-court-a", 2)
		self.assertEqual(quote["total_amount"], 600)
		self.assertEqual(quote["discount_percent"], 0.0)

	def test_get_quote_refuses_an_out_of_range_or_non_numeric_discount(self):
		"""Staff-gated, but a garbage discount must still be an ERROR rather than
		a silent list price: flt("abc") is 0.0, which would sail through a range
		check and put a wrong number in front of a paying customer."""
		from court_booking_tech.api.portal import get_quote

		court = "AYALA-makati-court-a"
		frappe.set_user(STELLA)
		# "nan"/"inf" (B15's ducky, 2026-08-27): float() parses them and nan fails
		# BOTH halves of the 0-100 check, so without an isfinite guard it rode
		# through as a non-JSON total.
		for bad in (-1, 101, "abc", "nan", "inf"):
			with self.subTest(discount=bad):
				self.assertRaises(
					frappe.ValidationError, get_quote, court, 1, discount_percent=bad
				)

	# --- open play endpoints (section-10, leak vector 3) ------------------

	def test_open_play_endpoints_cross_company_denied(self):
		"""Every section-10 engine endpoint joins the matrix: as AYALA staff, a
		QCSM session is unreachable through all of them."""
		from court_booking_tech.api.open_play import (
			add_players,
			confirm_participant_payment,
			game_done,
			get_open_play_board,
			open_session,
			start_session,
		)

		qcsm_session = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "QCSM-timog",
				"title": "QCSM Open Play",
				"session_date": "2027-02-13",
				"start_time": "10:00:00",
				"end_time": "12:00:00",
				"entry_fee": 100,
				"courts": [{"court": "QCSM-timog-court-1"}],
			}
		)
		qcsm_session.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Open Play Session",
			qcsm_session.name,
			force=True,
			ignore_permissions=True,
		)

		frappe.set_user(STELLA)
		self.assertFalse(
			frappe.get_doc(
				"CBT Open Play Session", qcsm_session.name
			).has_permission("read")
		)
		self.assertRaises(
			frappe.PermissionError,
			get_open_play_board,
			session=qcsm_session.name,
			company=QCSM,
		)
		self.assertRaises(frappe.PermissionError, open_session, qcsm_session.name)
		self.assertRaises(frappe.PermissionError, start_session, qcsm_session.name)
		self.assertRaises(
			frappe.PermissionError,
			add_players,
			qcsm_session.name,
			frappe.as_json(
				[{"customer": "cust.carla@example.com", "payment_method": "Cash"}]
			),
		)
		self.assertRaises(
			frappe.PermissionError, game_done, qcsm_session.name, "QCSM-timog-court-1"
		)
		self.assertRaises(
			frappe.PermissionError,
			confirm_participant_payment,
			qcsm_session.name,
			"no-such-row",
		)

	def test_open_play_session_list_scoped(self):
		# Seeds provide an AYALA session; the QCSM one above is created per
		# test, so this asserts the positive half of the scoping.
		frappe.set_user(STELLA)
		companies = {
			row.company
			for row in frappe.get_list(
				"CBT Open Play Session", fields=["company"], limit_page_length=0
			)
		}
		self.assertTrue(companies)
		self.assertEqual(companies, {AYALA})

	def test_open_play_board_platform_scope_requires_company(self):
		"""Platform scope has no session company — an empty one must fail
		closed, never widen to every tenant."""
		from court_booking_tech.api.open_play import get_open_play_board

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertRaises(frappe.PermissionError, get_open_play_board)

	def test_pending_payments_platform_scope_requires_company(self):
		"""Platform scope has no session company — company=None must fail
		closed (require_company_access rejects an empty company), never widen
		to all companies."""
		from court_booking_tech.api.board import get_pending_payments

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertRaises(frappe.PermissionError, get_pending_payments)

	# --- memberships & bans (section-11, leak vector 3) -------------------

	def test_membership_list_scoped_and_cross_read_denied(self):
		"""Seeds provide AYALA and QCSM memberships — Stella must see only her
		company's, and a customer's discount at another facility is invisible
		to her (it is that facility's commercial decision)."""
		qcsm_membership = frappe.db.get_value(
			"CBT Membership", {"company": QCSM}, "name"
		)
		self.assertTrue(qcsm_membership)

		frappe.set_user(STELLA)
		companies = {
			row.company
			for row in frappe.get_list(
				"CBT Membership", fields=["company"], limit_page_length=0
			)
		}
		self.assertEqual(companies, {AYALA})
		self.assertFalse(
			frappe.get_doc("CBT Membership", qcsm_membership).has_permission("read")
		)
		self.assertRaises(
			frappe.PermissionError, client_get, "CBT Membership", qcsm_membership
		)

	def test_membership_endpoint_is_company_scoped(self):
		from court_booking_tech.membership import get_member_discount_for

		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			get_member_discount_for,
			QCSM,
			"cust.milo@example.com",
		)

	def test_customer_ban_list_scoped_and_endpoints_denied(self):
		from court_booking_tech.api.bans import (
			ban_customer,
			get_customer_ban_status,
			lift_ban,
		)

		qcsm_ban = frappe.get_doc(
			{
				"doctype": "CBT Customer Ban",
				"company": QCSM,
				"customer": "cust.carla@example.com",
				"reason": "isolation probe",
			}
		)
		qcsm_ban.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Customer Ban",
			qcsm_ban.name,
			force=True,
			ignore_permissions=True,
		)

		frappe.set_user(STELLA)
		names = {
			row.name
			for row in frappe.get_list("CBT Customer Ban", limit_page_length=0)
		}
		self.assertNotIn(qcsm_ban.name, names)
		self.assertFalse(
			frappe.get_doc("CBT Customer Ban", qcsm_ban.name).has_permission("read")
		)
		self.assertRaises(
			frappe.PermissionError,
			ban_customer,
			QCSM,
			"cust.carla@example.com",
			"cross-tenant",
		)
		self.assertRaises(frappe.PermissionError, lift_ban, qcsm_ban.name)
		self.assertRaises(
			frappe.PermissionError,
			get_customer_ban_status,
			QCSM,
			"cust.carla@example.com",
		)

	def test_onboarding_checklist_is_platform_only(self):
		from court_booking_tech.api.onboarding import get_onboarding_checklist

		frappe.set_user(ALONA)  # a Company Admin, not the platform
		self.assertRaises(frappe.PermissionError, get_onboarding_checklist, AYALA)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertTrue(get_onboarding_checklist(AYALA)["steps"])

	# --- self-branch-management gate (section-3) --------------------------

	def _cleanup_branch(self, name):
		def _do():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Branch", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	def test_gated_admin_with_allow_creates_branch(self):
		# AYALA has allow_self_branch_management = 1.
		frappe.set_user(ALONA)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": AYALA,
				"slug": "gate-probe",
				"branch_name": "Gate Probe",
			}
		)
		doc.insert()
		self._cleanup_branch(doc.name)
		self.assertEqual(doc.name, "AYALA-gate-probe")

	def test_gated_admin_without_allow_cannot_create(self):
		# QCSM has allow_self_branch_management = 0.
		frappe.set_user(QUINTIN)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": QCSM,
				"slug": "gate-probe-q",
				"branch_name": "Gate Probe Q",
			}
		)
		self.assertRaises(frappe.PermissionError, doc.insert)

	def test_gated_admin_without_allow_cannot_write(self):
		frappe.set_user(QUINTIN)
		doc = frappe.get_doc("CBT Branch", "QCSM-timog")
		doc.phone = "0917-999-9999"
		self.assertRaises(frappe.PermissionError, doc.save)

	def test_platform_admin_bypasses_gate(self):
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Branch",
				"company": QCSM,
				"slug": "gate-probe-p",
				"branch_name": "Gate Probe P",
			}
		)
		doc.insert()
		self._cleanup_branch(doc.name)
		self.assertEqual(doc.name, "QCSM-gate-probe-p")

	def test_gate_does_not_apply_to_courts(self):
		# Quintin (allow=0) can still manage COURTS — the gate is branch-only.
		frappe.set_user(QUINTIN)
		doc = frappe.get_doc("CBT Court", "QCSM-timog-court-1")
		doc.description = "Resurfaced flooring"
		doc.save()
		self.assertEqual(
			frappe.db.get_value("CBT Court", doc.name, "description"),
			"Resurfaced flooring",
		)

	# --- writes ----------------------------------------------------------

	def test_permlevel_fields_silently_stripped_for_company_admin(self):
		"""Frappe silently reverts permlevel-1 changes for roles without
		level-1 write — assert the revert actually happens (the billing
		lever must stay platform-only)."""
		frappe.set_user(ALONA)
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.status = "Suspended"
		doc.subscription_fee = 99999
		doc.save()
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(frappe.db.get_value("CBT Company", AYALA, "status"), "Active")
		self.assertNotEqual(
			frappe.db.get_value("CBT Company", AYALA, "subscription_fee"), 99999
		)

	def test_level0_write_own_company_only(self):
		frappe.set_user(ALONA)
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.payment_instructions = "GCash 0917-000-0000 (Ayala Courts)"
		doc.save()
		self.assertEqual(
			frappe.db.get_value("CBT Company", AYALA, "payment_instructions"),
			"GCash 0917-000-0000 (Ayala Courts)",
		)
		other = frappe.get_doc("CBT Company", QCSM)
		other.payment_instructions = "should never persist"
		self.assertRaises(frappe.PermissionError, other.save)

	# --- leak vector 2: null company -------------------------------------

	def test_null_company_binding_rejected(self):
		email = "temp.nullco@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Nullco",
					"last_name": "Probe",
					"enabled": 1,
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User", email, force=True, ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Company User",
					"user": email,
					"company_role": "Company Staff",
				}
			).insert()

	# --- leak vector 1: global search ------------------------------------

	def test_tenant_doctypes_absent_from_global_search(self):
		placeholders = ", ".join(["%s"] * len(TENANT_DOCTYPES))
		count = frappe.db.sql(
			f"select count(*) from `__global_search` where doctype in ({placeholders})",
			TENANT_DOCTYPES,
		)[0][0]
		self.assertEqual(count, 0)

	# --- the cart: a caller-supplied LIST of tenant ids (Backlog B35) -----
	#
	# Every other endpoint in this app takes ONE tenant-scoped id, or none.
	# get_cart_quote and reserve_cart take a list, which is a new shape rather
	# than a re-run of an existing one — so the matrix says so out loud.

	def test_a_cart_cannot_mix_two_companies(self):
		from court_booking_tech.api.portal import get_cart_quote

		frappe.set_user("cust.pia@example.com")
		with self.assertRaisesRegex(frappe.ValidationError, "one branch at a time"):
			get_cart_quote(
				[
					{"court": "AYALA-makati-court-a", "booking_date": "2028-09-06", "start_time": "13:00:00"},
					{"court": "QCSM-timog-court-1", "booking_date": "2028-09-06", "start_time": "13:00:00"},
				]
			)

	def test_a_cart_cannot_mix_two_branches_of_one_company(self):
		"""Same tenant, still refused. The cart is priced and held against ONE
		branch's grid, business hours and expiry knob, so the bound is not only
		about tenancy — but it is the bound a cross-tenant probe would hit
		first, which is why it lives here as well as in test_cart."""
		from court_booking_tech.api.portal import get_cart_quote

		frappe.set_user("cust.pia@example.com")
		with self.assertRaisesRegex(frappe.ValidationError, "one branch at a time"):
			get_cart_quote(
				[
					{"court": "AYALA-makati-court-a", "booking_date": "2028-09-06", "start_time": "13:00:00"},
					{"court": "AYALA-bgc-court-1", "booking_date": "2028-09-06", "start_time": "13:00:00"},
				]
			)

	def test_the_cart_refusal_names_no_company(self):
		"""The message a cross-tenant probe gets back must not confirm which
		company either id belongs to — same discipline as the deliberately
		indistinguishable unknown/inactive court."""
		from court_booking_tech.api.portal import get_cart_quote

		frappe.set_user("cust.pia@example.com")
		try:
			get_cart_quote(
				[
					{"court": "AYALA-makati-court-a", "booking_date": "2028-09-06", "start_time": "13:00:00"},
					{"court": "QCSM-timog-court-1", "booking_date": "2028-09-06", "start_time": "13:00:00"},
				]
			)
			self.fail("a cross-company cart was accepted")
		except frappe.ValidationError as error:
			message = str(error)
			for leak in ("qc-smash", "QC Smash", "ayala-courts", "Ayala Courts"):
				self.assertNotIn(leak, message)

	def test_reserve_cart_takes_no_customer_parameter(self):
		"""The customer is the SESSION user and cannot be named by the caller —
		the same guarantee reserve_booking gives, restated because a list-shaped
		payload is where a customer field would look natural."""
		import inspect

		from court_booking_tech.api.portal import reserve_cart

		self.assertEqual(
			set(inspect.signature(reserve_cart).parameters) - {"items"},
			{"payment_channel", "apply_credit"},
		)


class TestReadListScope(FrappeTestCase):
	"""Backlog B38 — the User list and the customer picker.

	Its own class with its own fixtures: the rules are RELATIONSHIP-based, so a
	row that leans on which seeded customer happens to have booked where is a
	row that breaks the next time the seeds move.
	"""

	STRANGER = "cust.stranger.b38@example.com"
	AYALA_ONLY = "cust.ayalaonly.b38@example.com"
	AYALA_COURT = "AYALA-makati-court-a"
	B38_DATE = "2027-05-14"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _customer(self, email, full_name):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": full_name,
					"enabled": 1,
					"send_welcome_email": 0,
					"user_type": "Website User",
				}
			)
			user.append("roles", {"role": "CBT Customer"})
			user.insert(ignore_permissions=True)
			self.addCleanup(
				frappe.delete_doc, "User", email, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		return email

	def _ayala_booking(self, customer, start_time, status=None):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": self.AYALA_COURT,
				"customer": customer,
				"booking_date": self.B38_DATE,
				"start_time": start_time,
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		doc.insert(ignore_permissions=True)
		if status:
			frappe.db.set_value("CBT Court Booking", doc.name, "booking_status", status)
		self.addCleanup(
			frappe.delete_doc, "CBT Court Booking", doc.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)
		return doc

	def _picker(self, txt=""):
		from court_booking_tech.api.board import customer_query

		return {row[0] for row in customer_query("User", txt, "name", 0, 100, {})}

	# --- (a) the User list ------------------------------------------------

	def test_a_company_seat_lists_only_its_own_companys_seats(self):
		frappe.set_user(STELLA)
		names = {row.name for row in frappe.get_list("User", limit_page_length=0)}
		self.assertIn(STELLA, names, "a seat cannot even see itself")
		self.assertIn(ALONA, names, "own company's admin seat is missing")
		self.assertNotIn(QUINTIN, names, "another tenant's admin seat is listed")
		self.assertNotIn(PLATFORM_ADMIN_EMAIL, names, "the platform seat is listed")
		self.assertNotIn("cust.carla@example.com", names, "a customer is in the User list")

	def test_the_other_tenant_is_bounded_the_same_way(self):
		frappe.set_user(QUINTIN)
		names = {row.name for row in frappe.get_list("User", limit_page_length=0)}
		self.assertIn(QUINTIN, names)
		self.assertNotIn(STELLA, names)
		self.assertNotIn(ALONA, names)

	def test_the_scope_survives_a_filtered_query(self):
		"""⚠ THE parenthesis row. frappe joins hook conditions with a bare
		`" and ".join(...)` and wraps nothing, so an unparenthesised `A OR B`
		re-associates and WIDENS. With no other condition present the bug is
		invisible — this row supplies the other condition."""
		frappe.set_user(STELLA)
		names = {
			row.name
			for row in frappe.get_list("User", filters={"enabled": 1}, limit_page_length=0)
		}
		self.assertIn(STELLA, names)
		self.assertNotIn(QUINTIN, names, "the OR re-associated — the list widened")

	def test_the_platform_seat_still_sees_everyone(self):
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		names = {row.name for row in frappe.get_list("User", limit_page_length=0)}
		self.assertIn(STELLA, names)
		self.assertIn(QUINTIN, names)

	def test_a_customer_sees_exactly_themselves(self):
		"""The fail-closed branch: get_session_company raises for every unbound
		non-platform user, and this hook answers "yourself" rather than 1=0."""
		frappe.set_user("cust.carla@example.com")
		names = {row.name for row in frappe.get_list("User", limit_page_length=0)}
		self.assertEqual(names, {"cust.carla@example.com"})

	def test_a_seat_cannot_read_another_tenants_seat_document(self):
		frappe.set_user(STELLA)
		self.assertFalse(frappe.get_doc("User", QUINTIN).has_permission("read"))
		self.assertTrue(frappe.get_doc("User", STELLA).has_permission("read"))

	def test_company_admins_can_still_manage_their_own_seats(self):
		"""The hook must not break the flow that CREATES the seats it scopes."""
		from court_booking_tech.api.company_users import create_company_user

		email = "seat.b38@example.com"
		frappe.set_user(ALONA)
		result = create_company_user(
			company=AYALA, email=email, full_name="Seat Probe",
			company_role="Company Staff",
		)
		self.addCleanup(
			frappe.delete_doc, "User", email, force=True, ignore_permissions=True,
			ignore_missing=True,
		)
		self.assertTrue(result)
		names = {row.name for row in frappe.get_list("User", limit_page_length=0)}
		self.assertIn(email, names, "a seat this admin just created is invisible to them")

	# --- (b) the customer picker -----------------------------------------

	def test_a_customer_who_has_never_dealt_with_us_is_invisible(self):
		"""Ruling 1, 2026-09-05 — and no exact-email hatch: searching their
		full address must return nothing, or the picker is an oracle."""
		stranger = self._customer(self.STRANGER, "Stranger")
		frappe.set_user(STELLA)
		self.assertNotIn(stranger, self._picker())
		self.assertNotIn(stranger, self._picker(stranger))

	def test_one_booking_of_any_status_makes_them_pickable(self):
		"""Ruling 2 — a lapsed booking counts, because that is exactly when
		they phone back."""
		customer = self._customer(self.AYALA_ONLY, "Ayala Only")
		self._ayala_booking(customer, "07:00:00", status="Cancelled")
		frappe.set_user(STELLA)
		self.assertIn(customer, self._picker())

	def test_a_customer_of_the_other_tenant_only_is_invisible_here(self):
		customer = self._customer(self.AYALA_ONLY, "Ayala Only")
		self._ayala_booking(customer, "08:00:00")
		frappe.set_user(QUINTIN)
		self.assertNotIn(
			customer, self._picker(), "another tenant's customer base is enumerable"
		)

	def test_a_membership_alone_is_a_dealing(self):
		customer = self._customer(self.STRANGER, "Stranger")
		membership = frappe.get_doc(
			{
				"doctype": "CBT Membership",
				"company": AYALA,
				"customer": customer,
				"tier": "Standard",
				"discount_percent": 5,
				"start_date": "2025-01-01",
			}
		)
		membership.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "CBT Membership", membership.name, force=True,
			ignore_permissions=True, ignore_missing=True,
		)
		frappe.set_user(STELLA)
		self.assertIn(customer, self._picker())

	def test_administrator_is_never_offered_as_a_customer(self):
		"""Administrator carries the CBT Customer role on the seeded bench —
		the "admins included" half of the user's report. Both scopes."""
		for seat in (STELLA, PLATFORM_ADMIN_EMAIL):
			with self.subTest(seat=seat):
				frappe.set_user(seat)
				offered = self._picker()
				self.assertNotIn("Administrator", offered)
				self.assertNotIn("Guest", offered)

	def test_the_picker_still_offers_only_cbt_customers(self):
		"""The pre-existing guarantee must survive the rescoping."""
		self._ayala_booking(self._customer(self.AYALA_ONLY, "Ayala Only"), "09:00:00")
		frappe.set_user(STELLA)
		offered = self._picker()
		self.assertTrue(offered)
		self.assertNotIn(STELLA, offered)
		for email in offered:
			self.assertTrue(
				frappe.db.exists(
					"Has Role",
					{"parent": email, "role": "CBT Customer", "parenttype": "User"},
				),
				email,
			)

	# --- leak vector 4: customer perms / if_owner -------------------------

	def test_customer_has_no_docperms_and_no_if_owner_anywhere(self):
		for doctype in frappe.get_all(
			"DocType", filters={"module": "Court Booking Tech"}, pluck="name"
		):
			for perm in frappe.get_meta(doctype).permissions:
				self.assertNotEqual(
					perm.role, "CBT Customer", f"{doctype}: CBT Customer must have NO DocPerm"
				)
				self.assertFalse(
					perm.get("if_owner"), f"{doctype}: if_owner DocPerm is forbidden"
				)
