# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
/book?c=<company-slug>&b=<branch-slug>[&d=YYYY-MM-DD] — the availability grid
and checkout (PLAN §7).

ROUTE NOTE (section-9 as-built): the file is cbt-book.html, not book.html.
The solo `court_booking` app already ships www/book.html and www/my-bookings.html,
and both apps coexist on one site (D3) — whichever app came first in apps.txt
would silently win. hooks.website_route_rules maps the PUBLIC, printed URLs
(/book, /my-bookings/...) onto these prefixed files; route rules are resolved
before any www file (frappe.website.path_resolver.resolve_path -> resolve_from_map),
so the deep link in PLAN §5 keeps working AND resolution is deterministic.
Same class of lesson as the "CBT Hub" workspace and the cbt-court-board page.

Guests may browse the grid; only the Reserve action needs a session.
"""

import frappe
from frappe.utils import cint

from court_booking_tech.api.portal import portal_base_context, resolve_book_page

no_cache = 1


def _court_layout(branch: str) -> dict | None:
	"""The branch's floor plan, as a rows x columns matrix for the template.

	Section-22. The desk board has rendered this since section-3 (api/board.py
	ships `layout`, cbt_court_board.js lays the court CARDS out on it) while the
	customer — the person who actually has to find court 3 — has never been
	shown it. Same stored model, no second one: CBT Branch.layout_rows /
	layout_columns plus CBT Branch Court Cell rows, where a cell with no court
	is a walkway. That is how a 4-3-4 arrangement is expressed.

	Returns None when there is nothing honest to draw — no configured grid, or
	a grid whose every cell points at a court that no longer exists or is
	inactive. Most tenants have never configured one, and an empty box that
	says nothing is worse than no box.

	Reads the child table with frappe.get_all (which ignores permissions), the
	same way _branch_open_now reads CBT Business Hours on this same guest page.
	"""
	branch_row = frappe.db.get_value(
		"CBT Branch", branch, ["layout_rows", "layout_columns"], as_dict=True
	)
	if not branch_row:
		return None
	rows = cint(branch_row.layout_rows)
	columns = cint(branch_row.layout_columns)
	if not rows or not columns:
		return None

	cells = frappe.get_all(
		"CBT Branch Court Cell",
		filters={"parenttype": "CBT Branch", "parent": branch},
		fields=["row_index", "col_index", "court"],
		order_by="idx asc",
	)
	if not cells:
		return None

	# Only ACTIVE courts of this branch may be drawn — the availability grid
	# below the sketch lists exactly those, and a sketch offering a court the
	# grid does not have is a tap that goes nowhere.
	court_names = {
		court.name: court.court_name
		for court in frappe.get_all(
			"CBT Court",
			filters={"branch": branch, "is_active": 1},
			fields=["name", "court_name"],
		)
	}

	placed = {}
	for cell in cells:
		row_index = cint(cell.row_index)
		col_index = cint(cell.col_index)
		if not (1 <= row_index <= rows and 1 <= col_index <= columns):
			continue
		if cell.court in court_names:
			placed[(row_index, col_index)] = {
				"court": cell.court,
				"court_name": court_names[cell.court],
			}
	if not placed:
		return None

	return {
		"rows": rows,
		"columns": columns,
		# Backlog B32: the collapsed sketch's summary says how many courts it
		# holds — the DRAWN ones, so an inactive court is not counted either.
		"court_count": len(placed),
		"grid": [
			[placed.get((row, column)) for column in range(1, columns + 1)]
			for row in range(1, rows + 1)
		],
	}


def get_context(context):
	portal_base_context(context)
	company_slug = frappe.form_dict.get("c")
	branch_slug = frappe.form_dict.get("b")

	if not company_slug:
		# No company in the deep link: nothing to brand, send them shopping.
		frappe.local.flags.redirect_location = "/find-court"
		raise frappe.Redirect

	# A deep link is printed on the CLIENT's own website (PLAN §5), so the
	# people who hit a dead one are that facility's customers, not ours. A raw
	# 404 tells them the internet is broken; this tells them the truth they can
	# act on and offers the marketplace as the way forward.
	#
	# Unknown and suspended companies deliberately render the SAME card:
	# resolve_book_page already answers both identically so a stranger cannot
	# enumerate which slugs exist (anti-enumeration, same doctrine as the
	# signup wrapper's generic already-registered response).
	try:
		book = resolve_book_page(company_slug, branch_slug)
	except frappe.DoesNotExistError:
		# frappe records a 404 in the response when DoesNotExistError is
		# raised; clear it so this renders as a normal page.
		frappe.local.message_log = []
		frappe.clear_last_message()
		context.unavailable = True
		context.page_heading = frappe._("Not available")
		return context
	# server_now is a datetime; the template serialises `book` with |tojson,
	# so hand the page only JSON-safe values (the page reads its clock from
	# the availability payload anyway).
	book.pop("server_now", None)
	context.book = book
	context.page_heading = book["company_name"]
	context.selected_date = frappe.form_dict.get("d") or context.today

	context.layout = (
		_court_layout(book["selected"]["branch"]) if book.get("selected") else None
	)
	return context
