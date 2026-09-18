"""
Court Booking Tech — Open play walk-ins (section-20, Backlog B2)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_open_play_walkin

The stranger with cash who wants to join tonight's session. `customer` on the
participant and queue rows is optional; a free-text `customer_name` (+ optional
`customer_phone`) carries who they are; and the rotation engine stops keying on
`customer` altogether.

THE CENTRAL RISK THIS FILE EXISTS TO PIN is not the schema — it is the KEY.
Before this section every rotation action addressed a player by their user id,
so two account-less players would both have been `None` and `_queue_row` would
have handed the first walk-in it met to whichever ✕ was pressed. The engine now
keys on `_player_key(queue_row)` = the account id, or the QUEUE ROW NAME for a
walk-in. `test_two_same_named_walkins_are_separate_people` is the row that would
catch a regression there, and it is deliberately built out of two players who
are indistinguishable by every other field.

STANDALONE CLASSES on purpose (S13 as-built 11): subclassing the existing
`TestOpenPlay` would re-run every one of its methods here.

BACKEND MONTH: **March 2028**, verified free BOTH ways before it was claimed
(S13 as-built 1b) — no `2028-` date string exists anywhere in the app, and the
only months claimed as INTEGERS are 4, 8 and 9 (all year-scoped to 2027), by
test_reports. Clock is monkeypatched, never wall-clock.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from court_booking_tech.api.open_play import (
	_player_key,
	_queue_row,
	add_players,
	backfill,
	confirm_participant_payment,
	game_done,
	get_open_play_board,
	open_session,
	remove_from_session,
	return_to_queue,
	start_session,
)
from court_booking_tech.seeds.seed_test_data import OPEN_PLAY_CUSTOMERS, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"

STELLA = "staff.ayala@example.com"  # AYALA staff — a User, but NOT a customer
QUINTIN = "admin.qcsm@example.com"  # QCSM admin — the cross-tenant actor
MIA = "cust.mia@example.com"  # AYALA VIP member (2025-01-01 -> 2030-12-31)
PIA = "cust.pia@example.com"  # no membership anywhere

# March 2028 — this module's own month (see the docstring).
TEST_DATE = "2028-03-10"  # Friday; AYALA-bgc is open 06:00-22:00
T0 = datetime(2028, 3, 10, 9, 0)

BGC_1 = "AYALA-bgc-court-1"
BGC_2 = "AYALA-bgc-court-2"
BGC_3 = "AYALA-bgc-court-3"
MAKATI_A = "AYALA-makati-court-a"
QCSM_COURT = "QCSM-timog-court-1"

CLOCK = "court_booking_tech.clock.now_dt"

PLAYERS = [row[0] for row in OPEN_PLAY_CUSTOMERS]

WALKIN = "Wanda Cruz"
WALKIN_PHONE = "0917-555-0101"
# Two people who share a name. This is the whole point of the keying pin: they
# are distinguishable by NOTHING except their row identity.
TWIN = "Twin Tamayo"


class OpenPlayWalkInTestCase(FrappeTestCase):
	"""Fixtures only — no test methods, so it is safe to inherit."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ------------------------------------------------------------------
	# Fixtures
	# ------------------------------------------------------------------

	def _session(self, courts=(BGC_1,), **overrides):
		payload = {
			"doctype": "CBT Open Play Session",
			"branch": "AYALA-bgc",
			"title": "Walk-in Open Play",
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
		if not frappe.db.exists("CBT Open Play Session", name):
			return
		session = frappe.get_doc("CBT Open Play Session", name)
		for row in session.participants or []:
			if row.billing_doc:
				frappe.delete_doc(
					"CBT Booking Invoice", row.billing_doc, force=True,
					ignore_permissions=True, ignore_missing=True,
				)
		for block in frappe.get_all(
			"CBT Slot Block", filters={"open_play_session": name}, pluck="name"
		):
			frappe.delete_doc(
				"CBT Slot Block", block, force=True, ignore_permissions=True,
				ignore_missing=True,
			)
		frappe.delete_doc(
			"CBT Open Play Session", name, force=True, ignore_permissions=True,
			ignore_missing=True,
		)

	def _open(self, **kwargs):
		doc = self._session(**kwargs)
		open_session(doc.name)
		return frappe.get_doc("CBT Open Play Session", doc.name)

	def _reload(self, doc):
		return frappe.get_doc("CBT Open Play Session", doc.name)

	def _add(self, session, entries):
		add_players(session, frappe.as_json(entries))
		return frappe.get_doc("CBT Open Play Session", session)

	def _add_walkin(self, session, name=WALKIN, phone=None, method="Cash"):
		entry = {"customer_name": name, "payment_method": method}
		if phone:
			entry["customer_phone"] = phone
		return self._add(session, [entry])

	def _add_accounts(self, session, emails, method="Cash"):
		return self._add(
			session, [{"customer": e, "payment_method": method} for e in emails]
		)

	def _walkin_rows(self, doc, name=WALKIN):
		"""Queue rows for the account-less players called `name`, in idx order."""
		return [
			row
			for row in doc.queue
			if not row.customer and row.customer_name == name
		]

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

	def _seated_keys(self, doc, court):
		assignment = self._assignment(doc, court)
		return [
			assignment.get(field)
			for field in ("player_1", "player_2", "player_3", "player_4")
			if assignment.get(field)
		]


class TestWalkInJoinsAndPays(OpenPlayWalkInTestCase):
	def test_cash_walkin_is_paid_on_the_spot_under_their_own_name(self):
		doc = self._open()
		doc = self._add_walkin(doc.name, phone=WALKIN_PHONE)

		row = doc.participants[0]
		self.assertIsNone(row.customer, "a walk-in must carry no account")
		self.assertEqual(row.customer_name, WALKIN)
		self.assertEqual(row.customer_phone, WALKIN_PHONE)
		self.assertEqual(row.payment_status, "Paid")
		self.assertEqual(flt(row.fee), 150.0)
		self.assertEqual(flt(row.discount_percent), 0.0)
		self.assertTrue(row.checked_in_at)

		invoice = frappe.get_doc("CBT Booking Invoice", row.billing_doc)
		self.assertEqual(invoice.status, "Paid & Verified")
		self.assertEqual(invoice.customer_name, WALKIN)
		self.assertFalse(invoice.customer)
		self.assertEqual(invoice.participant_ref, row.name)
		self.assertEqual(flt(invoice.total_amount), 150.0)

		# They are in the rotation like anyone else.
		queue_row = self._walkin_rows(doc)[0]
		self.assertEqual(queue_row.status, "Waiting")
		self.assertEqual(queue_row.participant_ref, row.name)
		self.assertEqual(doc.current_participants, 1)
		self.assertEqual(flt(doc.total_revenue), 150.0)

	def test_fund_transfer_walkin_waits_for_staff_then_confirms(self):
		"""Clock-free (PLAN §8q): the player is standing at the desk, so a
		transfer is plain Unpaid-until-confirmed with no deadline anywhere."""
		doc = self._open()
		doc = self._add_walkin(doc.name, method="Fund Transfer")
		row = doc.participants[0]
		self.assertEqual(row.payment_status, "Unpaid")
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "status"),
			"Unpaid",
		)
		self.assertEqual(flt(self._reload(doc).total_revenue), 0.0)

		confirm_participant_payment(doc.name, row.name)
		doc = self._reload(doc)
		self.assertEqual(doc.participants[0].payment_status, "Paid")
		self.assertEqual(doc.participants[0].customer_name, WALKIN)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "status"),
			"Paid & Verified",
		)

	def test_free_walkin_is_never_billed_the_entry_fee(self):
		doc = self._open()
		doc = self._add_walkin(doc.name, method="Free")
		row = doc.participants[0]
		self.assertEqual(flt(row.fee), 0.0)
		self.assertEqual(row.payment_status, "Paid")
		self.assertEqual(
			flt(frappe.db.get_value("CBT Booking Invoice", row.billing_doc, "total_amount")),
			0.0,
		)

	def test_statement_prints_the_typed_name(self):
		"""The honest-receipt requirement — the reason B1/B2 chose free text over
		one shared "Walk-in" account per company."""
		doc = self._open()
		doc = self._add_walkin(doc.name)
		html = frappe.get_print(
			"CBT Booking Invoice",
			doc.participants[0].billing_doc,
			print_format="CBT Billing Statement",
		)
		self.assertIn(WALKIN, html)
		self.assertIn("PAID &amp; VERIFIED", html)
		# A blank or a literal "None" on a printed receipt is the failure mode
		# this whole story exists to avoid.
		self.assertNotIn("Customer:</strong> None", html)

	def test_walkin_leaving_unpaid_has_their_document_cancelled(self):
		doc = self._open()
		doc = self._add_walkin(doc.name, method="Fund Transfer")
		invoice = doc.participants[0].billing_doc
		key = _player_key(self._walkin_rows(doc)[0])

		remove_from_session(doc.name, key)
		self.assertEqual(
			frappe.db.get_value("CBT Booking Invoice", invoice, "status"), "Cancelled"
		)
		# Retained with its number consumed, never deleted (PLAN §8e).
		self.assertTrue(frappe.db.exists("CBT Booking Invoice", invoice))


class TestWalkInIdentityRules(OpenPlayWalkInTestCase):
	def test_an_entry_with_neither_identity_is_refused(self):
		doc = self._open()
		self.assertRaises(
			frappe.ValidationError,
			self._add,
			doc.name,
			[{"payment_method": "Cash"}],
		)

	def test_an_entry_with_both_identities_is_refused(self):
		"""A caller that sends both is contradicting itself. Resolving it
		silently would let a typo'd name ride along on an account booking and
		print on the statement."""
		doc = self._open()
		self.assertRaises(
			frappe.ValidationError,
			self._add,
			doc.name,
			[{"customer": PIA, "customer_name": WALKIN, "payment_method": "Cash"}],
		)

	def test_a_blank_walkin_name_is_refused(self):
		doc = self._open()
		self.assertRaises(
			frappe.ValidationError,
			self._add,
			doc.name,
			[{"customer_name": "   ", "payment_method": "Cash"}],
		)

	def test_account_entries_are_still_validated(self):
		"""The walk-in branch must not become a way past _validate_customer."""
		doc = self._open()
		self.assertRaises(
			frappe.ValidationError, self._add_accounts, doc.name, [STELLA]
		)
		self.assertRaises(
			frappe.ValidationError,
			self._add_accounts,
			doc.name,
			["nobody.at.all@example.com"],
		)

	def test_an_account_still_cannot_join_twice(self):
		doc = self._open()
		doc = self._add_accounts(doc.name, [PIA])
		self.assertRaises(frappe.ValidationError, self._add_accounts, doc.name, [PIA])

	def test_a_walkin_phone_is_never_stored_against_an_account(self):
		"""`customer_phone` is a WALK-IN field (PLAN §8j) — an account holder's
		number lives on their profile, and a value no screen ever shows is a
		privacy liability, not a feature."""
		doc = self._open()
		doc = self._add(
			doc.name,
			[{"customer": PIA, "customer_phone": "0917-000-9999", "payment_method": "Cash"}],
		)
		self.assertFalse(doc.participants[0].customer_phone)

	def test_the_controller_refuses_a_row_with_no_identity(self):
		"""The stored-row rule, which is weaker than the payload rule on
		purpose: an account row legitimately carries BOTH halves because
		fetch_from derives the name server-side. What it may never be is
		anonymous."""
		doc = self._open()
		doc.append("queue", {"status": "Waiting", "queue_position": 1})
		doc.flags.via_open_play_engine = True
		self.assertRaises(frappe.ValidationError, doc.save)

		doc = self._reload(doc)
		doc.append("participants", {"fee": 150, "payment_method": "Cash"})
		doc.flags.via_open_play_engine = True
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_player_key_refuses_an_unsaved_row(self):
		"""`_init_child` never assigns `name` before the parent is saved, so an
		appended walk-in row would key as None — which is exactly the aliasing
		_player_key exists to prevent. It must fail loud, not return a falsy
		key that quietly collapses two people into one."""
		doc = self._open()
		unsaved = doc.append("queue", {"customer_name": WALKIN, "status": "Waiting"})
		self.assertIsNone(getattr(unsaved, "name", None))
		self.assertRaises(frappe.ValidationError, _player_key, unsaved)

	def test_assignment_player_fields_are_plain_data(self):
		"""Schema pin for the Link -> Data flip. `player_1..4` hold a KEY, which
		for a walk-in is a queue-row name and can never resolve as a User — a
		leftover `options: User` would make frappe flag invalid Data-field
		metadata and would re-invite a get_doc("User", ...) somewhere."""
		meta = frappe.get_meta("CBT Open Play Assignment")
		for fieldname in ("player_1", "player_2", "player_3", "player_4"):
			df = meta.get_field(fieldname)
			self.assertEqual(df.fieldtype, "Data", fieldname)
			self.assertFalse(df.options, f"{fieldname} still carries options")


class TestWalkInKeying(OpenPlayWalkInTestCase):
	"""THE KEYING PIN — the section's load-bearing wall."""

	def _twins_and_two_accounts(self):
		doc = self._open()
		self._add_walkin(doc.name, name=TWIN)
		self._add_walkin(doc.name, name=TWIN)
		doc = self._add_accounts(doc.name, PLAYERS[:2])
		return doc

	def test_two_same_named_walkins_are_two_people(self):
		doc = self._twins_and_two_accounts()
		rows = self._walkin_rows(doc, TWIN)
		self.assertEqual(len(rows), 2, "both walk-ins must get their own row")

		key_a, key_b = _player_key(rows[0]), _player_key(rows[1])
		self.assertNotEqual(key_a, key_b)
		# The key is the row's own name, not the account (there is none) and not
		# the typed name (which they share).
		self.assertEqual({key_a, key_b}, {rows[0].name, rows[1].name})
		# And the lookup does not cross-match them.
		self.assertEqual(_queue_row(doc, key_a).name, rows[0].name)
		self.assertEqual(_queue_row(doc, key_b).name, rows[1].name)

		# Their money is separate too — two people, two statements.
		invoices = [
			row.billing_doc for row in doc.participants if row.customer_name == TWIN
		]
		self.assertEqual(len(set(invoices)), 2)

	def test_each_twin_is_seated_and_benched_independently(self):
		with patch(CLOCK, return_value=T0):
			doc = self._twins_and_two_accounts()
			start_session(doc.name)
		doc = self._reload(doc)

		rows = self._walkin_rows(doc, TWIN)
		key_a, key_b = _player_key(rows[0]), _player_key(rows[1])
		seated = self._seated_keys(doc, BGC_1)
		self.assertEqual(len(seated), 4)
		self.assertIn(key_a, seated)
		self.assertIn(key_b, seated)

		# Court ✕ on ONE twin. The other must not move — under the old
		# customer-keyed lookup both were `None` and this took the first row it
		# met, which would be a 50/50 coin flip on which player left the court.
		return_to_queue(doc.name, BGC_1, key_a)
		doc = self._reload(doc)
		seated = self._seated_keys(doc, BGC_1)
		self.assertNotIn(key_a, seated)
		self.assertIn(key_b, seated)
		self.assertEqual(
			_queue_row(doc, key_a).status, "Waiting", "twin A should be benched"
		)
		self.assertEqual(
			_queue_row(doc, key_b).status, "Playing", "twin B should still be playing"
		)

		# ...and backfilling puts exactly that twin back.
		backfill(doc.name, BGC_1, key_a)
		doc = self._reload(doc)
		self.assertIn(key_a, self._seated_keys(doc, BGC_1))
		self.assertEqual(_queue_row(doc, key_a).status, "Playing")

	def test_removing_one_twin_leaves_the_other_in_the_session(self):
		with patch(CLOCK, return_value=T0):
			doc = self._twins_and_two_accounts()
			start_session(doc.name)
		doc = self._reload(doc)
		rows = self._walkin_rows(doc, TWIN)
		key_a, key_b = _player_key(rows[0]), _player_key(rows[1])

		return_to_queue(doc.name, BGC_1, key_b)
		remove_from_session(doc.name, key_b)
		doc = self._reload(doc)

		self.assertEqual(_queue_row(doc, key_b, status="Left").name, rows[1].name)
		self.assertIsNone(_queue_row(doc, key_b), "twin B is gone for good")
		self.assertEqual(_queue_row(doc, key_a).status, "Playing")
		self.assertEqual(doc.current_participants, 3)

	def test_a_mixed_four_rotates_by_key(self):
		"""Accounts and walk-ins share one queue and one rotation. Game Done
		sends the four who played to the BACK in seat order and seats the next
		four — proven with keys on both sides of the mix."""
		with patch(CLOCK, return_value=T0):
			doc = self._open()
			self._add_walkin(doc.name, name="First Walkin")
			self._add_accounts(doc.name, PLAYERS[:2])
			self._add_walkin(doc.name, name="Second Walkin")
			doc = self._add_accounts(doc.name, PLAYERS[2:6])
			start_session(doc.name)
			doc = self._reload(doc)

			first_four = self._seated_keys(doc, BGC_1)
			waiting_before = [_player_key(row) for row in self._waiting(doc)]
			self.assertEqual(len(first_four), 4)
			self.assertEqual(len(waiting_before), 4)

			game_done(doc.name, BGC_1)
		doc = self._reload(doc)

		self.assertEqual(self._seated_keys(doc, BGC_1), waiting_before)
		self.assertEqual([_player_key(row) for row in self._waiting(doc)], first_four)
		self.assertEqual(
			{int(row.games_played or 0) for row in self._waiting(doc)},
			{1},
			"everyone who played gets exactly one game",
		)

	def test_the_board_payload_carries_a_key_for_every_player(self):
		with patch(CLOCK, return_value=T0):
			doc = self._twins_and_two_accounts()
			start_session(doc.name)
			payload = get_open_play_board(session=doc.name, company=AYALA)
		state = payload["session"]

		for row in state["queue"]:
			self.assertTrue(row["player_key"], row)
			self.assertTrue(row["customer_name"], row)
		# An account player's key IS their user id — which is what kept every
		# pre-B2 assertion and selector true across the migration.
		account_rows = [row for row in state["queue"] if row["customer"]]
		self.assertTrue(account_rows)
		for row in account_rows:
			self.assertEqual(row["player_key"], row["customer"])
		# A walk-in ships a key and a name, and NO account.
		walkin_rows = [row for row in state["queue"] if not row["customer"]]
		self.assertEqual(len(walkin_rows), 2)
		for row in walkin_rows:
			self.assertEqual(row["customer_name"], TWIN)
			self.assertNotEqual(row["player_key"], TWIN)

		players = state["assignments"][0]["players"]
		self.assertEqual(len(players), 4)
		self.assertEqual(
			{player["key"] for player in players},
			set(self._seated_keys(self._reload(doc), BGC_1)),
		)
		# The court card renders `name`, so a walk-in must never fall back to
		# showing a row hash to the room.
		for player in players:
			self.assertTrue(player["name"])
			self.assertNotEqual(player["name"], player["key"])

	def test_the_board_payload_never_ships_a_phone(self):
		"""PLAN §8j: TV mode renders this very payload on an unattended screen."""
		doc = self._open()
		self._add_walkin(doc.name, phone=WALKIN_PHONE)
		payload = get_open_play_board(session=doc.name, company=AYALA)
		state = payload["session"]
		self.assertNotIn(WALKIN_PHONE, frappe.as_json(state))
		for row in state["participants"] + state["queue"]:
			self.assertNotIn("customer_phone", row)


class TestWalkInMembershipAndBans(OpenPlayWalkInTestCase):
	def test_a_walkin_never_gets_a_membership_discount(self):
		"""Same person's NAME, no account: the discount is a plain 0, never a
		lookup. Open play prices membership from the SESSION (S11), so the
		account control below proves the seam still works."""
		member_name = frappe.db.get_value("User", MIA, "full_name")
		doc = self._open(member_discount_percent=15)

		# Pinned clock: MIA's membership runs 2025-01-01 -> 2030-12-31, so this
		# would pass on the wall clock for years — and a test whose result
		# depends on WHEN you run it is unsound, not flaky (Backlog B8).
		with patch(CLOCK, return_value=T0):
			doc = self._add_accounts(doc.name, [MIA])
		self.assertEqual(flt(doc.participants[0].discount_percent), 15.0)
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"CBT Booking Invoice", doc.participants[0].billing_doc, "total_amount"
				)
			),
			127.5,
		)

		doc = self._add_walkin(doc.name, name=member_name)
		walkin = doc.participants[1]
		self.assertIsNone(walkin.customer)
		self.assertEqual(walkin.customer_name, member_name)
		self.assertEqual(flt(walkin.discount_percent), 0.0)
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"CBT Booking Invoice", walkin.billing_doc, "total_amount"
				)
			),
			150.0,
		)

	def test_there_is_no_ban_gate_for_a_walkin_to_skip(self):
		"""RECORDED FINDING (section-20), pinned rather than changed.

		The section planned "walk-in rows skip the ban check". There is no ban
		check to skip: `add_players` has never consulted `CBT Customer Ban`, and
		`bans.ensure_customer_not_banned` is called from exactly one site — the
		booking controller's `customer_created` branch. A ban is scoped to
		booking ONLINE (its own message says so); open play is a staffed desk
		transaction with the person standing in front of you. The ACCOUNT half
		of that rule is already pinned by
		`test_bans.test_open_play_still_admits_a_banned_customer`; this row
		pins the structural half, so that a future section that decides open
		play SHOULD honour bans changes it deliberately — and for both kinds of
		player, since a walk-in has no account to check at all.

		The tripwire is deliberately narrow: it names the ban FUNCTIONS rather
		than the doctype, because a doctype string would also fire on a comment
		that merely mentions bans.
		"""
		import inspect

		from court_booking_tech.api import open_play as engine

		source = inspect.getsource(engine)
		self.assertNotIn("ensure_customer_not_banned", source)
		self.assertNotIn("is_banned", source)

		doc = self._open()
		doc = self._add_walkin(doc.name)
		self.assertIsNone(doc.participants[0].customer)


class TestWalkInIsolation(OpenPlayWalkInTestCase):
	"""The new payload SHAPE on a permission-sensitive endpoint earns its own
	refusal rows (the S18 lesson), even though the signature did not change."""

	def test_a_customer_cannot_add_a_walkin(self):
		doc = self._open()
		frappe.set_user(PIA)
		self.assertRaises(
			frappe.PermissionError,
			add_players,
			doc.name,
			frappe.as_json([{"customer_name": WALKIN, "payment_method": "Cash"}]),
		)

	def test_another_tenant_cannot_add_a_walkin(self):
		doc = self._open()
		frappe.set_user(QUINTIN)
		self.assertRaises(
			frappe.PermissionError,
			add_players,
			doc.name,
			frappe.as_json([{"customer_name": WALKIN, "payment_method": "Cash"}]),
		)

	def test_a_customer_cannot_move_a_walkin_around_the_board(self):
		with patch(CLOCK, return_value=T0):
			doc = self._open()
			self._add_walkin(doc.name)
			doc = self._add_accounts(doc.name, PLAYERS[:3])
			start_session(doc.name)
		doc = self._reload(doc)
		key = _player_key(self._walkin_rows(doc)[0])

		frappe.set_user(PIA)
		self.assertRaises(
			frappe.PermissionError, return_to_queue, doc.name, BGC_1, key
		)
		self.assertRaises(frappe.PermissionError, remove_from_session, doc.name, key)

	def test_a_walkin_is_invisible_to_every_portal_session(self):
		"""RECORDED FINDING (section-20): there is no portal open-play surface
		at all — `api/portal.py` ships no participant data of any kind, so the
		invisibility is structural rather than filtered. What a customer session
		CAN reach is pinned here, which is nothing.
		"""
		doc = self._open()
		self._add_walkin(doc.name, phone=WALKIN_PHONE)

		frappe.set_user(PIA)
		self.assertRaises(
			frappe.PermissionError, get_open_play_board, session=doc.name, company=AYALA
		)
		self.assertRaises(frappe.PermissionError, get_open_play_board, company=AYALA)
		self.assertFalse(
			frappe.has_permission("CBT Open Play Session", doc=doc.name),
			"a customer must not be able to read an open play session",
		)
		# `frappe.get_list`, NOT `frappe.get_all` — get_all forces
		# `ignore_permissions=True` (frappe/__init__.py:1367, and its own
		# docstring says "will **not** check for permissions"), so it can never
		# prove a tenancy boundary. It returns the row for anyone. The house
		# pattern for a scoped list view is test_isolation.py:51-54.
		# A doctype the caller cannot read at all raises rather than returning
		# an empty page; both outcomes mean the same thing here.
		try:
			listed = frappe.get_list(
				"CBT Open Play Session",
				filters={"name": doc.name},
				pluck="name",
				limit_page_length=0,
			)
		except frappe.PermissionError:
			listed = []
		self.assertFalse(
			listed, "the session must not appear in a customer's list view"
		)

		# The portal module itself has no participant surface to leak through —
		# the structural half of the finding.
		import court_booking_tech.api.portal as portal_module

		exposed = [
			name
			for name in dir(portal_module)
			if "participant" in name.lower() or "open_play" in name.lower()
		]
		self.assertEqual(exposed, [], f"portal grew an open play surface: {exposed}")
