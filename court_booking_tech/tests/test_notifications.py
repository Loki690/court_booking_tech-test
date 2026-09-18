"""
Court Booking Tech — Customer email (section-9)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_notifications

Email Queue rows are the ONLY assertion surface in dev/E2E: the seeds write a
site-config mail stub so frappe can resolve an outgoing account at queue time,
and mute_emails blocks the send leg (section-8 as-built 2). Never assert
delivery, never parse a queued body for links.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api.bookings import confirm_booking
from court_booking_tech.api.portal import reserve_booking
from court_booking_tech.api.proofs import accept_proofs, create_proof, reject_proofs
from court_booking_tech.notifications import (
	ACCOUNT_EXISTS_TEMPLATE,
	BOOKING_CONFIRMED_TEMPLATE,
	CART_CONFIRMED_TEMPLATE,
	PROOF_REJECTED_TEMPLATE,
	RESCHEDULED_TEMPLATE,
)
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, _proof_sample_bytes, seed_all

PIA = "cust.pia@example.com"
STELLA = "staff.ayala@example.com"

TEST_DATE = "2027-03-12"  # Friday, clear of every other fixture date
T0 = datetime(2027, 3, 12, 8, 0)
COURT = "AYALA-makati-court-a"
CLOCK = "court_booking_tech.clock.now_dt"


class NotificationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _reserve(self, start_time="10:00:00"):
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			result = reserve_booking(COURT, TEST_DATE, start_time, 1)
		self.addCleanup(self._purge, result["booking"])
		return result["booking"]

	def _purge(self, name):
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
				"CBT Booking Invoice", invoice, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

	def _queued(self, booking: str) -> list[dict]:
		return frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "CBT Court Booking", "reference_name": booking},
			fields=["name", "message"],
		)

	def _upload(self, booking):
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			create_proof(booking, "p.jpg", _proof_sample_bytes())


class TestEmailTemplatesInstalled(NotificationTestCase):
	def test_every_template_ships_as_a_fixture(self):
		"""The mail seam is only as shipped as its templates: _queue stays
		strict on purpose, so a template missing from hooks.py's fixtures filter
		turns every notification into an Error Log row instead of an email."""
		for name in (
			PROOF_REJECTED_TEMPLATE,
			BOOKING_CONFIRMED_TEMPLATE,
			RESCHEDULED_TEMPLATE,  # section-19
			ACCOUNT_EXISTS_TEMPLATE,  # Backlog B22
			CART_CONFIRMED_TEMPLATE,  # Backlog B35
		):
			self.assertTrue(
				frappe.db.exists("Email Template", name),
				f"{name} missing — run bench migrate to sync app fixtures",
			)


class TestProofRejectedEmail(NotificationTestCase):
	def test_recoverable_rejection_queues_reason_and_regrace(self):
		booking = self._reserve("09:00:00")
		self._upload(booking)
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			reject_proofs(booking, "Unreadable")
		rows = self._queued(booking)
		self.assertTrue(rows, "no proof-rejected mail was queued")
		body = " ".join(row.message or "" for row in rows)
		self.assertIn("Unreadable", body)
		# The regrace wording is what makes the restart-once rule defensible.
		self.assertIn("upload a clearer proof", body)

	def test_fatal_rejection_queues_the_released_wording(self):
		booking = self._reserve("11:00:00")
		self._upload(booking)
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			reject_proofs(booking, "Invalid / suspected fake")
		body = " ".join(row.message or "" for row in self._queued(booking))
		self.assertIn("released", body)

	def test_mail_failure_never_blocks_the_rejection(self):
		"""A broken template must not stop staff rejecting junk."""
		booking = self._reserve("13:00:00")
		self._upload(booking)
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0), patch(
			"court_booking_tech.notifications._queue", side_effect=RuntimeError("smtp down")
		):
			result = reject_proofs(booking, "Invalid / suspected fake")
		self.assertEqual(result["outcome"], "Expired")
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", booking, "booking_status"),
			"Expired",
		)


class TestBookingConfirmedEmail(NotificationTestCase):
	def test_proof_acceptance_queues_the_confirmation(self):
		booking = self._reserve("14:00:00")
		self._upload(booking)
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			accept_proofs(booking)
		body = " ".join(row.message or "" for row in self._queued(booking))
		self.assertIn("confirmed", body.lower())
		self.assertIn(booking, body)

	def test_desk_cash_walk_in_sends_no_confirmation(self):
		"""Born Confirmed at the desk with the customer standing there — the
		mail would be noise, and every seeded booking would trigger one."""
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with patch(CLOCK, return_value=T0):
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": COURT,
					"customer": PIA,
					"booking_date": TEST_DATE,
					"start_time": "16:00:00",
					"number_of_slots": 1,
					"payment_method": "Cash",
				}
			).insert()
		self.addCleanup(self._purge, doc.name)
		self.assertEqual(doc.booking_status, "Confirmed")
		self.assertEqual(self._queued(doc.name), [])

	def test_retro_confirm_queues_the_confirmation(self):
		booking = self._reserve("17:00:00")
		self._upload(booking)
		# The hold lapsed and the sweep already expired it — but the money
		# really arrived and the desk let them play (PLAN §5a retro-confirm).
		frappe.db.set_value("CBT Court Booking", booking, "booking_status", "Expired")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			confirm_booking(booking)  # Expired -> Completed (retro-confirm)
		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", booking, "booking_status"),
			"Completed",
		)
		body = " ".join(row.message or "" for row in self._queued(booking))
		self.assertIn(booking, body)


class TestWalkInEmail(NotificationTestCase):
	"""Section-13: a walk-in has no account and therefore no inbox.

	NotificationTestCase carries helpers only (no test methods), so subclassing
	it does not re-run anything.
	"""

	# October 2027 = section-13's backend month (September is test_reports'
	# integer-claimed empty-control month — see seeds WALKIN_DATE).
	WALK_DATE = "2027-10-06"  # Wednesday
	WALK_T0 = datetime(2027, 10, 6, 8, 0)

	def _walkin_hold(self, start_time="10:00:00"):
		frappe.set_user("Administrator")
		with patch(CLOCK, return_value=self.WALK_T0):
			doc = frappe.get_doc(
				{
					"doctype": "CBT Court Booking",
					"court": COURT,
					"customer_name": "Walk-in Wally",
					"customer_phone": "0917-777-8888",
					"booking_date": self.WALK_DATE,
					"start_time": start_time,
					"number_of_slots": 1,
					"payment_method": "Fund Transfer",
				}
			).insert(ignore_permissions=True)
		self.addCleanup(self._purge, doc.name)
		return doc

	def test_confirming_a_walk_in_queues_nothing_and_logs_nothing(self):
		booking = self._walkin_hold("10:00:00")
		errors_before = frappe.db.count("Error Log")

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0):
			confirm_booking(booking.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertEqual(
			frappe.db.get_value("CBT Court Booking", booking.name, "booking_status"),
			"Confirmed",
		)
		self.assertEqual(self._queued(booking.name), [])
		# Silent SKIP, not a swallowed failure: a logged error here would train
		# staff to ignore the Error Log at a cash-heavy branch.
		self.assertEqual(frappe.db.count("Error Log"), errors_before)

	def test_the_skip_is_ours_not_an_accident_of_frappes_recipient_filter(self):
		"""frappe would drop recipients=[None] on its own — Email Queue filters
		falsy recipients before resolving a sender — so asserting "no queue row"
		alone would pass on UNCHANGED code. Assert the guard itself: sendmail is
		never reached for a customer-less booking, and still is for a real one.
		"""
		booking = self._walkin_hold("11:00:00")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0), patch(
			"frappe.sendmail"
		) as sendmail:
			confirm_booking(booking.name)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		sendmail.assert_not_called()

		# Control: the same transition on an ACCOUNT booking still mails.
		account_booking = self._reserve("12:00:00")
		self._upload(account_booking)
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0), patch("frappe.sendmail") as sendmail:
			accept_proofs(account_booking)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertTrue(
			sendmail.called, "an account booking must still be notified"
		)

	def test_rejecting_a_walk_ins_proof_still_works_without_mail(self):
		"""The rejection must not depend on there being someone to tell."""
		booking = self._walkin_hold("13:00:00")
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=self.WALK_T0):
			create_proof(booking.name, "p.jpg", _proof_sample_bytes())
			result = reject_proofs(booking.name, "Invalid / suspected fake")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)

		self.assertEqual(result["outcome"], "Expired")
		self.assertEqual(self._queued(booking.name), [])
