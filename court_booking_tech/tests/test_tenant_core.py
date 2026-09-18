"""
Court Booking Tech — Tenant Core (section-2)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_tenant_core

CBT Company validation (slug, code immutability, fallback accessors, office
hours), CBT Company User role + User Permission sync, and the guarded
staff-management API.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api.company_users import create_company_user, disable_company_user
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"
ALONA = "admin.ayala@example.com"
STELLA = "staff.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"


def _delete_company(slug):
	if frappe.db.exists("CBT Company", slug):
		frappe.delete_doc("CBT Company", slug, force=True, ignore_permissions=True)


def _delete_binding_and_user(email):
	binding = frappe.db.get_value("CBT Company User", {"user": email})
	if binding:
		frappe.delete_doc("CBT Company User", binding, force=True, ignore_permissions=True)
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)


def _make_user(email, first_name, last_name):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"enabled": 1,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return email


class TenantCoreTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")


class TestCBTCompany(TenantCoreTestCase):
	def _make_company(self, slug, **overrides):
		_delete_company(slug)
		doc = frappe.get_doc(
			{
				"doctype": "CBT Company",
				"slug": slug,
				"company_name": overrides.pop("company_name", slug.replace("-", " ").title()),
				"registered_name": overrides.pop("registered_name", f"{slug} Test Corp."),
				**overrides,
			}
		)
		doc.insert()
		self.addCleanup(_delete_company, slug)
		return doc

	def test_slug_regex_rejects_bad_slugs(self):
		for bad_slug in ("Ayala", "ayala courts", "ayala--courts", "-ayala", "ayala-", "ayala_courts"):
			with self.assertRaises(frappe.ValidationError, msg=f"slug accepted: {bad_slug!r}"):
				frappe.get_doc(
					{
						"doctype": "CBT Company",
						"slug": bad_slug,
						"company_name": "Bad Slug Co",
						"registered_name": "Bad Slug Co Inc.",
					}
				).insert()

	def test_slug_change_on_existing_doc_blocked(self):
		company = self._make_company("test-sluglock-co")
		company.slug = "test-sluglock-changed"
		self.assertRaises(frappe.ValidationError, company.save)

	def test_company_code_derived_from_slug(self):
		company = self._make_company("test-derive-co")
		self.assertEqual(company.company_code, "TESTDE")

	def test_company_code_uppercased_and_validated(self):
		company = self._make_company("test-code-case-co", company_code="tcc")
		self.assertEqual(company.company_code, "TCC")
		for bad_code in ("TOOLONG7", "A", "BAD CODE"):
			with self.assertRaises(frappe.ValidationError, msg=f"code accepted: {bad_code!r}"):
				self._make_company("test-bad-code-co", company_code=bad_code)

	def test_company_code_immutable_after_first_save(self):
		company = self._make_company("test-immutable-co", company_code="IMMU")
		company.company_code = "OTHER"
		self.assertRaises(frappe.ValidationError, company.save)

	def test_vat_percent_depends_on_vat_registration(self):
		field = frappe.get_meta("CBT Company").get_field("vat_percent")
		self.assertIn("VAT", field.depends_on or "")

	def test_fallback_accessors(self):
		company = self._make_company("test-fallback-co", vat_registration="VAT")
		# 0 on the company -> platform defaults (30 min / 4 h / 12 % / 30 days)
		self.assertEqual(company.get_reservation_expiry_minutes(), 30)
		self.assertEqual(company.get_verification_hold_hours(), 4)
		self.assertEqual(company.get_advance_booking_days(), 30)
		company.vat_percent = 0
		self.assertEqual(company.get_vat_percent(), 12)
		# non-zero on the company wins
		company.reservation_expiry_minutes = 45
		company.verification_hold_hours = 8
		company.advance_booking_days = 14
		company.vat_percent = 12
		self.assertEqual(company.get_reservation_expiry_minutes(), 45)
		self.assertEqual(company.get_verification_hold_hours(), 8)
		self.assertEqual(company.get_advance_booking_days(), 14)
		self.assertEqual(company.get_vat_percent(), 12)

	def test_vat_percent_zero_for_non_vat_company(self):
		company = self._make_company("test-nonvat-co", vat_registration="NON-VAT")
		# The DB still carries the hidden field default (12); billing math must see 0.
		self.assertEqual(company.get_vat_percent(), 0)

	def test_office_hours_overnight_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._make_company(
				"test-overnight-co",
				office_hours=[
					{"day": "Monday", "is_open": 1, "opening_time": "18:00:00", "closing_time": "08:00:00"}
				],
			)

	def test_office_hours_duplicate_day_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._make_company(
				"test-dupday-co",
				office_hours=[
					{"day": "Monday", "is_open": 1, "opening_time": "09:00:00", "closing_time": "12:00:00"},
					{"day": "Monday", "is_open": 1, "opening_time": "13:00:00", "closing_time": "17:00:00"},
				],
			)

	def test_office_hours_open_row_needs_times(self):
		with self.assertRaises(frappe.ValidationError):
			self._make_company(
				"test-notimes-co",
				office_hours=[{"day": "Monday", "is_open": 1, "opening_time": "09:00:00"}],
			)

	def test_office_hours_closed_row_needs_no_times(self):
		company = self._make_company(
			"test-closedday-co",
			office_hours=[{"day": "Sunday", "is_open": 0}],
		)
		self.assertEqual(company.office_hours[0].day, "Sunday")


class TestCompanyUserSync(TenantCoreTestCase):
	TEMP = "temp.sync.user@example.com"

	def _make_binding(self, company=AYALA, company_role="Company Staff"):
		_delete_binding_and_user(self.TEMP)
		_make_user(self.TEMP, "Tempo", "SyncUser")
		binding = frappe.get_doc(
			{
				"doctype": "CBT Company User",
				"user": self.TEMP,
				"company": company,
				"company_role": company_role,
			}
		).insert()
		self.addCleanup(_delete_binding_and_user, self.TEMP)
		return binding

	def _user_permissions(self, user):
		return frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "CBT Company"},
			pluck="for_value",
		)

	def test_sync_on_insert(self):
		self._make_binding()
		self.assertIn("CBT Company Staff", frappe.get_roles(self.TEMP))
		self.assertEqual(self._user_permissions(self.TEMP), [AYALA])

	def test_role_flip_replaces_managed_role(self):
		binding = self._make_binding(company_role="Company Staff")
		binding.company_role = "Company Admin"
		binding.save()
		roles = frappe.get_roles(self.TEMP)
		self.assertIn("CBT Company Admin", roles)
		self.assertNotIn("CBT Company Staff", roles)
		self.assertEqual(self._user_permissions(self.TEMP), [AYALA])

	def test_sync_removed_on_delete(self):
		binding = self._make_binding()
		frappe.delete_doc("CBT Company User", binding.name)
		roles = frappe.get_roles(self.TEMP)
		self.assertNotIn("CBT Company Staff", roles)
		self.assertNotIn("CBT Company Admin", roles)
		self.assertEqual(self._user_permissions(self.TEMP), [])

	def test_one_company_per_user(self):
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			frappe.get_doc(
				{
					"doctype": "CBT Company User",
					"user": STELLA,  # already bound to ayala-courts by seeds
					"company": QCSM,
					"company_role": "Company Staff",
				}
			).insert()

	def test_platform_role_user_cannot_be_bound(self):
		# CONCERN-1 guard: the binding's User Permission would clamp a
		# platform account's visibility to one tenant.
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Company User",
					"user": PLATFORM_ADMIN_EMAIL,
					"company": AYALA,
					"company_role": "Company Staff",
				}
			).insert()

	def test_administrator_cannot_be_bound(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CBT Company User",
					"user": "Administrator",
					"company": AYALA,
					"company_role": "Company Admin",
				}
			).insert()


class TestStaffAPI(TenantCoreTestCase):
	TEMP = "temp.api.staff@example.com"

	def setUp(self):
		frappe.set_user("Administrator")
		_delete_binding_and_user(self.TEMP)

	def test_company_admin_creates_own_staff(self):
		frappe.set_user(ALONA)
		binding_name = create_company_user(self.TEMP, "Tempo ApiStaff", "Company Staff")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.addCleanup(_delete_binding_and_user, self.TEMP)
		binding = frappe.get_doc("CBT Company User", binding_name)
		self.assertEqual(binding.company, AYALA)
		self.assertEqual(binding.company_role, "Company Staff")
		self.assertIn("CBT Company Staff", frappe.get_roles(self.TEMP))

	def test_cross_company_blocked(self):
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.PermissionError):
			create_company_user(self.TEMP, "Tempo ApiStaff", "Company Staff", company=QCSM)

	def test_role_escalation_blocked(self):
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.ValidationError):
			create_company_user(self.TEMP, "Tempo ApiStaff", "CBT Platform Admin")
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertFalse(frappe.db.exists("User", self.TEMP))

	def test_staff_caller_blocked(self):
		frappe.set_user(STELLA)
		with self.assertRaises(frappe.PermissionError):
			create_company_user(self.TEMP, "Tempo ApiStaff", "Company Staff")

	def test_platform_admin_must_name_the_company(self):
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with self.assertRaises(frappe.ValidationError):
			create_company_user(self.TEMP, "Tempo ApiStaff", "Company Staff")
		binding_name = create_company_user(self.TEMP, "Tempo ApiStaff", "Company Staff", company=QCSM)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.addCleanup(_delete_binding_and_user, self.TEMP)
		self.assertEqual(frappe.db.get_value("CBT Company User", binding_name, "company"), QCSM)

	def test_disable_guards_and_self_disable(self):
		# Alona disables her own staff: OK
		frappe.set_user(ALONA)
		disable_company_user(STELLA)  # binding name == user email (autoname field:user)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		self.assertEqual(frappe.db.get_value("User", STELLA, "enabled"), 0)
		frappe.db.set_value("User", STELLA, "enabled", 1)  # restore for later tests

		# Self-disable blocked
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.ValidationError):
			disable_company_user(ALONA)

		# Cross-company blocked
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			disable_company_user(STELLA)

		# Unknown binding: fail-closed
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.PermissionError):
			disable_company_user("no.such.binding@example.com")
