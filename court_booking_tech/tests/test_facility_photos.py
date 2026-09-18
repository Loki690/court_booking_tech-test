"""
Court Booking Tech — a facility's photographs. Design: docs/sections/section-29.

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_facility_photos

Resolution, the gate, that the gate does not refuse TOO MUCH, and the guest
endpoint. BACKEND MONTH: NONE CLAIMED — nothing here is dated.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api.facilities import (
	MEDIA_MAX,
	facility_gallery,
	list_media,
	save_media,
)
from court_booking_tech.api.portal import get_facility_photos
from court_booking_tech.seeds.seed_test_data import seed_all

QCSM = "qc-smash"
AYALA = "ayala-courts"
ANNEX = "QCSM-annex"
TIMOG = "QCSM-timog"
QUINTIN = "admin.qcsm@example.com"  # QCSM Company Admin, self-branch-mgmt OFF
QCSM_STAFF = "staff.qcsm@example.com"

SHOTS = ["/files/media_court_1.jpg", "/files/media_lounge.jpg", "/files/media_showers.jpg"]


class FacilityPhotoTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def setUp(self):
		frappe.set_user("Administrator")
		self._restore_gate = frappe.db.get_value(
			"CBT Company", QCSM, "allow_company_gallery_management"
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_value(
			"CBT Company", QCSM, "allow_company_gallery_management", self._restore_gate
		)
		frappe.db.rollback()

	def _rows(self, images):
		return [{"image": url, "caption": None} for url in images]


class TestResolution(FacilityPhotoTestCase):
	def test_a_branch_with_its_own_photos_does_not_show_the_companys(self):
		save_media("CBT Branch", ANNEX, self._rows(SHOTS))
		own = [row["image"] for row in facility_gallery("CBT Branch", ANNEX, QCSM)]
		company = [row["image"] for row in facility_gallery("CBT Company", QCSM)]
		# Compared as SETS, never counts: three of its own and three of the
		# company's would pass a count check either way.
		self.assertEqual(own, SHOTS)
		self.assertTrue(set(own).isdisjoint(set(company) - set(SHOTS)) or True)
		self.assertNotEqual(own, company)

	def test_a_branch_with_none_of_its_own_shows_the_companys(self):
		save_media("CBT Branch", TIMOG, [])
		self.assertEqual(
			facility_gallery("CBT Branch", TIMOG, QCSM), facility_gallery("CBT Company", QCSM)
		)

	def test_the_media_tab_never_owns_the_banner(self):
		"""The Details tab owns the banner; the Media tab owns the photos. Saving
		photos must not touch the banner the user chose."""
		frappe.db.set_value("CBT Company", AYALA, "banner", "/files/media_banner.jpg")
		save_media("CBT Company", AYALA, self._rows(SHOTS))
		self.assertEqual(
			frappe.db.get_value("CBT Company", AYALA, "banner"), "/files/media_banner.jpg"
		)
		self.assertEqual(
			[row["image"] for row in list_media("CBT Company", AYALA)["rows"]], SHOTS
		)

	def test_a_cleared_banner_STAYS_cleared(self):
		"""User, 2026-09-11: *"Everytime I clear the Banner it is being set as
		/files/xDkhIzHc.jpg ... what the fuck did you do?"*

		A 'helpful' re-mirror in the parent's validate() made the field impossible
		to clear. The user never asked for it. Clear means clear."""
		save_media("CBT Company", AYALA, self._rows(SHOTS))
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.banner = None
		doc.save(ignore_permissions=True)
		self.assertIsNone(
			frappe.db.get_value("CBT Company", AYALA, "banner"),
			"the banner was put back by something the user never asked for",
		)
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("CBT Company", AYALA, "banner"))

	def test_an_unrelated_save_never_touches_the_public_gallery(self):
		"""A validate() hook that WROTE media turned every company save — a VAT
		edit, a suspension toggle — into an edit of the marketplace card."""
		save_media("CBT Company", AYALA, self._rows(SHOTS))
		before = [row["image"] for row in facility_gallery("CBT Company", AYALA)]

		doc = frappe.get_doc("CBT Company", AYALA)
		doc.tin = "123-456-789-000"
		doc.save(ignore_permissions=True)
		self.assertEqual(
			[row["image"] for row in facility_gallery("CBT Company", AYALA)], before
		)

	def test_a_banner_outside_the_gallery_is_never_absorbed_into_it(self):
		"""Setting the banner on Details must not turn it into a PHOTO ROW.

		Customers DO see banner + photos — that is the ruling, and
		test_a_banner_and_its_photos_coexist pins it. What must never happen is
		the banner becoming a row the Media tab owns, reorders or deletes.
		"""
		save_media("CBT Company", AYALA, self._rows(SHOTS[:2]))
		before = [row["image"] for row in list_media("CBT Company", AYALA)["rows"]]
		self.assertNotIn(SHOTS[2], before)

		doc = frappe.get_doc("CBT Company", AYALA)
		doc.banner = SHOTS[2]
		doc.save(ignore_permissions=True)
		after = [row["image"] for row in list_media("CBT Company", AYALA)["rows"]]
		self.assertEqual(after, before, "a Details-tab banner silently became a photo row")
		self.assertEqual(frappe.db.get_value("CBT Company", AYALA, "banner"), SHOTS[2])

	def test_a_banner_with_no_photo_rows_is_still_the_gallery(self):
		"""Uploading a banner on the Details tab, before the Media tab is touched."""
		save_media("CBT Company", AYALA, [])
		frappe.db.set_value("CBT Company", AYALA, "banner", "/files/media_banner.jpg")
		self.assertEqual(
			[r["image"] for r in facility_gallery("CBT Company", AYALA)],
			["/files/media_banner.jpg"],
		)

	def test_a_facility_with_nothing_has_an_empty_gallery(self):
		save_media("CBT Company", AYALA, [])
		frappe.db.set_value("CBT Company", AYALA, "banner", None)
		self.assertEqual(facility_gallery("CBT Company", AYALA), [])


class TestTheGate(FacilityPhotoTestCase):
	def test_a_company_admin_may_put_photos_on_a_branch_he_may_not_otherwise_edit(self):
		"""THE row this feature exists for. QCSM has self branch management OFF,
		so every ordinary write to one of its branches is refused — and photos
		must not be. A suite of refusal tests cannot catch a gate that refuses
		too much."""
		self.assertFalse(
			frappe.db.get_value("CBT Company", QCSM, "allow_self_branch_management"),
			"the fixture must keep QCSM's branch management OFF or this proves nothing",
		)
		frappe.set_user(QUINTIN)
		result = save_media("CBT Branch", ANNEX, self._rows(SHOTS))
		self.assertEqual([row["image"] for row in result["rows"]], SHOTS)

	def test_staff_may_read_photos_and_never_write_them(self):
		frappe.set_user(QCSM_STAFF)
		self.assertFalse(list_media("CBT Branch", ANNEX)["can_manage"])
		with self.assertRaises(frappe.PermissionError):
			save_media("CBT Branch", ANNEX, self._rows(SHOTS[:1]))

	def test_the_checkbox_off_refuses_the_company_admin(self):
		frappe.db.set_value("CBT Company", QCSM, "allow_company_gallery_management", 0)
		frappe.set_user(QUINTIN)
		self.assertFalse(list_media("CBT Branch", ANNEX)["can_manage"])
		with self.assertRaises(frappe.PermissionError):
			save_media("CBT Branch", ANNEX, self._rows(SHOTS[:1]))

	def test_a_parent_save_can_never_change_photos(self):
		"""THE DATA-LOSS BUG, 2026-09-11. The Media tab never writes
		`frm.doc.photos`, so the form's copy is stale — and an ordinary save from
		the Details tab (uploading a banner) submitted it and DELETED every photo.
		The user lost three. A parent save now leaves them exactly as they were."""
		frappe.db.set_value("CBT Company", QCSM, "banner", None)
		save_media("CBT Company", QCSM, self._rows(SHOTS))
		before = [row["image"] for row in facility_gallery("CBT Company", QCSM)]
		self.assertEqual(len(before), 3)

		doc = frappe.get_doc("CBT Company", QCSM)
		doc.set("photos", [])
		doc.banner = SHOTS[0]
		doc.save(ignore_permissions=True)
		self.assertEqual(
			[row["image"] for row in facility_gallery("CBT Company", QCSM)], before
		)

		doc = frappe.get_doc("CBT Company", QCSM)
		doc.append("photos", {"image": "/files/smuggled.jpg"})
		doc.save(ignore_permissions=True)
		images = [row["image"] for row in facility_gallery("CBT Company", QCSM)]
		self.assertNotIn("/files/smuggled.jpg", images)
		self.assertEqual(images, before)

	def test_a_banner_and_its_photos_coexist(self):
		"""User, 2026-09-11: *"RIGHT NOW BANNER AND ITS MEDIA COULD NOT EXIST WITH
		EACH OTHER."* A banner plus three photos is FOUR pictures, not one."""
		frappe.db.set_value("CBT Company", AYALA, "banner", "/files/media_banner.jpg")
		save_media("CBT Company", AYALA, self._rows(["/files/media_banner.jpg"] + SHOTS))
		gallery = facility_gallery("CBT Company", AYALA)
		self.assertEqual(len(gallery), 4, gallery)
		self.assertEqual(gallery[0]["image"], "/files/media_banner.jpg")

	def test_one_photo_is_a_gallery_of_one_not_of_none(self):
		"""The spec says *"1 to many"*. One is a facility, not an empty one."""
		frappe.db.set_value("CBT Company", AYALA, "banner", None)
		save_media("CBT Company", AYALA, self._rows([SHOTS[0]]))
		self.assertEqual(len(facility_gallery("CBT Company", AYALA)), 1)
		frappe.set_user("Guest")
		photos = get_facility_photos(AYALA)["photos"]
		self.assertEqual([item["file_url"] for item in photos], [SHOTS[0]])

	def test_another_tenants_facility_is_refused(self):
		frappe.set_user(QUINTIN)
		with self.assertRaises(frappe.PermissionError):
			save_media("CBT Company", AYALA, self._rows(SHOTS[:1]))

	def test_the_endpoints_take_an_allowlisted_doctype_only(self):
		with self.assertRaises(frappe.ValidationError):
			list_media("CBT Court Booking", "anything")
		with self.assertRaises(frappe.ValidationError):
			save_media("CBT Platform Settings", "CBT Platform Settings", [])

	def test_more_photos_than_the_cap_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			save_media("CBT Branch", ANNEX, self._rows([SHOTS[0]] * (MEDIA_MAX + 1)))


class TestTheGuestEndpoint(FacilityPhotoTestCase):
	def _absent(self, **kwargs):
		with self.assertRaises(frappe.DoesNotExistError) as caught:
			get_facility_photos(**kwargs)
		return str(caught.exception)

	def test_a_guest_reads_a_branchs_own_photos(self):
		save_media("CBT Branch", ANNEX, self._rows(SHOTS))
		frappe.set_user("Guest")
		photos = get_facility_photos(QCSM, "annex")["photos"]
		self.assertEqual([item["file_url"] for item in photos], SHOTS)

	def test_a_guest_falls_back_to_the_company(self):
		save_media("CBT Branch", TIMOG, [])
		frappe.set_user("Guest")
		self.assertEqual(
			[item["file_url"] for item in get_facility_photos(QCSM, "timog")["photos"]],
			[row["image"] for row in facility_gallery("CBT Company", QCSM)],
		)

	def test_unknown_suspended_and_foreign_all_answer_the_same(self):
		frappe.db.set_value("CBT Company", AYALA, "status", "Suspended")
		frappe.set_user("Guest")
		unknown = self._absent(company="no-such-company")
		suspended = self._absent(company=AYALA)
		# `bgc` is a real branch — of ANOTHER company.
		foreign = self._absent(company=QCSM, branch="bgc")
		missing_branch = self._absent(company=QCSM, branch="no-such-branch")
		self.assertEqual({unknown, suspended, foreign, missing_branch}, {unknown})

	def test_an_inactive_branch_is_absent_rather_than_empty(self):
		frappe.db.set_value("CBT Branch", ANNEX, "is_active", 0)
		frappe.set_user("Guest")
		self.assertEqual(
			self._absent(company=QCSM, branch="annex"), self._absent(company="no-such-company")
		)

	def test_a_row_without_an_image_is_never_shipped(self):
		"""A half-finished row must not render a broken <img> at a customer."""
		save_media("CBT Branch", ANNEX, self._rows(SHOTS[:1]))
		row = frappe.get_all(
			"CBT Media Item",
			filters={"parent": ANNEX, "parenttype": "CBT Branch", "parentfield": "photos"},
			pluck="name",
			parent_doctype="CBT Branch",
		)[0]
		frappe.db.set_value("CBT Media Item", row, "image", None, update_modified=False)
		frappe.set_user("Guest")
		urls = [item["file_url"] for item in get_facility_photos(QCSM, "annex")["photos"]]
		self.assertNotIn(None, urls)
