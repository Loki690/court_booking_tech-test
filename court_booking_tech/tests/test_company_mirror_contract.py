"""
The derived-`company` contract (Backlog B17).

WHY THIS EXISTS. Frappe pre-fills any field literally named `company` from a user
default — `create_new.js` keys that off `df.fieldname`, never `df.options` — and
`frappe/model/document.py` runs `_validate_links()` at :456 and `_validate()` (the
mandatory check) at :464, both BEFORE `run_method("before_insert")` on :457. So on a
CBT doctype whose `company` is DERIVED from the branch, a controller can never set it
in time. The value has to arrive client-side or the desk form is unusable.

There are exactly two legal protections, and every derived-company doctype must carry
one of them:

  fetch_from    — the field declares `fetch_from: "branch.company"` with no
                  `fetch_if_empty`, so frappe fills it from the source link during
                  `_fetch_from_value` (base_document.py:1019-1024 / :1054-1056).
                  CBT Court uses this.
  client_mirror — a `<doctype>.js` with a `branch` handler that reads
                  `CBT Branch.company` and writes it. CBT Slot Block and
                  CBT Open Play Session use this.

MEASURED 2026-08-18 on a fresh bench, which is why the list below is asserted rather
than assumed: CBT Court saved cleanly from the desk form (HTTP 200, company mirrored
from the branch). CBT Open Play Session did NOT — its `company` is reqd:1 + read_only:1
with no `fetch_from` and, at the time, no `.js`, and the site default
`hide_empty_read_only_fields` meant the field was not even rendered. The save aborted
CLIENT-side with "Company is required." for a field the operator could neither see nor
edit, so no HTTP request was ever made. (Backlog B17 predicted an HTTP 417 from a bogus
prefill; the prefill never fired and the failure mode was different and harder.)

THE TRIPWIRE. `test_every_readonly_company_is_classified` fails when a NEW CBT doctype
grows a read-only `company` field without being classified here. That is the whole
point: the server cannot defend this, so the decision must be made deliberately, once,
per doctype.
"""

import unittest

import frappe

# doctype -> protection mechanism. Adding a row is a deliberate act; see the tripwire
# test at the bottom, which fails if a doctype is missing from it.
DERIVED_COMPANY = {
    "CBT Court": "fetch_from",
    "CBT Slot Block": "client_mirror",
    "CBT Open Play Session": "client_mirror",
}

# Derived-company doctypes that need NO mirror because no CBT role can create one from
# a desk form — they are written by the engine. Measured 2026-08-18 from their shipped
# permissions. `test_engine_only_doctypes_stay_uncreatable` re-checks that claim on every
# run: the moment someone grants `create` to a CBT role, this stops being safe and the
# doctype needs a real mechanism.
ENGINE_ONLY = {
    "CBT Booking Invoice": (
        "create is System Manager only; `company` is permlevel 1 and only System "
        "Manager holds permlevel-1 write. Written by billing.py."
    ),
    "CBT Payment Proof": (
        "create is System Manager only and every field is read_only. Written by the "
        "portal/verification engine."
    ),
    "CBT Platform Statement": (
        "create is System Manager only and every field is read_only. Issued by "
        "cbt_platform_statement.issue_statements from a CBT Platform Month Close "
        "(Backlog B21(a))."
    ),
    "CBT Customer Credit": (
        "create is System Manager only and every field is read_only. Minted by "
        "credits.issue_credit off a refund and drained by credits.take_credit "
        "(Backlog B39)."
    ),
}

# Roles that represent a real operator seat. If one of these can create a doctype, its
# desk form has to actually work.
OPERATOR_ROLES = {"CBT Platform Admin", "CBT Company Admin", "CBT Company Staff"}

MODULE = "Court Booking Tech"


class TestCompanyMirrorContract(unittest.TestCase):
    def _cbt_doctypes(self):
        return frappe.get_all(
            "DocType",
            filters={"module": MODULE, "istable": 0},
            pluck="name",
        )

    def _company_field(self, doctype):
        return frappe.get_meta(doctype).get_field("company")

    def _doctype_js(self, doctype):
        """The shipped client script for `doctype`, or None."""
        import os

        from frappe.modules.utils import get_doc_path

        slug = frappe.scrub(doctype)
        path = os.path.join(get_doc_path(MODULE, "DocType", doctype), f"{slug}.js")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_fetch_from_doctypes_declare_it_correctly(self):
        """`fetch_from` only protects if it is unconditional."""
        for doctype, mechanism in DERIVED_COMPANY.items():
            if mechanism != "fetch_from":
                continue
            with self.subTest(doctype=doctype):
                df = self._company_field(doctype)
                self.assertIsNotNone(df, f"{doctype} has no `company` field")
                self.assertEqual(
                    df.fetch_from,
                    "branch.company",
                    f"{doctype}.company must fetch from branch.company",
                )
                # fetch_if_empty would skip the overwrite whenever the prefill DID
                # land a bogus value — i.e. exactly the case this protects against.
                self.assertFalse(
                    df.get("fetch_if_empty"),
                    f"{doctype}.company must NOT set fetch_if_empty — that reinstates "
                    "the bug on any seat where the prefill fires",
                )
                self.assertTrue(
                    df.read_only,
                    f"{doctype}.company is derived and must stay read_only",
                )

    def test_client_mirror_doctypes_ship_the_mirror(self):
        """A client_mirror doctype must actually have the branch->company handler."""
        for doctype, mechanism in DERIVED_COMPANY.items():
            if mechanism != "client_mirror":
                continue
            with self.subTest(doctype=doctype):
                js = self._doctype_js(doctype)
                self.assertIsNotNone(
                    js,
                    f"{doctype} is classified client_mirror but ships no "
                    f"{frappe.scrub(doctype)}.js — its desk form cannot be saved",
                )
                self.assertIn(
                    "branch(frm)",
                    js,
                    f"{doctype}.js has no `branch` handler — nothing mirrors company",
                )
                self.assertIn(
                    "CBT Branch",
                    js,
                    f"{doctype}.js never reads CBT Branch — the mirror has no source",
                )
                self.assertIn(
                    'set_value("company"',
                    js,
                    f"{doctype}.js never writes company — the mirror is inert",
                )

    def test_every_readonly_company_is_classified(self):
        """TRIPWIRE — a new CBT doctype with a read-only `company` must be classified.

        The server cannot defend a derived company field, so a new one silently ships
        an unsaveable desk form. Failing here forces the choice to be made.
        """
        unclassified = []
        for doctype in self._cbt_doctypes():
            df = self._company_field(doctype)
            if not df or not df.read_only:
                continue
            if doctype in DERIVED_COMPANY or doctype in ENGINE_ONLY:
                continue
            unclassified.append(doctype)
        self.assertEqual(
            unclassified,
            [],
            "these CBT doctypes have a read-only `company` and no declared "
            "protection — each needs either fetch_from (no fetch_if_empty) or a "
            f"client mirror, then a row in DERIVED_COMPANY: {unclassified}",
        )

    def test_engine_only_doctypes_stay_uncreatable(self):
        """The ENGINE_ONLY exemption is only valid while it stays true.

        These doctypes dodge the mirror requirement solely because no operator seat can
        create one from a desk form. Grant `create` to a CBT role and the exemption is
        void — the form would hit the same unsaveable wall CBT Open Play Session did.
        """
        for doctype in ENGINE_ONLY:
            with self.subTest(doctype=doctype):
                creators = {
                    p.role
                    for p in frappe.get_meta(doctype).permissions
                    if p.create and not p.permlevel
                }
                offenders = sorted(creators & OPERATOR_ROLES)
                self.assertEqual(
                    offenders,
                    [],
                    f"{doctype} is exempted as engine-only, but {offenders} can now "
                    "create one from the desk. Its `company` is derived and read-only "
                    "with no mirror, so that form cannot be saved — give it a "
                    "client mirror (or fetch_from) and move it to DERIVED_COMPANY.",
                )

    def test_classified_doctypes_still_exist(self):
        """Keep the map honest — a renamed/removed doctype must not linger here."""
        known = set(self._cbt_doctypes())
        stale = sorted(
            dt for dt in (set(DERIVED_COMPANY) | set(ENGINE_ONLY)) if dt not in known
        )
        self.assertEqual(
            stale,
            [],
            f"the classification maps name doctypes that no longer exist: {stale}",
        )
