"""
Court Booking Tech — Branch & Court validations (section-3)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_branch_court

Structural rules: branch slug regex/uniqueness/immutability, court-hours
auto-populate + shared-validator rejections, coordinate sanity, floor-plan
layout validation, and court naming/rate/company rules. Tenancy/permission
behavior for these doctypes lives in test_isolation.py.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_time

from court_booking_tech.seeds.seed_test_data import seed_all

AYALA = "ayala-courts"
QCSM = "qc-smash"


class TestBranchCourt(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers ---------------------------------------------------------

	def _branch_doc(self, slug, company=AYALA, **overrides):
		payload = {
			"doctype": "CBT Branch",
			"company": company,
			"slug": slug,
			"branch_name": f"Test {slug}",
		}
		payload.update(overrides)
		return frappe.get_doc(payload)

	def _insert_branch(self, slug, company=AYALA, **overrides):
		doc = self._branch_doc(slug, company, **overrides)
		doc.insert()
		self._cleanup_branch(doc.name)
		return doc

	def _cleanup_branch(self, name):
		def _do():
			frappe.set_user("Administrator")
			for court in frappe.get_all(
				"CBT Court", filters={"branch": name}, pluck="name"
			):
				frappe.delete_doc(
					"CBT Court", court, force=True, ignore_permissions=True,
					ignore_missing=True,
				)
			frappe.delete_doc(
				"CBT Branch", name, force=True, ignore_permissions=True,
				ignore_missing=True,
			)

		self.addCleanup(_do)

	def _insert_court(self, branch, court_name, **overrides):
		payload = {
			"doctype": "CBT Court",
			"branch": branch,
			"court_name": court_name,
			"court_type": "Pickleball",
			"hourly_rate": 500,
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert()
		return doc

	# --- branch slug -----------------------------------------------------

	def test_slug_regex_rejected(self):
		for bad in ("Bad_Slug", "UPPER", "double--hyphen", "-lead", "trail-"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				self._branch_doc(bad).insert()

	def test_slug_unique_per_company_only(self):
		self._insert_branch("tb-dup")
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc("tb-dup", branch_name="Other").insert()
		# Same slug under ANOTHER company is fine (name is code-prefixed).
		other = self._insert_branch("tb-dup", company=QCSM)
		self.assertEqual(other.name, "QCSM-tb-dup")

	def test_slug_immutable_after_insert(self):
		doc = self._insert_branch("tb-fixed")
		doc.slug = "tb-changed"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_company_immutable_after_insert(self):
		doc = self._insert_branch("tb-co-fixed")
		doc.company = QCSM
		self.assertRaises(frappe.ValidationError, doc.save)

	# --- court hours -----------------------------------------------------

	def test_hours_auto_populated_on_first_save(self):
		doc = self._insert_branch("tb-hours")
		self.assertEqual(len(doc.business_hours), 7)
		days = [row.day for row in doc.business_hours]
		self.assertEqual(len(set(days)), 7)
		for row in doc.business_hours:
			self.assertTrue(row.is_open)
			self.assertEqual(get_time(row.opening_time), get_time("06:00:00"))
			self.assertEqual(get_time(row.closing_time), get_time("22:00:00"))

	def test_overnight_row_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc(
				"tb-overnight",
				business_hours=[
					{
						"day": "Monday",
						"is_open": 1,
						"opening_time": "20:00:00",
						"closing_time": "06:00:00",
					}
				],
			).insert()

	def test_duplicate_day_rejected(self):
		row = {
			"day": "Monday",
			"is_open": 1,
			"opening_time": "06:00:00",
			"closing_time": "22:00:00",
		}
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc("tb-dupday", business_hours=[row, dict(row)]).insert()

	# --- coordinates -----------------------------------------------------

	def test_lat_without_lng_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc("tb-halfpin", latitude=14.55).insert()

	def test_out_of_range_coordinates_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc("tb-badlat", latitude=95.0, longitude=121.0).insert()
		with self.assertRaises(frappe.ValidationError):
			self._branch_doc("tb-badlng", latitude=14.55, longitude=200.0).insert()

	# --- floor plan ------------------------------------------------------

	def test_layout_validations(self):
		branch = self._insert_branch("tb-layout", layout_rows=2, layout_columns=2)
		court = self._insert_court(branch.name, "L Court")

		def try_layout(cells):
			doc = frappe.get_doc("CBT Branch", branch.name)
			doc.set("layout", [])
			for row_index, col_index, court_link in cells:
				doc.append(
					"layout",
					{
						"row_index": row_index,
						"col_index": col_index,
						"court": court_link,
					},
				)
			doc.save()
			return doc

		with self.assertRaises(frappe.ValidationError):  # outside dims
			try_layout([(3, 1, court.name)])
		with self.assertRaises(frappe.ValidationError):  # duplicate cell
			try_layout([(1, 1, court.name), (1, 1, None)])
		with self.assertRaises(frappe.ValidationError):  # foreign court
			try_layout([(1, 1, "AYALA-bgc-court-1")])

		second = self._insert_court(branch.name, "L Court 2")
		with self.assertRaises(frappe.ValidationError):  # duplicate court
			try_layout([(1, 1, court.name), (1, 2, court.name)])

		# Valid: two courts + an explicit empty cell (floor-plan gap).
		doc = try_layout([(1, 1, court.name), (1, 2, second.name), (2, 2, None)])
		self.assertEqual(len(doc.layout), 3)

	# --- courts ----------------------------------------------------------

	def test_court_rate_must_be_positive(self):
		branch = self._insert_branch("tb-rate")
		for bad_rate in (0, -100):
			with self.assertRaises(frappe.ValidationError, msg=bad_rate):
				self._insert_court(branch.name, f"Rate {bad_rate}", hourly_rate=bad_rate)

	def test_court_slug_scrubbed_into_name(self):
		branch = self._insert_branch("tb-scrub")
		court = self._insert_court(branch.name, "Center Court!")
		self.assertEqual(court.court_slug, "center-court")
		self.assertEqual(court.name, f"{branch.name}-center-court")

	def test_duplicate_court_name_in_branch_rejected(self):
		branch = self._insert_branch("tb-dupcourt")
		self._insert_court(branch.name, "Court 1")
		with self.assertRaises(frappe.ValidationError):
			self._insert_court(branch.name, "Court 1")

	def test_court_company_follows_branch(self):
		branch = self._insert_branch("tb-courtco")
		court = self._insert_court(branch.name, "Court X")
		self.assertEqual(court.company, AYALA)
		# frappe re-fetches read-only fetch_from fields server-side on save
		# (base_document._validate_links -> set_fetch_from_value), so a bogus
		# company is silently corrected BEFORE controller validate — assert
		# the invariant, not an exception. The controller's mismatch throw
		# stays as a guard against fetch_from/read_only ever being removed.
		court.company = QCSM
		court.save()
		self.assertEqual(court.company, AYALA)
		self.assertEqual(
			frappe.db.get_value("CBT Court", court.name, "company"), AYALA
		)

	def test_court_branch_immutable_after_insert(self):
		branch = self._insert_branch("tb-courtmove")
		court = self._insert_court(branch.name, "Court Y")
		court.branch = "AYALA-bgc"
		self.assertRaises(frappe.ValidationError, court.save)
