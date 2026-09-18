"""
Court Booking Tech — Open Play Tests (section-10)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_open_play

Covers the tenant-scoped Open Play rewrite: the guarded status machine with its
frozen and engine-owned fields, slot-block create/remove, participant billing
(Cash/Free/Fund Transfer) on the per-company INV series, the rotation queue
(seating, Game Done, court ✕ / backfill, queue ✕ reindex, drag-reorder), and
the PLAN §8q guarantee that open play payments carry NO verification clocks.
Clock is monkeypatched — never wall-clock.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech.api.open_play import (
	add_players,
	auto_rotate_tick,
	backfill,
	cancel_session,
	complete_session,
	confirm_participant_payment,
	create_participant_proof,
	game_done,
	get_open_play_board,
	open_session,
	remove_from_session,
	reorder_queue,
	return_to_queue,
	start_session,
)
from court_booking_tech.seeds.seed_test_data import OPEN_PLAY_CUSTOMERS, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"
STELLA = "staff.ayala@example.com"

# A Friday no other test file claims (seeds 2027-01-15/16/23; booking and
# verification 2027-02-05/06/12/13; billing 2027-02-19).
TEST_DATE = "2027-03-05"
T0 = datetime(2027, 3, 5, 9, 0)

BGC_1 = "AYALA-bgc-court-1"
BGC_2 = "AYALA-bgc-court-2"
BGC_3 = "AYALA-bgc-court-3"
MAKATI_A = "AYALA-makati-court-a"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"
PROOF_JPG = Path(__file__).resolve().parents[1] / "seeds" / "files" / "proof_sample.jpg"

PLAYERS = [row[0] for row in OPEN_PLAY_CUSTOMERS]


class TestOpenPlay(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ------------------------------------------------------------------
	# Fixtures
	# ------------------------------------------------------------------

	def _session(self, courts=(BGC_1, BGC_2), **overrides):
		payload = {
			"doctype": "CBT Open Play Session",
			"branch": "AYALA-bgc",
			"title": "Test Open Play",
			"session_date": TEST_DATE,
			"start_time": "09:00:00",
			"end_time": "12:00:00",
			"court_type": "Pickleball",
			"rotation_mode": "Timed",
			"rotation_minutes": 15,
			"entry_fee": 150,
			"courts": [{"court": court} for court in courts],
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._cleanup_session, doc.name)
		return doc

	def _cleanup_session(self, name):
		frappe.set_user("Administrator")
		for block in frappe.get_all(
			"CBT Slot Block", filters={"open_play_session": name}, pluck="name"
		):
			frappe.delete_doc(
				"CBT Slot Block", block, force=True, ignore_permissions=True
			)
		frappe.delete_doc(
			"CBT Open Play Session",
			name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)

	def _open(self, **kwargs):
		doc = self._session(**kwargs)
		open_session(doc.name)
		return frappe.get_doc("CBT Open Play Session", doc.name)

	def _add(self, session, emails, method="Cash"):
		add_players(
			session,
			frappe.as_json([{"customer": e, "payment_method": method} for e in emails]),
		)
		return frappe.get_doc("CBT Open Play Session", session)

	def _reload(self, doc):
		return frappe.get_doc("CBT Open Play Session", doc.name)

	def _waiting(self, doc):
		return sorted(
			[row for row in doc.queue if row.status == "Waiting"],
			key=lambda row: row.queue_position,
		)

	def _assignment(self, doc, court):
		return next(
			(row for row in doc.assignments if row.court == court and row.is_active),
			None,
		)

	# ------------------------------------------------------------------
	# Naming & validation
	# ------------------------------------------------------------------

	def test_autoname_uses_per_company_series(self):
		doc = self._session()
		self.assertRegex(doc.name, r"^OPS-AYALA-\d{5}$")

	def test_court_from_another_branch_rejected(self):
		self.assertRaises(
			frappe.ValidationError, self._session, courts=(BGC_1, MAKATI_A)
		)

	def test_duplicate_court_rejected(self):
		self.assertRaises(frappe.ValidationError, self._session, courts=(BGC_1, BGC_1))

	def test_window_must_move_forward(self):
		self.assertRaises(
			frappe.ValidationError,
			self._session,
			start_time="12:00:00",
			end_time="09:00:00",
		)

	def test_rotation_knobs_validated(self):
		self.assertRaises(frappe.ValidationError, self._session, rotation_minutes=0)
		self.assertRaises(
			frappe.ValidationError,
			self._session,
			rotation_mode="Rally",
			rally_points=0,
		)
		self.assertRaises(frappe.ValidationError, self._session, entry_fee=-1)

	def test_new_session_must_start_scheduled(self):
		self.assertRaises(frappe.ValidationError, self._session, status="Open")

	def test_company_is_mirrored_from_branch(self):
		doc = self._session()
		self.assertEqual(doc.company, AYALA)

	def test_cross_company_session_denied(self):
		frappe.set_user(STELLA)
		self.assertRaises(
			frappe.PermissionError,
			lambda: frappe.get_doc(
				{
					"doctype": "CBT Open Play Session",
					"branch": "QCSM-timog",
					"title": "Sneaky",
					"session_date": TEST_DATE,
					"start_time": "09:00:00",
					"end_time": "10:00:00",
					"courts": [{"court": QCSM_COURT}],
				}
			).insert(),
		)

	# ------------------------------------------------------------------
	# Status machine & engine ownership
	# ------------------------------------------------------------------

	def test_illegal_transition_rejected(self):
		doc = self._session()
		doc.status = "Completed"
		doc.flags.via_open_play_engine = True
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_status_change_requires_the_engine(self):
		doc = self._session()
		doc.status = "Open"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_schedule_frozen_once_open(self):
		doc = self._open()
		doc.session_date = "2027-03-06"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_courts_frozen_once_open(self):
		doc = self._open()
		doc.append("courts", {"court": BGC_3})
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_rotation_knobs_stay_editable_once_open(self):
		# Staff really do shorten rotations when a session runs long.
		doc = self._open()
		doc.rotation_minutes = 10
		doc.auto_rotate = 1
		doc.save()
		self.assertEqual(self._reload(doc).rotation_minutes, 10)

	def test_engine_tables_reject_hand_edits(self):
		doc = self._open()
		doc.append("queue", {"customer": PLAYERS[0], "status": "Waiting"})
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_child_row_names_survive_engine_saves(self):
		"""Invoices and proofs point at participant ROW NAMES — rebuilding a
		child table would mint new names and orphan that billing history."""
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:2])
		names_before = [row.name for row in doc.participants]
		invoice_refs = [
			frappe.db.get_value(
				"CBT Booking Invoice", row.billing_doc, "participant_ref"
			)
			for row in doc.participants
		]
		doc = self._add(doc.name, PLAYERS[2:4])
		names_after = [row.name for row in doc.participants][:2]
		self.assertEqual(names_before, names_after)
		self.assertEqual(sorted(invoice_refs), sorted(names_before))

	# ------------------------------------------------------------------
	# Slot blocks
	# ------------------------------------------------------------------

	def test_open_blocks_its_courts(self):
		doc = self._open()
		blocks = frappe.get_all(
			"CBT Slot Block",
			filters={"open_play_session": doc.name},
			fields=["court", "reason"],
		)
		self.assertEqual(len(blocks), 2)
		self.assertEqual({b.court for b in blocks}, {BGC_1, BGC_2})
		self.assertEqual({b.reason for b in blocks}, {"Open Play"})

		booking = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": BGC_1,
				"customer": PLAYERS[0],
				"booking_date": TEST_DATE,
				"start_time": "10:00:00",
				"number_of_slots": 1,
				"payment_method": "Cash",
			}
		)
		self.assertRaises(frappe.ValidationError, booking.insert)

	def test_open_requires_a_court(self):
		doc = self._session(courts=())
		self.assertRaises(frappe.ValidationError, open_session, doc.name)

	def test_cancel_removes_only_its_own_blocks(self):
		doc = self._open()
		foreign = frappe.get_doc(
			{
				"doctype": "CBT Slot Block",
				"branch": "AYALA-bgc",
				"court": BGC_3,
				"block_date": TEST_DATE,
				"start_time": "09:00:00",
				"end_time": "12:00:00",
				"reason": "Open Play",
				"notes": "Hand-made by staff — must survive a session cancel.",
			}
		)
		foreign.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"CBT Slot Block",
			foreign.name,
			force=True,
			ignore_permissions=True,
		)

		cancel_session(doc.name)
		self.assertFalse(
			frappe.get_all("CBT Slot Block", filters={"open_play_session": doc.name})
		)
		self.assertTrue(frappe.db.exists("CBT Slot Block", foreign.name))

	def test_complete_keeps_blocks_as_history(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:4])
		complete_session(doc.name)
		self.assertEqual(self._reload(doc).status, "Completed")
		self.assertEqual(
			len(
				frappe.get_all(
					"CBT Slot Block", filters={"open_play_session": doc.name}
				)
			),
			2,
		)

	# ------------------------------------------------------------------
	# Participants & billing
	# ------------------------------------------------------------------

	def test_cash_player_is_paid_with_a_billing_document(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]])
		row = doc.participants[0]
		self.assertEqual(row.payment_status, "Paid")
		self.assertEqual(flt(row.fee), 150.0)
		self.assertTrue(row.checked_in_at)

		invoice = frappe.get_doc("CBT Booking Invoice", row.billing_doc)
		self.assertRegex(invoice.name, r"^INV-AYALA-\d{4}-\d{5}$")
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.participant_ref, row.name)
		self.assertFalse(invoice.booking)
		self.assertEqual(flt(invoice.total_amount), 150.0)
		# AYALA is VAT: the split must sum back to the total exactly.
		self.assertEqual(flt(invoice.vatable_amount + invoice.vat_amount, 2), 150.0)

	def test_free_player_is_never_billed_the_session_fee(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Free")
		row = doc.participants[0]
		self.assertEqual(flt(row.fee), 0.0)
		self.assertEqual(row.payment_status, "Paid")
		invoice = frappe.get_doc("CBT Booking Invoice", row.billing_doc)
		self.assertEqual(flt(invoice.total_amount), 0.0)

	def test_fund_transfer_player_is_unpaid_until_confirmed(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		row = doc.participants[0]
		self.assertEqual(row.payment_status, "Unpaid")
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "status"),
			"Unpaid",
		)

		confirm_participant_payment(doc.name, row.name)
		doc = self._reload(doc)
		self.assertEqual(doc.participants[0].payment_status, "Paid")
		invoice = frappe.get_doc("CBT Booking Invoice", row.billing_doc)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertTrue(invoice.verified_by)
		self.assertTrue(invoice.verified_at)

	def test_revenue_and_headcount_track_payments(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:3])
		doc = self._add(doc.name, [PLAYERS[3]], method="Fund Transfer")
		# 3 × 150 collected; the fund-transfer player is not revenue yet.
		self.assertEqual(flt(doc.total_revenue), 450.0)
		self.assertEqual(doc.current_participants, 4)

	def test_capacity_cap_enforced(self):
		doc = self._open(max_participants=2)
		doc = self._add(doc.name, PLAYERS[:2])
		self.assertRaises(frappe.ValidationError, self._add, doc.name, [PLAYERS[2]])

	def test_duplicate_active_player_rejected(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]])
		self.assertRaises(frappe.ValidationError, self._add, doc.name, [PLAYERS[0]])

	def test_non_customer_rejected(self):
		doc = self._open()
		self.assertRaises(frappe.ValidationError, self._add, doc.name, [STELLA])

	def test_player_may_rejoin_after_leaving(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]])
		first_invoice = doc.participants[0].billing_doc
		remove_from_session(doc.name, PLAYERS[0])
		doc = self._add(doc.name, [PLAYERS[0]])
		self.assertEqual(len(doc.participants), 2)
		self.assertNotEqual(doc.participants[1].billing_doc, first_invoice)
		self.assertEqual(doc.current_participants, 1)

	def test_unpaid_leaver_has_their_document_cancelled(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		invoice = doc.participants[0].billing_doc
		remove_from_session(doc.name, PLAYERS[0])
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", invoice, "status"), "Cancelled"
		)
		# Retained with its number consumed, never deleted (PLAN §8e).
		self.assertTrue(frappe.db.exists("CBT Booking Invoice", invoice))

	def test_cancel_session_leaves_billing_alone_by_default(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:2])
		invoices = [row.billing_doc for row in doc.participants]
		cancel_session(doc.name, cancel_billing=0)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", invoices[0], "status"),
			"Paid & Verified",
		)

	def test_cancel_session_can_cancel_billing_too(self):
		doc = self._open(courts=(BGC_3,))
		doc = self._add(doc.name, PLAYERS[2:4])
		invoices = [row.billing_doc for row in doc.participants]
		cancel_session(doc.name, cancel_billing=1, reason="test: rained out")
		for name in invoices:
			self.assertEqual(
				frappe.db.get_value("CBT Booking Invoice", name, "status"), "Cancelled"
			)
			self.assertTrue(frappe.db.exists("CBT Booking Invoice", name))

	# ------------------------------------------------------------------
	# Rotation
	# ------------------------------------------------------------------

	def test_start_seats_four_per_court(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open()
			doc = self._add(doc.name, PLAYERS[:8])
			start_session(doc.name)
		doc = self._reload(doc)
		court_1 = self._assignment(doc, BGC_1)
		court_2 = self._assignment(doc, BGC_2)
		self.assertIsNotNone(court_1)
		self.assertIsNotNone(court_2)
		self.assertEqual(
			[court_1.player_1, court_1.player_2, court_1.player_3, court_1.player_4],
			PLAYERS[:4],
		)
		self.assertEqual(court_2.player_1, PLAYERS[4])
		self.assertEqual(court_1.ends_at, T0 + timedelta(minutes=15))
		self.assertFalse(self._waiting(doc))

	def test_start_needs_four_players(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:3])
		self.assertRaises(frappe.ValidationError, start_session, doc.name)

	def test_start_twice_rejected(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:4])
		start_session(doc.name)
		self.assertRaises(frappe.ValidationError, start_session, doc.name)

	def test_game_done_rotates_the_queue(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open(courts=(BGC_1,))
			doc = self._add(doc.name, PLAYERS[:8])
			start_session(doc.name)
			game_done(doc.name, BGC_1)
		doc = self._reload(doc)

		# The four who just played go to the BACK in seat order with their
		# counter bumped; the four who were waiting take the court.
		court = self._assignment(doc, BGC_1)
		self.assertEqual(
			[court.player_1, court.player_2, court.player_3, court.player_4],
			PLAYERS[4:8],
		)
		waiting = self._waiting(doc)
		self.assertEqual([row.customer for row in waiting], PLAYERS[:4])
		self.assertEqual([row.queue_position for row in waiting], [1, 2, 3, 4])
		self.assertEqual({row.games_played for row in waiting}, {1})

	def test_court_x_then_backfill(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open(courts=(BGC_1,))
			doc = self._add(doc.name, PLAYERS[:5])
			start_session(doc.name)
			return_to_queue(doc.name, BGC_1, PLAYERS[0])
		doc = self._reload(doc)
		court = self._assignment(doc, BGC_1)
		self.assertIsNone(court.player_1)
		# Sat out, still in the session, at the back of the line.
		self.assertEqual(
			[row.customer for row in self._waiting(doc)], [PLAYERS[4], PLAYERS[0]]
		)

		backfill(doc.name, BGC_1, PLAYERS[4])
		doc = self._reload(doc)
		court = self._assignment(doc, BGC_1)
		self.assertEqual(court.player_1, PLAYERS[4])
		self.assertEqual([row.customer for row in self._waiting(doc)], [PLAYERS[0]])

	def test_backfill_rejects_a_full_court(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open(courts=(BGC_1,))
			doc = self._add(doc.name, PLAYERS[:5])
			start_session(doc.name)
		self.assertRaises(frappe.ValidationError, backfill, doc.name, BGC_1, PLAYERS[4])

	def test_queue_x_marks_left_and_reindexes(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:5])
		remove_from_session(doc.name, PLAYERS[1])
		doc = self._reload(doc)
		waiting = self._waiting(doc)
		self.assertEqual(
			[row.customer for row in waiting],
			[PLAYERS[0], PLAYERS[2], PLAYERS[3], PLAYERS[4]],
		)
		self.assertEqual([row.queue_position for row in waiting], [1, 2, 3, 4])
		left = next(row for row in doc.queue if row.customer == PLAYERS[1])
		self.assertEqual(left.status, "Left")
		self.assertEqual(left.queue_position, 0)

	def test_queue_x_rejects_a_player_on_court(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open(courts=(BGC_1,))
			doc = self._add(doc.name, PLAYERS[:4])
			start_session(doc.name)
		self.assertRaises(
			frappe.ValidationError, remove_from_session, doc.name, PLAYERS[0]
		)

	def test_reorder_queue(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:4])
		waiting = self._waiting(doc)
		reversed_names = [row.name for row in reversed(waiting)]
		reorder_queue(doc.name, frappe.as_json(reversed_names))
		doc = self._reload(doc)
		self.assertEqual(
			[row.customer for row in self._waiting(doc)],
			list(reversed([row.customer for row in waiting])),
		)

	def test_reorder_rejects_a_stale_board(self):
		doc = self._open()
		doc = self._add(doc.name, PLAYERS[:4])
		names = [row.name for row in self._waiting(doc)]
		self.assertRaises(
			frappe.ValidationError, reorder_queue, doc.name, frappe.as_json(names[:2])
		)

	def test_auto_rotate_only_when_enabled_and_due(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open(courts=(BGC_1,))
			doc = self._add(doc.name, PLAYERS[:8])
			start_session(doc.name)

		# Not due yet.
		with patch(CLOCK, return_value=T0 + timedelta(minutes=5)):
			self.assertEqual(auto_rotate_tick(doc.name)["rotated"], [])

		# Due, but auto-rotate is off: a timer reading "TIME!" is not a licence
		# to rotate on its own — staff click Game Done.
		with patch(CLOCK, return_value=T0 + timedelta(minutes=16)):
			self.assertEqual(auto_rotate_tick(doc.name)["rotated"], [])

		doc = self._reload(doc)
		doc.auto_rotate = 1
		doc.save()
		with patch(CLOCK, return_value=T0 + timedelta(minutes=16)):
			self.assertEqual(auto_rotate_tick(doc.name)["rotated"], [BGC_1])
		doc = self._reload(doc)
		self.assertEqual([row.customer for row in self._waiting(doc)], PLAYERS[:4])

	# ------------------------------------------------------------------
	# Clock-free proofs (PLAN §8q)
	# ------------------------------------------------------------------

	def test_participant_proof_carries_no_clocks(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		row = doc.participants[0]
		result = create_participant_proof(
			doc.name, row.name, "proof_sample.jpg", PROOF_JPG.read_bytes()
		)

		proof = frappe.get_doc("CBT Payment Proof", result["proof"])
		self.assertEqual(proof.status, "Pending")
		self.assertEqual(proof.source, "Staff")
		self.assertEqual(proof.open_play_session, doc.name)
		self.assertEqual(proof.participant_ref, row.name)
		self.assertFalse(proof.booking)
		self.assertEqual(proof.company, AYALA)
		self.assertEqual(self._reload(doc).participants[0].proof_ref, proof.name)

		# The exemption is STRUCTURAL, not a convention: the open play schema
		# has nowhere to put a deadline, and the sweep only reads bookings — so
		# a proof with no booking is unreachable by both clocks. (Asserted here
		# rather than by running the sweep, which would expire seeded holds for
		# every later test in this class.)
		fieldnames = {
			df.fieldname for df in frappe.get_meta("CBT Open Play Session").fields
		} | {
			df.fieldname for df in frappe.get_meta("CBT Open Play Participant").fields
		}
		self.assertFalse(
			{"reservation_expires_at", "verification_deadline_at"} & fieldnames
		)

	def test_proof_belongs_to_exactly_one_parent(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		booking = frappe.db.get_value("CBT Court Booking", {"company": AYALA}, "name")

		both = frappe.get_doc(
			{
				"doctype": "CBT Payment Proof",
				"booking": booking,
				"open_play_session": doc.name,
				"participant_ref": doc.participants[0].name,
				"file": "/private/files/nope.jpg",
				"source": "Staff",
			}
		)
		self.assertRaises(frappe.ValidationError, both.insert, ignore_permissions=True)

		neither = frappe.get_doc(
			{
				"doctype": "CBT Payment Proof",
				"file": "/private/files/nope.jpg",
				"source": "Staff",
			}
		)
		self.assertRaises(
			frappe.ValidationError, neither.insert, ignore_permissions=True
		)

	def test_confirm_accepts_the_pending_proof(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		row = doc.participants[0]
		result = create_participant_proof(
			doc.name, row.name, "proof_sample.jpg", PROOF_JPG.read_bytes()
		)
		confirm_participant_payment(doc.name, row.name)
		self.assertEqual(
			frappe.db.get_value("CBT Payment Proof", result["proof"], "status"),
			"Accepted",
		)

	def test_payment_may_be_confirmed_after_the_session_ends(self):
		"""Finance really does confirm a transfer the next working day."""
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]], method="Fund Transfer")
		row = doc.participants[0]
		complete_session(doc.name)
		confirm_participant_payment(doc.name, row.name)
		self.assertEqual(self._reload(doc).participants[0].payment_status, "Paid")

	def test_proof_rejected_for_a_paid_participant(self):
		doc = self._open()
		doc = self._add(doc.name, [PLAYERS[0]])
		row = doc.participants[0]
		self.assertRaises(
			frappe.ValidationError,
			create_participant_proof,
			doc.name,
			row.name,
			"proof_sample.jpg",
			PROOF_JPG.read_bytes(),
		)

	# ------------------------------------------------------------------
	# Board payload
	# ------------------------------------------------------------------

	def test_board_payload_shape(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open()
			doc = self._add(doc.name, PLAYERS[:4])
			start_session(doc.name)
			payload = get_open_play_board(session=doc.name, company=AYALA)

		self.assertEqual(payload["company"], AYALA)
		self.assertTrue(payload["server_now"])
		# The clock is patched to the session's own date, so it is "today".
		self.assertIn(doc.name, {row["name"] for row in payload["sessions"]})

		state = payload["session"]
		self.assertEqual(state["name"], doc.name)
		self.assertEqual(len(state["assignments"]), 1)
		self.assertEqual(len(state["assignments"][0]["players"]), 4)
		self.assertEqual(state["assignments"][0]["vacancies"], 0)
		self.assertEqual(len(state["participants"]), 4)
		self.assertTrue(all(p["invoice_status"] for p in state["participants"]))
