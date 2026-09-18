"""
Court Booking Tech — Company customer bans (section-11)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_bans

A ban is a COMPANY-scoped block-list entry against a customer who keeps making
reservations that never materialise. The tests pin down exactly how narrow it
is, because an over-broad ban is the dangerous failure mode:

  BLOCKED   online (portal) booking creation at THAT company
  ALLOWED   the same customer at every OTHER company (marketplace premise)
  ALLOWED   staff booking them at the desk (they are standing there with cash)
  ALLOWED   open play entry (staffed, live, paid on the spot)
  ALLOWED   everything about their EXISTING bookings — proof upload, cancel

Plus the raise/lift asymmetry: front desk may raise a ban, only a Company Admin
may clear one.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech import bans
from court_booking_tech.api import bans as bans_api
from court_booking_tech.api import bookings as bookings_api
from court_booking_tech.api import open_play, portal
from court_booking_tech.api.proofs import create_proof
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, _proof_sample_bytes, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"

MIA = "cust.mia@example.com"
PIA = "cust.pia@example.com"
ALONA = "admin.ayala@example.com"  # AYALA Company Admin
STELLA = "staff.ayala@example.com"  # AYALA Company Staff
SAMUEL = "staff.qcsm@example.com"  # QCSM Company Staff

# July 2027: owned by this module alone (membership tests use June).
BOOK_DATE = "2027-07-09"  # Friday
T0 = datetime(2027, 7, 9, 8, 0)

BGC_1 = "AYALA-bgc-court-1"
MAKATI_A = "AYALA-makati-court-a"
QCSM_1 = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"


class BanTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _ban(self, company=AYALA, customer=MIA, reason="Repeated no-show reservations"):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Customer Ban",
				"company": company,
				"customer": customer,
				"reason": reason,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(self._drop_ban, doc.name)
		return doc

	def _drop_ban(self, name):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"CBT Customer Ban", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)

	def _cleanup_booking(self, name):
		def _do():
			frappe.set_user("Administrator")
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

		self.addCleanup(_do)


class TestBanBlocksOnlineBooking(BanTestCase):
	def test_banned_customer_cannot_book_online(self):
		self._ban()
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			with self.assertRaises(frappe.ValidationError) as ctx:
				portal.reserve_booking(BGC_1, BOOK_DATE, "09:00:00", 1)
		# The wording must NOT be the suspension message: telling a banned
		# customer the venue is closed is false and sends them to complain
		# about an outage that is not happening.
		message = str(ctx.exception)
		self.assertIn("cannot book at this facility online", message)
		self.assertIn("contact the branch", message)

	def test_ban_does_not_bleed_to_other_companies(self):
		"""One account, book anywhere (PLAN §1) — a ban is one tenant's call."""
		self._ban(company=AYALA, customer=MIA)
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(QCSM_1, BOOK_DATE, "09:00:00", 1)
		self._cleanup_booking(result["booking"])
		self.assertTrue(result["booking"].startswith("BK-QCSM-"))

	def test_ban_does_not_affect_other_customers(self):
		self._ban(company=AYALA, customer=MIA)
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "10:00:00", 1)
		self._cleanup_booking(result["booking"])
		self.assertTrue(result["booking"].startswith("BK-AYALA-"))

	def test_lifting_a_ban_restores_online_booking(self):
		ban = self._ban()
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			self.assertRaises(
				frappe.ValidationError,
				portal.reserve_booking,
				BGC_1,
				BOOK_DATE,
				"11:00:00",
				1,
			)

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		bans_api.lift_ban(ban.name)

		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "11:00:00", 1)
		self._cleanup_booking(result["booking"])
		self.assertTrue(result["booking"].startswith("BK-AYALA-"))


class TestBanIsNarrow(BanTestCase):
	"""Everything a ban deliberately does NOT block."""

	def test_staff_can_still_book_a_banned_customer_at_the_desk(self):
		"""The customer is standing at the counter with cash — that is a
		judgement call the front desk owns, not something the block-list
		overrides."""
		self._ban()
		with patch(CLOCK, return_value=T0):
			result = bookings_api.create_booking(
				court=MAKATI_A,
				booking_date=BOOK_DATE,
				start_time="09:00:00",
				customer=MIA,
				payment_method="Cash",
			)
		self._cleanup_booking(result["name"])
		self.assertEqual(result["booking_status"], "Confirmed")

	def test_existing_booking_still_accepts_a_payment_proof(self):
		"""Banning must not strand money already in flight."""
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "14:00:00", 1)
		self._cleanup_booking(result["booking"])

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self._ban()

		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			proof = create_proof(
				result["booking"], "proof_sample.jpg", _proof_sample_bytes()
			)
		self.assertTrue(proof.get("proof"))

	def test_existing_booking_can_still_be_self_cancelled(self):
		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			result = portal.reserve_booking(BGC_1, BOOK_DATE, "15:00:00", 1)
		self._cleanup_booking(result["booking"])

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self._ban()

		frappe.set_user(MIA)
		with patch(CLOCK, return_value=T0):
			cancelled = portal.cancel_my_booking(result["booking"])
		self.assertEqual(cancelled["booking_status"], "Cancelled")

	def test_open_play_still_admits_a_banned_customer(self):
		"""Open play is staffed, live and paid on the spot — none of the
		unattended-squatting risk a ban exists to stop."""
		self._ban()
		session = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "AYALA-makati",
				"title": "Ban Probe Session",
				"session_date": BOOK_DATE,
				"start_time": "18:00:00",
				"end_time": "20:00:00",
				"court_type": "Badminton",
				"entry_fee": 100,
				"courts": [{"court": "AYALA-makati-court-b"}],
			}
		)
		session.insert(ignore_permissions=True)

		def _cleanup():
			frappe.set_user("Administrator")
			doc = frappe.get_doc("CBT Open Play Session", session.name)
			for row in doc.participants or []:
				if row.billing_doc:
					frappe.delete_doc(
						"CBT Booking Invoice", row.billing_doc, force=True,
						ignore_permissions=True, ignore_missing=True,
					)
			for block in frappe.get_all(
				"CBT Slot Block", filters={"open_play_session": session.name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Slot Block", block, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Open Play Session", session.name, force=True,
				ignore_permissions=True, ignore_missing=True,
			)

		self.addCleanup(_cleanup)
		open_play.open_session(session.name)
		open_play.add_players(
			session.name, frappe.as_json([{"customer": MIA, "payment_method": "Cash"}])
		)
		doc = frappe.get_doc("CBT Open Play Session", session.name)
		self.assertEqual(doc.participants[0].customer, MIA)


class TestBanRecordRules(BanTestCase):
	def test_second_active_ban_is_rejected(self):
		self._ban()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Customer Ban",
					"company": AYALA,
					"customer": MIA,
					"reason": "duplicate",
				}
			).insert(ignore_permissions=True)

	def test_a_new_ban_is_allowed_after_the_previous_one_is_lifted(self):
		first = self._ban()
		bans_api.lift_ban(first.name)
		second = self._ban(reason="Did it again")
		self.assertNotEqual(first.name, second.name)
		self.assertTrue(bans.is_banned(AYALA, MIA))

	def test_lifted_ban_cannot_be_re_armed(self):
		"""Re-arming would reuse the original reason and date — a NEW ban is
		the honest record of a new decision."""
		ban = self._ban()
		bans_api.lift_ban(ban.name)
		doc = frappe.get_doc("CBT Customer Ban", ban.name)
		doc.status = "Active"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_reason_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Customer Ban",
					"company": AYALA,
					"customer": MIA,
					"reason": "   ",
				}
			).insert(ignore_permissions=True)

	def test_null_company_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "CBT Customer Ban", "customer": MIA, "reason": "no company"}
			).insert(ignore_permissions=True)

	def test_audit_stamps_are_recorded(self):
		ban = self._ban()
		self.assertEqual(ban.banned_by, "Administrator")
		self.assertTrue(ban.banned_at)
		bans_api.lift_ban(ban.name)
		ban.reload()
		self.assertEqual(ban.status, "Lifted")
		self.assertEqual(ban.lifted_by, "Administrator")
		self.assertTrue(ban.lifted_at)
		# The reason survives the lift — the record IS the audit trail.
		self.assertTrue(ban.reason)

	def test_per_company_naming_series(self):
		ban = self._ban()
		self.assertTrue(ban.name.startswith("BAN-AYALA-"), ban.name)


class TestBanApiGuards(BanTestCase):
	def test_staff_can_raise_a_ban_for_their_own_company(self):
		frappe.set_user(STELLA)
		result = bans_api.ban_customer(AYALA, MIA, "Fake proofs")
		self.addCleanup(self._drop_ban, result["ban"])
		self.assertEqual(result["status"], "Active")
		self.assertTrue(bans.is_banned(AYALA, MIA))

	def test_staff_cannot_ban_for_another_company(self):
		frappe.set_user(STELLA)  # AYALA staff
		self.assertRaises(
			frappe.PermissionError, bans_api.ban_customer, QCSM, MIA, "cross-tenant"
		)

	def test_staff_cannot_lift_a_ban(self):
		"""`status` is permlevel 1: frappe would SILENTLY revert a staff write,
		so the API refuses loudly instead of quietly doing nothing."""
		ban = self._ban()
		frappe.set_user(STELLA)
		self.assertRaises(frappe.PermissionError, bans_api.lift_ban, ban.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(
			frappe.db.get_value("CBT Customer Ban", ban.name, "status"), "Active"
		)

	def test_company_admin_can_lift_a_ban(self):
		ban = self._ban()
		frappe.set_user(ALONA)
		result = bans_api.lift_ban(ban.name)
		self.assertEqual(result["status"], "Lifted")

	def test_other_company_admin_cannot_lift_the_ban(self):
		ban = self._ban()
		frappe.set_user(SAMUEL)  # QCSM
		self.assertRaises(frappe.PermissionError, bans_api.lift_ban, ban.name)

	def test_lifting_twice_is_refused(self):
		ban = self._ban()
		bans_api.lift_ban(ban.name)
		self.assertRaises(frappe.ValidationError, bans_api.lift_ban, ban.name)

	def test_banning_an_already_banned_customer_reports_the_existing_ban(self):
		"""Idempotent-ish: the desk gets a clear answer, not a duplicate-key
		stack trace."""
		first = self._ban()
		frappe.set_user(STELLA)
		result = bans_api.ban_customer(AYALA, MIA, "again")
		self.assertTrue(result["already_banned"])
		self.assertEqual(result["ban"], first.name)

	def test_ban_status_lookup_is_company_scoped(self):
		self._ban()
		frappe.set_user(STELLA)
		self.assertTrue(bans_api.get_customer_ban_status(AYALA, MIA)["banned"])
		self.assertRaises(
			frappe.PermissionError, bans_api.get_customer_ban_status, QCSM, MIA
		)

	def test_unknown_customer_is_rejected(self):
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.ValidationError, bans_api.ban_customer, AYALA, "nobody@example.com", "x"
		)
