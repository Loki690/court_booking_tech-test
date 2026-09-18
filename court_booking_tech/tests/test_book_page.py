"""
Court Booking Tech — the /book page context: floor-plan layout and public links.

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_book_page

Two things are under test and each fails independently:

- **the /book layout context** — the sketch matrix, and its absence;
- **the company's public links** (Backlog B24) — website / Facebook / Instagram
  on CBT Company, validated as full http(s) addresses on the way in (they render
  to LOGGED-OUT visitors on a page carrying our domain, so a `javascript:` or a
  bare hostname must never reach the template), shipped by `resolve_book_page`
  in a fixed order with unset ones omitted, and writable by the Company Admin
  only.

Photographs moved off this file with section-29 — they live on the facility now
and are covered by `tests/test_facility_photos.py`.

BACKEND MONTH: **NONE CLAIMED.** Nothing here is dated.
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase

from court_booking_tech.api.portal import resolve_book_page
from court_booking_tech.seeds.seed_test_data import (
	COMPANY_MEDIA,
	MEDIA_COMPANY,
	PLATFORM_ADMIN_EMAIL,
	seed_all,
)

AYALA = "ayala-courts"
QCSM = "qc-smash"
ALONA = "admin.ayala@example.com"  # AYALA Company Admin
STELLA = "staff.ayala@example.com"  # AYALA Company Staff
PIA = "cust.pia@example.com"  # portal customer

AYALA_COURT = "AYALA-bgc-court-1"
QCSM_COURT = "QCSM-timog-court-1"

FILES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "files")


def _image(name: str) -> bytes:
	with open(os.path.join(FILES_DIR, name), "rb") as handle:
		return handle.read()


IMAGE_A = _image("proof_sample.jpg")  # 407-byte 1x1
IMAGE_B = _image("media_lounge.jpg")  # a different picture entirely


class BookPageTestCase(FrappeTestCase):
	"""Fixtures only — no test methods, so it is safe to inherit (S13 as-built
	11: subclassing a class that HAS tests re-runs every one of them here)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_all()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _file(self, is_private=0, content=IMAGE_A, file_name="test_media.jpg") -> str:
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"is_private": is_private,
			}
		).insert(ignore_permissions=True)
		self._forget_file(doc.name)
		return doc.file_url

	def _forget_file(self, name):
		self.addCleanup(
			frappe.delete_doc,
			"File",
			name,
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)


class TestBookPageLayout(BookPageTestCase):
	def _layout(self, branch):
		from court_booking_tech.www.cbt_book import _court_layout

		return _court_layout(branch)

	def test_seeded_branch_carries_its_grid_including_the_gaps(self):
		layout = self._layout("QCSM-timog")
		self.assertIsNotNone(layout)
		self.assertEqual((layout["rows"], layout["columns"]), (2, 3))
		self.assertEqual(len(layout["grid"]), 2)
		self.assertTrue(all(len(row) == 3 for row in layout["grid"]))
		# 3 courts placed, 3 walkway cells — the shape a gap-less model could
		# not express, and the reason the sketch reuses CBT Branch Court Cell
		# rather than inventing a second layout model.
		placed = [cell for row in layout["grid"] for cell in row if cell]
		gaps = [cell for row in layout["grid"] for cell in row if cell is None]
		self.assertEqual(len(placed), 3)
		self.assertEqual(len(gaps), 3)
		self.assertEqual(layout["court_count"], len(placed))
		self.assertEqual(layout["grid"][0][0]["court"], "QCSM-timog-court-1")
		self.assertEqual(layout["grid"][0][0]["court_name"], "Court 1")
		self.assertIsNone(layout["grid"][0][1])
		self.assertEqual(layout["grid"][1][1]["court"], "QCSM-timog-center-court")

	def test_branch_without_layout_cells_carries_none(self):
		"""AYALA-makati is a load-bearing NO-layout fixture — tests/test_board.py
		asserts its board ships `cells == []`. Note it still has non-zero
		layout_rows/layout_columns, because those carry DocType defaults of 2
		and 3: a check on the dimensions alone would draw an empty box on every
		branch on the platform."""
		self.assertIsNone(self._layout("AYALA-makati"))
		self.assertEqual(
			frappe.db.get_value(
				"CBT Branch", "AYALA-makati", ["layout_rows", "layout_columns"]
			),
			(2, 3),
		)

	def test_inactive_court_is_not_drawn(self):
		"""A sketch cell whose court the availability grid below does not list
		is a tap that goes nowhere."""
		self.addCleanup(
			frappe.db.set_value, "CBT Court", "QCSM-timog-court-2", "is_active", 1
		)
		frappe.db.set_value("CBT Court", "QCSM-timog-court-2", "is_active", 0)
		layout = self._layout("QCSM-timog")
		courts = {cell["court"] for row in layout["grid"] for cell in row if cell}
		self.assertNotIn("QCSM-timog-court-2", courts)
		self.assertIn("QCSM-timog-court-1", courts)
		# B32: the summary's count follows the DRAWN courts, not the branch's.
		self.assertEqual(layout["court_count"], 2)

	def test_unknown_branch_carries_none(self):
		self.assertIsNone(self._layout("NO-SUCH-BRANCH"))


LINK_FIELDS = ("website", "facebook_url", "instagram_url")


class TestCompanyPublicLinks(BookPageTestCase):
	"""Backlog B24 — the tenant's own website and social pages, shown while
	booking. Links ONLY: we host, curate and sync nothing (user ruling
	2026-08-20); the tenant's own feed supplies the photos and updates.

	Every row here restores the three fields to NULL — the seed leaves them
	unset, and E2E `test_company_links.py` relies on qc-smash starting empty.
	"""

	def _company(self, name=AYALA):
		self.addCleanup(
			frappe.db.set_value,
			"CBT Company",
			name,
			{field: None for field in LINK_FIELDS},
			update_modified=False,
		)
		return frappe.get_doc("CBT Company", name)

	def _save_with(self, **values):
		doc = self._company()
		for field, value in values.items():
			setattr(doc, field, value)
		doc.save()
		return frappe.db.get_value("CBT Company", AYALA, LINK_FIELDS, as_dict=True)

	def test_a_link_must_be_a_full_web_address(self):
		"""The guest-exposure trap, at the controller. Each of these would render
		into an <a href> on a logged-out page under our domain."""
		for bad in (
			"javascript:alert(1)",
			"ayalacourts.ph",  # no scheme — a browser would resolve it RELATIVE to /book
			"ftp://ayalacourts.ph",
			"https://",
			"https://ayala courts.ph",
			"https://ayalacourts.ph/a b",
			"data:text/html,hi",
			"https://exa\x00mple.com",  # a control char is not str.isspace()
			"https://user:pass@ayalacourts.ph",  # userinfo — never in a public link
			# Python reads the host after the LAST "@" (ayalacourts.ph); a browser
			# ends the authority at the backslash and lands on evil.example.
			"https://evil.example\\@ayalacourts.ph",
		):
			with self.subTest(value=bad):
				doc = self._company()
				doc.website = bad
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.save()
				self.assertIn("Website", str(caught.exception))
				self.assertIn("https://", str(caught.exception))
		# The refusals left nothing behind.
		self.assertIsNone(frappe.db.get_value("CBT Company", AYALA, "website"))

	def test_social_links_must_point_at_their_own_network(self):
		"""A chip labelled "Facebook" that opens some other site is exactly the
		phishing shape the row warned about — the label is OUR word, so the
		host has to earn it."""
		doc = self._company()
		doc.facebook_url = "https://ayalacourts.ph/facebook"
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save()
		self.assertIn("Facebook", str(caught.exception))

		doc = self._company()
		doc.instagram_url = "https://www.facebook.com/ayalacourts"
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save()
		self.assertIn("Instagram", str(caught.exception))

		# The parser-divergence bypass: urlsplit says facebook.com, a browser
		# says evil.example. Must be refused even though the allow-list "passes".
		doc = self._company()
		doc.facebook_url = "https://evil.example\\@facebook.com"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		self.assertIsNone(frappe.db.get_value("CBT Company", AYALA, "facebook_url"))

		saved = self._save_with(
			facebook_url="https://www.facebook.com/ayalacourts",
			instagram_url="https://instagram.com/ayalacourts",
		)
		self.assertEqual(saved.facebook_url, "https://www.facebook.com/ayalacourts")
		self.assertEqual(saved.instagram_url, "https://instagram.com/ayalacourts")
		# The short hosts the networks themselves hand out.
		saved = self._save_with(facebook_url="https://fb.com/ayalacourts")
		self.assertEqual(saved.facebook_url, "https://fb.com/ayalacourts")

	def test_links_are_trimmed_and_blank_means_unset(self):
		saved = self._save_with(website="  https://ayalacourts.ph/  ", facebook_url="   ")
		self.assertEqual(saved.website, "https://ayalacourts.ph/")
		self.assertFalse(saved.facebook_url)  # "   " is not a link, it is nothing

	def test_http_is_accepted_for_the_website(self):
		"""Plenty of small facilities still run plain http. Refusing it would
		leave them with no link at all, which is worse than an http one."""
		saved = self._save_with(website="http://ayalacourts.ph")
		self.assertEqual(saved.website, "http://ayalacourts.ph")

	def test_resolver_ships_links_in_a_fixed_order_and_omits_unset(self):
		"""What the booking page renders. Website first, then the networks;
		an unset one is ABSENT, not an empty-string entry — the template must
		never draw a dead chip (the row's empty-state rule)."""
		self._save_with(
			instagram_url="https://instagram.com/ayalacourts",
			website="https://ayalacourts.ph",
		)
		frappe.set_user("Guest")
		data = resolve_book_page(AYALA, "bgc")
		self.assertEqual(
			data["links"],
			[
				{"kind": "website", "label": "Website", "url": "https://ayalacourts.ph"},
				{
					"kind": "instagram",
					"label": "Instagram",
					"url": "https://instagram.com/ayalacourts",
				},
			],
		)

	def test_resolver_never_ships_a_value_that_bypassed_validation(self):
		"""A db_set / SQL write skips `validate`; the render seam must not
		trust the column on its own."""
		self._company()  # registers the cleanup
		frappe.db.set_value(
			"CBT Company", AYALA, "website", "javascript:alert(1)", update_modified=False
		)
		frappe.set_user("Guest")
		self.assertEqual(resolve_book_page(AYALA, "bgc")["links"], [])

	def test_resolver_carries_an_empty_list_when_nothing_is_set(self):
		"""Key PRESENT, value empty. This is a whitelisted method, so a None
		would survive the wire (S13 as-built 15 is about /api/resource) — but
		the page's `{% if book.links %}` needs a list either way."""
		frappe.set_user("Guest")
		data = resolve_book_page("e2e-fast", "main")
		self.assertIn("links", data)
		self.assertEqual(data["links"], [])

	def test_company_admin_may_set_links_but_staff_may_not(self):
		"""Editing is a Company Admin capability, not a front-desk one. The
		mechanism is the existing DocPerm split on CBT Company (Admin writes,
		Staff reads) — the new fields sit at permlevel 0 and inherit it."""
		self._company()  # registers the cleanup
		frappe.set_user(ALONA)
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.website = "https://ayalacourts.ph"
		doc.save()
		self.assertEqual(
			frappe.db.get_value("CBT Company", AYALA, "website"), "https://ayalacourts.ph"
		)

		frappe.set_user(STELLA)
		doc = frappe.get_doc("CBT Company", AYALA)
		doc.website = "https://not-for-staff.example"
		self.assertRaises(frappe.PermissionError, doc.save)
		self.assertEqual(
			frappe.db.get_value("CBT Company", AYALA, "website"), "https://ayalacourts.ph"
		)


class TestThePublicEndpointSurface(FrappeTestCase):
	"""A decorator that slides onto the WRONG function is invisible to every test.

	B56 inserted `_photo_resolver` beneath `resolve_book_page`'s whitelist, so
	the helper became guest-callable and the real endpoint stopped being one.
	The www page calls it in Python, so nothing went red. See section-30.
	"""

	def test_the_book_resolver_is_the_guest_endpoint_and_the_helper_is_not(self):
		from court_booking_tech.api import portal

		self.assertTrue(
			portal.resolve_book_page in frappe.whitelisted,
			"resolve_book_page is not whitelisted — /book's deep link is not callable",
		)
		self.assertTrue(
			portal.resolve_book_page in frappe.guest_methods,
			"resolve_book_page is not guest-callable — a logged-out visitor cannot use it",
		)
		self.assertFalse(
			portal._photo_resolver in frappe.whitelisted,
			"_photo_resolver is a PRIVATE helper and is exposed as an endpoint",
		)
		self.assertFalse(
			portal._photo_resolver in frappe.guest_methods,
			"_photo_resolver is a PRIVATE helper and is callable by guests",
		)

	def test_no_underscore_private_function_is_whitelisted_in_our_api(self):
		"""The general rule the one above is an instance of."""
		import importlib
		import pkgutil

		import court_booking_tech.api as api_pkg

		leaked = []
		for mod in pkgutil.iter_modules(api_pkg.__path__):
			module = importlib.import_module(f"court_booking_tech.api.{mod.name}")
			for attr in dir(module):
				if not attr.startswith("_"):
					continue
				fn = getattr(module, attr)
				if callable(fn) and fn in frappe.whitelisted:
					leaked.append(f"{mod.name}.{attr}")
		self.assertEqual(leaked, [], f"private helpers exposed as endpoints: {leaked}")
