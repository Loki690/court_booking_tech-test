# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import re
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from court_booking_tech.court_booking_tech.doctype.cbt_business_hours.cbt_business_hours import (
	validate_business_hours,
)
from court_booking_tech.payment_channels import ensure_default_channels
from court_booking_tech.platform_fees import PER_BOOKING, validate_tiers
from court_booking_tech.tenancy import reconcile_photos_on_parent_save

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,6}$")
RENAME_ROLES = {"System Manager", "CBT Platform Admin"}

# Backlog B24 (2026-08-27): the tenant's own website and social pages, shown
# to customers WHILE BOOKING (user ruling 2026-08-20 — its own strip on /book,
# opened in a new tab, links only: we host, curate and sync nothing).
#
# One ordered table drives both the validation here and the portal payload
# (api/portal.resolve_book_page), so the chips render in this order and a
# network added later is a one-line change in exactly one place.
#
#   (fieldname, kind, label, allowed hosts or None)
#
# The host list is the guest-exposure guard the row warned about: these
# values are tenant-authored and render as <a href> to LOGGED-OUT visitors on a
# page carrying our domain. The chip's label is OUR word ("Facebook"), so the
# host has to earn it — a "Facebook" chip opening some other site is exactly
# the phishing shape. The website chip may point anywhere http(s).
PUBLIC_LINKS = (
	("website", "website", "Website", None),
	("facebook_url", "facebook", "Facebook", ("facebook.com", "fb.com", "fb.me")),
	("instagram_url", "instagram", "Instagram", ("instagram.com", "instagr.am")),
)
PUBLIC_LINK_FIELDS = tuple(row[0] for row in PUBLIC_LINKS)


def _has_forbidden_chars(value: str) -> bool:
	# Whitespace, C0 controls and DEL are never part of a URL a human typed;
	# `str.isspace()` alone misses the controls (\x00 splits fine in urlsplit).
	return any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def clean_public_link(value, label: str, hosts=None) -> str | None:
	"""Strip, then insist on a FULL http(s) web address; None when blank.

	Rejected on purpose, each with a real consequence on a guest page:
	- no scheme ("ayalacourts.ph") — a browser resolves it RELATIVE to /book;
	- any scheme but http/https — `javascript:` and `data:` run in our origin;
	- whitespace or a control character anywhere — never a URL, always a paste
	  that went wrong;
	- no host — "https://" alone;
	- a backslash or userinfo in the value. Python's urlsplit reads the host of
	  `https://evil.example\\@facebook.com` as facebook.com (everything after the
	  LAST `@`), while every browser follows the WHATWG parser, where `\\` ends
	  the authority — so the "Facebook" chip would open evil.example. Refusing
	  both characters outright closes the whole family (ducky, 2026-08-27);
	- for a social network, a host outside that network (see PUBLIC_LINKS).
	"""
	value = (value or "").strip()
	if not value:
		return None
	parts = None
	if not _has_forbidden_chars(value) and "\\" not in value and "@" not in value:
		parts = urlsplit(value)
	host = (parts.hostname or "").lower() if parts else ""
	ok = bool(parts) and parts.scheme in ("http", "https") and bool(host)
	if ok and hosts:
		ok = any(host == allowed or host.endswith("." + allowed) for allowed in hosts)
	if not ok:
		if hosts:
			frappe.throw(
				_(
					"{0} must be a full web address on {1}, starting with https:// "
					"(e.g. https://www.{2}/yourpage)."
				).format(_(label), " or ".join(hosts), hosts[0])
			)
		frappe.throw(
			_(
				"{0} must be a full web address starting with https:// "
				"(e.g. https://www.example.com)."
			).format(_(label))
		)
	return value


def public_links(company) -> list[dict]:
	"""The chips /book draws, in PUBLIC_LINKS order, UNSET ONES OMITTED.

	`company` is any mapping carrying the link fieldnames (a get_value row or a
	Document). Absent-not-empty is the row's empty-state rule: a company with
	nothing set gets no strip, never a row of dead chips.
	"""
	links = []
	for fieldname, kind, label, _hosts in PUBLIC_LINKS:
		url = (company.get(fieldname) or "").strip()
		# Defence in depth at the render seam: the controller validates on
		# save, but a db_set / SQL write bypasses `validate` — so nothing that is
		# not a plain http(s) address is ever handed to a guest <a href>.
		if url and url.lower().startswith(("http://", "https://")):
			links.append({"kind": kind, "label": _(label), "url": url})
	return links

# Four weeks + a couple of days. The hard floor exists because a 0 horizon
# would mean "today only" — see resolve_advance_booking_days.
DEFAULT_ADVANCE_BOOKING_DAYS = 30


def resolve_advance_booking_days(value) -> int:
	"""company override -> platform default -> hard constant.

	The constant is LOAD-BEARING, unlike the other accessors' two-step chains:
	frappe.db.get_single_value returns 0 (not the field default) for a field
	added to an already-saved Singles row, so every site that opened CBT
	Platform Settings before this field existed reads 0 — and 0 days would
	silently mean "customers can only book today". Same trap the verification
	walker guards with DEFAULT_HOLD_HOURS; here it must never be optional, so
	it lives in the resolver instead of at each call site.

	Module-level (not just a Document method) because resolve_book_page reads
	the company through get_value and must not pay for a full doc load.
	"""
	return (
		cint(value)
		or cint(
			frappe.db.get_single_value(
				"CBT Platform Settings", "default_advance_booking_days"
			)
		)
		or DEFAULT_ADVANCE_BOOKING_DAYS
	)


DEFAULT_MAX_CART_ITEMS = 8


def resolve_max_cart_items(value) -> int:
	"""company override -> platform default -> constant, CLAMPED to the platform.

	⚠ Bounds BOOKINGS only. The payability bound is on slots, in api/portal.
	"""
	ceiling = (
		cint(frappe.db.get_single_value("CBT Platform Settings", "max_cart_items"))
		or DEFAULT_MAX_CART_ITEMS
	)
	return min(cint(value) or ceiling, ceiling)


class CBTCompany(Document):
	def validate(self):
		self._validate_slug()
		self._validate_company_code()
		validate_business_hours(self.office_hours, label=_("Office Hours"))
		self._validate_public_links()
		self._validate_billing_mode()
		reconcile_photos_on_parent_save(self)

	def after_insert(self):
		# Backlog B29 (user ruling): "BY DEFAULT WE HAVE CASH, GCASH". Created with
		# the company so the very first booking has a channel to land on; the
		# platform edits, disables or adds banks from there.
		ensure_default_channels(self.name)

	def on_trash(self):
		# A company's channels are its CONFIGURATION, not payment history — they
		# go with it. `ignore_on_trash`: the channel's own guard refuses a delete
		# while payments name it, and a `force=True` company delete (the shape
		# every test cleanup and seed uses) leaves those payment rows in place —
		# the guard would otherwise turn a cascade that used to succeed into a
		# throw (ducky finding 4). The rows it protected belong to the company
		# being deleted.
		for name in frappe.get_all(
			"CBT Payment Channel", filters={"company": self.name}, pluck="name"
		):
			frappe.delete_doc(
				"CBT Payment Channel",
				name,
				force=True,
				ignore_permissions=True,
				ignore_on_trash=True,
			)

	def _validate_billing_mode(self):
		"""Backlog B27: a Per Booking tenant must carry a usable tier table.

		Refused rather than defaulted to ₱0 (the author's recommendation, put to
		the user 2026-08-27): a silent zero is how a tenant finds out in December
		that no fee was ever charged. The other two modes ignore the table.
		"""
		if self.billing_mode == PER_BOOKING:
			validate_tiers(self.booking_fee_tiers, label=_("Booking Fee Tiers"))

	def _validate_public_links(self):
		for fieldname, _kind, label, hosts in PUBLIC_LINKS:
			self.set(fieldname, clean_public_link(self.get(fieldname), label, hosts))

	def _validate_slug(self):
		if not SLUG_PATTERN.match(self.slug or ""):
			frappe.throw(
				_(
					"Slug must be lowercase letters/digits separated by single hyphens "
					"(e.g. ayala-courts)."
				)
			)
		if not self.is_new() and self.slug != self.name:
			frappe.throw(
				_(
					"The slug is this company's ID and printed deep-link key. "
					"Use Rename (Platform Admin only) to change it."
				)
			)

	def _validate_company_code(self):
		if not self.company_code:
			self.company_code = re.sub(r"[^a-z0-9]", "", self.slug or "")[:6]
		self.company_code = (self.company_code or "").upper()
		if not CODE_PATTERN.match(self.company_code):
			frappe.throw(_("Company Code must be 2–6 uppercase letters or digits."))
		if not self.is_new():
			before = frappe.db.get_value("CBT Company", self.name, "company_code")
			if before and before != self.company_code:
				frappe.throw(
					_(
						"Company Code is immutable after first save — it feeds the "
						"BK-/INV- numbering series."
					)
				)

	def before_rename(self, old, new, merge=False):
		# frappe.rename_doc only checks write permission, so this hook is the
		# Platform-Admin-only gate on slug changes (printed deep links break).
		if frappe.session.user != "Administrator" and not (
			RENAME_ROLES & set(frappe.get_roles())
		):
			frappe.throw(
				_("Only a Platform Admin may rename a company (the slug is printed in deep links)."),
				frappe.PermissionError,
			)
		if merge:
			frappe.throw(_("Merging companies is not supported."))
		if not SLUG_PATTERN.match(new or ""):
			frappe.throw(
				_(
					"New slug must be lowercase letters/digits separated by single hyphens "
					"(e.g. ayala-courts)."
				)
			)
		# No after_rename needed: core update_autoname_field() syncs the slug
		# column to the new name for field:-autoname doctypes.

	# ------------------------------------------------------------------
	# Fallback accessors (consumed by sections 4/5/6). 0/empty on the
	# company means "use the CBT Platform Settings default".
	# ------------------------------------------------------------------

	def get_reservation_expiry_minutes(self) -> int:
		return cint(self.reservation_expiry_minutes) or cint(
			frappe.db.get_single_value("CBT Platform Settings", "default_reservation_expiry_minutes")
		)

	def get_advance_booking_days(self) -> int:
		return resolve_advance_booking_days(self.advance_booking_days)

	def get_no_show_release_minutes(self) -> int:
		"""Minutes after start before an un-checked-in Confirmed booking is
		released (section-16, PLAN §8s). 0 = OFF.

		DELIBERATELY NOT A CHAIN — the ONE accessor on this document that does
		not fall back to CBT Platform Settings. Everywhere else 0 means
		"inherit the platform default"; here it has to mean OFF, because a
		platform default would silently switch on automatic cancellation of
		PAID bookings for every tenant on the site the day it is set. A
		facility (or the platform admin acting for one) opts in per company.
		"""
		return cint(self.no_show_release_minutes)

	def get_verification_hold_hours(self) -> int:
		return cint(self.verification_hold_hours) or cint(
			frappe.db.get_single_value("CBT Platform Settings", "default_verification_hold_hours")
		)

	def get_vat_percent(self) -> float:
		"""VAT rate for billing math. ALWAYS 0 for a NON-VAT company — the DB
		still carries the hidden default (12) there, and billing math must
		never see it."""
		if self.vat_registration != "VAT":
			return 0.0
		return flt(self.vat_percent) or flt(
			frappe.db.get_single_value("CBT Platform Settings", "default_vat_percent")
		)
