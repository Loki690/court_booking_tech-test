# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
The facilities tree in place (section-27, Backlog B30): the **Branches** tab on
CBT Company and the **Courts** tab on CBT Branch read AND edit the children
without leaving the parent form.

The user's words (2026-08-27): *"You should not go out of `CBT Company Page` to
read and modify your `CBT Branch` … you should not go out of that specific CBT
Branch in order to read and modify your `CBT Court`."* And on what the in-place
editor exposes: *"everything … it would look like the same but inside a tab …
but of course CBT Company field is readonly and disabled since it is presumed"*.

So the client renders the child's WHOLE layout from its own meta, and this
module is the server side of that: two list endpoints for the tab, one read
for the editor, one save. THE SAVE TRUSTS NOTHING THE CLIENT SAYS ABOUT
TENANCY. `Document.insert()` runs `check_permission("create")` BEFORE
`before_insert` derives a court's company from its branch (document.py:454 vs
:457), so a payload that names another tenant's branch and its own company
would pass the permission hook and then be "corrected" by the controller —
a court inserted into someone else's branch. The company is therefore
resolved HERE from the database (existing row → its own company; new court →
its branch's company; new branch → the payload's company, which is then
gated), the tenant gate runs on THAT value, and it is forced onto the
document before any save. The controllers' own immutability guards remain as
a second line, never the first.

The editor sends every field of the child's meta (blank = explicit null) plus
`name` + `modified`; the save rebuilds the target from the stored row and that
payload the way `frappe.desk.form.save.savedocs` does, so frappe's own
`check_if_latest` refuses a stale edit. Identity and audit columns frappe
owns are never taken from the client.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from court_booking_tech.tenancy import (
	branch_management_allowed,
	ensure_branch_management_allowed,
	ensure_media_editing_allowed,
	has_platform_scope,
	media_editing_allowed,
	require_company_access,
)

FACILITY_DOCTYPES = ("CBT Branch", "CBT Court")
# The two forms that carry a Media tab. An allowlist, not a parameter the
# client chooses from — these endpoints take a doctype name.
MEDIA_DOCTYPES = ("CBT Company", "CBT Branch")
# The read cap and the write cap are the SAME number, or the tab shows a
# truncated list and the next save writes that truncation back as the truth.
MEDIA_MAX = 40
PARENT_FIELD = {"CBT Branch": "company", "CBT Court": "branch"}

# Never taken from the client — frappe's identity/audit columns, and the keys the
# desk model decorates a document with.
_RESERVED = frozenset(
	{
		"doctype",
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
	}
)
_LAYOUT_TYPES = frozenset({"Section Break", "Column Break", "Tab Break", "HTML", "Fold", "Page Break"})


def _check_doctype(doctype: str):
	if doctype not in FACILITY_DOCTYPES:
		frappe.throw(
			_("{0} is not a facility document.").format(doctype or ""),
			frappe.ValidationError,
		)


def _branch_company(branch: str | None) -> str:
	if not branch:
		frappe.throw(_("Branch is required."), frappe.ValidationError)
	company = frappe.db.get_value("CBT Branch", branch, "company")
	if not company:
		frappe.throw(_("Branch {0} not found.").format(branch), frappe.DoesNotExistError)
	return company


# ---------------------------------------------------------------------------
# Reads — the tab's list and the editor's document. Reads pass allow_suspended
# (tenancy.require_company_access's own rule): a suspended tenant still sees
# and maintains its facilities.
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def list_branches(company: str) -> dict:
	"""The Branches tab of one company: every branch the seat may read, with
	its court count and lowest active rate, plus the two flags the tab renders
	from — may this seat manage branches, and if not, is that the platform's
	doing (the self-management switch) rather than the seat's role."""
	require_company_access(company, allow_suspended=True)
	rows = frappe.get_list(
		"CBT Branch",
		filters={"company": company},
		fields=[
			"name",
			"branch_name",
			"slug",
			"is_active",
			"phone",
			"address_text",
			"latitude",
			"longitude",
		],
		order_by="branch_name asc",
		limit=500,
	)
	names = [r.name for r in rows]
	# get_list, not get_all: the court rows go through court_query like every
	# other read in this app. Filtered on the already-scoped branch names too.
	courts = (
		frappe.get_list(
			"CBT Court",
			filters={"branch": ("in", names)},
			fields=["branch", "is_active", "hourly_rate"],
			limit=5000,
		)
		if names
		else []
	)
	summary: dict[str, dict] = {}
	for court in courts:
		entry = summary.setdefault(
			court.branch, {"courts": 0, "active_courts": 0, "rate_from": None}
		)
		entry["courts"] += 1
		if cint(court.is_active):
			entry["active_courts"] += 1
			rate = flt(court.hourly_rate)
			if rate > 0 and (entry["rate_from"] is None or rate < entry["rate_from"]):
				entry["rate_from"] = rate
	for row in rows:
		row.update(summary.get(row.name, {"courts": 0, "active_courts": 0, "rate_from": None}))
		row["pinned"] = bool(row.latitude and row.longitude)
	managed_by_platform = not has_platform_scope() and not cint(
		frappe.db.get_value("CBT Company", company, "allow_self_branch_management")
	)
	return {
		"rows": rows,
		"can_manage": bool(
			frappe.has_permission("CBT Branch", "write") and branch_management_allowed(company)
		),
		"managed_by_platform": bool(managed_by_platform),
	}


@frappe.whitelist(methods=["GET"])
def list_courts(branch: str) -> dict:
	"""The Courts tab of one branch. Courts carry no management gate (S3): a
	Company Admin always manages courts, Staff read them."""
	company = _branch_company(branch)
	require_company_access(company, allow_suspended=True)
	rows = frappe.get_list(
		"CBT Court",
		filters={"branch": branch},
		fields=["name", "court_name", "court_type", "hourly_rate", "is_active", "description"],
		order_by="court_name asc",
		limit=500,
	)
	names = [r.name for r in rows]
	rules = (
		frappe.get_all(
			"CBT Court Rate Rule",
			filters={"parent": ("in", names), "parenttype": "CBT Court"},
			fields=["parent"],
			parent_doctype="CBT Court",
			limit=5000,
		)
		if names
		else []
	)
	rule_count: dict[str, int] = {}
	for rule in rules:
		rule_count[rule.parent] = rule_count.get(rule.parent, 0) + 1
	for row in rows:
		row["rate_rules"] = rule_count.get(row.name, 0)
	return {
		"rows": rows,
		"can_manage": bool(frappe.has_permission("CBT Court", "write")),
	}


@frappe.whitelist(methods=["GET"])
def get_facility(doctype: str, name: str) -> dict:
	"""The editor's document — the whole row with its child tables, tenant-gated
	and then read-permission-checked (the has_permission hooks run in the second)."""
	_check_doctype(doctype)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found.").format(doctype, name), frappe.DoesNotExistError)
	doc = frappe.get_doc(doctype, name)
	require_company_access(doc.company, allow_suspended=True)
	doc.check_permission("read")
	return doc.as_dict()


# ---------------------------------------------------------------------------
# The save
# ---------------------------------------------------------------------------


def _clean_payload(doctype: str, payload: dict, existing_name: str | None) -> dict:
	"""Only the child meta's own value fields survive; child rows likewise, and a
	row keeps its `name` only when that row already belongs to THIS parent (a
	no-form grid stamps random names on new rows — grid.js:171 — which would
	otherwise be mistaken for existing rows and updated into nothing)."""
	meta = frappe.get_meta(doctype)
	clean: dict = {}
	for df in meta.fields:
		if df.fieldtype in _LAYOUT_TYPES or df.fieldname in _RESERVED:
			continue
		if df.fieldname not in payload:
			continue
		value = payload.get(df.fieldname)
		if df.fieldtype == "Table":
			child_meta = frappe.get_meta(df.options)
			allowed = {
				f.fieldname
				for f in child_meta.fields
				if f.fieldtype not in _LAYOUT_TYPES and f.fieldname not in _RESERVED
			}
			rows = []
			for raw in value or []:
				if not isinstance(raw, dict):
					continue
				row = {k: v for k, v in raw.items() if k in allowed}
				row_name = raw.get("name")
				if (
					existing_name
					and row_name
					and frappe.db.exists(
						df.options,
						{"name": row_name, "parent": existing_name, "parentfield": df.fieldname},
					)
				):
					row["name"] = row_name
				rows.append(row)
			clean[df.fieldname] = rows
		else:
			clean[df.fieldname] = value
	return clean


def _resolve_company(doctype: str, payload: dict, existing_name: str | None) -> str:
	"""The tenant this save belongs to — from the DATABASE, never the payload,
	except for a brand-new branch whose company IS the payload's claim (and is
	then gated like any other claim)."""
	if existing_name:
		company = frappe.db.get_value(doctype, existing_name, "company")
		if not company:
			frappe.throw(_("{0} {1} has no company.").format(doctype, existing_name))
		return company
	if doctype == "CBT Court":
		return _branch_company(payload.get("branch"))
	company = payload.get("company")
	if not company:
		frappe.throw(_("Company is required."), frappe.ValidationError)
	if not frappe.db.exists("CBT Company", company):
		frappe.throw(_("Company {0} not found.").format(company), frappe.DoesNotExistError)
	return company


@frappe.whitelist(methods=["POST"])
def save_facility(doc) -> dict:
	"""Insert or update one CBT Branch / CBT Court from the in-place editor.

	Order matters: whitelist the doctype, resolve the tenant from the database,
	run the tenant gate (and the branch-management gate for a branch) on that
	value, strip the payload to the meta's own fields, force the resolved
	company onto it, THEN let frappe save — whose permission hooks now see the
	truth rather than the client's claim.
	"""
	payload = json.loads(doc) if isinstance(doc, str) else dict(doc or {})
	doctype = payload.get("doctype")
	_check_doctype(doctype)
	name = payload.get("name")
	existing_name = name if (name and frappe.db.exists(doctype, name)) else None

	company = _resolve_company(doctype, payload, existing_name)
	require_company_access(company, allow_suspended=True)
	if doctype == "CBT Branch":
		ensure_branch_management_allowed(company)

	clean = _clean_payload(doctype, payload, existing_name)
	clean["company"] = company
	if existing_name:
		# The parent link is immutable (S3 as-built 2) and the editor renders it
		# read-only — the stored value is the only one that counts.
		clean.pop(PARENT_FIELD[doctype], None)
		if not payload.get("modified"):
			frappe.throw(
				_("Reload {0} before saving it — the edit carries no timestamp.").format(
					existing_name
				),
				frappe.ValidationError,
			)
		stored = frappe.get_doc(doctype, existing_name)
		base = stored.as_dict()
		base.update(clean)
		# `modified` from the client is what check_if_latest compares with the
		# row — the same contract as the desk form's own save.
		base["modified"] = payload["modified"]
		target = frappe.get_doc(base)
		target.save()
	else:
		clean.pop("name", None)
		target = frappe.get_doc({"doctype": doctype, **clean})
		target.insert()
	return target.as_dict()


# The Media tab. Photos do NOT ride the parent's own save — a CBT Branch
# refuses every write when self branch management is off (section-29 as-built).


def _media_company(doctype: str, name: str) -> str:
	"""The tenant a Media tab belongs to, resolved from the DB, never the client."""
	if doctype not in MEDIA_DOCTYPES:
		frappe.throw(_("{0} does not have photos.").format(doctype or ""), frappe.ValidationError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found.").format(doctype, name), frappe.DoesNotExistError)
	return name if doctype == "CBT Company" else _branch_company(name)


def _photo_rows(doctype: str, name: str) -> list[dict]:
	return frappe.get_all(
		"CBT Media Item",
		filters={"parent": name, "parenttype": doctype, "parentfield": "photos"},
		fields=["image", "caption", "idx"],
		order_by="idx asc",
		parent_doctype=doctype,
		limit=MEDIA_MAX,
	)


def facility_gallery(doctype: str, name: str, company: str | None = None) -> list[dict]:
	"""A facility's photographs as ONE ordered list, cover first (section-29).

	A company's banner IS element 0. A branch shows its own, else the company's.
	"""
	if doctype == "CBT Company":
		# The banner leads, then the Media photos. Both, never one or the other.
		banner = frappe.db.get_value("CBT Company", name, "banner")
		out = [{"image": banner, "caption": None}] if banner else []
		out += [
			{"image": r.image, "caption": r.caption}
			for r in _photo_rows(doctype, name)
			if r.image != banner
		]
		return out

	own = _photo_rows("CBT Branch", name)
	if own:
		return [{"image": r.image, "caption": r.caption} for r in own]
	return facility_gallery("CBT Company", company or _branch_company(name))


@frappe.whitelist(methods=["GET"])
def list_media(doctype: str, name: str) -> dict:
	"""One Media tab, plus what it needs to say WHICH set the customer is shown."""
	company = _media_company(doctype, name)
	require_company_access(company, allow_suspended=True)

	if doctype == "CBT Company":
		# The tab owns PHOTOS only. The banner lives on Details and is never a tile.
		rows = [{"image": r.image, "caption": r.caption} for r in _photo_rows(doctype, name)]
		inherited, company_count = False, len(facility_gallery("CBT Company", name))
	else:
		own = _photo_rows("CBT Branch", name)
		rows = [{"image": r.image, "caption": r.caption} for r in own]
		inherited = not rows
		company_count = len(facility_gallery("CBT Company", company))

	return {
		"rows": rows,
		"can_manage": bool(media_editing_allowed(company)),
		"is_branch": doctype == "CBT Branch",
		"inherited_from_company": inherited,
		"company_photo_count": company_count,
		"max_photos": MEDIA_MAX,
	}


DEFAULT_MEDIA_MAX_MB = 10
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@frappe.whitelist(methods=["POST"])
def upload_media_photo(doctype: str, name: str) -> dict:
	"""One photo into a facility's gallery, PUBLIC by construction (section-29).

	frappe's own upload_file needs WRITE on the parent — a 403 for a gated seat.
	"""
	company = _media_company(doctype, name)
	require_company_access(company, allow_suspended=True)
	ensure_media_editing_allowed(company)

	upload = (frappe.request.files or {}).get("file")
	if not upload:
		frappe.throw(_("No photo was sent."), frappe.ValidationError)
	if (upload.content_type or "").split(";")[0].strip().lower() not in ALLOWED_IMAGE_TYPES:
		frappe.throw(_("Photos must be JPEG, PNG, WebP or GIF."), frappe.ValidationError)

	content = upload.stream.read()
	max_mb = (
		cint(frappe.db.get_single_value("CBT Platform Settings", "media_max_mb"))
		or DEFAULT_MEDIA_MAX_MB
	)
	if len(content) > max_mb * 1024 * 1024:
		frappe.throw(_("That photo is too large — the limit is {0} MB.").format(max_mb))

	# is_private 0 by CONSTRUCTION, never flipped: a private file 403s every
	# customer while the src attribute reads perfectly to the admin who uploaded it.
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": upload.filename,
			"content": content,
			"is_private": 0,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
		}
	).insert(ignore_permissions=True)
	return {"file_url": doc.file_url}


@frappe.whitelist(methods=["POST"])
def save_media(doctype: str, name: str, rows) -> dict:
	"""Replace this facility's photos with `rows`, in order. idx 1 is the cover,
	so reordering and adding are the same operation and there is no per-photo save."""
	company = _media_company(doctype, name)
	require_company_access(company, allow_suspended=True)
	ensure_media_editing_allowed(company)

	if isinstance(rows, str):
		rows = json.loads(rows)
	if not isinstance(rows, list):
		frappe.throw(_("Photos must be a list."), frappe.ValidationError)

	clean = []
	for row in rows:
		image = (row or {}).get("image")
		if not image:
			continue  # a row with no file must never render a broken <img>
		clean.append({"image": image, "caption": (row.get("caption") or "").strip() or None})
	if len(clean) > MEDIA_MAX:
		frappe.throw(
			_("A facility can hold {0} photos.").format(MEDIA_MAX), frappe.ValidationError
		)

	# Child rows written DIRECTLY: saving the parent would run its whole
	# validate(), and dropping a JPEG must not fail on a stale floor-plan cell.
	frappe.db.delete(
		"CBT Media Item", {"parent": name, "parenttype": doctype, "parentfield": "photos"}
	)
	for idx, entry in enumerate(clean, start=1):
		frappe.get_doc(
			{
				"doctype": "CBT Media Item",
				"parent": name,
				"parenttype": doctype,
				"parentfield": "photos",
				"idx": idx,
				**entry,
			}
		).insert(ignore_permissions=True)
	# The whole tab payload — the resolution sentence changes with the save.
	return list_media(doctype, name)
