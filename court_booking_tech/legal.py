# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""The two legal Web Pages, editable in the desk.

⛔ **THE REPO COPY IS NOT THE MASTER OF A LIVE PAGE.** (User ruling, 2026-09-11:
*"if those pages already exist. DO NOT OVERWRITE THEM."*) The production pages
were written BY HAND in the browser, which is exactly what building them as
`Web Page` rows was for — the wording changes without a deploy. `legal/*.html`
exists only so a FRESH site (dev, a rebuild, a new tenant site) has something to
install. Live diverging from the repo is by design, not drift.

So `force=1` is refused outright on a site that is not a test site unless the
caller also passes `overwrite_live=1`. One flag was not enough: a future session
reading "force overwrites from the repo" would run it on production and destroy
hand-written legal copy, which is how this rule was earned.
"""

from pathlib import Path

import frappe
from frappe.utils import cint

# route -> (page title, source file beside this module)
PAGES = {
	"privacy-policy": ("Privacy Policy", "privacy-policy.html"),
	"terms-of-service": ("Terms of Service", "terms-of-service.html"),
}

_SOURCE_DIR = Path(__file__).resolve().parent / "legal"


def page_source(filename: str) -> str:
	return (_SOURCE_DIR / filename).read_text(encoding="utf-8")


def push_legal_pages(force: int = 0, overwrite_live: int = 0) -> dict:
	"""Idempotent installer. Creates a missing page; NEVER touches one that exists.

	`force=1` overwrites from the repo — allowed freely on a test site (the seed
	needs it), refused on a live site unless `overwrite_live=1` is passed too.
	"""
	force = cint(force)
	if force and not frappe.conf.get("allow_tests") and not cint(overwrite_live):
		frappe.throw(
			frappe._(
				"These pages are maintained in the browser on a live site, and the repo "
				"copy is not their master. Edit the Web Page in the desk instead. If you "
				"really mean to replace the live wording with the repo's, call this again "
				"with overwrite_live=1."
			),
			frappe.PermissionError,
		)
	result = {}
	for route, (title, filename) in PAGES.items():
		existing = frappe.db.exists("Web Page", {"route": route})
		if existing and not force:
			result[route] = "kept"
			continue

		doc = (
			frappe.get_doc("Web Page", existing)
			if existing
			else frappe.new_doc("Web Page")
		)
		doc.title = title
		doc.route = route
		doc.published = 1
		doc.content_type = "HTML"
		doc.main_section_html = page_source(filename)
		doc.show_title = 0  # the page's own <h1> is in the HTML
		doc.save(ignore_permissions=True)
		result[route] = "overwritten" if existing else "created"

	frappe.db.commit()
	return result


def legal_pages_status() -> dict:
	"""bench --site <site> execute court_booking_tech.legal.legal_pages_status"""
	out = {}
	for route in PAGES:
		row = frappe.db.get_value(
			"Web Page", {"route": route}, ["name", "published"], as_dict=True
		)
		out[route] = (
			{"exists": True, "published": bool(row.published), "name": row.name}
			if row
			else {"exists": False, "published": False, "name": None}
		)
	return out
