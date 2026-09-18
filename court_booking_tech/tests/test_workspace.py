"""
Court Booking Tech — CBT Hub workspace contract (section-12, extended by 23)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_workspace

Section-12 UAT finding: the CBT Hub renders ONLY shortcut blocks — its `links`
child rows never appear anywhere on the page — and two of the three reports had
no shortcut, making them unreachable from the Hub for every persona. The E2E
could not see this because file 01 drives the Hub as Administrator and
`is_item_allowed` short-circuits True for Administrator, so no role filtering
was ever exercised.

POLICY (deliberate): every report shipped in this app MUST be reachable from
the CBT Hub as a shortcut. If a future report is intentionally off-Hub, amend
EXPECTED_OFF_HUB below with a comment saying why — do not delete these tests.

BACKLOG B25/B26 (2026-08-27) widened the same policy from reports to DOCTYPES,
because the report-shaped guard let `CBT Company Media` ship with no Hub entry
and no Connection anywhere — a new doctype was invisible to every reachability
test in this file. Two contracts were added:

  * every doctype with a FORM (istable=0, Singles included) has a Hub shortcut —
    `EXPECTED_OFF_HUB_DOCTYPES` is the exclusion set, EMPTY by decision (the
    three doctypes it would have needed on day one — Company Media, Payment
    Proof, Customer Profile — got shortcuts instead);
  * every INBOUND Link between two parent doctypes is declared as a Connection
    (`links` on the target's JSON) or excluded WITH A REASON —
    `EXPECTED_CONNECTIONS` is the whole map, and the completeness test derives
    the inbound set from the site's own DocField AND Custom Field rows, so a new
    Link field with no Connection goes red here rather than shipping as another
    B26. Two things are outside that contract on purpose: child-table Links (a
    child row has no list view to open, and its outbound link is already on
    the parent form as the table itself) and Dynamic Links (a `links` row
    names one static target doctype; a Dynamic Link has none).

SECTION-23 adds the number-card half. Two things about Number Cards make an
existence check worthless as a guard:

  * `NumberCard.validate()` checks almost nothing this app cares about — not
    `label`, not `module`, not `show_percentage_stats` — so a card can install
    perfectly and still render wrong. These tests assert VALUES, never
    `frappe.db.exists`.
    (Validation DOES run on fixture install, contrary to what one might assume
    from `import_file_by_path`: it sets ignore_validate only when
    `data_import` is false, and `utils/fixtures.py` passes `data_import=True`.
    So a card that fails validate ABORTS migrate rather than installing
    quietly — a better failure mode than the silent one, and worth knowing
    before someone "hardens" this.)
  * a content block and its Workspace child row are matched on the child row's
    LABEL (frappe/.../workspace/blocks/block.js `make()`); when they disagree,
    `render()` returns an empty wrapper and the card SILENTLY vanishes. There
    is no error anywhere. That pairing gets its own test.
"""

import json
from pathlib import Path

import frappe
from frappe.desk.doctype.number_card.number_card import get_result
from frappe.tests.utils import FrappeTestCase
from court_booking_tech.seeds.seed_test_data import PLATFORM_ADMIN_EMAIL

# Reports deliberately kept off the CBT Hub (none today). Add a name here WITH
# a reason comment if that ever becomes a real decision.
EXPECTED_OFF_HUB: set[str] = set()

# Doctypes with a form that are deliberately NOT on the Hub (none today —
# Backlog B25 chose shortcuts over an exclusion list). Same rule as above: a
# name goes here WITH the reason, never silently.
EXPECTED_OFF_HUB_DOCTYPES: set[str] = set()

WORKSPACE = "CBT Hub"
MODULE = "Court Booking Tech"

# Backlog B26 — the desk's Connections, target doctype -> its `links` rows, in
# JSON order. Every row is an INBOUND Link: `link_doctype.link_fieldname` is a
# Link field whose options is the key. Where a Hub section fits, the group is
# named after it (Facilities / Bookings / Open Play / Members / Tenants) so the
# form dashboard and the Hub tell one story; a booking's own groups, Billing
# and Extensions, have no Hub counterpart.
EXPECTED_CONNECTIONS: dict[str, list[dict]] = {
	"CBT Company": [
		{"group": "Facilities", "link_doctype": "CBT Branch", "link_fieldname": "company"},
		{"group": "Facilities", "link_doctype": "CBT Court", "link_fieldname": "company"},
		{"group": "Bookings", "link_doctype": "CBT Court Booking", "link_fieldname": "company"},
		{"group": "Bookings", "link_doctype": "CBT Slot Block", "link_fieldname": "company"},
		{"group": "Bookings", "link_doctype": "CBT Booking Invoice", "link_fieldname": "company"},
		{"group": "Bookings", "link_doctype": "CBT Payment Proof", "link_fieldname": "company"},
		{"group": "Open Play", "link_doctype": "CBT Open Play Session", "link_fieldname": "company"},
		{"group": "Members", "link_doctype": "CBT Membership", "link_fieldname": "company"},
		{"group": "Members", "link_doctype": "CBT Customer Ban", "link_fieldname": "company"},
		{"group": "Members", "link_doctype": "CBT Customer Credit", "link_fieldname": "company"},
		{"group": "Tenants", "link_doctype": "CBT Company User", "link_fieldname": "company"},
		{"group": "Tenants", "link_doctype": "CBT Payment Channel", "link_fieldname": "company"},
		{"group": "Platform", "link_doctype": "CBT Platform Statement", "link_fieldname": "company"},
	],
	# Backlog B29: every payment row names the channel it went through, so the
	# channel form answers "what was paid through this?" in one click.
	"CBT Payment Channel": [
		{"group": "Payments", "link_doctype": "CBT Court Booking", "link_fieldname": "payment_channel"},
		{"group": "Payments", "link_doctype": "CBT Booking Invoice", "link_fieldname": "payment_channel"},
		{"group": "Payments", "link_doctype": "CBT Payment Proof", "link_fieldname": "payment_channel"},
		{"group": "Platform", "link_doctype": "CBT Platform Statement", "link_fieldname": "payment_channel"},
	],
	"CBT Branch": [
		{"group": "Facilities", "link_doctype": "CBT Court", "link_fieldname": "branch"},
		{"group": "Bookings", "link_doctype": "CBT Court Booking", "link_fieldname": "branch"},
		{"group": "Bookings", "link_doctype": "CBT Slot Block", "link_fieldname": "branch"},
		{"group": "Bookings", "link_doctype": "CBT Booking Invoice", "link_fieldname": "branch"},
		{"group": "Open Play", "link_doctype": "CBT Open Play Session", "link_fieldname": "branch"},
	],
	"CBT Court": [
		{"group": "Bookings", "link_doctype": "CBT Court Booking", "link_fieldname": "court"},
		{"group": "Bookings", "link_doctype": "CBT Slot Block", "link_fieldname": "court"},
	],
	"CBT Court Booking": [
		{"group": "Billing", "link_doctype": "CBT Booking Invoice", "link_fieldname": "booking"},
		{"group": "Billing", "link_doctype": "CBT Payment Proof", "link_fieldname": "booking"},
		# Backlog B39: the credit this booking's refund minted.
		{"group": "Billing", "link_doctype": "CBT Customer Credit", "link_fieldname": "source_booking"},
		{"group": "Extensions", "link_doctype": "CBT Court Booking", "link_fieldname": "extended_from"},
	],
	# Backlog B39. `source_invoice` is the document the credit came OUT of;
	# `credit_document` on the booking is the one it was spent ON, so the credit
	# form answers both halves of "where did this money go?".
	"CBT Booking Invoice": [
		{"group": "Billing", "link_doctype": "CBT Customer Credit", "link_fieldname": "source_invoice"},
	],
	"CBT Customer Credit": [
		{"group": "Bookings", "link_doctype": "CBT Court Booking", "link_fieldname": "credit_document"},
	],
	"CBT Open Play Session": [
		{"group": "Open Play", "link_doctype": "CBT Payment Proof", "link_fieldname": "open_play_session"},
		{"group": "Open Play", "link_doctype": "CBT Slot Block", "link_fieldname": "open_play_session"},
	],
}

# Inbound Links that are deliberately NOT Connections — (target, source, field)
# -> why. Each must still be a real inbound Link (the completeness test checks
# that too, so a stale entry goes red instead of hiding a regression).
#
# The reschedule pair: frappe keys `non_standard_fieldnames` by LINKED DOCTYPE,
# last row wins (measured 2026-08-27: three self-links on CBT Court Booking gave
# `{"CBT Court Booking": "rescheduled_to"}` and one badge that silently ignored
# the other two fields). One self-link per doctype is therefore the ceiling, and
# `extended_from` is the one that carries information a form does not already
# show: the ORIGINAL booking has no field pointing at its extensions, whereas a
# rescheduled booking shows `rescheduled_from` and its original shows
# `rescheduled_to` as plain fields — both directions already on the form.
#
# The same rule, applied consistently (ducky, 2026-08-27): an invoice is issued
# per booking (a reschedule re-issues — walkthrough FD-05b), so the invoice
# form's own `booking` field IS the reverse of `billing_doc`, and a badge that
# always reads "1" and opens the record already named on the form adds nothing.
CONNECTIONS_NOT_DECLARED: dict[tuple, str] = {
	("CBT Court Booking", "CBT Court Booking", "rescheduled_from"):
		"reverse direction is the `rescheduled_to` field on the original booking",
	("CBT Court Booking", "CBT Court Booking", "rescheduled_to"):
		"reverse direction is the `rescheduled_from` field on the replacement",
	("CBT Booking Invoice", "CBT Court Booking", "billing_doc"):
		"one invoice per booking — reverse direction is the invoice's `booking` field",
	("CBT Court Booking", "CBT Court Booking", "fee_chained_to"):
		"self-link ceiling is ONE per doctype and `extended_from` holds it; "
		"declaring this would silently drop the Extensions badge",
}

# The number-card row the Hub opens with (section-23, Backlog B14).
# name -> (label, document_type, function)
EXPECTED_CARDS = {
	"CBT Active Bookings": ("Active Bookings", "CBT Court Booking", "Count"),
	"CBT Pending Proofs": ("Pending Proofs", "CBT Payment Proof", "Count"),
	"CBT Memberships": ("Memberships", "CBT Membership", "Count"),
}

ACTIVE_STATUSES = ["Reserved", "Confirmed", "Extended"]

# Two seeded tenants and one admin each — the pair that makes a scoping
# assertion mean something (test_isolation.py owns the same constants).
AYALA = "ayala-courts"
ALONA = "admin.ayala@example.com"


def _app_report_names() -> set[str]:
	"""Every report the app ships, read from the report/ folder JSONs."""
	report_dir = Path(frappe.get_app_path("court_booking_tech")) / (
		"court_booking_tech/report"
	)
	names = set()
	for json_file in report_dir.glob("*/*.json"):
		data = json.loads(json_file.read_text(encoding="utf-8"))
		if data.get("doctype") == "Report":
			names.add(data["report_name"])
	return names


def _workspace_json() -> dict:
	path = Path(frappe.get_app_path("court_booking_tech")) / (
		"court_booking_tech/workspace/cbt_hub/cbt_hub.json"
	)
	return json.loads(path.read_text(encoding="utf-8"))


def _app_parent_doctypes() -> set[str]:
	"""Every doctype of this app that has a FORM, from the SITE (tabDocType),
	not the folder — a doctype that exists on disk but never migrated is a
	reachability failure too, and a folder glob would not see it that way."""
	return {
		row.name
		for row in frappe.get_all(
			"DocType", filters={"module": MODULE, "istable": 0}, fields=["name"]
		)
	}


def _doctype_json(doctype: str) -> dict:
	scrub = frappe.scrub(doctype)
	path = Path(frappe.get_app_path("court_booking_tech")) / (
		f"court_booking_tech/doctype/{scrub}/{scrub}.json"
	)
	return json.loads(path.read_text(encoding="utf-8"))


def _link_rows(rows) -> list[tuple]:
	"""(group, link_doctype, link_fieldname) triples, order preserved, from
	either JSON dicts or DocType Link child docs."""
	out = []
	for row in rows:
		get = row.get if isinstance(row, dict) else (lambda k, r=row: getattr(r, k, None))
		out.append((get("group"), get("link_doctype"), get("link_fieldname")))
	return out


class TestCBTHubWorkspaceContract(FrappeTestCase):
	def test_app_ships_reports(self):
		"""Guard the guard: an empty report set would green the contract."""
		self.assertGreaterEqual(len(_app_report_names()), 3)

	def test_every_report_has_a_hub_shortcut_row(self):
		ws = _workspace_json()
		shortcut_links = {
			s["link_to"] for s in ws.get("shortcuts", []) if s.get("type") == "Report"
		}
		missing = _app_report_names() - shortcut_links - EXPECTED_OFF_HUB
		self.assertFalse(
			missing,
			f"reports with no CBT Hub shortcut row: {sorted(missing)} — the Hub "
			"renders shortcuts only (links rows never display), so these are "
			"unreachable from the Hub",
		)

	def test_every_report_shortcut_is_in_the_rendered_content(self):
		"""A shortcuts row without a content block still renders NOTHING."""
		ws = _workspace_json()
		content_shortcuts = {
			b["data"]["shortcut_name"]
			for b in json.loads(ws["content"])
			if b.get("type") == "shortcut"
		}
		missing = _app_report_names() - content_shortcuts - EXPECTED_OFF_HUB
		self.assertFalse(
			missing,
			f"reports missing from the Hub CONTENT blocks: {sorted(missing)}",
		)

	def test_migrated_workspace_carries_the_report_shortcuts(self):
		"""The DB half: what the site actually serves after migrate."""
		doc = frappe.get_doc("Workspace", WORKSPACE)
		db_shortcuts = {s.link_to for s in doc.shortcuts if s.type == "Report"}
		missing = _app_report_names() - db_shortcuts - EXPECTED_OFF_HUB
		self.assertFalse(
			missing,
			f"migrated Workspace '{WORKSPACE}' lacks report shortcuts: "
			f"{sorted(missing)} — did migrate run after editing cbt_hub.json?",
		)

	def test_content_and_links_sections_agree(self):
		"""The page's section order and the sidebar card list must match.

		They did not before section-23 (content read Bookings / Open Play /
		Members / Reports / Tenants / Facilities, `links` read Bookings /
		Open Play / Tenants / Facilities / Members / Reports), which is a
		silent inconsistency: nothing errors, the two lists simply tell staff
		different stories about where things are.
		"""
		ws = _workspace_json()
		content_headers = [
			b["data"]["text"]
			for b in json.loads(ws["content"])
			if b.get("type") == "header"
		]
		# Strip the <span class="h4"><b>…</b></span> wrapper the editor stores.
		content_sections = [
			text.split("<b>")[-1].split("</b>")[0] for text in content_headers
		]
		card_breaks = [
			row["label"] for row in ws["links"] if row.get("type") == "Card Break"
		]
		# "At a glance" heads the number-card row and owns no links rows.
		self.assertEqual(content_sections[0], "At a glance", content_sections)
		self.assertEqual(content_sections[1:], card_breaks)


class TestCBTHubDoctypeReachability(FrappeTestCase):
	"""Backlog B25 — the report contract above, applied to every doctype with a
	form. Same three-place shape (shortcuts row, content block, migrated row)
	because a miss in any one of them renders nothing and says nothing."""

	def test_app_ships_parent_doctypes(self):
		"""Guard the guard: an empty enumerator would green the whole contract."""
		parents = _app_parent_doctypes()
		self.assertGreaterEqual(len(parents), 10, parents)
		self.assertIn("CBT Payment Channel", parents)

	def test_every_parent_doctype_has_a_hub_shortcut_row(self):
		ws = _workspace_json()
		shortcut_links = {
			s["link_to"] for s in ws.get("shortcuts", []) if s.get("type") == "DocType"
		}
		missing = _app_parent_doctypes() - shortcut_links - EXPECTED_OFF_HUB_DOCTYPES
		self.assertFalse(
			missing,
			f"doctypes with no CBT Hub shortcut row: {sorted(missing)} — the Hub "
			"renders shortcuts only, so these are unreachable from it (B25)",
		)

	def test_every_doctype_shortcut_is_in_the_rendered_content(self):
		ws = _workspace_json()
		content_shortcuts = {
			b["data"]["shortcut_name"]
			for b in json.loads(ws["content"])
			if b.get("type") == "shortcut"
		}
		missing = _app_parent_doctypes() - content_shortcuts - EXPECTED_OFF_HUB_DOCTYPES
		self.assertFalse(
			missing, f"doctypes missing from the Hub CONTENT blocks: {sorted(missing)}"
		)

	def test_migrated_workspace_carries_the_doctype_shortcuts(self):
		doc = frappe.get_doc("Workspace", WORKSPACE)
		db_shortcuts = {s.link_to for s in doc.shortcuts if s.type == "DocType"}
		missing = _app_parent_doctypes() - db_shortcuts - EXPECTED_OFF_HUB_DOCTYPES
		self.assertFalse(
			missing,
			f"migrated Workspace '{WORKSPACE}' lacks doctype shortcuts: "
			f"{sorted(missing)} — did migrate run after editing cbt_hub.json?",
		)


class TestConnections(FrappeTestCase):
	"""Backlog B26 (and B25's Company -> Company Media half): the desk's
	Connections, pinned three ways — the repo JSON, the migrated meta, and the
	Link field each row claims to hang off. Plus the contract's own guard: the
	set of inbound Links is DERIVED from the site, so the map cannot quietly
	fall behind the schema."""

	def test_repo_json_declares_the_connections(self):
		for doctype, rows in EXPECTED_CONNECTIONS.items():
			with self.subTest(doctype=doctype):
				self.assertEqual(
					_link_rows(_doctype_json(doctype).get("links", [])),
					_link_rows(rows),
					f"{doctype}.json `links` disagrees with EXPECTED_CONNECTIONS",
				)

	def test_migrated_meta_carries_the_connections(self):
		for doctype, rows in EXPECTED_CONNECTIONS.items():
			with self.subTest(doctype=doctype):
				self.assertEqual(
					_link_rows(frappe.get_meta(doctype).links),
					_link_rows(rows),
					f"site meta for {doctype} lacks the Connections — did migrate run?",
				)

	def test_every_connection_names_a_real_inbound_link_field(self):
		"""A typo here renders a badge whose click filters on nothing."""
		for doctype, rows in EXPECTED_CONNECTIONS.items():
			for row in rows:
				with self.subTest(doctype=doctype, row=row):
					field = frappe.get_meta(row["link_doctype"]).get_field(
						row["link_fieldname"]
					)
					self.assertIsNotNone(field, row)
					self.assertEqual(field.fieldtype, "Link", row)
					self.assertEqual(field.options, doctype, row)

	def test_every_inbound_link_between_parent_doctypes_is_a_connection(self):
		"""The completeness half, with NO exclusion set: every Link field on a
		parent doctype that points at a parent doctype of this app must be a
		Connection on its target. Child-table Links are excluded by construction
		(the source filter is istable=0), per the module docstring."""
		parents = _app_parent_doctypes()
		inbound = frappe.get_all(
			"DocField",
			filters={
				"fieldtype": "Link",
				"parent": ("in", sorted(parents)),
				"options": ("in", sorted(parents)),
			},
			fields=["parent", "fieldname", "options"],
		)
		# A Link bolted on later through Customize Form / a fixture lives in
		# tabCustom Field, not tabDocField — invisible to the query above, and
		# exactly the path a later session is most likely to take.
		inbound += frappe.get_all(
			"Custom Field",
			filters={
				"fieldtype": "Link",
				"dt": ("in", sorted(parents)),
				"options": ("in", sorted(parents)),
			},
			fields=["dt as parent", "fieldname", "options"],
		)
		self.assertGreaterEqual(len(inbound), 20, "inbound Link set looks empty")
		declared = {
			(target, row["link_doctype"], row["link_fieldname"])
			for target, rows in EXPECTED_CONNECTIONS.items()
			for row in rows
		}
		present = {(f.options, f.parent, f.fieldname) for f in inbound}
		stale = set(CONNECTIONS_NOT_DECLARED) - present
		self.assertFalse(stale, f"exclusions that are no longer inbound Links: {stale}")
		missing = sorted(present - declared - set(CONNECTIONS_NOT_DECLARED))
		self.assertFalse(
			missing,
			f"inbound Links with no Connection on their target: {missing}",
		)

	def test_one_connection_per_linked_doctype(self):
		"""The frappe trap behind CONNECTIONS_NOT_DECLARED, pinned: a second row
		for the same `link_doctype` on one target does not add a badge — it
		silently re-points the existing one at the last row's field."""
		for doctype, rows in EXPECTED_CONNECTIONS.items():
			linked = [row["link_doctype"] for row in rows]
			self.assertEqual(
				len(linked), len(set(linked)),
				f"{doctype} declares the same link_doctype twice: {linked}",
			)


class TestCBTHubNumberCards(FrappeTestCase):
	"""Section-23 (Backlog B14) — the Hub's at-a-glance row."""

	def _fixture_rows(self) -> dict:
		path = Path(frappe.get_app_path("court_booking_tech")) / (
			"fixtures/number_card.json"
		)
		rows = json.loads(path.read_text(encoding="utf-8"))
		return {row["name"]: row for row in rows}

	def test_fixture_file_declares_the_three_cards(self):
		rows = self._fixture_rows()
		self.assertEqual(set(rows), set(EXPECTED_CARDS))
		for name, (label, document_type, function) in EXPECTED_CARDS.items():
			row = rows[name]
			self.assertEqual(row["label"], label, name)
			self.assertEqual(row["type"], "Document Type", name)
			self.assertEqual(row["document_type"], document_type, name)
			self.assertEqual(row["function"], function, name)
			# Not cosmetic. A named `module` is ANDed into Number Card's own
			# list-permission hook, which would hide these from a CBT Company
			# Admin who is not a System Manager — precisely the audience.
			self.assertEqual(row["module"], "", name)
			self.assertEqual(row["is_standard"], 0, name)
			# The %-delta compares against `creation < previous_date`, which is
			# meaningless for a status-filtered count.
			self.assertEqual(row["show_percentage_stats"], 0, name)
			# Must be valid JSON: filters_json is stored as a STRING and a
			# malformed one fails at render, never at install.
			json.loads(row["filters_json"])

	def test_migrated_cards_carry_the_fixture_values(self):
		"""`import_doc` sets ignore_validate, so existence proves nothing."""
		for name, (label, document_type, function) in EXPECTED_CARDS.items():
			doc = frappe.get_doc("Number Card", name)
			self.assertEqual(doc.label, label, name)
			self.assertEqual(doc.type, "Document Type", name)
			self.assertEqual(doc.document_type, document_type, name)
			self.assertEqual(doc.function, function, name)
			self.assertFalse(doc.module, name)
			self.assertFalse(doc.show_percentage_stats, name)
			self.assertTrue(doc.is_public, name)

	def test_every_content_block_is_paired_with_a_child_row(self):
		"""The silent-vanish trap, pinned.

		block.js matches a content block's `number_card_name` against the
		Workspace child row's LABEL — not against the Number Card doc name. A
		mismatch renders an empty wrapper with no error anywhere.
		"""
		ws = _workspace_json()
		blocks = [
			b["data"]["number_card_name"]
			for b in json.loads(ws["content"])
			if b.get("type") == "number_card"
		]
		child_by_label = {r["label"]: r["number_card_name"] for r in ws["number_cards"]}
		self.assertTrue(blocks, "the Hub content has no number_card blocks")
		for block_name in blocks:
			self.assertIn(
				block_name,
				child_by_label,
				f"content block '{block_name}' has no matching number_cards row "
				"label — the card renders NOTHING and says nothing",
			)
			self.assertIn(child_by_label[block_name], EXPECTED_CARDS)

	def test_migrated_workspace_carries_the_number_card_rows(self):
		doc = frappe.get_doc("Workspace", WORKSPACE)
		rows = {r.label: r.number_card_name for r in doc.number_cards}
		self.assertEqual(
			rows,
			{label: name for name, (label, _dt, _fn) in EXPECTED_CARDS.items()},
			"did migrate run after editing cbt_hub.json?",
		)

	def test_counts_are_tenant_scoped(self):
		"""A Company Admin's card must count THEIR company only.

		Number Card's get_result goes through `frappe.get_list`, which applies
		this app's permission_query_conditions hooks — so scoping is inherited
		rather than implemented. That is exactly why it needs a test: nothing in
		this app would fail if the hook were dropped from hooks.py.
		"""
		self.addCleanup(frappe.set_user, "Administrator")

		card = frappe.get_doc("Number Card", "CBT Active Bookings").as_dict()
		filters = json.loads(card["filters_json"])

		frappe.set_user(PLATFORM_ADMIN_EMAIL)
		everyone = int(get_result(json.dumps(card, default=str), filters))
		ayala = frappe.db.count(
			"CBT Court Booking",
			{"company": AYALA, "booking_status": ("in", ACTIVE_STATUSES)},
		)
		others = frappe.db.count(
			"CBT Court Booking",
			{"company": ("!=", AYALA), "booking_status": ("in", ACTIVE_STATUSES)},
		)
		# Guard the guard: with no other tenant's rows on the site the scoping
		# assertion below would pass while doing nothing.
		self.assertGreater(
			others,
			0,
			"no ACTIVE booking outside Ayala is seeded, so the scoping assertion "
			"below would pass while proving nothing. This is a FIXTURE problem, "
			"not a scoping regression — check seed_test_data before touching "
			"tenancy.booking_query.",
		)
		self.assertEqual(everyone, ayala + others)

		frappe.set_user(ALONA)
		scoped = int(get_result(json.dumps(card, default=str), filters))
		self.assertEqual(scoped, ayala)
		self.assertLess(scoped, everyone)
