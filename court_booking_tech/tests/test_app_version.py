"""
Court Booking Tech — App version + asset cache-busting (2026-09-04)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_app_version

Why this exists: on 2026-09-03 an iPhone rendered the 2026-09-01 marketplace
markup with the pre-09-01 stylesheet — the asset links were bare paths, so a
returning browser kept its cached copy. Every portal/desk asset link now
carries ``?v=<version>-<content hash>``; the last row here is a RATCHET that
refuses a bare link anywhere it could come back.

BACKEND MONTH: NONE CLAIMED — nothing here is dated.
"""

import os
import re
import tempfile
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

import court_booking_tech
from court_booking_tech import assets_version, hooks

APP_DIR = Path(court_booking_tech.__file__).resolve().parent
ASSET_PREFIX = "/assets/court_booking_tech/"


class TestAppVersion(FrappeTestCase):
	def test_version_is_semver_and_past_the_scaffold(self):
		self.assertRegex(court_booking_tech.__version__, r"^\d+\.\d+\.\d+$")
		self.assertNotEqual(court_booking_tech.__version__, "0.0.1")

	def test_every_hashed_asset_exists(self):
		# content_hash never raises on a missing file (a raise inside
		# hooks.py import would take the bench down) — so THIS is the check.
		for rel in assets_version.ASSET_FILES:
			self.assertTrue((assets_version.PUBLIC_DIR / rel).is_file(), rel)

	def test_asset_version_is_deterministic_and_content_derived(self):
		assets_version._cache = ()
		first = assets_version.asset_version()
		assets_version._cache = ()
		second = assets_version.asset_version()
		self.assertEqual(first, second)
		expected = assets_version.content_hash(
			assets_version.PUBLIC_DIR / rel for rel in assets_version.ASSET_FILES
		)
		self.assertEqual(first, f"{court_booking_tech.__version__}-{expected}")
		self.assertRegex(first, r"^\d+\.\d+\.\d+-[0-9a-f]{8}$")

	def test_content_hash_follows_the_bytes(self):
		with tempfile.TemporaryDirectory() as tmp:
			a = Path(tmp) / "a.css"
			b = Path(tmp) / "b.js"
			a.write_bytes(b"body{}")
			b.write_bytes(b"var x;")
			before = assets_version.content_hash([a, b])
			self.assertEqual(before, assets_version.content_hash([a, b]))
			b.write_bytes(b"var y;")
			self.assertNotEqual(before, assets_version.content_hash([a, b]))
			# A missing file degrades to a different hash, never to a raise.
			missing = Path(tmp) / "gone.css"
			self.assertNotEqual(before, assets_version.content_hash([a, missing]))

	def test_rendered_head_stamps_every_asset(self):
		html = frappe.render_template(
			"court_booking_tech/templates/includes/cbt_portal_head.html",
			{"is_guest": True},
			is_path=True,
		)
		stamp = f"?v={assets_version.asset_version()}"
		# Backlog B45 added the fourth: cbt_time_format.js, the app's ONE
		# client-side time language. It is the only file BOTH faces load — the
		# desk through hooks.app_include_js, the portal through this include —
		# which is what makes "the board and the customer's grid say the same
		# hour" true by construction rather than by two copies agreeing.
		portal_assets = (
			"css/cbt_theme.css",
			"css/cbt_portal.css",
			"js/cbt_time_format.js",
			"js/cbt_portal.js",
		)
		for asset in portal_assets:
			self.assertIn(f"{ASSET_PREFIX}{asset}{stamp}", html, asset)
		# The COUNT is the ratchet: a new asset added here without a stamp, or
		# without a line above, fails rather than slipping through.
		self.assertEqual(html.count(ASSET_PREFIX), len(portal_assets))

	def test_every_stamped_asset_feeds_the_hash(self):
		"""Stamped files and hashed files are one set: an edit to any stamped file must move ?v=."""
		call = re.compile(r"cbt_asset_url\(\s*['\"]" + re.escape(ASSET_PREFIX) + r"([^'\"?]+)")
		stamped = set()
		for path in sorted(APP_DIR.rglob("*")):
			rel = path.relative_to(APP_DIR).as_posix()
			if not path.is_file() or path.suffix not in (".py", ".html", ".json", ".md"):
				continue
			if rel.startswith("tests/") or rel == "assets_version.py":
				continue
			stamped.update(call.findall(path.read_text(encoding="utf-8")))
		hashed = set(assets_version.ASSET_FILES)
		self.assertEqual(
			(sorted(stamped - hashed), sorted(hashed - stamped)),
			([], []),
			"(stamped but never hashed, hashed but never stamped)",
		)

	def test_hooks_stamp_the_desk_includes(self):
		self.assertIn("?v=", hooks.app_include_css)
		for entry in hooks.app_include_js:
			self.assertIn("?v=", entry)
		self.assertEqual(
			hooks.jinja["methods"],
			[
				"court_booking_tech.assets_version.cbt_asset_url",
				"court_booking_tech.assets_version.cbt_asset_version",
				"court_booking_tech.assets_version.cbt_app_version",
			],
		)

	def test_no_bare_portal_asset_link_survives(self):
		"""The ratchet. Every occurrence of the asset prefix in a browser-served
		template must sit inside a cbt_asset_url( call on the same line.

		Attribute-agnostic on purpose: href='…', src = "…", url(/assets/…) and
		frappe.require("/assets/…") in a template all fail the same way.
		Allowlisted, with the reason: print formats (rendered to paper/PDF by
		wkhtmltopdf — no browser cache) and the vendored leaflet tree (versioned
		by its directory, never edited in place).
		"""
		offenders = []
		roots = (APP_DIR / "www", APP_DIR / "templates")
		for root in roots:
			for path in sorted(root.rglob("*.html")):
				rel = path.relative_to(APP_DIR).as_posix()
				if rel.startswith("templates/print_formats/"):
					continue
				for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
					for match in re.finditer(re.escape(ASSET_PREFIX), line):
						if "/vendor/" in line[match.start() :]:
							continue
						if "cbt_asset_url(" not in line[: match.start()]:
							offenders.append(f"{rel}:{lineno}: {line.strip()}")
		self.assertFalse(offenders, "bare asset links:\n" + "\n".join(offenders))
