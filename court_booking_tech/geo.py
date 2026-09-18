# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Geo engine (PLAN §5 nearest-first, D4 zero-cost maps).

haversine_km is pure math; get_branches is the ONE guest-safe marketplace
listing API — E2E file 04 and the portal `/` page consume it. It returns a
hand-built whitelist of fields, never document dumps: extending the payload
is a deliberate act, and tests assert the exact key set.
"""

import math
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint

from court_booking_tech import clock
from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
	PUBLIC_LINK_FIELDS,
	public_links,
)
from court_booking_tech.timeutil import (
	END_OF_DAY,
	_as_timedelta,
	closing_boundary,
	label_short,
)

EARTH_RADIUS_KM = 6371.0088

DAY_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_ABBR = {day: day[:3] for day in DAY_ORDER}
ALL_DAY = (timedelta(0), END_OF_DAY)

# The exact guest-visible payload — tests assert this is ALL that leaks.
BRANCH_PAYLOAD_KEYS = frozenset(
	{
		"company_slug",
		"company_name",
		"company_logo",
		# The card's picture and how many are behind it (section-29).
		"cover_url",
		"photo_count",
		# The company's own website / Facebook / Instagram (Backlog B24), which
		# /book has shown since section-24. Same shape and same ordered source
		# (cbt_company.PUBLIC_LINKS), unset ones omitted — the marketplace card
		# AND its branch modal both render from this one row, so one key serves
		# both. Public by design: these exist to be followed by a shopper who
		# has not logged in.
		"links",
		"branch_slug",
		"branch_name",
		"address_text",
		"latitude",
		"longitude",
		"distance_km",
		"court_types",
		"price_from",
		"open_now",
		# Backlog B48: what the card prints — `Book now` / `Opens 6 AM`, and the
		# week's hours in one line. Both faces read these strings verbatim.
		"status_label",
		"hours_summary",
	}
)


def haversine_km(lat1, lng1, lat2, lng2) -> float:
	"""Great-circle distance in km between two WGS84 points. Pure Python."""
	phi1 = math.radians(float(lat1))
	phi2 = math.radians(float(lat2))
	dphi = math.radians(float(lat2) - float(lat1))
	dlmb = math.radians(float(lng2) - float(lng1))
	a = (
		math.sin(dphi / 2) ** 2
		+ math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
	)
	return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _day_shape(rows, day):
	"""(opening, closing) as timedeltas for `day`, or None when closed / unset.

	None-checks, NOT truthiness: a midnight opening is timedelta(0) — falsy but
	open (same fix as slots.get_slot_grid). A 23:59 closing reads as MIDNIGHT
	through closing_boundary, exactly as the slot grid reads it.
	"""
	row = next((r for r in rows or [] if r.get("day") == day), None)
	if not row or not cint(row.get("is_open")):
		return None
	opening, closing = row.get("opening_time"), row.get("closing_time")
	if opening is None or closing is None:
		return None
	return (_as_timedelta(opening), closing_boundary(closing))


def _is_open(rows, dt) -> bool:
	"""Weekly business-hours rows × a datetime -> open right then?

	One row per day (validator-enforced), same-day windows only. No row for
	the day, a closed row, or an empty table all mean closed — conservative
	for a "Book now" badge. Half-open window [opening, closing); a 23:59
	closing is midnight, so a 24/7 branch never reads closed for its last
	minute (the section-4 edge Backlog B48 closed).
	"""
	shape = _day_shape(rows, dt.strftime("%A"))
	if shape is None:
		return False
	now = timedelta(hours=dt.hour, minutes=dt.minute, seconds=dt.second)
	return shape[0] <= now < shape[1]


def _window_label(shape) -> str:
	return f"{label_short(shape[0])} – {label_short(shape[1])}"


def hours_summary(rows) -> str:
	"""The week in one line, the way a facility writes it on its door (B48):
	`24/7`, `Daily 6 AM – 10 PM`, or consecutive days with the same hours
	grouped — `Sun–Thu 8 AM – 9 PM · Fri–Sat 8 AM – 12 MN`, `Mon–Sat 6 AM –
	10 PM · Sun closed`. The grouping wraps the week (Sun–Thu is ONE group)
	and the listing starts with the group that holds Monday.
	"""
	shapes = [_day_shape(rows, day) for day in DAY_ORDER]
	if all(shape is None for shape in shapes):
		return _("Closed")
	if all(shape == ALL_DAY for shape in shapes):
		return "24/7"
	if all(shape == shapes[0] for shape in shapes):
		return _("Daily {0}").format(_window_label(shapes[0]))

	# Walk the week from a boundary so a group never splits at the wrap.
	start = next(i for i in range(7) if shapes[i] != shapes[i - 1])
	groups = []
	for step in range(7):
		index = (start + step) % 7
		if groups and groups[-1][2] == shapes[index]:
			groups[-1] = (groups[-1][0], index, shapes[index])
		else:
			groups.append((index, index, shapes[index]))
	# Monday's group leads: the only one that starts at 0 or spans the wrap.
	lead = next(
		i for i, (first, last, _shape) in enumerate(groups) if first == 0 or first > last
	)
	groups = groups[lead:] + groups[:lead]

	parts = []
	for first, last, shape in groups:
		days = DAY_ABBR[DAY_ORDER[first]]
		if first != last:
			days = f"{days}–{DAY_ABBR[DAY_ORDER[last]]}"
		parts.append(
			_("{0} closed").format(days) if shape is None else f"{days} {_window_label(shape)}"
		)
	return " · ".join(parts)


def opens_label(rows, now) -> str:
	"""`Opens 6 AM` (later today) / `Opens tomorrow 6 AM` / `Opens Mon 6 AM`
	within the week, else `Closed`. Meaningful only while _is_open is False."""
	today = now.date()
	now_td = timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
	for offset in range(0, 8):
		day = today + timedelta(days=offset)
		shape = _day_shape(rows, day.strftime("%A"))
		if shape is None or (offset == 0 and now_td >= shape[0]):
			continue
		when = label_short(shape[0])
		if offset == 0:
			return _("Opens {0}").format(when)
		if offset == 1:
			return _("Opens tomorrow {0}").format(when)
		return _("Opens {0} {1}").format(DAY_ABBR[day.strftime("%A")], when)
	return _("Closed")


def branch_status(rows, now) -> dict:
	"""What the customer's card prints (Backlog B48): `Book now` while open,
	`Opens …` while closed, and the week's hours in one line either way."""
	open_now = _is_open(rows, now)
	return {
		"open_now": open_now,
		"status_label": _("Book now") if open_now else opens_label(rows, now),
		"hours_summary": hours_summary(rows),
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_home_ads() -> list[dict]:
	"""Guest-safe: the paid banners live TODAY, in sort order (Backlog B52).

	`get_all`, not `get_list`: CBT Ad grants no Guest DocPerm and never should.
	This endpoint is the only guest door onto it, and it ships four public fields
	— the title (which is also the alt text), the image, the link, and the name.
	The dates are INCLUSIVE at both ends, which is what a person selling a week
	of placement means by "the 1st to the 7th".
	"""
	today = clock.now_dt().date()
	rows = frappe.get_all(
		"CBT Ad",
		filters={
			"is_active": 1,
			"placement": "Top",
			"starts_on": ("<=", today),
			"ends_on": (">=", today),
		},
		fields=["name", "title", "image", "link_url"],
		order_by="sort_order asc, name asc",
	)
	# A row with no banner or no destination is not an ad; it is a hole in the
	# page. The doctype makes both mandatory — this is the belt.
	return [row for row in rows if row.get("image") and row.get("link_url")]


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_branches(lat=None, lng=None, court_type=None):
	"""Guest-safe marketplace listing: active branches of Active companies,
	nearest-first from (lat, lng) when given; pin-less branches last,
	alphabetical; no origin -> alphabetical."""
	origin = None
	if lat not in (None, "") and lng not in (None, ""):
		try:
			origin = (float(lat), float(lng))
		except (TypeError, ValueError):
			frappe.throw(_("lat and lng must be numbers."))
	elif frappe.session.user != "Guest":
		# Section-8: no explicit origin -> fall back to the session user's
		# saved home pin (same 0/None = pin-less convention as branches).
		pin = frappe.db.get_value(
			"CBT Customer Profile",
			{"user": frappe.session.user},
			["home_latitude", "home_longitude"],
		)
		if pin and pin[0] and pin[1]:
			origin = (float(pin[0]), float(pin[1]))

	companies = {
		row.name: row
		for row in frappe.get_all(
			"CBT Company",
			filters={"status": "Active"},
			# The link fields ride along on a query this function already runs
			# once for every company — no N+1, the row is reused per branch.
			fields=["name", "company_name", "logo", "banner", *PUBLIC_LINK_FIELDS],
		)
	}
	if not companies:
		return []

	branches = frappe.get_all(
		"CBT Branch",
		filters={"is_active": 1, "company": ("in", list(companies))},
		fields=[
			"name",
			"company",
			"slug",
			"branch_name",
			"address_text",
			"latitude",
			"longitude",
		],
	)
	if not branches:
		return []
	branch_names = [row.name for row in branches]

	court_types_by_branch = {}
	# "from ₱X" across the branch's courts, from BASE rates only — a stated
	# section-14 simplification (PLAN §4). Rate rules are per-court and
	# per-hour, so the true floor would need every rule of every court resolved
	# across a whole week to be honest. The marketplace card is a browsing hint;
	# the moment a customer picks a date and a slot, /book shows the real
	# per-slot price and the checkout shows the server quote. A rule that prices
	# BELOW base (an off-peak discount) therefore reads as slightly pessimistic
	# here, never optimistic — the safe direction for a headline number.
	price_from_by_branch = {}
	for court in frappe.get_all(
		"CBT Court",
		filters={"is_active": 1, "branch": ("in", branch_names)},
		fields=["branch", "court_type", "hourly_rate"],
	):
		court_types_by_branch.setdefault(court.branch, set()).add(court.court_type)
		current = price_from_by_branch.get(court.branch)
		if current is None or court.hourly_rate < current:
			price_from_by_branch[court.branch] = court.hourly_rate

	BH = frappe.qb.DocType("CBT Business Hours")
	hours_rows = (
		frappe.qb.from_(BH)
		.select(BH.parent, BH.day, BH.is_open, BH.opening_time, BH.closing_time)
		.where((BH.parenttype == "CBT Branch") & (BH.parent.isin(branch_names)))
	).run(as_dict=True)
	hours_by_branch = {}
	for row in hours_rows:
		hours_by_branch.setdefault(row.parent, []).append(row)

	# ONE read for every facility's photos, never one per branch (section-29).
	photo_rows = frappe.get_all(
		"CBT Media Item",
		filters={
			"parentfield": "photos",
			"parent": ("in", branch_names + list(companies)),
		},
		fields=["parent", "image", "idx"],
		order_by="idx asc",
		ignore_permissions=True,  # customers hold no DocPerm here, by design
		limit=0,
	)
	photos_by_parent: dict[str, list] = {}
	for row in photo_rows:
		if row.image:
			photos_by_parent.setdefault(row.parent, []).append(row.image)

	def gallery_for(branch) -> list:
		"""A branch's own photos, else the company's — whose cover IS its banner."""
		own = photos_by_parent.get(branch.name)
		if own:
			return own
		company = companies[branch.company]
		return ([company.banner] if company.banner else []) + [
			url for url in photos_by_parent.get(branch.company, []) if url != company.banner
		]

	now = clock.now_dt()  # site TZ = Asia/Manila (PLAN §8i, no DST)
	results = []
	for branch in branches:
		types = sorted(court_types_by_branch.get(branch.name, ()))
		if court_type and court_type not in types:
			continue
		company = companies[branch.company]
		gallery = gallery_for(branch)
		pinned = bool(branch.latitude and branch.longitude)
		distance_km = None
		if origin and pinned:
			distance_km = round(
				haversine_km(origin[0], origin[1], branch.latitude, branch.longitude), 2
			)
		results.append(
			{
				"company_slug": company.name,  # CBT Company name == slug
				"company_name": company.company_name,
				"company_logo": company.logo,
				# The picture this card shows, and how many are behind it. The
				# branch's own if it has any, else the company's — never a list.
				"cover_url": gallery[0] if gallery else None,
				"photo_count": len(gallery),
				"links": public_links(company),
				"branch_slug": branch.slug,
				"branch_name": branch.branch_name,
				"address_text": branch.address_text,
				"latitude": branch.latitude if pinned else None,
				"longitude": branch.longitude if pinned else None,
				"distance_km": distance_km,
				"court_types": types,
				"price_from": price_from_by_branch.get(branch.name),
				**branch_status(hours_by_branch.get(branch.name), now),
			}
		)

	results.sort(
		key=lambda row: (
			row["distance_km"] is None,
			row["distance_km"] if row["distance_km"] is not None else 0.0,
			(row["branch_name"] or "").lower(),
		)
	)
	return results
