"""
Court Booking Tech — Payment channels (Backlog B29, Batch 13, 2026-08-27)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_payment_channels

WHERE the money landed: a per-tenant `CBT Payment Channel` beside the payment
method — Cash + GCash seeded, banks added, each disable-able — recorded on the
booking, the participant, the proof and the invoice, corrected by staff at
verification, recorded on the platform statement for OUR side, and read back
by two reports: the by-channel split and the journal-shaped Tenant Ledger.

MONTH. **September 2028** — banked free by the ledger (May/June 2028 belong to
test_booking_fee; nothing else reaches 2028). 2028-09-06 is a Wednesday;
AYALA-makati is open every day, court-a/-b at ₱300 flat; QCSM-timog court-1 is
₱350 (timog closes Sundays — Wednesday is safe).

TENANTS. AYALA (VAT 12%, Percentage 10%) seeds THREE channels — Cash, GCash
and a BDO bank — QCSM (NON-VAT, Subscription ₱2,999) two. Nothing here changes
a billing mode, so test_reports' and test_booking_fee's ground truth is
untouched; channels a test disables are re-enabled in addCleanup.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate

from court_booking_tech.api.bookings import (
	cancel_booking,
	confirm_booking,
	create_booking,
	extend_booking,
	reschedule_booking,
)
from court_booking_tech.api.channels import list_company_channels, list_platform_channels
from court_booking_tech.api.open_play import (
	add_players,
	confirm_participant_payment,
	open_session,
)
from court_booking_tech.api.portal import get_my_booking_detail, reserve_booking, resolve_book_page
from court_booking_tech.api.proofs import accept_proofs, create_proof
from court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close import (
	close_month,
)
from court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement import (
	issue_statements,
	mark_paid,
)
from court_booking_tech.court_booking_tech.report.cbt_collections_by_channel import (
	cbt_collections_by_channel,
)
from court_booking_tech.court_booking_tech.report.cbt_tenant_ledger import cbt_tenant_ledger
from court_booking_tech.payment_channels import (
	PLATFORM,
	default_channel,
	ensure_default_channels,
	list_channels,
	resolve_channel,
)
from court_booking_tech.seeds.seed_test_data import (
	OPEN_PLAY_CUSTOMERS,
	PLATFORM_ADMIN_EMAIL,
	seed_all,
)

AYALA = "ayala-courts"
QCSM = "qc-smash"
PIA = "cust.pia@example.com"
STELLA = "staff.ayala@example.com"
ALONA = "admin.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"

MAKATI_A = "AYALA-makati-court-a"
MAKATI_B = "AYALA-makati-court-b"
QCSM_1 = "QCSM-timog-court-1"

DAY = "2028-09-06"  # Wednesday
NEXT_DAY = "2028-09-07"
T0 = datetime(2028, 9, 6, 8, 0)
T_CANCEL = datetime(2028, 9, 7, 9, 0)
AFTER_SEP = datetime(2028, 10, 3, 10, 0)
PAY_DAY = datetime(2028, 10, 10, 10, 0)
PERIOD = "2028-09"

CLOCK = "court_booking_tech.clock.now_dt"
PROOF_JPG = Path(__file__).resolve().parents[1] / "seeds" / "files" / "proof_sample.jpg"


def _channel(company, label):
	name = frappe.db.get_value("CBT Payment Channel", {"company": company, "label": label}, "name")
	assert name, f"{company} has no channel labelled {label}"
	return name


def _platform_channel(label):
	name = frappe.db.get_value(
		"CBT Payment Channel", {"scope": PLATFORM, "label": label}, "name"
	)
	assert name, f"the platform has no channel labelled {label}"
	return name


class ChannelTestCase(FrappeTestCase):
	"""Shared fixtures; holds NO test methods (S13 as-built 11)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _set_enabled(self, name, enabled):
		"""Flip a channel and RESTORE it afterwards — a disabled seeded channel
		would leak into every later module."""
		before = frappe.db.get_value("CBT Payment Channel", name, "enabled")
		frappe.db.set_value("CBT Payment Channel", name, "enabled", 1 if enabled else 0)

		def _restore():
			frappe.set_user("Administrator")
			frappe.db.set_value("CBT Payment Channel", name, "enabled", before)

		self.addCleanup(_restore)

	def _book(self, court=MAKATI_A, start_time="10:00:00", method="Cash", date=DAY, at=T0, **overrides):
		payload = {
			"doctype": "CBT Court Booking",
			"court": court,
			"customer": PIA,
			"booking_date": date,
			"start_time": start_time,
			"number_of_slots": 1,
			"payment_method": method,
			"discount_percent": 0,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		with patch(CLOCK, return_value=at):
			doc.insert()
		self._cleanup_booking(doc.name)
		return doc

	def _cleanup_booking(self, name):
		"""Drop the booking AND its billing document. The two reports under test
		read invoices WITHOUT joining the booking (a document whose booking is
		gone is not a state production ever reaches — bookings are retained,
		§8e), so an orphaned invoice left by an earlier test would count again."""

		def _do():
			frappe.set_user("Administrator")
			invoice = frappe.db.get_value("CBT Court Booking", name, "billing_doc")
			frappe.delete_doc(
				"CBT Court Booking", name, force=True, ignore_permissions=True, ignore_missing=True
			)
			if invoice:
				frappe.delete_doc(
					"CBT Booking Invoice", invoice, force=True, ignore_permissions=True, ignore_missing=True
				)

		self.addCleanup(_do)

	def _reload(self, name):
		return frappe.get_doc("CBT Court Booking", name)

	def _invoice(self, booking_name):
		doc = self._reload(booking_name)
		self.assertTrue(doc.billing_doc, f"{booking_name} has no billing document")
		return frappe.get_doc("CBT Booking Invoice", doc.billing_doc)

	def _proof(self, booking_name, at, **kwargs):
		with patch(CLOCK, return_value=at):
			return create_proof(booking_name, "proof.jpg", PROOF_JPG.read_bytes(), **kwargs)


class TestChannelRows(ChannelTestCase):
	def test_seeds_gave_every_company_cash_and_gcash_and_ayala_a_bank(self):
		ayala = {row.label: row for row in list_channels(AYALA, enabled_only=False)}
		self.assertEqual(set(ayala), {"Cash", "GCash", "BDO"})
		self.assertEqual(ayala["Cash"].kind, "Cash")
		self.assertEqual(ayala["GCash"].kind, "Transfer")
		self.assertEqual(ayala["BDO"].kind, "Transfer")
		self.assertEqual(ayala["Cash"].account_label, "Cash on Hand")
		self.assertEqual(ayala["GCash"].account_label, "Cash in E-Wallet - GCash")
		self.assertEqual(ayala["GCash"].mode_of_payment, "GCash")
		qcsm = {row.label for row in list_channels(QCSM, enabled_only=False)}
		self.assertEqual(qcsm, {"Cash", "GCash"})
		# The seeded GCash rows carry the same numbers payment_instructions print.
		gcash = frappe.get_doc("CBT Payment Channel", _channel(AYALA, "GCash"))
		self.assertEqual(gcash.account_number, "0917-000-1111")
		self.assertRegex(gcash.name, r"^PCH-AYALA-GCASH-\d{2}$")
		# And the platform has its own pair, with no company.
		platform = {row.label: row for row in list_channels(scope=PLATFORM, enabled_only=False)}
		self.assertEqual(set(platform), {"Cash", "GCash"})
		self.assertIsNone(frappe.db.get_value("CBT Payment Channel", _platform_channel("GCash"), "company"))
		self.assertRegex(_platform_channel("GCash"), r"^PCH-PLATFORM-GCASH-\d{2}$")

	def test_a_new_company_starts_with_cash_and_gcash_and_takes_them_with_it(self):
		company = frappe.get_doc(
			{
				"doctype": "CBT Company",
				"slug": "channel-test",
				"company_code": "CHNT",
				"company_name": "Channel Test",
				"registered_name": "Channel Test Corp.",
				"vat_registration": "NON-VAT",
				"allow_self_branch_management": 0,
				"status": "Active",
				"office_hours": [
					{"day": "Monday", "is_open": 1, "opening_time": "09:00:00", "closing_time": "18:00:00"}
				],
			}
		)
		company.insert(ignore_permissions=True)

		def _drop():
			frappe.set_user("Administrator")
			if frappe.db.exists("CBT Company", company.name):
				frappe.delete_doc("CBT Company", company.name, force=True, ignore_permissions=True)

		self.addCleanup(_drop)
		rows = list_channels(company.name, enabled_only=False)
		self.assertEqual([(r.label, r.kind) for r in rows], [("Cash", "Cash"), ("GCash", "Transfer")])
		# Idempotent: a second call creates nothing (a tenant that disabled
		# GCash on purpose must not have it re-created by a migrate).
		self.assertEqual(ensure_default_channels(company.name), [])
		frappe.delete_doc("CBT Company", company.name, force=True, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("CBT Payment Channel", {"company": company.name}))

	def test_kind_company_and_scope_are_fixed_and_labels_are_unique_per_company(self):
		maya = frappe.get_doc(
			{
				"doctype": "CBT Payment Channel",
				"scope": "Tenant",
				"company": QCSM,
				"label": "Maya",
				"kind": "Transfer",
				"account_number": "0918-000-3333",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc(
				"CBT Payment Channel", maya.name, force=True, ignore_permissions=True, ignore_missing=True
			)
		)
		self.assertEqual(maya.mode_of_payment, "Maya")  # defaulted from the label
		self.assertEqual(maya.account_label, "Maya")
		self.assertRegex(maya.name, r"^PCH-QCSM-MAYA-\d{2}$")
		# A channel added later lands LAST — never the picker's new default
		# (E2E finding 2026-08-27: a blank sort order sorted it FIRST).
		self.assertEqual([r.label for r in list_channels(QCSM, kind="Transfer")], ["GCash", "Maya"])
		self.assertEqual(default_channel(QCSM, "Fund Transfer"), _channel(QCSM, "GCash"))

		for field, value, expect in (
			("kind", "Cash", "Kind is fixed"),
			("company", AYALA, "Company is fixed"),
			("scope", "Platform", "Scope is fixed"),
		):
			with self.subTest(field=field):
				doc = frappe.get_doc("CBT Payment Channel", maya.name)
				doc.set(field, value)
				with self.assertRaisesRegex(frappe.ValidationError, expect):
					doc.save(ignore_permissions=True)

		duplicate = frappe.get_doc(
			{"doctype": "CBT Payment Channel", "company": QCSM, "label": "  gcash ", "kind": "Transfer"}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "already exists"):
			duplicate.insert(ignore_permissions=True)
		# The same label at ANOTHER company is fine — labels are per company.
		self.assertTrue(_channel(AYALA, "GCash"))

		# A platform row carries no company even if one is sent.
		platform_bank = frappe.get_doc(
			{
				"doctype": "CBT Payment Channel",
				"scope": "Platform",
				"company": AYALA,
				"label": "UnionBank",
				"kind": "Transfer",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc(
				"CBT Payment Channel", platform_bank.name, force=True, ignore_permissions=True, ignore_missing=True
			)
		)
		self.assertIsNone(platform_bank.company)
		self.assertRegex(platform_bank.name, r"^PCH-PLATFORM-UNIONBANK-\d{2}$")

		# A tenant row must name its company (leak vector 2).
		with self.assertRaisesRegex(frappe.ValidationError, "Company is required"):
			frappe.get_doc(
				{"doctype": "CBT Payment Channel", "scope": "Tenant", "label": "Orphan", "kind": "Cash"}
			).insert(ignore_permissions=True)

	def test_customer_facing_text_is_trimmed_and_refuses_control_characters(self):
		doc = frappe.get_doc("CBT Payment Channel", _channel(QCSM, "GCash"))
		before = doc.account_name
		self.addCleanup(
			lambda: frappe.db.set_value("CBT Payment Channel", doc.name, "account_name", before)
		)
		doc.account_name = "  QC Smash  "
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.account_name, "QC Smash")
		doc.account_name = "QC\x00Smash"
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be shown to customers"):
			doc.save(ignore_permissions=True)

	def test_a_channel_with_payments_is_never_deleted_only_disabled(self):
		bdo = _channel(AYALA, "BDO")
		booking = self._book(method="Fund Transfer", payment_channel=bdo)
		self.assertEqual(booking.payment_channel, bdo)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("CBT Payment Channel", bdo, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("CBT Payment Channel", bdo))
		# Disabling is the way: hidden from new payments, kept on this one.
		self._set_enabled(bdo, False)
		self.assertNotIn(bdo, {r.name for r in list_channels(AYALA)})
		self.assertEqual(self._reload(booking.name).payment_channel, bdo)
		# A save that leaves the channel as stored still passes (history keeps
		# its channel)...
		doc = self._reload(booking.name)
		doc.notes = "channel disabled after the sale"
		doc.save()
		self.assertEqual(self._reload(booking.name).payment_channel, bdo)
		# ...but a NEW payment on it is refused.
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			self._book(start_time="11:00:00", method="Fund Transfer", payment_channel=bdo)

	def test_tenant_seats_read_their_own_channels_only_and_never_write(self):
		ayala_gcash = _channel(AYALA, "GCash")
		platform_gcash = _platform_channel("GCash")

		frappe.set_user(STELLA)
		mine = {row.company for row in frappe.get_list("CBT Payment Channel", fields=["company"], limit_page_length=0)}
		self.assertEqual(mine, {AYALA})
		self.assertFalse(frappe.get_doc("CBT Payment Channel", platform_gcash).has_permission("read"))
		doc = frappe.get_doc("CBT Payment Channel", ayala_gcash)
		self.assertTrue(doc.has_permission("read"))
		doc.enabled = 0
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(frappe.db.get_value("CBT Payment Channel", ayala_gcash, "enabled"), 1)
		with self.assertRaises(frappe.PermissionError):
			list_platform_channels()
		# The staff picker answers for the seat's own company only.
		self.assertEqual(
			[r.label for r in list_company_channels(AYALA, "Fund Transfer")], ["GCash", "BDO"]
		)
		self.assertEqual([r.label for r in list_company_channels(AYALA, "Cash")], ["Cash"])
		self.assertEqual(list_company_channels(AYALA, "Free"), [])
		with self.assertRaises(frappe.PermissionError):
			list_company_channels(QCSM)

		frappe.set_user(QUINTIN)
		self.assertFalse(frappe.get_doc("CBT Payment Channel", ayala_gcash).has_permission("read"))

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual([r.label for r in list_platform_channels()], ["Cash", "GCash"])
		everything = {row.company for row in frappe.get_list("CBT Payment Channel", fields=["company"], limit_page_length=0)}
		self.assertTrue({AYALA, QCSM, None} <= everything)


class TestBookingChannel(ChannelTestCase):
	def test_defaults_follow_the_kind_and_free_carries_none(self):
		cash = self._book(method="Cash")
		self.assertEqual(cash.payment_channel, _channel(AYALA, "Cash"))
		self.assertEqual(self._invoice(cash.name).payment_channel, _channel(AYALA, "Cash"))
		transfer = self._book(start_time="11:00:00", method="Fund Transfer")
		self.assertEqual(transfer.payment_channel, _channel(AYALA, "GCash"))  # first enabled transfer
		free = self._book(start_time="12:00:00", method="Free")
		self.assertIsNone(free.payment_channel)
		self.assertIsNone(self._invoice(free.name).payment_channel)
		with self.assertRaisesRegex(frappe.ValidationError, "Free payment carries no"):
			self._book(start_time="13:00:00", method="Free", payment_channel=_channel(AYALA, "Cash"))
		self.assertEqual(default_channel(AYALA, "Fund Transfer"), _channel(AYALA, "GCash"))
		self.assertIsNone(default_channel(AYALA, "Free"))

	def test_an_explicit_channel_must_match_the_kind_and_the_company(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Cash payment cannot go through"):
			self._book(method="Cash", payment_channel=_channel(AYALA, "GCash"))
		with self.assertRaisesRegex(frappe.ValidationError, "Fund Transfer payment cannot go through"):
			self._book(method="Fund Transfer", payment_channel=_channel(AYALA, "Cash"))
		# Another tenant's channel reads as non-existent — never "belongs to X".
		with self.assertRaisesRegex(frappe.ValidationError, "does not exist"):
			self._book(method="Fund Transfer", payment_channel=_channel(QCSM, "GCash"))
		# A name that is not a channel at all dies on frappe's own Link check
		# ("Could not find Payment Channel"), before the controller's sentence.
		with self.assertRaises(frappe.ValidationError):
			self._book(method="Fund Transfer", payment_channel="PCH-AYALA-NOPE-01")
		with self.assertRaisesRegex(frappe.ValidationError, "does not exist"):
			self._book(method="Fund Transfer", payment_channel=_platform_channel("GCash"))
		# The desk endpoint threads it through unchanged.
		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0):
			result = create_booking(
				MAKATI_B, DAY, "10:00:00", "Fund Transfer", customer=PIA, payment_channel=_channel(AYALA, "BDO")
			)
		self._cleanup_booking(result["name"])
		self.assertEqual(self._reload(result["name"]).payment_channel, _channel(AYALA, "BDO"))

	def test_disabling_every_transfer_channel_switches_online_payment_off(self):
		for label in ("GCash", "BDO"):
			self._set_enabled(_channel(AYALA, label), False)
		self.assertEqual(resolve_book_page(AYALA)["payment_channels"], [])
		with self.assertRaisesRegex(frappe.ValidationError, "No enabled transfer payment channel"):
			self._book(method="Fund Transfer")
		# Cash is untouched...
		self._book(method="Cash")
		# ...and the portal refuses in the customer's words, minting no hold.
		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			with self.assertRaisesRegex(frappe.ValidationError, "not taking online payments"):
				reserve_booking(MAKATI_B, DAY, "10:00:00")
		self.assertFalse(
			frappe.db.exists("CBT Court Booking", {"court": MAKATI_B, "booking_date": DAY, "customer": PIA})
		)

	def test_the_customer_picks_at_checkout_and_staff_correct_at_verification(self):
		"""The ruling in one row: PIA books through BDO, uploads a receipt that
		says GCash, and Stella confirms it as GCash — the booking and its
		statement end on GCash, the proof keeps what the customer claimed."""
		bdo, gcash = _channel(AYALA, "BDO"), _channel(AYALA, "GCash")
		page = resolve_book_page(AYALA)
		self.assertEqual([c["label"] for c in page["payment_channels"]], ["GCash", "BDO"])
		self.assertEqual(
			set(page["payment_channels"][0]),
			{"name", "label", "kind", "account_name", "account_number", "qr_image", "instructions"},
		)

		frappe.set_user(PIA)
		with patch(CLOCK, return_value=T0):
			result = reserve_booking(MAKATI_A, DAY, "10:00:00", payment_channel=bdo)
		self._cleanup_booking(result["booking"])
		self.assertEqual(result["payment_channel"], bdo)
		self.assertEqual(result["payment_channel_detail"]["account_number"], "0012-3456-7890")
		detail = get_my_booking_detail(result["booking"])
		self.assertEqual(detail["payment_channel"], bdo)
		self.assertEqual(detail["payment_channel_detail"]["label"], "BDO")
		self.assertEqual([c["label"] for c in detail["payment_channels"]], ["GCash", "BDO"])
		# The customer cannot name a cash channel or another tenant's.
		with patch(CLOCK, return_value=T0):
			with self.assertRaisesRegex(frappe.ValidationError, "cannot go through"):
				reserve_booking(MAKATI_A, DAY, "11:00:00", payment_channel=_channel(AYALA, "Cash"))
			with self.assertRaisesRegex(frappe.ValidationError, "does not exist"):
				reserve_booking(MAKATI_A, DAY, "11:00:00", payment_channel=_channel(QCSM, "GCash"))

		# The receipt says GCash.
		proof = self._proof(result["booking"], T0 + timedelta(minutes=5), payment_channel=gcash)
		self.assertEqual(frappe.db.get_value("CBT Payment Proof", proof["proof"], "payment_channel"), gcash)
		self.assertEqual(self._reload(result["booking"]).payment_channel, bdo)  # unchanged until staff say so
		with patch(CLOCK, return_value=T0 + timedelta(minutes=6)):
			with self.assertRaisesRegex(frappe.ValidationError, "cannot go through"):
				create_proof(result["booking"], "p.jpg", PROOF_JPG.read_bytes(), payment_channel=_channel(AYALA, "Cash"))

		frappe.set_user(STELLA)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			accept_proofs(result["booking"], payment_channel=gcash)
		booking = self._reload(result["booking"])
		self.assertEqual(booking.booking_status, "Confirmed")
		self.assertEqual(booking.payment_channel, gcash)
		invoice = self._invoice(booking.name)
		self.assertEqual(invoice.payment_channel, gcash)
		self.assertEqual(invoice.status, "Paid & Verified")
		# The desk form may correct it too — to an enabled channel of the kind.
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		booking.payment_channel = bdo
		booking.save()
		self.assertEqual(self._invoice(booking.name).payment_channel, bdo)
		booking = self._reload(booking.name)
		booking.payment_channel = _channel(AYALA, "Cash")
		with self.assertRaisesRegex(frappe.ValidationError, "cannot go through"):
			booking.save()

	def test_a_hold_whose_channel_was_disabled_can_still_upload_its_proof(self):
		"""Ducky finding 1: PIA reserves through BDO, the platform disables BDO,
		PIA's booking page still posts BDO (it is the ONLY option, pre-selected
		and hidden) — the upload must go through. A NEW choice of a disabled
		channel is still refused."""
		bdo, gcash = _channel(AYALA, "BDO"), _channel(AYALA, "GCash")
		booking = self._book(method="Fund Transfer", payment_channel=bdo)
		self._set_enabled(bdo, False)
		proof = self._proof(booking.name, T0 + timedelta(minutes=5), payment_channel=bdo)
		self.assertEqual(frappe.db.get_value("CBT Payment Proof", proof["proof"], "payment_channel"), bdo)
		# Omitting the channel defaults to the booking's, disabled or not.
		proof2 = self._proof(booking.name, T0 + timedelta(minutes=6))
		self.assertEqual(frappe.db.get_value("CBT Payment Proof", proof2["proof"], "payment_channel"), bdo)
		# A different disabled channel is a NEW choice — refused.
		self._set_enabled(gcash, False)
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			self._proof(booking.name, T0 + timedelta(minutes=7), payment_channel=gcash)
		# Staff confirming what is on record keeps working too.
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			confirm_booking(booking.name, payment_channel=bdo)
		self.assertEqual(self._reload(booking.name).payment_channel, bdo)

	def test_the_patch_stamps_cancelled_at_on_documents_refunded_before_channels(self):
		"""Ducky finding 2: an upgraded site's already-cancelled paid documents
		carry no `cancelled_at`; without the backfill both reports would show
		the money as collected and never refunded."""
		from court_booking_tech.payment_channels import ensure_all

		booking = self._book(method="Cash")
		with patch(CLOCK, return_value=T_CANCEL):
			cancel_booking(booking.name, reason="test: backfill")
		invoice = self._invoice(booking.name)
		self.assertEqual(invoice.cancelled_at, T_CANCEL)
		# Wind it back to the pre-batch shape. `modified` is the REAL wall clock
		# (the cancel sync ran outside the patched clock), so the refund will
		# land on TODAY — beside the seeded paid-then-cancelled documents the
		# seed's own ensure_all already stamped there. Hence a DELTA, not a count.
		frappe.db.set_value("CBT Booking Invoice", invoice.name, "cancelled_at", None, update_modified=False)
		modified = frappe.db.get_value("CBT Booking Invoice", invoice.name, "modified")
		day = str(getdate(modified))

		def _cash_refunds():
			_columns, rows = cbt_collections_by_channel.execute(
				{"company": AYALA, "from_date": day, "to_date": day}
			)
			row = next(
				(r for r in rows if not r.get("is_total_row") and r["payment_channel"] == _channel(AYALA, "Cash")),
				None,
			)
			return (row["refund_count"], flt(row["refunded"])) if row else (0, 0.0)

		before_count, before_pesos = _cash_refunds()
		result = ensure_all()
		self.assertGreaterEqual(result["cancelled_stamped"], 1)
		stamped = frappe.db.get_value("CBT Booking Invoice", invoice.name, "cancelled_at")
		self.assertEqual(stamped, modified)
		# Idempotent: nothing left to stamp.
		self.assertEqual(ensure_all()["cancelled_stamped"], 0)
		after_count, after_pesos = _cash_refunds()
		self.assertEqual(after_count, before_count + 1)
		self.assertEqual(after_pesos, flt(before_pesos + 300.0, 2))

	def test_confirm_without_a_channel_keeps_the_customers_choice(self):
		bdo = _channel(AYALA, "BDO")
		booking = self._book(method="Fund Transfer", payment_channel=bdo)
		self._proof(booking.name, T0 + timedelta(minutes=5))
		self.assertEqual(
			frappe.db.get_value("CBT Payment Proof", {"booking": booking.name}, "payment_channel"), bdo
		)
		with patch(CLOCK, return_value=T0 + timedelta(minutes=10)):
			confirm_booking(booking.name)
		self.assertEqual(self._reload(booking.name).payment_channel, bdo)
		self.assertEqual(self._invoice(booking.name).payment_channel, bdo)

	def test_a_reschedule_carries_the_channel_even_after_it_is_disabled(self):
		bdo = _channel(AYALA, "BDO")
		booking = self._book(method="Fund Transfer", payment_channel=bdo)
		self._set_enabled(bdo, False)
		with patch(CLOCK, return_value=T0):
			moved = reschedule_booking(booking.name, start_time="14:00:00")
		self._cleanup_booking(moved["name"])
		self.assertEqual(self._reload(moved["name"]).payment_channel, bdo)
		self.assertEqual(self._invoice(moved["name"]).payment_channel, bdo)

	def test_an_extension_takes_its_own_channel(self):
		cash = self._book(method="Cash")
		self.assertEqual(cash.booking_status, "Confirmed")
		with patch(CLOCK, return_value=T0):
			extension = extend_booking(
				cash.name, slots=1, payment_method="Fund Transfer", payment_channel=_channel(AYALA, "GCash")
			)
		self._cleanup_booking(extension)
		self.assertEqual(self._reload(extension).payment_channel, _channel(AYALA, "GCash"))
		self.assertEqual(self._reload(cash.name).payment_channel, _channel(AYALA, "Cash"))

	def test_a_cancelled_paid_document_is_stamped_for_the_reversal(self):
		booking = self._book(method="Cash")
		invoice = self._invoice(booking.name)
		self.assertTrue(invoice.verified_at)
		self.assertIsNone(invoice.cancelled_at)
		with patch(CLOCK, return_value=T_CANCEL):
			cancel_booking(booking.name, reason="test: stamped for the reversal")
		invoice = self._invoice(booking.name)
		self.assertEqual(invoice.status, "Cancelled")
		self.assertEqual(invoice.cancelled_at, T_CANCEL)
		self.assertTrue(invoice.verified_at)  # the audit trail survives
		self.assertEqual(invoice.payment_channel, _channel(AYALA, "Cash"))


class TestOpenPlayChannel(ChannelTestCase):
	OP_DATE = "2028-09-09"  # Saturday, AYALA-bgc, open every day
	PLAYERS = [row[0] for row in OPEN_PLAY_CUSTOMERS]

	def _session(self):
		doc = frappe.get_doc(
			{
				"doctype": "CBT Open Play Session",
				"branch": "AYALA-bgc",
				"title": "Channel Open Play",
				"session_date": self.OP_DATE,
				"start_time": "09:00:00",
				"end_time": "12:00:00",
				"court_type": "Pickleball",
				"rotation_mode": "Timed",
				"rotation_minutes": 15,
				"entry_fee": 150,
				"courts": [{"court": "AYALA-bgc-court-1"}, {"court": "AYALA-bgc-court-2"}],
			}
		)
		doc.insert(ignore_permissions=True)

		def _cleanup():
			frappe.set_user("Administrator")
			for block in frappe.get_all("CBT Slot Block", filters={"open_play_session": doc.name}, pluck="name"):
				frappe.delete_doc("CBT Slot Block", block, force=True, ignore_permissions=True)
			frappe.delete_doc(
				"CBT Open Play Session", doc.name, force=True, ignore_permissions=True, ignore_missing=True
			)

		self.addCleanup(_cleanup)
		with patch(CLOCK, return_value=datetime(2028, 9, 9, 8, 30)):
			open_session(doc.name)
		return doc.name

	def test_players_take_a_channel_by_kind_and_staff_correct_a_transfer(self):
		session = self._session()
		bdo, gcash, cash = _channel(AYALA, "BDO"), _channel(AYALA, "GCash"), _channel(AYALA, "Cash")
		with patch(CLOCK, return_value=datetime(2028, 9, 9, 9, 0)):
			add_players(
				session,
				frappe.as_json(
					[
						{"customer": self.PLAYERS[0], "payment_method": "Cash"},
						{"customer": self.PLAYERS[1], "payment_method": "Fund Transfer", "payment_channel": bdo},
						{"customer": self.PLAYERS[2], "payment_method": "Free"},
					]
				),
			)
			with self.assertRaisesRegex(frappe.ValidationError, "cannot go through"):
				add_players(
					session,
					frappe.as_json([{"customer": self.PLAYERS[3], "payment_method": "Cash", "payment_channel": gcash}]),
				)
		doc = frappe.get_doc("CBT Open Play Session", session)
		rows = {row.customer: row for row in doc.participants}
		self.assertEqual(rows[self.PLAYERS[0]].payment_channel, cash)
		self.assertEqual(rows[self.PLAYERS[1]].payment_channel, bdo)
		self.assertIsNone(rows[self.PLAYERS[2]].payment_channel)
		invoices = {c: frappe.get_doc("CBT Booking Invoice", r.billing_doc) for c, r in rows.items()}
		self.assertEqual(invoices[self.PLAYERS[0]].payment_channel, cash)
		self.assertEqual(invoices[self.PLAYERS[1]].payment_channel, bdo)
		self.assertIsNone(invoices[self.PLAYERS[2]].payment_channel)

		with patch(CLOCK, return_value=datetime(2028, 9, 9, 9, 30)):
			confirm_participant_payment(session, rows[self.PLAYERS[1]].name, payment_channel=gcash)
		doc = frappe.get_doc("CBT Open Play Session", session)
		row = next(r for r in doc.participants if r.customer == self.PLAYERS[1])
		self.assertEqual(row.payment_status, "Paid")
		self.assertEqual(row.payment_channel, gcash)
		invoice = frappe.get_doc("CBT Booking Invoice", row.billing_doc)
		self.assertEqual(invoice.payment_channel, gcash)
		self.assertEqual(invoice.status, "Paid & Verified")


class TestChannelReports(ChannelTestCase):
	"""₱300 cash, ₱300 GCash, ₱300 BDO (later refunded) on 2028-09-06 at AYALA
	(VAT 12%: each ₱300 = ₱267.86 + ₱32.14) and ₱350 cash at QCSM."""

	def _cast(self):
		cash = self._book(method="Cash")
		gcash = self._book(start_time="11:00:00", method="Fund Transfer", payment_channel=_channel(AYALA, "GCash"))
		bdo = self._book(start_time="12:00:00", method="Fund Transfer", payment_channel=_channel(AYALA, "BDO"))
		with patch(CLOCK, return_value=T0 + timedelta(hours=1)):
			confirm_booking(gcash.name)
			confirm_booking(bdo.name)
		with patch(CLOCK, return_value=T_CANCEL):
			cancel_booking(bdo.name, reason="test: the cast's refund")
		self._book(court=QCSM_1, start_time="10:00:00", method="Cash")
		return cash, gcash, bdo

	def test_collections_by_channel_splits_the_day_and_shows_the_refund(self):
		self._cast()
		_columns, rows = cbt_collections_by_channel.execute(
			{"company": AYALA, "from_date": DAY, "to_date": NEXT_DAY}
		)
		by_key = {(r["day"], r["channel_label"]): r for r in rows if not r.get("is_total_row")}
		self.assertEqual(
			set(by_key), {(DAY, "Cash"), (DAY, "GCash"), (DAY, "BDO"), (NEXT_DAY, "BDO")}
		)
		cash = by_key[(DAY, "Cash")]
		self.assertEqual(cash["kind"], "Cash")
		self.assertEqual(cash["account_label"], "Cash on Hand")
		self.assertEqual(cash["paid_count"], 1)
		self.assertEqual(flt(cash["collected"]), 300.0)
		self.assertEqual(flt(cash["court_revenue"]), 300.0)
		self.assertEqual(flt(cash["open_play_revenue"]), 0.0)
		self.assertEqual(flt(cash["platform_fees"]), 0.0)
		self.assertEqual(flt(cash["vat_amount"]), 32.14)
		self.assertEqual(cash["refund_count"], 0)
		self.assertEqual(flt(by_key[(DAY, "GCash")]["collected"]), 300.0)
		self.assertEqual(by_key[(DAY, "BDO")]["paid_count"], 1)
		refund = by_key[(NEXT_DAY, "BDO")]
		self.assertEqual(refund["paid_count"], 0)
		self.assertEqual(refund["refund_count"], 1)
		self.assertEqual(flt(refund["refunded"]), 300.0)
		total = rows[-1]
		self.assertTrue(total["is_total_row"])
		self.assertEqual(total["paid_count"], 3)
		self.assertEqual(flt(total["collected"]), 900.0)
		self.assertEqual(flt(total["refunded"]), 300.0)
		# QCSM's ₱350 is not here — and a tenant seat is forced onto its own.
		frappe.set_user(QUINTIN)
		_columns, rows = cbt_collections_by_channel.execute(
			{"company": AYALA, "from_date": DAY, "to_date": DAY}
		)
		self.assertEqual([r["channel_label"] for r in rows], ["Cash", "Total"])
		self.assertEqual(flt(rows[0]["collected"]), 350.0)
		self.assertEqual(flt(rows[0]["vat_amount"]), 0.0)  # NON-VAT

	def test_the_tenant_ledger_balances_and_names_the_accounts(self):
		cash, _gcash, bdo = self._cast()
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": AYALA, "from_date": DAY, "to_date": NEXT_DAY, "books": "Tenant"}
		)
		total = rows[-1]
		self.assertTrue(total["is_total_row"])
		self.assertEqual(flt(total["debit"]), flt(total["credit"]))
		self.assertEqual(flt(total["debit"]), 1200.0)  # 3 × 300 in, 300 reversed
		lines = [r for r in rows if not r.get("is_total_row")]
		cash_lines = [r for r in lines if r["voucher"] == self._invoice(cash.name).name]
		self.assertEqual(
			[(r["account"], flt(r["debit"]), flt(r["credit"])) for r in cash_lines],
			[("Cash on Hand", 300.0, 0.0), ("Service Revenue", 0.0, 267.86), ("Output VAT Payable", 0.0, 32.14)],
		)
		self.assertEqual(cash_lines[0]["posting_date"], DAY)
		self.assertEqual(cash_lines[0]["payment_channel"], _channel(AYALA, "Cash"))
		self.assertIn("Payment received", cash_lines[0]["description"])
		bdo_lines = [r for r in lines if r["voucher"] == self._invoice(bdo.name).name]
		self.assertEqual(len(bdo_lines), 6)  # the payment and its reversal
		reversal = [r for r in bdo_lines if r["posting_date"] == NEXT_DAY]
		self.assertEqual(
			[(r["account"], flt(r["debit"]), flt(r["credit"])) for r in reversal],
			[("BDO Current Account", 0.0, 300.0), ("Service Revenue", 267.86, 0.0), ("Output VAT Payable", 32.14, 0.0)],
		)
		self.assertTrue(all(r["is_reversal"] for r in reversal))
		self.assertIn("Refund / reversal", reversal[0]["description"])
		# Every voucher balances on its own.
		for voucher in {r["voucher"] for r in lines}:
			for day in {r["posting_date"] for r in lines if r["voucher"] == voucher}:
				group = [r for r in lines if r["voucher"] == voucher and r["posting_date"] == day]
				self.assertEqual(
					flt(sum(r["debit"] for r in group), 2), flt(sum(r["credit"] for r in group), 2), voucher
				)
		# The platform's books are not a tenant's to read.
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.PermissionError):
			cbt_tenant_ledger.execute(
				{"company": AYALA, "from_date": DAY, "to_date": DAY, "books": "Platform"}
			)

	def test_the_statement_records_the_platform_channel_and_both_ledgers_post_it(self):
		self._cast()

		def _drop_close():
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"CBT Platform Month Close", PERIOD, force=True, ignore_permissions=True, ignore_missing=True
			)

		def _drop_statements():
			frappe.set_user("Administrator")
			for name in frappe.get_all("CBT Platform Statement", filters={"period": PERIOD}, pluck="name"):
				frappe.delete_doc(
					"CBT Platform Statement", name, force=True, ignore_permissions=True, ignore_missing=True
				)

		self.addCleanup(_drop_close)
		self.addCleanup(_drop_statements)  # LIFO: statements first, then the close

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with patch(CLOCK, return_value=AFTER_SEP):
			close_month(2028, 9)
			issue_statements(PERIOD)
		qcsm = frappe.get_doc(
			"CBT Platform Statement",
			frappe.get_all("CBT Platform Statement", filters={"period": PERIOD, "company": QCSM}, pluck="name")[0],
		)
		self.assertEqual(flt(qcsm.amount_due), 2999.0)
		platform_gcash = _platform_channel("GCash")
		with patch(CLOCK, return_value=PAY_DAY):
			# A tenant's channel is not the platform's.
			with self.assertRaisesRegex(frappe.ValidationError, "does not exist"):
				mark_paid(qcsm.name, "2028-10-05", "REF-1", payment_channel=_channel(QCSM, "GCash"))
			mark_paid(qcsm.name, "2028-10-05", "REF-1", payment_channel=platform_gcash)
		qcsm.reload()
		self.assertEqual(qcsm.status, "Paid")
		self.assertEqual(qcsm.payment_channel, platform_gcash)
		html = frappe.get_print("CBT Platform Statement", qcsm.name, "CBT Platform Statement")
		self.assertIn("via GCash", html)
		self.assertIn("REF-1", html)

		# Platform's books, QCSM as the party: issued in October (AFTER_SEP),
		# paid 2028-10-05 through the platform's GCash.
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": QCSM, "from_date": "2028-10-01", "to_date": "2028-10-31", "books": "Platform"}
		)
		lines = [r for r in rows if not r.get("is_total_row")]
		self.assertEqual(
			[(r["posting_date"], r["account"], flt(r["debit"]), flt(r["credit"])) for r in lines],
			[
				("2028-10-03", "Accounts Receivable", 2999.0, 0.0),
				("2028-10-03", "Subscription Revenue", 0.0, 2999.0),
				("2028-10-05", "Cash in E-Wallet - GCash", 2999.0, 0.0),
				("2028-10-05", "Accounts Receivable", 0.0, 2999.0),
			],
		)
		self.assertEqual(lines[0]["party"], "QC Smash")
		self.assertEqual(lines[2]["payment_channel"], platform_gcash)
		self.assertIn("party", [c["fieldname"] for c in _columns])
		# The tenant's own books for the same month.
		_columns, rows = cbt_tenant_ledger.execute(
			{"company": QCSM, "from_date": "2028-10-01", "to_date": "2028-10-31", "books": "Tenant"}
		)
		lines = [r for r in rows if not r.get("is_total_row")]
		self.assertEqual(
			[(r["posting_date"], r["account"], flt(r["debit"]), flt(r["credit"])) for r in lines],
			[
				("2028-10-03", "Platform Fees Expense", 2999.0, 0.0),
				("2028-10-03", "Due to Platform", 0.0, 2999.0),
				("2028-10-05", "Due to Platform", 2999.0, 0.0),
				("2028-10-05", "Cash/Bank (remitted to platform)", 0.0, 2999.0),
			],
		)
		self.assertNotIn("party", [c["fieldname"] for c in _columns])
		# And the close's own figures were untouched by any of this.
		close = frappe.get_doc("CBT Platform Month Close", PERIOD)
		ayala = next(r for r in close.rows if r.company == AYALA)
		self.assertEqual(flt(ayala.confirmed_revenue), 600.0)  # cash + GCash; BDO refunded
		self.assertEqual(flt(ayala.amount_due), 60.0)
