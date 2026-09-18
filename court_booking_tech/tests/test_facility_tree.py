"""
Court Booking Tech — the facilities tree in place (section-27, Backlog B30)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_facility_tree

The Branches tab on CBT Company and the Courts tab on CBT Branch: what each
seat is told (rows, `can_manage`, `managed_by_platform`), what the in-place
editor may read, and — the part that matters — that `save_facility` resolves
the tenant from the DATABASE and refuses a payload whose `company` claims
one tenant while its `branch` belongs to another. Plus the JSON/asset
contract the two form scripts depend on.

Seats (seed): Alona = AYALA admin (self-management ON), Stella = AYALA staff,
Quintin = QCSM admin (self-management OFF), Samuel = QCSM staff, and the
PLATFORM seat `cbt.admin@example.com` — the CBT Platform Admin role and nothing
else. Platform behaviour is asserted as THAT seat, never as Administrator:
Administrator bypasses DocPerms, so a test run as it cannot notice a missing
permission row for the role (user ruling, 2026-08-28). Administrator appears
only in tearDown, for cleanup.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api import facilities
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL, seed_all

ALONA = "admin.ayala@example.com"
STELLA = "staff.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"
SAMUEL = "staff.qcsm@example.com"

AYALA = "ayala-courts"
QCSM = "qc-smash"

APP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))


def _json(path):
	with open(path, encoding="utf-8") as fh:
		return json.load(fh)


class TestFacilityTree(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		self._made = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, name in reversed(self._made):
			frappe.delete_doc(
				doctype, name, force=True, ignore_permissions=True, ignore_missing=True
			)

	def _track(self, doctype, name):
		self._made.append((doctype, name))

	# --- lists -----------------------------------------------------------

	def test_list_branches_is_scoped_and_counted(self):
		frappe.set_user(ALONA)
		data = facilities.list_branches(AYALA)
		by_name = {r.name: r for r in data["rows"]}
		self.assertEqual(set(by_name), {"AYALA-bgc", "AYALA-makati"})
		self.assertEqual(by_name["AYALA-bgc"]["courts"], 3)
		self.assertEqual(by_name["AYALA-bgc"]["active_courts"], 3)
		self.assertEqual(by_name["AYALA-bgc"]["rate_from"], 400)
		self.assertTrue(by_name["AYALA-bgc"]["pinned"])
		self.assertEqual(by_name["AYALA-makati"]["courts"], 2)
		self.assertTrue(data["can_manage"])
		self.assertFalse(data["managed_by_platform"])

	def test_list_branches_gate_off_admin_reads_only(self):
		frappe.set_user(QUINTIN)
		data = facilities.list_branches(QCSM)
		self.assertEqual({r.name for r in data["rows"]}, {"QCSM-timog", "QCSM-annex"})
		self.assertFalse(data["can_manage"])
		self.assertTrue(data["managed_by_platform"])
		annex = next(r for r in data["rows"] if r.name == "QCSM-annex")
		self.assertFalse(annex["pinned"])

	def test_list_branches_staff_reads_only(self):
		frappe.set_user(STELLA)
		data = facilities.list_branches(AYALA)
		self.assertEqual(len(data["rows"]), 2)
		self.assertFalse(data["can_manage"])
		# The gate is ON for AYALA — Stella's refusal is her ROLE, not the platform.
		self.assertFalse(data["managed_by_platform"])

	def test_list_branches_cross_tenant_refused(self):
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			facilities.list_branches(AYALA)

	def test_list_branches_platform_manages(self):
		"""`can_manage` is `frappe.has_permission("CBT Branch", "write")` AND the
		gate (facilities.py) — for a non-Administrator seat that is a REAL DocPerm
		check, so this row fails the day the role loses its write row."""
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		data = facilities.list_branches(QCSM)
		self.assertTrue(data["can_manage"])
		self.assertFalse(data["managed_by_platform"])

	def test_list_courts_by_seat(self):
		frappe.set_user(QUINTIN)
		data = facilities.list_courts("QCSM-timog")
		self.assertEqual(len(data["rows"]), 3)
		self.assertTrue(data["can_manage"])  # courts carry no management gate
		self.assertTrue(all("rate_rules" in r for r in data["rows"]))
		frappe.set_user(SAMUEL)
		self.assertFalse(facilities.list_courts("QCSM-timog")["can_manage"])
		frappe.set_user(ALONA)
		with self.assertRaises(frappe.PermissionError):
			facilities.list_courts("QCSM-timog")

	def test_get_facility_is_scoped(self):
		frappe.set_user(ALONA)
		doc = facilities.get_facility("CBT Branch", "AYALA-bgc")
		self.assertEqual(doc["company"], AYALA)
		self.assertEqual(len(doc["business_hours"]), 7)
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			facilities.get_facility("CBT Branch", "AYALA-bgc")
		with self.assertRaises(frappe.ValidationError):
			facilities.get_facility("CBT Company", AYALA)

	# --- the save --------------------------------------------------------

	def test_save_branch_round_trip_by_tenant_admin(self):
		frappe.set_user(ALONA)
		saved = facilities.save_facility(
			json.dumps(
				{
					"doctype": "CBT Branch",
					"company": AYALA,
					"slug": "tree-test",
					"branch_name": "Tree Test",
					"phone": None,
					# Audit columns from the client are ignored, never written.
					"owner": QUINTIN,
					"creation": "2001-01-01 00:00:00",
				}
			)
		)
		self._track("CBT Branch", saved["name"])
		self.assertEqual(saved["name"], "AYALA-tree-test")
		self.assertEqual(saved["owner"], ALONA)
		self.assertEqual(len(saved["business_hours"]), 7)

		# The editor's shape: the loaded document, edited, sent back whole.
		doc = facilities.get_facility("CBT Branch", saved["name"])
		hours = [dict(r) for r in doc["business_hours"]]
		hours[0]["closing_time"] = "21:00:00"
		dropped = hours.pop()  # Sunday goes
		payload = dict(doc)
		payload.update(
			{
				"phone": "0917 000 0000",
				"business_hours": hours + [
					# A row the grid stamped with a random name must INSERT.
					{"name": "zz9random", "day": dropped["day"], "is_open": 0,
					 "opening_time": "08:00:00", "closing_time": "12:00:00"}
				],
			}
		)
		updated = facilities.save_facility(json.dumps(payload, default=str))
		self.assertEqual(updated["phone"], "0917 000 0000")
		rows = {r["day"]: r for r in updated["business_hours"]}
		self.assertEqual(len(rows), 7)
		self.assertEqual(str(rows["Monday"]["closing_time"]), "21:00:00")
		self.assertEqual(rows[dropped["day"]]["is_open"], 0)
		self.assertNotEqual(rows[dropped["day"]]["name"], "zz9random")
		self.assertEqual(
			frappe.db.get_value("CBT Branch", saved["name"], "phone"), "0917 000 0000"
		)

		# A stale edit — the timestamp the editor loaded is older than the row.
		stale = dict(payload)
		stale["phone"] = "clobber"
		with self.assertRaises(frappe.TimestampMismatchError):
			facilities.save_facility(json.dumps(stale, default=str))
		# And no timestamp at all is refused rather than trusted.
		no_stamp = dict(updated)
		no_stamp.pop("modified", None)
		with self.assertRaises(frappe.ValidationError):
			facilities.save_facility(json.dumps(no_stamp, default=str))
		self.assertEqual(
			frappe.db.get_value("CBT Branch", saved["name"], "phone"), "0917 000 0000"
		)

	def test_save_branch_refused_when_platform_manages(self):
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			facilities.save_facility(
				{"doctype": "CBT Branch", "company": QCSM, "slug": "tree-q", "branch_name": "Q"}
			)
		self.assertFalse(frappe.db.exists("CBT Branch", "QCSM-tree-q"))

	def test_save_court_cross_tenant_claim_refused(self):
		"""The hole the ducky found: `insert()` checks create permission BEFORE
		`before_insert` derives the court's company from its branch, so a payload
		claiming its OWN company on ANOTHER tenant's branch would pass the hook
		and be 'corrected' into the other tenant's branch. The tenant is
		resolved server-side, so it is refused."""
		before = frappe.db.count("CBT Court", {"branch": "AYALA-bgc"})
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			facilities.save_facility(
				{
					"doctype": "CBT Court",
					"branch": "AYALA-bgc",
					"company": QCSM,
					"court_name": "Smuggled",
					"court_type": "Pickleball",
					"hourly_rate": 100,
				}
			)
		self.assertEqual(frappe.db.count("CBT Court", {"branch": "AYALA-bgc"}), before)

	def test_save_court_by_tenant_admin_then_staff_refused(self):
		frappe.set_user(QUINTIN)
		saved = facilities.save_facility(
			{
				"doctype": "CBT Court",
				"branch": "QCSM-annex",
				"company": AYALA,  # a wrong claim on the RIGHT branch is overwritten, not trusted
				"court_name": "Tree Court",
				"court_type": "Badminton",
				"hourly_rate": 275,
			}
		)
		self._track("CBT Court", saved["name"])
		self.assertEqual(saved["name"], "QCSM-annex-tree-court")
		self.assertEqual(saved["company"], QCSM)

		payload = dict(saved)
		payload["hourly_rate"] = 300
		payload["branch"] = "QCSM-timog"  # immutable — the stored value wins
		updated = facilities.save_facility(json.dumps(payload, default=str))
		self.assertEqual(updated["hourly_rate"], 300)
		self.assertEqual(updated["branch"], "QCSM-annex")

		frappe.set_user(SAMUEL)
		payload = dict(updated)
		payload["hourly_rate"] = 999
		with self.assertRaises(frappe.PermissionError):
			facilities.save_facility(json.dumps(payload, default=str))
		self.assertEqual(frappe.db.get_value("CBT Court", saved["name"], "hourly_rate"), 300)

	def test_platform_seat_builds_on_a_gated_tenant(self):
		"""The platform seat — CBT Platform Admin and nothing else — creates a
		branch under a tenant whose self-management is OFF, then a court under it,
		then edits the court. `save_facility` calls `insert()` / `save()` with NO
		`ignore_permissions` (facilities.py), and `insert()` runs
		`check_permission("create")` for a non-Administrator user, so this is the
		role's create/write DocPerm on CBT Branch and CBT Court being proven —
		the day someone adds `ignore_permissions=True` there, this test keeps
		passing and stops proving anything."""
		roles = frappe.get_roles(PLATFORM_ADMIN_EMAIL)
		self.assertIn("CBT Platform Admin", roles)
		self.assertNotIn("System Manager", roles)
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		branch = facilities.save_facility(
			{
				"doctype": "CBT Branch",
				"company": QCSM,
				"slug": "tree-platform",
				"branch_name": "Tree Platform",
			}
		)
		self._track("CBT Branch", branch["name"])
		self.assertEqual(branch["name"], "QCSM-tree-platform")
		self.assertEqual(branch["owner"], PLATFORM_ADMIN_EMAIL)
		self.assertEqual(branch["company"], QCSM)

		court = facilities.save_facility(
			{
				"doctype": "CBT Court",
				"branch": branch["name"],
				"court_name": "Tree P",
				"court_type": "Badminton",
				"hourly_rate": 350,
			}
		)
		self._track("CBT Court", court["name"])
		self.assertEqual(court["name"], "QCSM-tree-platform-tree-p")
		self.assertEqual(court["owner"], PLATFORM_ADMIN_EMAIL)
		self.assertEqual(court["company"], QCSM)

		payload = dict(court)
		payload["hourly_rate"] = 375
		updated = facilities.save_facility(json.dumps(payload, default=str))
		self.assertEqual(updated["hourly_rate"], 375)
		self.assertEqual(updated["modified_by"], PLATFORM_ADMIN_EMAIL)
		self.assertEqual(frappe.db.get_value("CBT Court", court["name"], "hourly_rate"), 375)

	def test_save_rejects_other_doctypes(self):
		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		with self.assertRaises(frappe.ValidationError):
			facilities.save_facility({"doctype": "CBT Company", "company_name": "x"})
		with self.assertRaises(frappe.ValidationError):
			facilities.save_facility({"doctype": "CBT Court", "court_name": "no branch"})

	# --- the contract the form scripts depend on -------------------------

	def test_json_and_assets_contract(self):
		company = _json(
			os.path.join(APP_DIR, "court_booking_tech", "doctype", "cbt_company", "cbt_company.json")
		)
		self.assertEqual(
			company["field_order"][-7:],
			[
				"onboarding_tab",
				"onboarding_checklist_html",
				"branches_tab",
				"branches_html",
				"media_tab",
				"media_html",
				"photos",
			],
		)
		branch = _json(
			os.path.join(APP_DIR, "court_booking_tech", "doctype", "cbt_branch", "cbt_branch.json")
		)
		self.assertEqual(
			branch["field_order"][-5:],
			["courts_tab", "courts_html", "media_tab", "media_html", "photos"],
		)
		by_name = {f["fieldname"]: f for f in company["fields"] + branch["fields"]}
		self.assertEqual(by_name["branches_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(by_name["branches_html"]["fieldtype"], "HTML")
		self.assertEqual(by_name["courts_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(by_name["courts_html"]["fieldtype"], "HTML")
		self.assertEqual(by_name["media_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(by_name["media_html"]["fieldtype"], "HTML")
		# The grid is rendered by cbt_media_tab.js; the Table field is the model only.
		self.assertEqual(by_name["photos"]["fieldtype"], "Table")
		self.assertEqual(by_name["photos"]["options"], "CBT Media Item")
		self.assertEqual(by_name["photos"].get("hidden"), 1)
		# A guest <img> 403s without this on the doctype the file is attached to.
		for doc in (company, branch):
			self.assertEqual(doc.get("make_attachments_public"), 1, doc["name"])
		# Migrated meta agrees (a JSON edit without a migrate is invisible).
		self.assertEqual(frappe.get_meta("CBT Company").get_field("branches_tab").fieldtype, "Tab Break")
		self.assertEqual(frappe.get_meta("CBT Branch").get_field("courts_tab").fieldtype, "Tab Break")
		for doctype in ("CBT Company", "CBT Branch"):
			self.assertEqual(frappe.get_meta(doctype).get_field("media_tab").fieldtype, "Tab Break")

		public_js = os.path.join(APP_DIR, "public", "js")
		for asset in ("cbt_facility_tab.js", "cbt_branch_map.js", "cbt_media_tab.js"):
			self.assertTrue(os.path.isfile(os.path.join(public_js, asset)), asset)
		for script in (
			os.path.join(APP_DIR, "court_booking_tech", "doctype", "cbt_company", "cbt_company.js"),
			os.path.join(APP_DIR, "court_booking_tech", "doctype", "cbt_branch", "cbt_branch.js"),
		):
			with open(script, encoding="utf-8") as fh:
				source = fh.read()
			self.assertIn("/assets/court_booking_tech/js/cbt_facility_tab.js", source, script)
			self.assertIn("/assets/court_booking_tech/js/cbt_branch_map.js", source, script)
			self.assertIn("/assets/court_booking_tech/js/cbt_media_tab.js", source, script)
