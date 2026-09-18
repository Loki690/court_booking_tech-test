# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Portal API (section-9, PLAN §7) — the ENTIRE customer-facing server surface.

Customers hold NO DocPerm on any tenant DocType (leak vector 4): the portal is
served exclusively by these whitelisted endpoints, every one of which filters
on `frappe.session.user`. Nothing here trusts a company/branch/customer id from
the client — the customer is always the session user, and money is always
computed server-side (the page renders numbers, it never derives them).

Guard vocabulary used throughout:
- `_require_login()`      — Guest -> PermissionError (pages redirect earlier).
- `_own_booking(name)`    — loads a booking that MUST belong to the session user.
- `_booking_viewer(name)` — owner OR that company's staff (billing/proof views).
"""

import math
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt, getdate

from court_booking_tech import (
	billing,
	clock,
	credits,
	payment_channels,
	platform_fees,
	pricing,
)
from court_booking_tech.billing import compute_vat_breakdown
from court_booking_tech.court_booking_tech.doctype.cbt_company.cbt_company import (
	PUBLIC_LINK_FIELDS,
	public_links,
	resolve_advance_booking_days,
	resolve_max_cart_items,
)
from court_booking_tech.customer import ensure_customer_profile
from court_booking_tech.geo import branch_status, haversine_km
from court_booking_tech.membership import get_member_discount
from court_booking_tech.slots import (
	DEFAULT_SLOT_MINUTES,
	_as_timedelta,
	_fmt,
	get_slot_grid,
)
from court_booking_tech.tenancy import require_company_access
from court_booking_tech.throttle import enforce_user_rate_limit
from court_booking_tech.timeutil import label_short
from court_booking_tech.verification import office_hours_summary

DEFAULT_PORTAL_BOOKINGS_PER_HOUR = 10

# Statuses the customer sees as "live" (sorted first in /my-bookings).
OPEN_STATUSES = ("Reserved", "Confirmed", "Extended")


def _setting(fieldname: str, default: int) -> int:
	# Never-saved Singles yield 0/None — every knob needs its hard default
	# (S3 as-built 1, same pattern as api/proofs._setting).
	return cint(frappe.db.get_single_value("CBT Platform Settings", fieldname)) or default


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to continue."), frappe.PermissionError)


def _own_booking(name: str):
	"""Load a booking the session user OWNS. ignore_permissions is deliberate:
	customers have no DocPerm by design, so ownership IS the permission."""
	_require_login()
	if not frappe.db.exists("CBT Court Booking", name):
		frappe.throw(_("Booking not found."), frappe.DoesNotExistError)
	doc = frappe.get_doc("CBT Court Booking", name)
	if doc.customer != frappe.session.user:
		frappe.throw(_("This booking is not yours."), frappe.PermissionError)
	return doc


def _booking_viewer(name: str):
	"""Owner OR the company's staff/platform scope — the guard for artefacts
	that BOTH sides legitimately open (billing statement, proof image)."""
	_require_login()
	if not frappe.db.exists("CBT Court Booking", name):
		frappe.throw(_("Booking not found."), frappe.DoesNotExistError)
	doc = frappe.get_doc("CBT Court Booking", name)
	if doc.customer == frappe.session.user:
		return doc
	# Staff path: reading is not booking-creating, so a suspended company's
	# staff must still be able to open their own documents.
	require_company_access(doc.company, allow_suspended=True)
	return doc


# ---------------------------------------------------------------------------
# Marketplace: company/branch resolution for /book
# ---------------------------------------------------------------------------


def _photo_resolver(company, branch_names):
	"""Every facility's photo list on this page in ONE read (section-29)."""
	rows = frappe.get_all(
		"CBT Media Item",
		filters={"parentfield": "photos", "parent": ("in", list(branch_names) + [company.name])},
		fields=["parent", "image", "idx"],
		order_by="idx asc",
		ignore_permissions=True,  # customers hold no DocPerm here, by design
		limit=0,
	)
	by_parent: dict[str, list] = {}
	for row in rows:
		if row.image:
			by_parent.setdefault(row.parent, []).append(row.image)
	company_list = ([company.banner] if company.banner else []) + [
		url for url in by_parent.get(company.name, []) if url != company.banner
	]

	def resolve(branch_name=None):
		own = by_parent.get(branch_name) if branch_name else None
		gallery = own or company_list
		return {"cover_url": gallery[0] if gallery else None, "photo_count": len(gallery)}

	return resolve


@frappe.whitelist(allow_guest=True, methods=["GET"])
def resolve_book_page(c: str, b: str | None = None, lat=None, lng=None) -> dict:
	"""Deep-link resolver (PLAN §5): `c` = company slug, `b` = branch slug.

	Slug semantics are FROZEN — companies print these URLs on their own
	websites. Returns the branded header + that company's active branches
	(distance-ordered when an origin is known), each with its branch DOC NAME
	so the page can call get_public_availability without reconstructing ids.
	"""
	company = frappe.db.get_value(
		"CBT Company",
		c,
		[
			"name",
			"company_name",
			"logo",
			"banner",
			"status",
			"vat_registration",
			"payment_instructions",
			"advance_booking_days",
			"refund_policy",
			*PUBLIC_LINK_FIELDS,
		],
		as_dict=True,
	)
	if not company or company.status != "Active":
		# Same answer for "no such company" and "suspended": a suspended tenant
		# is simply absent from the marketplace (PLAN §5 suspension gate).
		frappe.throw(_("This facility is not available."), frappe.DoesNotExistError)

	origin = _origin(lat, lng)
	branches = frappe.get_all(
		"CBT Branch",
		filters={"company": company.name, "is_active": 1},
		fields=[
			"name",
			"slug",
			"branch_name",
			"address_text",
			"latitude",
			"longitude",
			"phone",
		],
	)
	photos_for = _photo_resolver(company, [b.name for b in branches])
	rows = []
	for branch in branches:
		pinned = bool(branch.latitude and branch.longitude)
		rows.append(
			{
				**photos_for(branch.name),
				"branch": branch.name,
				"branch_slug": branch.slug,
				"branch_name": branch.branch_name,
				"address_text": branch.address_text,
				"phone": branch.phone,
				"latitude": branch.latitude if pinned else None,
				"longitude": branch.longitude if pinned else None,
				"distance_km": round(
					haversine_km(origin[0], origin[1], branch.latitude, branch.longitude),
					2,
				)
				if origin and pinned
				else None,
				# Backlog B48: open_now, status_label, hours_summary — the same
				# three strings the marketplace card prints, for the chooser.
				**_branch_status(branch.name),
			}
		)
	rows.sort(
		key=lambda row: (
			row["distance_km"] is None,
			row["distance_km"] if row["distance_km"] is not None else 0.0,
			(row["branch_name"] or "").lower(),
		)
	)

	selected = None
	if b:
		selected = next((row for row in rows if row["branch_slug"] == b), None)
		if not selected:
			frappe.throw(_("This branch is not available."), frappe.DoesNotExistError)

	return {
		"company_slug": company.name,
		"company_name": company.company_name,
		"company_logo": company.logo,
		# The hero and the size of the set behind it (section-29).
		**photos_for(selected["branch"] if selected else None),
		# Backlog B24: the tenant's website / Facebook / Instagram, in a fixed
		# order with unset ones OMITTED (never an empty-string entry — the page
		# draws one chip per entry). Public by design, like the payment
		# instructions: they exist to be followed by a shopper who has not
		# logged in. Validated on the way IN (cbt_company.clean_public_link) —
		# this is the same guest page the row called a phishing vector, and
		# the template adds target=_blank + rel=noopener noreferrer.
		"links": public_links(company),
		"vat_registration": company.vat_registration,
		# Public by design: companies print their payment instructions on their
		# own sites; the checkout is useless without them.
		"payment_instructions": company.payment_instructions,
		# Backlog B29: the ENABLED transfer channels a customer may pay through,
		# PUBLIC fields only (label + account details + QR + note) — the checkout
		# lists them and the customer picks one. Same guest-exposure rule as the
		# links above: tenant-typed text on a logged-out page, control characters
		# refused on the way in (cbt_payment_channel._clean_public_text).
		"payment_channels": payment_channels.public_channels(company.name),
		# How far the date strip may page. The SAME number the booking
		# controller enforces (one resolver, two readers) — a strip that
		# offered a date the server then refused would be the worse bug.
		"advance_booking_days": resolve_advance_booking_days(
			company.advance_booking_days
		),
		# B53: what this facility does with a paid booking it cannot honour —
		# printed where the money is committed. `None` at a Refund company, so
		# nothing new renders for the tenants that were here before.
		"refund_note": (
			billing.no_cash_refund_sentence(company.name)
			if company.refund_policy == billing.RESCHEDULE_ONLY
			else None
		),
		"branches": rows,
		"selected": selected,
		"server_now": clock.now_dt(),
	}


def _origin(lat, lng):
	"""Explicit coords -> saved pin (logged in) -> nothing. Mirrors
	geo.get_branches so the home page and /book order branches identically."""
	if lat not in (None, "") and lng not in (None, ""):
		try:
			return (float(lat), float(lng))
		except (TypeError, ValueError):
			frappe.throw(_("lat and lng must be numbers."))
	if frappe.session.user != "Guest":
		pin = frappe.db.get_value(
			"CBT Customer Profile",
			{"user": frappe.session.user},
			["home_latitude", "home_longitude"],
		)
		if pin and pin[0] and pin[1]:
			return (float(pin[0]), float(pin[1]))
	return None


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_facility_photos(company: str, branch: str | None = None) -> dict:
	"""The pictures behind the banner, fetched only when somebody taps it
	(section-29). ANTI-ENUMERATION: unknown slug, suspended company and foreign
	branch all answer with the SAME exception and the SAME string."""
	absent = lambda: frappe.throw(  # noqa: E731 — one string, one throw, used 3×
		_("This facility is not available."), frappe.DoesNotExistError
	)
	if frappe.db.get_value("CBT Company", company, "status") != "Active":
		absent()

	branch_name = None
	if branch:
		# The SLUG, the same key /book?b= takes — never a document id.
		branch_name = frappe.db.get_value(
			"CBT Branch", {"company": company, "slug": branch, "is_active": 1}, "name"
		)
		if not branch_name:
			absent()

	rows = frappe.get_all(
		"CBT Media Item",
		filters={
			"parentfield": "photos",
			"parent": ("in", [branch_name, company] if branch_name else [company]),
		},
		fields=["parent", "image", "caption", "idx"],
		order_by="idx asc",
		ignore_permissions=True,  # customers hold no DocPerm here, by design
		limit=0,
	)
	own = [r for r in rows if branch_name and r.parent == branch_name and r.image]
	if own:
		return {"photos": [{"file_url": r.image, "caption": r.caption} for r in own]}

	banner = frappe.db.get_value("CBT Company", company, "banner")
	photos = [{"file_url": banner, "caption": None}] if banner else []
	photos += [
		{"file_url": r.image, "caption": r.caption}
		for r in rows
		if r.parent == company and r.image and r.image != banner
	]
	return {"photos": photos}


def _branch_status(branch: str) -> dict:
	rows = frappe.get_all(
		"CBT Business Hours",
		filters={"parenttype": "CBT Branch", "parent": branch},
		fields=["day", "is_open", "opening_time", "closing_time"],
	)
	return branch_status(rows, clock.now_dt())


# ---------------------------------------------------------------------------
# Checkout quote — the ONLY source of money shown in the portal
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_quote(
	court: str,
	number_of_slots=1,
	booking_date: str | None = None,
	start_time: str | None = None,
	customer: str | None = None,
	discount_percent=None,
	hourly_rate=None,
	platform_fee=None,
	payment_method: str | None = None,
) -> dict:
	"""Server-computed price for a prospective booking (PLAN §6 VAT-INCLUSIVE).

	The page NEVER multiplies rates itself: the checkout modal, the
	confirmation screen and the E2E assertions all read these numbers, so the
	printed statement and the quote can never disagree.

	`booking_date` + `start_time` are section-14 additions and STRICTLY
	ADDITIVE — price is time-dependent once a court carries rate rules, so a
	caller that knows WHEN gets the per-slot answer plus a `segments`
	breakdown, and `hourly_rate` becomes their blended average. A caller that
	does not (the pre-section-14 contract, still exercised by tests and by any
	cached client) gets the base-rate answer byte-for-byte as before, with
	`segments: []`.

	Both must be supplied together, and both go through the SAME seam and the
	SAME single-rounding formula as the booking controller — that is what makes
	quote == reserve to the centavo across a rate boundary.

	`customer` + `discount_percent` are section-18 additions (Backlog B4) and
	are STAFF-GATED. Before them this endpoint was the rate half of the board's
	quick-book money and the DISCOUNT was multiplied in JS, because the discount
	here came from the SESSION user — the staff member — not the customer being
	booked. One money area with two authorities; now there is one.

	PRECEDENCE, and it is deliberate rather than convenient:

	  1. an explicit `discount_percent` WINS — including **0**, which is a real
	     value (an untouched frappe Percent field reads 0, indistinguishable
	     from a deliberate 0 — S11 as-built 5). Every gate below therefore keys
	     on IS-NOT-NONE, never on truthiness. Do not "simplify" it.
	  2. else `customer` -> THAT customer's membership at THIS company.
	  3. else the session user's own membership (the pre-B4 behaviour, which is
	     what keeps every existing caller byte-identical).

	Note what (1) means for the board: its dialog always sends the discount
	field's current value, so from THAT caller `customer` never decides the
	money. That is the WYSIWYG rule, not an oversight — the number staff can see
	and edit is the number that gets charged. `customer` is here for a caller
	that does NOT know the discount and wants the membership resolved for it
	(the same shape as create_booking's tri-state), and its behaviour is pinned
	by backend rows rather than by the dialog.

	`hourly_rate` is the Backlog B15 addition (2026-08-27) and is STAFF-GATED
	by the same rule. It exists for ONE caller: the reschedule dialog. A move
	of a booking that was priced FLAT — a rule-less court, a legacy booking, or
	a staff override the desk already agreed with the customer — INHERITS that
	flat rate (`api/bookings.reschedule_booking` copies `original.hourly_rate`
	and inserts with `flags.rate_override`), so the seam is never consulted for
	its money and a quote that re-prices through the court's rules would show a
	number the server then contradicts. With the param the quote prices the
	window at exactly that rate, `segments` comes back EMPTY (the same shape the
	booking will carry), and the dialog can say "Total" instead of "Estimated".
	The window is still VALIDATED against the branch grid when a date and time
	are given — only the money is taken from the caller, never the schedule.

	`platform_fee` and `payment_method` are the Backlog B27 additions
	(2026-08-27). A Per Booking tenant's customers pay a per-booking ADD-ON on
	top of the court, so the quote now returns `platform_fee` and `total_amount`
	INCLUDES it — `court_total` is the
	court after discount, the number the tenant keeps. Ungated `payment_method`
	only ever LOWERS the figure: a Free booking carries no fee, and the desk is
	the only place Free exists. STAFF-GATED `platform_fee` is the reschedule
	dialog's case, the twin of `hourly_rate`: the fee a booking being moved
	already carries, sent back so the court is re-priced at the new slot while
	the fee stays what the customer paid (0 is a real value — "carries none").

	ANY staff param arms `require_company_access`. Without that gate
	`customer` would be a membership-tier oracle for any logged-in stranger:
	guest and portal-customer sessions hold no CBT Company User binding, so they
	fail closed, and cross-tenant staff are refused too. The gate runs BEFORE
	the non-Active-company throw on purpose — a cross-tenant caller learns
	"not permitted", never "that facility is suspended".
	"""
	# Normalize the GET strings FIRST, before any gate reads them: a query
	# string carries "" for an omitted value, and jQuery serialises a JS null
	# the same way.
	customer = (customer or "").strip() or None
	if discount_percent in (None, ""):
		discount_percent = None
	else:
		try:
			discount_percent = float(discount_percent)
		except (TypeError, ValueError):
			# flt() alone would coerce garbage to 0.0 and then sail through the
			# 0-100 range check below — a wrong number that nobody notices,
			# which is the whole class of bug this endpoint exists to remove.
			frappe.throw(_("Discount must be a number."))
		# float("nan") parses, and nan fails BOTH halves of a range check — so
		# it would ride straight into total_amount as non-JSON. Same for inf.
		if not math.isfinite(discount_percent):
			frappe.throw(_("Discount must be a number."))
		discount_percent = flt(discount_percent)
	if hourly_rate in (None, ""):
		hourly_rate = None
	else:
		try:
			hourly_rate = float(hourly_rate)
		except (TypeError, ValueError):
			# Same reasoning as the discount: flt("abc") is 0.0, and a ₱0 court
			# hour is a wrong number nobody notices.
			frappe.throw(_("Rate must be a number."))
		if not math.isfinite(hourly_rate):
			frappe.throw(_("Rate must be a number."))
		hourly_rate = flt(hourly_rate)
	if platform_fee in (None, ""):
		platform_fee = None
	else:
		try:
			platform_fee = float(platform_fee)
		except (TypeError, ValueError):
			frappe.throw(_("Booking fee must be a number."))
		if not math.isfinite(platform_fee):
			frappe.throw(_("Booking fee must be a number."))
		platform_fee = flt(platform_fee)
	payment_method = (payment_method or "").strip() or None

	court_row = frappe.db.get_value(
		"CBT Court",
		court,
		["name", "court_name", "branch", "company", "hourly_rate", "is_active"],
		as_dict=True,
	)
	if not court_row or not cint(court_row.is_active):
		frappe.throw(_("This court is not available."), frappe.DoesNotExistError)

	if (
		customer is not None
		or discount_percent is not None
		or hourly_rate is not None
		or platform_fee is not None
	):
		# allow_suspended=True: quoting is a READ, and the throw right below
		# still refuses a non-Active company for everyone.
		require_company_access(court_row.company, allow_suspended=True)

	company = frappe.get_doc("CBT Company", court_row.company)
	if company.status != "Active":
		frappe.throw(_("This facility is not accepting bookings."))

	branch = frappe.get_doc("CBT Branch", court_row.branch)
	return _quote_core(
		court_row,
		branch,
		company,
		booking_date=booking_date,
		start_time=start_time,
		number_of_slots=number_of_slots,
		customer=customer,
		discount_percent=discount_percent,
		hourly_rate=hourly_rate,
		platform_fee=platform_fee,
		payment_method=payment_method,
	)


def _quote_core(
	court_row,
	branch,
	company,
	booking_date=None,
	start_time=None,
	number_of_slots=1,
	customer=None,
	discount_percent=None,
	hourly_rate=None,
	platform_fee=None,
	payment_method=None,
) -> dict:
	"""Everything get_quote does AFTER its gates — and the only place money for
	a prospective booking is computed.

	Split out for Backlog B35 (the cart), which prices N runs in ONE request.
	The split is a boundary move, not a rewrite: every line below is the code
	get_quote has always run, in the same order. Two things made it necessary:

	1. The cart would otherwise re-fetch CBT Company and CBT Branch once per
	   run on a GUEST-OPEN endpoint. Company, branch and the slot grid are
	   properties of the cart, not of the item, so the caller hoists them.
	2. `platform_fee` is STAFF-GATED on get_quote (it arms
	   require_company_access). The cart has to inject a per-run fee — the
	   ordinal advances inside the basket — and that injection is server-side,
	   from platform_fees.booking_fees_for_units, never from the client. The
	   gate belongs on the ENDPOINT, which is where it stays.

	It is deliberately NOT whitelisted: it takes resolved documents and applies
	no permission gate of its own.
	"""
	slot_minutes = branch.get_slot_duration_minutes() or DEFAULT_SLOT_MINUTES
	slots = max(cint(number_of_slots), 1)
	duration_hours = flt(slots * slot_minutes / 60.0, 2)

	segments = (
		_quote_segments(court_row, branch, booking_date, start_time, slots)
		if booking_date and start_time
		else []
	)
	# `raw_subtotal` is kept UNROUNDED on the two flat branches: the controller's
	# flat formula (_compute_amounts) rounds ONCE, on rate × hours × (1 − d), and
	# rounding the subtotal first can differ from it by a centavo (B15's ducky —
	# the comment below had promised single rounding while the code rounded
	# twice). `subtotal` is the rounded figure shown; the total is derived from
	# the raw product. The segmented branch is a sum of already-rounded segment
	# amounts, which is what the controller multiplies too.
	if hourly_rate is not None:
		# B15: the caller's FLAT rate. Range-checked here, after the gate, for
		# the same reason as the discount below. The seam above has already
		# validated the window; its prices are discarded, exactly as
		# _compute_amounts discards them for a `flags.rate_override` insert —
		# and `segments` is forced EMPTY, which the reschedule dialog relies on
		# to render one flat line for an inherited rate.
		if hourly_rate < 0:
			frappe.throw(_("Rate cannot be negative."))
		segments = []
		raw_subtotal = hourly_rate * duration_hours
	elif segments:
		hourly_rate = pricing.blended_rate(segments, duration_hours)
		raw_subtotal = pricing.segments_subtotal(segments)
	else:
		hourly_rate = flt(court_row.hourly_rate)
		raw_subtotal = hourly_rate * duration_hours
	subtotal = flt(raw_subtotal, 2)

	# The discount, resolved in the precedence order this endpoint documents.
	# The discounted total is computed with the SAME single-rounding formula the
	# booking controller uses (_compute_amounts) — rounding the subtotal first
	# and the discount second can differ by a centavo, and a quote that
	# disagrees with the booking it produces is a customer-facing money bug.
	if discount_percent is not None:
		# Section-18: a staff override. Range-checked HERE rather than at
		# normalization time so an ungated caller still gets PermissionError
		# from the gate above, not a validation message that confirms the
		# endpoint took its parameter seriously.
		if discount_percent < 0 or discount_percent > 100:
			frappe.throw(_("Discount must be between 0 and 100."))
	elif customer:
		# Section-18: the membership of the customer being BOOKED.
		discount_percent = get_member_discount(court_row.company, customer)
	elif frappe.session.user != "Guest":
		# Section-11: the signed-in customer's own membership at THIS company.
		# Guests have none, so their numbers are unchanged.
		discount_percent = get_member_discount(court_row.company, frappe.session.user)
	else:
		discount_percent = 0.0
	court_total = flt(raw_subtotal * (1 - flt(discount_percent) / 100.0), 2)

	# Backlog B27: the platform's add-on, from the SAME function the booking
	# controller calls at insert — or the fee a booking being moved already
	# carries. Range-checked after the gate, like the other staff params.
	if platform_fee is not None:
		if platform_fee < 0:
			frappe.throw(_("Booking fee cannot be negative."))
		fee = flt(platform_fee, 2)
	else:
		_unit, fee = platform_fees.booking_fee(
			company.name,
			booking_date or clock.now_dt().date(),
			payment_method,
		)
	total = flt(court_total + fee, 2)
	# B29 ruling: the tenant's VAT is on the COURT SHARE — the fee is the
	# platform's non-VAT line, outside the footer (billing._apply_booking_amounts
	# does the same, which is what keeps quote == statement).
	breakdown = compute_vat_breakdown(
		court_total, company.vat_registration, company.get_vat_percent()
	)
	return {
		"court": court_row.name,
		"court_name": court_row.court_name,
		"branch": branch.name,
		"branch_name": branch.branch_name,
		"company_name": company.company_name,
		"hourly_rate": hourly_rate,
		"number_of_slots": slots,
		"slot_duration_minutes": slot_minutes,
		"buffer_minutes": cint(branch.buffer_minutes),
		"duration_hours": duration_hours,
		"subtotal": subtotal,
		"segments": pricing.segments_payload(segments),
		"discount_percent": flt(discount_percent),
		"discount_amount": flt(subtotal - court_total, 2),
		"court_total": court_total,
		"platform_fee": fee,
		# The unit NUMBER is deliberately NOT returned: on the guest-open path it
		# would tell any anonymous caller how many bookings a facility has sold
		# this month (the ducky's finding, 2026-08-27). Nothing on a screen
		# needs it; the booking stores its own.
		"total_amount": total,
		"vat_mode": company.vat_registration,
		"vat_percent": company.get_vat_percent(),
		"vatable_amount": breakdown["vatable_amount"],
		"vat_amount": breakdown["vat_amount"],
		"payment_instructions": company.payment_instructions,
		"reservation_expiry_minutes": company.get_reservation_expiry_minutes(),
		# Backlog B39. The BOOKED customer's balance when a staff caller named one
		# — that parameter already armed require_company_access, so this is not a
		# new door — otherwise the signed-in customer's own. A guest sees 0.
		"credit_available": credits.available_credit(
			company.name,
			customer
			or (None if frappe.session.user == "Guest" else frappe.session.user),
		),
	}


def _quote_segments(court_row, branch, booking_date, start_time, slots) -> list[dict]:
	"""The same grid slice the booking controller will price, resolved here.

	Refuses rather than guesses: quoting a base rate for a window the branch
	cannot actually sell would put a WRONG price on a customer's screen, which
	is worse than an error asking them to reload. The page cannot reach this —
	its slot chips are built from the availability grid — so this is an API
	contract guard, not a UX path.
	"""
	grid = get_slot_grid(branch, booking_date)
	start = _as_timedelta(start_time)
	index = next(
		(i for i, slot in enumerate(grid) if slot["start_time"] == start), None
	)
	if index is None or index + slots > len(grid):
		frappe.throw(
			_(
				"That time is no longer on this branch's schedule — please "
				"reload and pick a slot again."
			)
		)
	return pricing.build_rate_segments(
		court_row.name, booking_date, grid[index : index + slots]
	)


# ---------------------------------------------------------------------------
# Reserve / cancel
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def reserve_booking(
	court: str,
	booking_date: str,
	start_time: str,
	number_of_slots=1,
	payment_channel: str | None = None,
	apply_credit=0,
) -> dict:
	"""The portal's booking-creating endpoint.

	Deliberately NOT api/bookings.create_booking (S7 as-built 3): that one is
	the DESK quick-book and calls require_company_access, which fails closed
	for a customer (no CBT Company User binding). Here the doc is built
	entirely server-side — the client cannot choose the customer, the rate, the
	payment method or the status — and flags.customer_created arms both the
	customer suspension gate and the past-slot reject (S4 as-built 6).

	`payment_channel` (Backlog B29) is the ONE thing the customer does choose:
	which of the company's enabled transfer channels they will pay through
	(GCash, this bank…). Omitted = the first enabled one, which is what the
	checkout pre-selects. The controller refuses anything that is not one of
	this company's enabled transfer channels. A company with NONE enabled has
	switched online payment off (user ruling: the tenant decides its modes) —
	refused here, before a hold is minted, in the customer's own words.
	"""
	_require_login()

	# The read-only refusals come BEFORE the rate limiter: a facility that has
	# switched transfers off (or an inactive court) must not burn the customer's
	# hourly reservation quota on every attempt (ducky finding 6).
	court_row = frappe.db.get_value(
		"CBT Court",
		court,
		["name", "branch", "company", "is_active"],
		as_dict=True,
	)
	if not court_row or not cint(court_row.is_active):
		frappe.throw(_("This court is not available."))
	if not cint(frappe.db.get_value("CBT Branch", court_row.branch, "is_active")):
		frappe.throw(_("This branch is not available."))
	if not payment_channels.list_channels(
		court_row.company, kind=payment_channels.TRANSFER, fields=("name",)
	):
		frappe.throw(
			_(
				"This facility is not taking online payments right now — please "
				"contact the branch to book."
			)
		)

	enforce_user_rate_limit(
		"portal-booking",
		_setting("portal_booking_limit_per_hour", DEFAULT_PORTAL_BOOKINGS_PER_HOUR),
	)
	ensure_customer_profile()  # PLAN §11.4 — identity exists from the first booking

	doc = frappe.get_doc(
		{
			"doctype": "CBT Court Booking",
			"court": court_row.name,
			"branch": court_row.branch,
			"company": court_row.company,
			"customer": frappe.session.user,
			"booking_date": getdate(booking_date),
			"start_time": start_time,
			"number_of_slots": max(cint(number_of_slots), 1),
			# Section-14: `hourly_rate` is deliberately NOT sent. The portal can
			# never override a price, so the field is left empty for fetch_from
			# to seed with the court's base rate and the controller resolves the
			# court's rate rules per slot — the same seam get_quote just used,
			# which is what makes the checkout total and the booking agree.
			# Section-11: membership discount is applied SERVER-side from the
			# session user's own membership — the client never sends a discount
			# (it would be free money). Matches get_quote by construction: both
			# read the same helper and the controller applies the same formula.
			"discount_percent": get_member_discount(
				court_row.company, frappe.session.user
			),
			# Portal payments are Fund Transfer only in MVP (PLAN §2 D5):
			# Cash/Free are desk actions and would self-confirm.
			"payment_method": "Fund Transfer",
			# B29: the customer's pick (validated by the controller); empty =
			# the company's first enabled transfer channel.
			"payment_channel": (payment_channel or "").strip() or None,
		}
	)
	doc.flags.customer_created = True
	doc.flags.apply_credit = bool(cint(apply_credit))  # B39
	doc.insert(ignore_permissions=True)

	company = frappe.get_doc("CBT Company", doc.company)
	return {
		"booking": doc.name,
		"booking_date": str(doc.booking_date),
		"start_time": _fmt(_as_timedelta(doc.start_time)),
		"end_time": _fmt(_as_timedelta(doc.end_time)),
		"total_amount": flt(doc.total_amount),
		"credit_applied": flt(doc.credit_applied),
		"booking_status": doc.booking_status,
		"reservation_expires_at": doc.reservation_expires_at,
		"server_now": clock.now_dt(),
		"company_name": company.company_name,
		"payment_instructions": company.payment_instructions,
		"payment_channel": doc.payment_channel,
		"payment_channel_detail": payment_channels.channel_detail(doc.payment_channel),
		"branch_name": frappe.db.get_value(
			"CBT Branch", doc.branch, "branch_name"
		),
		"detail_url": f"/my-bookings/{doc.name}",
	}


# ---------------------------------------------------------------------------
# Cart — several courts and several dates, one checkout (Backlog B35)
# The unit is a contiguous RUN, not a cart item: see section-9 as-built 15-16.
# ---------------------------------------------------------------------------

DEFAULT_MAX_CART_ITEMS = 8
MAX_CART_PAYLOAD_ENTRIES = 200  # guest-open payload guard, not a product limit


def _parse_cart_items(items) -> list[dict]:
	"""Normalize and bound the caller's list; ids are re-resolved below."""
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not isinstance(items, (list, tuple)):
		frappe.throw(_("A cart is a list of bookings."))

	rows = []
	for raw in items:
		if not isinstance(raw, dict):
			frappe.throw(_("A cart is a list of bookings."))
		court = (raw.get("court") or "").strip()
		booking_date = raw.get("booking_date")
		start_time = raw.get("start_time")
		if not (court and booking_date and start_time):
			frappe.throw(
				_("Every booking in the cart needs a court, a date and a start time.")
			)
		rows.append(
			{
				"court": court,
				"booking_date": getdate(booking_date),
				"start_time": str(start_time),
				"number_of_slots": max(cint(raw.get("number_of_slots") or 1), 1),
			}
		)

	if not rows:
		frappe.throw(_("Your cart is empty."))
	# ⚠ PAYLOAD guard only — entries are SLOTS, not bookings. The bookings and
	# slots bounds are in _reject_oversized_cart, after normalisation.
	if len(rows) > MAX_CART_PAYLOAD_ENTRIES:
		frappe.throw(
			_("That is too many entries for one checkout — please book in smaller batches.")
		)
	return rows


def _cart_courts(rows) -> dict:
	"""Resolve every court once; refuse a cart spanning companies or branches."""
	names = sorted({row["court"] for row in rows})
	courts = {
		court.name: court
		for court in frappe.get_all(
			"CBT Court",
			filters={"name": ("in", names)},
			fields=[
				"name",
				"court_name",
				"branch",
				"company",
				"hourly_rate",
				"is_active",
				"allow_continuation",
			],
		)
	}
	for name in names:
		court = courts.get(name)
		if not court or not cint(court.is_active):
			# Unknown and inactive stay indistinguishable (anti-enumeration).
			frappe.throw(_("This court is not available."), frappe.DoesNotExistError)

	if len({court.company for court in courts.values()}) > 1 or len(
		{court.branch for court in courts.values()}
	) > 1:
		frappe.throw(
			_("A cart books one branch at a time — please check out each branch separately.")
		)
	return courts


def _normalise_cart(rows, branch, courts) -> list[dict]:
	"""The set-of-slots reduction described at the top of this section."""
	grids: dict = {}
	selected: dict = {}
	for row in rows:
		date = row["booking_date"]
		if date not in grids:
			grids[date] = get_slot_grid(branch, date)
		grid = grids[date]
		if not grid:
			frappe.throw(
				_("{0} is closed on {1}.").format(
					branch.branch_name, date.strftime("%A")
				)
			)
		start = _as_timedelta(row["start_time"])
		index = next(
			(i for i, slot in enumerate(grid) if slot["start_time"] == start), None
		)
		if index is None or index + row["number_of_slots"] > len(grid):
			frappe.throw(
				_(
					"That time is no longer on this branch's schedule — please "
					"reload and pick a slot again."
				)
			)
		selected.setdefault((row["court"], date), set()).update(
			range(index, index + row["number_of_slots"])
		)

	def _run(court, date, grid, indexes):
		return {
			"court": court,
			"booking_date": date,
			"start_index": indexes[0],
			"number_of_slots": len(indexes),
			"start_time": _fmt(grid[indexes[0]]["start_time"]),
			"end_time": _fmt(grid[indexes[-1]]["end_time"]),
		}

	runs = []
	for (court, date), indexes in selected.items():
		grid = grids[date]
		ordered = sorted(indexes)
		run = [ordered[0]]
		for index in ordered[1:]:
			if index == run[-1] + 1:
				run.append(index)
				continue
			runs.append(_run(court, date, grid, run))
			run = [index]
		runs.append(_run(court, date, grid, run))

	# Reading order for the checkout list, and the fee-ordinal order.
	runs.sort(
		key=lambda run: (
			run["booking_date"],
			courts[run["court"]].court_name,
			run["start_index"],
		)
	)
	return runs


def _chain_runs(runs, courts) -> list[dict]:
	"""Backlog B49 — mark continuations, in place.

	A run CONTINUES a chain when, on the same date, its first grid index is
	exactly where the chain's last run ends, the courts differ, and BOTH courts
	carry `allow_continuation`. Greedy in (start_index, court_name) order, so
	parallel runs pair off deterministically; an overlap, a gap, an unflagged
	court or another date never chains. Every run gets `chain_head` — the index
	(into `runs`) of its chain's FIRST run, or None when it is a head itself.
	Section-9 as-built 18 carries the ruled table.
	"""
	for run in runs:
		run["chain_head"] = None
	by_date: dict = {}
	for index, run in enumerate(runs):
		by_date.setdefault(run["booking_date"], []).append(index)
	for indexes in by_date.values():
		ordered = sorted(
			indexes,
			key=lambda i: (runs[i]["start_index"], courts[runs[i]["court"]].court_name),
		)
		# grid index a chain currently ends at -> runs that may still be continued
		open_tails: dict = {}
		for index in ordered:
			run = runs[index]
			if not cint(courts[run["court"]].allow_continuation):
				continue
			candidates = open_tails.get(run["start_index"], [])
			tail = next((t for t in candidates if runs[t]["court"] != run["court"]), None)
			if tail is not None:
				candidates.remove(tail)
				head = runs[tail]["chain_head"]
				run["chain_head"] = tail if head is None else head
			open_tails.setdefault(run["start_index"] + run["number_of_slots"], []).append(
				index
			)
	return runs


def _insert_order(runs, courts) -> list[int]:
	"""B49: heads before their continuations. A continuation starts strictly
	after its head on the same date, so (date, start_index, court_name) is enough."""
	return sorted(
		range(len(runs)),
		key=lambda i: (
			runs[i]["booking_date"],
			runs[i]["start_index"],
			courts[runs[i]["court"]].court_name,
		),
	)


def _reject_unavailable_runs(branch, runs, courts, past_from_end=False):
	"""Name the gone slot before inserting; the controller's lock is the backstop.

	⚠ `past_from_end` is the STAFF view's past-line and it is not cosmetic here
	(Backlog B46). Staff may back-record the hour that is RUNNING — a walk-in who
	turned up mid-session — and `create_booking` has always accepted it (S4
	as-built 6, S18/B7). Without the flag this check would read that slot as
	`past`, and a desk cart containing the current hour would be refused by the
	very screen that offered it.
	"""
	from court_booking_tech.slots import _availability

	by_date: dict = {}
	for run in runs:
		date = run["booking_date"]
		if date not in by_date:
			by_date[date] = {
				court["court"]: court
				for court in _availability(
					branch, date, include_customer=False, past_from_end=past_from_end
				)["courts"]
			}
		court_row = by_date[date].get(run["court"])
		slots = (court_row or {}).get("slots") or []
		window = range(run["start_index"], run["start_index"] + run["number_of_slots"])
		if any(
			index >= len(slots) or slots[index]["status"] != "available"
			for index in window
		):
			frappe.throw(
				_(
					"{0} on {1} at {2} is no longer available — nothing in your "
					"cart was booked. Please pick another time."
				).format(
					courts[run["court"]].court_name,
					frappe.format(run["booking_date"], {"fieldtype": "Date"}),
					# B45: the app's ONE time language. This used to be a local
					# `_fmt_12h` printing "1:00 PM" — a sixth rendering of the
					# same hour, in the one sentence that tells a customer which
					# slot they just lost.
					label_short(run["start_time"]),
				)
			)


def _reject_oversized_cart(company, rows, runs, payment_method="Fund Transfer"):
	"""Bookings against the company's cap, and slots against the hold cap.

	⚠ The slot bound is the payability guarantee: _check_customer_holds_cap runs
	at proof upload, not at reserve, so an over-large cart would book then fail.

	Backlog B46: that guarantee is about a PROOF, so it binds Fund Transfer only.
	A desk cart paid in Cash (or Free) mints no hold and needs no receipt — the
	money is in the drawer before the operator clicks Book — and capping a
	tournament's twelve courts at the proof limit would refuse a sale the
	facility has already taken. The BOOKINGS cap still applies to both: it is the
	tenant's own knob, and one rule is better than two.
	"""
	from court_booking_tech.api.proofs import DEFAULT_MAX_HOLDS_PER_CUSTOMER

	limit = resolve_max_cart_items(company.max_cart_items)
	if len(runs) > limit:
		frappe.throw(
			_(
				"A cart can hold at most {0} bookings — please check out in "
				"smaller batches."
			).format(limit)
		)

	if payment_method != "Fund Transfer":
		return

	slots = sum(run["number_of_slots"] for run in runs)
	hold_cap = _setting(
		"max_active_proof_holds_per_customer", DEFAULT_MAX_HOLDS_PER_CUSTOMER
	)
	if slots > hold_cap:
		frappe.throw(
			_(
				"That is {0} hours in one checkout, and a single payment can "
				"cover at most {1}. Please book the rest separately."
			).format(slots, hold_cap)
		)


def _cart_setup(items, require_active_company=True, payment_method="Fund Transfer"):
	"""Shared front half of every cart endpoint: parse, resolve, normalize."""
	rows = _parse_cart_items(items)
	courts = _cart_courts(rows)
	any_court = next(iter(courts.values()))
	company = frappe.get_doc("CBT Company", any_court.company)
	if require_active_company and company.status != "Active":
		frappe.throw(_("This facility is not accepting bookings."))
	branch = frappe.get_doc("CBT Branch", any_court.branch)
	runs = _normalise_cart(rows, branch, courts)
	_chain_runs(runs, courts)
	_reject_oversized_cart(company, rows, runs, payment_method)
	return company, branch, courts, runs


def cart_quote_core(
	company,
	branch,
	courts,
	runs,
	customer: str | None = None,
	discount_percent=None,
	payment_method: str = "Fund Transfer",
) -> dict:
	"""Price a whole cart — N runs, ONE basket. The only place a cart's money is
	computed, for either face.

	Split out for Backlog B46 (the desk cart), which needs the same arithmetic
	with a different GATE, a different IDENTITY and a payment method that can be
	Cash or Free. Every line below is what `get_cart_quote` has always run; the
	three parameters are the desk's, and the portal passes none of them.

	Deliberately NOT whitelisted: it takes resolved documents and applies no
	permission gate of its own — `get_cart_quote` is guest-open, and
	`api.board.get_desk_cart_quote` is staff-gated, and that difference is the
	whole reason they are two endpoints.
	"""
	# The fee ordinal advances inside the basket (see booking_fees_for_units).
	# Backlog B49: HEADS only — a continuation carries no unit of its own; what
	# it prints as waived is its head's fee.
	heads = [i for i, run in enumerate(runs) if run.get("chain_head") is None]
	head_fees = dict(
		zip(
			heads,
			platform_fees.booking_fees_for_units(
				company.name, [runs[i]["booking_date"] for i in heads], payment_method
			),
		)
	)

	lines = []
	for index, run in enumerate(runs):
		head = run.get("chain_head")
		_seq, fee = head_fees[index] if head is None else (0, 0.0)
		line = _quote_core(
			courts[run["court"]],
			branch,
			company,
			booking_date=run["booking_date"],
			start_time=run["start_time"],
			number_of_slots=run["number_of_slots"],
			customer=customer,
			discount_percent=discount_percent,
			platform_fee=fee,
			payment_method=payment_method,
		)
		line["booking_date"] = str(run["booking_date"])
		line["start_time"] = run["start_time"]
		line["end_time"] = run["end_time"]
		line["fee_chained_to_index"] = head
		line["platform_fee_waived"] = 0.0 if head is None else flt(head_fees[head][1], 2)
		lines.append(line)

	court_total = flt(sum(flt(line["court_total"]) for line in lines), 2)
	fee_total = flt(sum(flt(line["platform_fee"]) for line in lines), 2)
	continuous_discount = flt(sum(flt(line["platform_fee_waived"]) for line in lines), 2)
	# ⚠ S25: VAT on the COURT SHARE, computed on the SUM — not per line (rounding).
	breakdown = compute_vat_breakdown(
		court_total, company.vat_registration, company.get_vat_percent()
	)
	# B39 + B46: whose credit this is. A staff caller NAMED the customer (and
	# that parameter already armed the endpoint's gate); the portal always means
	# the session user; a walk-in has no account and therefore no credit.
	holder = customer or (None if frappe.session.user == "Guest" else frappe.session.user)
	# ⚠ NOT min(balance, total). take_credit spends ONE credit document per
	# BOOKING, so a cart's spend is a walk, not a cap — see credits.plan_spend.
	# The portal printed the cap until 2026-09-05 and could overstate it.
	planned = credits.plan_spend(
		company.name, holder, [flt(line["total_amount"]) for line in lines]
	)
	for line, spend in zip(lines, planned):
		line["credit_spend"] = spend

	return {
		"items": lines,
		"count": len(lines),
		"booking_fee_count": sum(1 for line in lines if flt(line["platform_fee"]) > 0),
		"company_name": company.company_name,
		"branch_name": branch.branch_name,
		"subtotal": flt(sum(flt(line["subtotal"]) for line in lines), 2),
		"discount_amount": flt(sum(flt(line["discount_amount"]) for line in lines), 2),
		"court_total": court_total,
		"platform_fee": fee_total,
		# B49: the two lines every face prints — the fee as the bookings would
		# have carried it, and what a continued session saved. Net = platform_fee.
		"booking_fee_as_if": flt(fee_total + continuous_discount, 2),
		"continuous_discount": continuous_discount,
		"total_amount": flt(court_total + fee_total, 2),
		# B39: ONE credit drains across the cart's rows, so the basket reports the
		# balance once rather than repeating each line's copy.
		"credit_available": credits.available_credit(company.name, holder),
		# B46: and what that balance would ACTUALLY settle here, which is the
		# number both checkouts now print.
		"credit_spend": flt(sum(planned), 2),
		"vat_mode": company.vat_registration,
		"vat_percent": company.get_vat_percent(),
		"vatable_amount": breakdown["vatable_amount"],
		"vat_amount": breakdown["vat_amount"],
		"payment_instructions": company.payment_instructions,
		"reservation_expiry_minutes": company.get_reservation_expiry_minutes(),
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_cart_quote(items) -> dict:
	"""Server-computed price for a whole cart. No staff params, so guest-safe.

	⚠ The discount comes from the SESSION user — do not cache a quote across a login.
	"""
	company, branch, courts, runs = _cart_setup(items)
	return cart_quote_core(company, branch, courts, runs)


def mint_cart_group(company, runs) -> str | None:
	"""The cart's group stamp — or None, because ONE run is just a booking.

	⚠ Per-COMPANY series: a global counter would leak cross-tenant volume. Shared
	by both carts (B35's portal one and B46's desk one) so the two can never
	start numbering differently.
	"""
	if len(runs) <= 1:
		return None
	return make_autoname(f"CART-{company.company_code}-.YYYY.-.#####")


@frappe.whitelist(methods=["POST"])
def reserve_cart(items, payment_channel: str | None = None, apply_credit=0) -> dict:
	"""Reserve every run of a cart in one transaction, on one pay-by clock.

	Read-only refusals precede the limiter, which fires ONCE — a cart is one attempt.

	B39: `apply_credit` drains ONE store credit across the rows in insert order
	until it runs out. The whole cart is one transaction, so a row that throws
	rolls the drain back with it.
	"""
	from court_booking_tech.court_booking_tech.doctype.cbt_court_booking.cbt_court_booking import (
		reservation_expiry_minutes,
	)

	_require_login()
	# Company status is left to the controller's require_company_bookable.
	company, branch, courts, runs = _cart_setup(items, require_active_company=False)

	if not cint(branch.is_active):
		frappe.throw(_("This branch is not available."))
	if not payment_channels.list_channels(
		company.name, kind=payment_channels.TRANSFER, fields=("name",)
	):
		frappe.throw(
			_(
				"This facility is not taking online payments right now — please "
				"contact the branch to book."
			)
		)
	_reject_unavailable_runs(branch, runs, courts)

	enforce_user_rate_limit(
		"portal-booking",
		_setting("portal_booking_limit_per_hour", DEFAULT_PORTAL_BOOKINGS_PER_HOUR),
	)
	ensure_customer_profile()

	# Only a real cart gets a group — one run is just a booking (B46 shares the
	# rule with the desk cart: mint_cart_group).
	group = mint_cart_group(company, runs)
	expires = clock.now_dt() + timedelta(
		minutes=reservation_expiry_minutes(company)
	)
	discount = get_member_discount(company.name, frappe.session.user)

	# B49: heads before their continuations, so a continuation can name the
	# booking that carries its session's fee. `booked` keeps the RUNS order.
	booked = [None] * len(runs)
	for index in _insert_order(runs, courts):
		run = runs[index]
		doc = frappe.get_doc(
			{
				"doctype": "CBT Court Booking",
				"court": run["court"],
				"branch": branch.name,
				"company": company.name,
				"customer": frappe.session.user,
				"booking_date": run["booking_date"],
				"start_time": run["start_time"],
				"number_of_slots": run["number_of_slots"],
				# No rate and no status from the client — as reserve_booking.
				"discount_percent": discount,
				"payment_method": "Fund Transfer",
				"payment_channel": (payment_channel or "").strip() or None,
				"booking_group": group,
			}
		)
		doc.flags.customer_created = True
		doc.flags.cart_expires_at = expires
		doc.flags.apply_credit = bool(cint(apply_credit))
		if run.get("chain_head") is not None:
			doc.flags.fee_chained_to = booked[run["chain_head"]].name
		doc.insert(ignore_permissions=True)
		booked[index] = doc

	first = booked[0]
	return {
		"booking_group": group,
		"bookings": [
			{
				"booking": doc.name,
				"court_name": courts[doc.court].court_name,
				"booking_date": str(doc.booking_date),
				"start_time": _fmt(_as_timedelta(doc.start_time)),
				"end_time": _fmt(_as_timedelta(doc.end_time)),
				"total_amount": flt(doc.total_amount),
				"platform_fee": flt(doc.platform_fee),
				"detail_url": f"/my-bookings/{doc.name}",
			}
			for doc in booked
		],
		"count": len(booked),
		"total_amount": flt(sum(flt(doc.total_amount) for doc in booked), 2),
		"credit_applied": flt(sum(flt(doc.credit_applied) for doc in booked), 2),
		# ONE clock for the whole cart — every row carries this same value.
		"reservation_expires_at": first.reservation_expires_at,
		"server_now": clock.now_dt(),
		"company_name": company.company_name,
		"branch_name": branch.branch_name,
		"payment_instructions": company.payment_instructions,
		"payment_channel": first.payment_channel,
		"payment_channel_detail": payment_channels.channel_detail(first.payment_channel),
		"detail_url": "/my-bookings",
	}


@frappe.whitelist(methods=["POST"])
def cancel_my_booking(name: str) -> dict:
	"""Customer self-cancel — UNPAID Reserved only (PLAN §5).

	"Unpaid" means no proof has claimed the money yet: once a Pending or
	Accepted proof exists, cancelling is a staff action with a refund
	conversation attached. The row is locked first so this serialises against
	the sweep and against staff confirming the very same booking.
	"""
	from court_booking_tech.api.bookings import _locked_booking

	_require_login()
	doc = _locked_booking(name)
	if doc.customer != frappe.session.user:
		frappe.throw(_("This booking is not yours."), frappe.PermissionError)
	if doc.booking_status != "Reserved":
		frappe.throw(
			_("Only a reserved booking can be cancelled online — please contact the branch.")
		)
	if frappe.db.exists(
		"CBT Payment Proof",
		{"booking": doc.name, "status": ("in", ("Pending", "Accepted"))},
	):
		frappe.throw(
			_(
				"You have already sent a payment proof for this booking — "
				"please contact the branch to cancel it."
			)
		)

	doc.booking_status = "Cancelled"
	doc.flags.customer_cancel = True
	doc.save(ignore_permissions=True)  # on_update syncs the invoice to Cancelled
	# B39: dropping a hold you part-paid with store credit gives the credit back.
	restored = credits.restore_credit(doc.name)
	return {
		"booking": doc.name,
		"booking_status": doc.booking_status,
		"credit_restored": restored,
	}


# ---------------------------------------------------------------------------
# My bookings
# ---------------------------------------------------------------------------


def _booking_card(row: dict) -> dict:
	card = {
		"booking": row["name"],
		"company_name": row.get("company_name"),
		"company_logo": row.get("company_logo"),
		"branch_name": row.get("branch_name"),
		"court_name": row.get("court_name"),
		"booking_date": str(row["booking_date"]),
		"start_time": _fmt(_as_timedelta(row["start_time"])),
		"end_time": _fmt(_as_timedelta(row["end_time"])),
		"total_amount": flt(row["total_amount"]),
		"booking_status": row["booking_status"],
		"reservation_expires_at": row.get("reservation_expires_at"),
		"verification_deadline_at": row.get("verification_deadline_at"),
		"rejection_count": cint(row.get("rejection_count")),
		"billing_doc": row.get("billing_doc"),
	}
	# Section-19 (Backlog B5): the move story. Keys are added only when the
	# booking really is half of a move, so an ordinary booking's card carries
	# NEITHER. That absence is DELIBERATE, not a side effect: these are
	# whitelisted-method payloads, where None serialises as `null` with the key
	# present (unlike /api/resource, which drops nulls — S13 as-built 15). The
	# page's truthiness check reads the same either way; the omission is what
	# lets a test state "this booking was never moved" as key absence.
	for field in ("rescheduled_to", "rescheduled_from", "booking_group"):
		if row.get(field):
			card[field] = row[field]
	return card


def _moved_when(row) -> dict:
	"""A move counterpart's slot, in the SAME shape a card carries its own — so
	the page formats both through one function and they cannot drift apart."""
	return {
		"date": str(row["booking_date"]),
		"start_time": _fmt(_as_timedelta(row["start_time"])),
	}


@frappe.whitelist(methods=["GET"])
def get_my_bookings() -> dict:
	"""Cross-company history — one account, every facility (PLAN §1)."""
	_require_login()
	# Backlog B44: the balances come FIRST and are returned from both exits.
	# A customer whose only booking was refunded into credit and cancelled holds
	# credit and has no bookings at all — which is exactly the person this line
	# exists for, and exactly the person the zero-rows early return below would
	# have shown nothing. Keyed on frappe.session.user like the query itself:
	# this endpoint takes no arguments, and giving it one would be the leak.
	credit_balances = credits.balances_by_company(frappe.session.user)
	rows = frappe.get_all(
		"CBT Court Booking",
		filters={"customer": frappe.session.user},
		fields=[
			"name",
			"company",
			"branch",
			"court",
			"booking_date",
			"start_time",
			"end_time",
			"total_amount",
			"booking_status",
			"reservation_expires_at",
			"verification_deadline_at",
			"rejection_count",
			"billing_doc",
			# Section-19 (B5): the two halves of a move link to each other.
			"rescheduled_to",
			"rescheduled_from",
			"booking_group",
		],
		order_by="booking_date desc, start_time desc",
		ignore_permissions=True,
	)
	# The company names are resolved for the BOOKINGS and the CREDITS together —
	# a credit at a company this customer has never booked at would otherwise
	# render its raw slug.
	companies = _name_map(
		"CBT Company",
		{row.company for row in rows} | set(credit_balances),
		["company_name", "logo"],
	)

	def _credit_cards() -> list:
		return [
			{
				"company": company,
				"company_name": (companies.get(company) or {}).get("company_name")
				or company,
				"balance": balance,
			}
			for company, balance in sorted(credit_balances.items())
		]

	if not rows:
		return {
			"bookings": [],
			"credits": _credit_cards(),
			"server_now": clock.now_dt(),
		}

	branches = _name_map("CBT Branch", {row.branch for row in rows}, ["branch_name"])
	courts = _name_map("CBT Court", {row.court for row in rows}, ["court_name"])
	pending = _bookings_with_pending_proof([row.name for row in rows])

	cards = []
	for row in rows:
		company = companies.get(row.company) or {}
		row_dict = dict(row)
		row_dict.update(
			{
				"company_name": company.get("company_name") or row.company,
				"company_logo": company.get("logo"),
				"branch_name": (branches.get(row.branch) or {}).get("branch_name")
				or row.branch,
				"court_name": (courts.get(row.court) or {}).get("court_name")
				or row.court,
			}
		)
		card = _booking_card(row_dict)
		card["has_pending_proof"] = row.name in pending
		card["is_open"] = row.booking_status in OPEN_STATUSES
		cards.append(card)

	# Section-19 (B5): resolve each move link to WHEN the counterpart is, so the
	# cancelled half can say "Moved to <date> <time>" rather than showing a bare
	# document id. Done as one in-payload cross-reference rather than a query per
	# card: reschedule_booking copies `customer`, so a counterpart is ALWAYS this
	# same customer's booking and therefore already in this list — which also
	# means the pass structurally cannot surface another customer's booking. If a
	# counterpart is ever missing, the key is simply absent and the page falls
	# back to the link without a date.
	by_name = {row.name: row for row in rows}
	for card in cards:
		for field, key in (("rescheduled_to", "moved_to"), ("rescheduled_from", "moved_from")):
			other = by_name.get(card.get(field))
			if other:
				card[key] = _moved_when(other)

	# Live bookings first (soonest first), then history (most recent first).
	cards.sort(
		key=lambda card: (
			not card["is_open"],
			card["booking_date"] if card["is_open"] else "",
			card["start_time"] if card["is_open"] else "",
		)
	)
	history = [card for card in cards if not card["is_open"]]
	history.sort(key=lambda card: (card["booking_date"], card["start_time"]), reverse=True)
	return {
		"bookings": [card for card in cards if card["is_open"]] + history,
		"credits": _credit_cards(),
		"server_now": clock.now_dt(),
	}


def _name_map(doctype: str, names: set, fields: list[str]) -> dict:
	names = {name for name in names if name}
	if not names:
		return {}
	return {
		row["name"]: row
		for row in frappe.get_all(
			doctype,
			filters={"name": ("in", list(names))},
			fields=["name", *fields],
			ignore_permissions=True,
		)
	}


def _bookings_with_pending_proof(names: list[str]) -> set:
	if not names:
		return set()
	return {
		row.booking
		for row in frappe.get_all(
			"CBT Payment Proof",
			filters={"booking": ("in", names), "status": "Pending"},
			fields=["booking"],
			ignore_permissions=True,
		)
	}


@frappe.whitelist(methods=["GET"])
def get_my_booking_detail(name: str) -> dict:
	"""Everything /my-bookings/<name> renders — including the two things the
	customer cannot compute: the exact verification deadline and the company's
	office-hours sentence (PLAN §5a)."""
	doc = _own_booking(name)
	company = frappe.get_doc("CBT Company", doc.company)
	branch = frappe.db.get_value(
		"CBT Branch",
		doc.branch,
		["branch_name", "address_text", "latitude", "longitude", "phone"],
		as_dict=True,
	)
	proofs = frappe.get_all(
		"CBT Payment Proof",
		filters={"booking": doc.name},
		fields=[
			"name",
			"status",
			"source",
			"reference_no",
			"payment_channel",
			"remarks",
			"file",
			# Backlog B10 (section-22): the APP's own upload stamp, not frappe's
			# insert stamp. `creation` never passes through clock.now_dt() and
			# `uploaded_at` always does (cbt_payment_proof.py:59), so under any
			# moved clock — a monkeypatched backend test, section-17's E2E lever
			# — this page used to show an upload time inconsistent with the
			# verification deadline computed FROM uploaded_at: the countdown and
			# the timestamp explaining it disagreed. The board always shipped the
			# doc field (api/board.py:251); only the customer's own copy lied.
			"uploaded_at",
			"rejected_at",
			"rejection_reason",
		],
		# Deliberately still `creation`: this is INSERTION order, which is what
		# "the proofs in the order they arrived" means, and it is the one sort
		# key a moved clock cannot reorder.
		order_by="creation asc",
		ignore_permissions=True,
	)
	has_pending = any(proof.status == "Pending" for proof in proofs)

	card = _booking_card(
		{
			**dict(doc.as_dict()),
			"company_name": company.company_name,
			"company_logo": company.logo,
			"branch_name": branch.branch_name if branch else doc.branch,
			"court_name": frappe.db.get_value("CBT Court", doc.court, "court_name"),
		}
	)
	card.update(
		{
			"has_pending_proof": has_pending,
			"is_open": doc.booking_status in OPEN_STATUSES,
			"payment_method": doc.payment_method,
			"hourly_rate": flt(doc.hourly_rate),
			"duration_hours": flt(doc.duration_hours),
			# Section-11: shown as a "Member discount" line so the customer can
			# see WHY the total is lower than rate × hours.
			"discount_percent": flt(doc.discount_percent),
			# Backlog B27: the platform booking fee, shown as its own line so the
			# customer can see why the total is higher than the court.
			"platform_fee": flt(doc.platform_fee),
			# Backlog B49: a continuation prints the fee it would have carried
			# and the same amount back as "Continuous booking discount".
			"platform_fee_waived": flt(doc.get("platform_fee_waived")),
			"fee_chained_to": doc.get("fee_chained_to"),
			# Backlog B29: the channel this customer chose, its account details
			# for the "How to pay" card, and the company's enabled transfer
			# channels for the proof upload's "I paid through…" picker. Public
			# fields only — this is the customer's own page.
			"payment_channel": doc.payment_channel,
			"payment_channel_detail": payment_channels.channel_detail(doc.payment_channel),
			"payment_channels": payment_channels.public_channels(doc.company),
			# Section-14: the customer's own copy of the rate breakdown. Without
			# it the page would print "Rate ₱275.00/hr × 2" for a booking that
			# crossed a rate boundary — an average matching no rule, on the one
			# screen the customer checks before paying. Pricing structure only;
			# it carries no identity and nothing another tenant could read.
			"rate_segments": pricing.segments_payload(doc.get("rate_segments")),
		}
	)

	# Section-19 (B5): WHEN the other half of a move is. One page shows ONE
	# booking, so unlike the list this cannot cross-reference — it reads the
	# counterpart directly, at most twice, and only when the link exists.
	# The `customer` filter is defence-in-depth, not decoration: every portal
	# endpoint answers about the session user's own data, and this is the one
	# place the payload is derived from a document id rather than from a
	# session-filtered query. reschedule_booking copies `customer`, so today the
	# filter can never fail — which is exactly when to put it in.
	for field, key in (("rescheduled_to", "moved_to"), ("rescheduled_from", "moved_from")):
		counterpart = doc.get(field)
		if not counterpart:
			continue
		other = frappe.db.get_value(
			"CBT Court Booking",
			{"name": counterpart, "customer": frappe.session.user},
			["booking_date", "start_time"],
			as_dict=True,
		)
		if other:
			card[key] = _moved_when(other)

	# A recoverable rejection buys exactly one re-upload window (S5): while it
	# runs, reservation_expires_at IS the re-upload deadline.
	in_regrace = (
		doc.booking_status == "Reserved"
		and cint(doc.rejection_count) == 1
		and not has_pending
	)
	last_rejected = next(
		(proof for proof in reversed(proofs) if proof.status == "Rejected"), None
	)

	# B29 ruling: VAT on the court share only — the fee line is outside it.
	breakdown = compute_vat_breakdown(
		flt(doc.total_amount) - flt(doc.platform_fee),
		company.vat_registration,
		company.get_vat_percent(),
	)
	invoice_status = (
		frappe.db.get_value("CBT Booking Invoice", doc.billing_doc, "status")
		if doc.billing_doc
		else None
	)

	return {
		**card,
		"proofs": [
			{
				"name": proof.name,
				"status": proof.status,
				"source": proof.source,
				"reference_no": proof.reference_no,
				"payment_channel": proof.payment_channel,
				"remarks": proof.remarks,
				"uploaded_at": proof.uploaded_at,
				"rejected_at": proof.rejected_at,
				"rejection_reason": proof.rejection_reason,
				"is_pdf": (proof.file or "").lower().endswith(".pdf"),
				# Never the raw /private/files URL: File permission follows the
				# attached doc and customers hold no proof DocPerm — it 403s
				# for its own owner (S5 as-built 8).
				"file_url": f"/api/method/court_booking_tech.api.portal.get_proof_file?proof={proof.name}",
			}
			for proof in proofs
		],
		"can_upload_proof": doc.booking_status == "Reserved",
		"can_cancel": doc.booking_status == "Reserved" and not _has_claiming_proof(doc.name),
		"in_regrace": in_regrace,
		"last_rejection_reason": last_rejected.rejection_reason if last_rejected else None,
		"office_hours_summary": office_hours_summary(company),
		"payment_instructions": company.payment_instructions,
		"vat_mode": company.vat_registration,
		"vat_percent": company.get_vat_percent(),
		"vatable_amount": breakdown["vatable_amount"],
		"vat_amount": breakdown["vat_amount"],
		"invoice_status": invoice_status,
		"branch_address": branch.address_text if branch else None,
		"branch_phone": branch.phone if branch else None,
		"branch_latitude": branch.latitude if branch else None,
		"branch_longitude": branch.longitude if branch else None,
		"proof_max_mb": _setting("proof_max_mb", 10),
		"server_now": clock.now_dt(),
	}


def _has_claiming_proof(booking: str) -> bool:
	return bool(
		frappe.db.exists(
			"CBT Payment Proof",
			{"booking": booking, "status": ("in", ("Pending", "Accepted"))},
		)
	)


# ---------------------------------------------------------------------------
# Guarded artefacts: proof image, billing statement PDF
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_proof_file(proof: str):
	"""Serve a proof image/PDF to the people entitled to it.

	Customers have no DocPerm on CBT Payment Proof, so the File's own
	permission check (which follows attached_to) 403s them out of their OWN
	upload — this endpoint is the sanctioned way in (S5 as-built 8 hand-off).
	"""
	_require_login()
	row = frappe.db.get_value(
		"CBT Payment Proof", proof, ["name", "booking", "file"], as_dict=True
	)
	if not row:
		frappe.throw(_("Proof not found."), frappe.DoesNotExistError)
	_booking_viewer(row.booking)  # owner or that company's staff

	file_name = frappe.db.get_value("File", {"file_url": row.file}, "name")
	if not file_name:
		frappe.throw(_("Proof file is missing."), frappe.DoesNotExistError)
	file_doc = frappe.get_doc("File", file_name)

	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"
	# Renders in an <img>/PDF viewer instead of forcing a save dialog.
	frappe.local.response.display_content_as = "inline"


def render_billing_statement(booking: str) -> dict:
	"""The company's billing document, rendered with the REAL print format.

	get_rendered_template enforces read/print permission unless
	ignore_print_permissions is set — customers legitimately have neither on
	CBT Booking Invoice, so the flag is flipped around OUR OWN guard
	(_booking_viewer, already run by the caller) and restored in finally.
	Rendering the shipped format (rather than re-implementing it) is what
	guarantees the customer's copy is byte-identical to the desk's.
	"""
	from frappe.www.printview import (
		get_print_format_doc,
		get_print_style,
		get_rendered_template,
	)

	invoice_name = frappe.db.get_value("CBT Court Booking", booking, "billing_doc")
	if not invoice_name:
		frappe.throw(_("This booking has no billing statement yet."))
	invoice = frappe.get_doc("CBT Booking Invoice", invoice_name)
	print_format = get_print_format_doc(
		"CBT Billing Statement", meta=frappe.get_meta("CBT Booking Invoice")
	)

	previous = frappe.flags.ignore_print_permissions
	frappe.flags.ignore_print_permissions = True
	try:
		body = get_rendered_template(
			doc=invoice,
			print_format=print_format,
			meta=invoice.meta,
			no_letterhead=1,
		)
		style = get_print_style(print_format=print_format)
	finally:
		frappe.flags.ignore_print_permissions = previous

	return {"invoice": invoice.name, "status": invoice.status, "body": body, "style": style}


@frappe.whitelist(methods=["GET"])
def download_invoice_pdf(booking: str):
	"""Same document, same guard, as a PDF attachment."""
	doc = _booking_viewer(booking)
	rendered = render_billing_statement(doc.name)

	from frappe.utils.pdf import get_pdf

	frappe.local.response.filename = f"{rendered['invoice']}.pdf"
	frappe.local.response.filecontent = get_pdf(
		f"<style>{rendered['style']}</style>{rendered['body']}"
	)
	frappe.local.response.type = "pdf"


# ---------------------------------------------------------------------------
# Page helpers (www controllers import these — not whitelisted)
# ---------------------------------------------------------------------------


def portal_login_redirect(path: str):
	"""Guest hitting a customer-only page: bounce to login and come back."""
	frappe.local.flags.redirect_location = f"/login?redirect-to={path}"
	raise frappe.Redirect


def portal_base_context(context):
	"""Shared context for every portal page: who is looking, and TODAY in site
	time (Asia/Manila). The date strip must never be derived from the browser
	clock — a customer abroad would be offered yesterday's slots."""
	context.no_cache = 1
	context.is_guest = frappe.session.user == "Guest"
	context.full_name = (
		None
		if context.is_guest
		else frappe.db.get_value("User", frappe.session.user, "full_name")
	)
	context.today = str(clock.now_dt().date())
	return context
