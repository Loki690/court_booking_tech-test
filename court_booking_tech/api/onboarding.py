# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Platform onboarding checklist (section-11, PLAN §7 "Platform desk").

The go-live steps for a new tenant, DERIVED from the data rather than tracked
as stored flags: a checkbox someone ticked says the branch was pinned once, the
query says it is pinned now. Same doctrine as membership's live active-window
and the derived worklists rule — nothing here can rot.

MVP is a guided checklist with links, not a wizard engine (section-11): the
platform admin does the steps in the normal desk forms, and this tab answers
"what is still missing before I hand this client their link?".
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from court_booking_tech import payment_channels
from court_booking_tech.tenancy import has_platform_scope

# Ordered: each step is a prerequisite of the client actually being able to
# take a booking, ending with the deep link the platform hands over.
STEP_KEYS = (
	"admin_user",
	"branch_pinned",
	"court_priced",
	"office_hours",
	"payment_instructions",
	"billing_identity",
	"deep_link",
)


@frappe.whitelist(methods=["GET"])
def get_onboarding_checklist(company: str) -> dict:
	"""Live go-live status for one tenant. Platform-only.

	Not routed through require_company_access: this is the PLATFORM's view of a
	tenant it is standing up (a company admin has no business seeing their own
	onboarding scorecard), so the gate is the platform-scope check.
	"""
	if not has_platform_scope():
		frappe.throw(
			_("The onboarding checklist is a platform-admin view."),
			frappe.PermissionError,
		)
	if not frappe.db.exists("CBT Company", company):
		frappe.throw(_("Company {0} not found.").format(company), frappe.DoesNotExistError)

	doc = frappe.get_doc("CBT Company", company)

	has_admin = bool(
		frappe.db.exists(
			"CBT Company User", {"company": company, "company_role": "Company Admin"}
		)
	)

	branches = frappe.get_all(
		"CBT Branch",
		filters={"company": company, "is_active": 1},
		fields=["name", "slug", "branch_name", "latitude", "longitude"],
	)
	pinned = [b for b in branches if b.latitude and b.longitude]

	priced_courts = frappe.db.count(
		"CBT Court", {"company": company, "is_active": 1, "hourly_rate": (">", 0)}
	)

	open_office_rows = [
		row
		for row in (doc.office_hours or [])
		if cint(row.is_open) and row.opening_time is not None and row.closing_time is not None
	]

	steps = [
		{
			"key": "admin_user",
			"label": _("Company admin user"),
			"hint": _("Someone at the client can log in and manage the facility."),
			"done": has_admin,
			"route": "/app/cbt-company-user/new",
		},
		{
			"key": "branch_pinned",
			"label": _("Branch with a map pin"),
			"hint": _("Customers find branches nearest-first — an unpinned branch sorts last."),
			"done": bool(pinned),
			"route": "/app/cbt-branch/new",
		},
		{
			"key": "court_priced",
			"label": _("Court with an hourly rate"),
			"hint": _("Nothing is bookable until at least one active court has a rate."),
			"done": priced_courts > 0,
			"route": "/app/cbt-court/new",
		},
		{
			"key": "office_hours",
			"label": _("Office hours (payment verification)"),
			"hint": _("Sets how long staff have to verify a transfer proof."),
			"done": bool(open_office_rows),
			"route": f"/app/cbt-company/{company}",
		},
		{
			# Backlog B31, closed 2026-09-04 on the user's ruling. The key is
			# unchanged so nothing keyed on it moves; what it MEASURES changed.
			# Until B29 the company's free-text `payment_instructions` really was
			# where a customer was told how to pay. It is not any more — the
			# transfer CHANNELS are (GCash, the bank), each carrying its own
			# account name, number, QR and note. Checking the old text box made
			# this step lie in both directions: a tenant with working channels
			# was held back from "Ready to go live" for leaving an obsolete field
			# empty, and a tenant who typed "pay us" into it went green with no
			# payable channel at all.
			"key": "payment_instructions",
			"label": _("Payment channel"),
			"hint": _("Where the customer sends the transfer — GCash, a bank, or both."),
			"done": bool(
				payment_channels.list_channels(
					company, kind=payment_channels.TRANSFER, fields=("name",)
				)
			),
			"route": f"/app/cbt-payment-channel?company={company}",
		},
		{
			"key": "billing_identity",
			"label": _("Billing identity (registered name + TIN)"),
			"hint": _("Printed on the billing statement, in the company's own name."),
			"done": bool((doc.registered_name or "").strip() and (doc.tin or "").strip()),
			"route": f"/app/cbt-company/{company}",
		},
	]

	# The hand-over artefact. "Done" once there is something to link TO —
	# a deep link to a company with no bookable branch is a broken promise.
	deep_links = [
		{
			"label": _("All branches"),
			"url": f"/book?c={doc.name}",
		}
	] + [
		{
			"label": b.branch_name or b.slug,
			"url": f"/book?c={doc.name}&b={b.slug}",
		}
		for b in branches
	]
	steps.append(
		{
			"key": "deep_link",
			"label": _("Deep link ready to hand over"),
			"hint": _("The client pastes this on their own website."),
			"done": bool(branches) and priced_courts > 0,
			"route": f"/book?c={doc.name}",
			"links": deep_links,
		}
	)

	done_count = sum(1 for step in steps if step["done"])
	return {
		"company": doc.name,
		"company_name": doc.company_name,
		"status": doc.status,
		"billing_mode": doc.billing_mode,
		"commission_percent": flt(doc.commission_percent),
		"subscription_fee": flt(doc.subscription_fee),
		"steps": steps,
		"done_count": done_count,
		"total_count": len(steps),
		"ready": done_count == len(steps),
	}
