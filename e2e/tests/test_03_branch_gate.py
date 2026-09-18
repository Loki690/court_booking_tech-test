"""
E2E file 03 — Branch self-management gate (section-7; PLAN §10 row 03).

`allow_self_branch_management` on vs off, across every surface: a gated-ON
admin (Alona/AYALA) creates a branch through the desk form; a gated-OFF admin
(Quintin/QCSM) sees the form DISABLED client-side (frm.disable_form + intro —
the UX mirror) AND is rejected server-side on a direct REST insert; the gate
does NOT extend to courts; platform scope bypasses it.

The branch-form GEO smoke (Leaflet/embed/map-click) already lives in
test_branch_form_geo.py — this file covers the GATE only (S3 as-built 6).
"""
from datetime import datetime

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright

from helpers import gestures
from helpers.auth import login_as
from helpers.desk_form import wait_for_new_form
from helpers.board import platform_api, api_csrf, csrf
from helpers.navigation import goto_form, wait_for_page_load

ALONA = "admin.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"
QCSM_COURT = "QCSM-timog-court-1"

GATE_INTRO = "managed by the platform"


def _delete_branch_if_exists(playwright: Playwright, name: str):
    api = platform_api(playwright)
    try:
        if api.get(f"/api/resource/CBT Branch/{name}").ok:
            deleted = api.delete(
                f"/api/resource/CBT Branch/{name}",
                headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            )
            assert deleted.ok, f"delete {name}: HTTP {deleted.status} {deleted.text()}"
    finally:
        api.dispose()


def _new_branch_form(page: Page):
    page.goto("/desk/cbt-branch/new", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    # BOTH controls the fill touches first must be RENDERED before any
    # set_value: a Link set against a missing control silently no-ops and the
    # save then dies on a client-side Missing Fields check (S11 note 22 — this
    # exact race fired here in the section-12 round-1 run once the loadfile
    # schedule reshuffled).
    page.wait_for_selector(".frappe-control[data-fieldname='company']", timeout=15000)
    page.wait_for_selector(".frappe-control[data-fieldname='slug']", timeout=15000)
    page.wait_for_function("() => window.cur_frm && cur_frm.doc", timeout=30000)
    # B11, and no longer optional now this file TYPES: the new-doc route can resolve
    # TWICE under load and the first write lands on a discarded doc. A `set_value`
    # chain could be replayed wholesale; typed keystrokes and a clicked option cannot.
    # LAST of the three gates — a rendered control and `cur_frm.doc` are both true of
    # the document that is about to be thrown away.
    wait_for_new_form(page, "CBT Branch")


def _fill_branch(page: Page, company: str, slug: str, branch_name: str):
    # Typed and clicked, not written. `gestures.fill_link` still proves the Link
    # COMMITTED before moving on — a silent no-op fails HERE, at the cause, rather
    # than as a Missing Fields timeout thirty seconds later.
    gestures.fill_link(page, "company", company)
    gestures.fill(page, "slug", slug)
    gestures.fill(page, "branch_name", branch_name)


def _save_form(page: Page) -> dict:
    """frm.save() can RESOLVE even when the server rejects (S4 as-built 10) —
    capture the outcome + modal text and decide loudly.

    SECTION-23 SWEEP. A SUCCESSFUL first save renames the new-doc route, and
    that navigation can destroy the execution context before the resolved value
    crosses back — `page.evaluate` then raises "Execution context was
    destroyed". That is the save WORKING. File 01 has handled it since
    section-11 (`test_01_platform_onboarding.py:108-120`); file 17 had not, and
    went red on 1 of 3 identical passes while banking section-23's gate. This
    was the second of the three first-save sites still missing the guard —
    swept rather than left for the next session to meet.

    The distinction that keeps this honest: a destroyed context means the save
    SUCCEEDED (a rejected save never navigates), so it is reported as
    `is_new: 0`. Every caller here decides on `is_new`, and the branch-gate
    tests that expect a REFUSAL still get `is_new: 1` from the rejection path.
    """
    # Ctrl+S. `gestures.save_form` owns the destroyed-context guard the docstring
    # above describes, so the three first-save sites no longer each carry a copy.
    # A save that does not commit surfaces as `is_new: 1` plus the modal text —
    # which is what the gate tests expecting a REFUSAL read.
    try:
        outcome = {"name": gestures.save_form(page, timeout=20000), "is_new": 0}
    except PlaywrightError as exc:
        outcome = {"rejected": str(exc)[:300], "is_new": 1}
    # `.modal.show` — closed frappe dialogs stay in the DOM, so a bare `.modal`
    # reports a PREVIOUS dialog's text as though it were this save's error.
    outcome["modals"] = page.evaluate(
        """() => Array.from(document.querySelectorAll('.modal.show'))
            .map(m => (m.innerText || '').trim()).filter(Boolean).slice(0, 3)"""
    )
    return outcome


@pytest.mark.e2e
class TestBranchGate:
    def test_admin_with_allow_creates_branch(self, page: Page, playwright: Playwright):
        _delete_branch_if_exists(playwright, "AYALA-e2e-gate")
        login_as(page, ALONA)
        _new_branch_form(page)
        _fill_branch(page, "ayala-courts", "e2e-gate", "E2E Gate Branch")
        outcome = _save_form(page)
        assert not outcome.get("is_new"), f"branch save failed: {outcome}"
        assert outcome["name"] == "AYALA-e2e-gate", outcome
        # Court-hours auto-populate proves the controller pipeline ran.
        doc = page.request.get("/api/resource/CBT Branch/AYALA-e2e-gate")
        assert doc.ok
        assert len(doc.json()["data"]["business_hours"]) == 7
        _delete_branch_if_exists(playwright, "AYALA-e2e-gate")

    def test_admin_without_allow_form_disabled_and_insert_denied(self, page: Page):
        login_as(page, QUINTIN)
        _new_branch_form(page)
        # Setting the company fires the gate check (client UX mirror): the
        # form grays out with a clear intro BEFORE any save attempt.
        gestures.fill_link(page, "company", "qc-smash")
        page.wait_for_selector(
            f".form-message:has-text('{GATE_INTRO}')", timeout=15000
        )
        page.wait_for_function(
            "() => !!cur_frm.fields_dict.branch_name.df.read_only",
            timeout=15000,
        )
        # Server truth: a direct REST insert fails regardless of the UI.
        resp = page.request.post(
            "/api/resource/CBT Branch",
            headers={"X-Frappe-CSRF-Token": csrf(page)},
            data={"company": "qc-smash", "slug": "e2e-gate-q", "branch_name": "Blocked"},
        )
        assert resp.status == 403, f"expected 403, got {resp.status}: {resp.text()}"

    def test_gate_does_not_apply_to_courts(self, page: Page):
        login_as(page, QUINTIN)
        goto_form(page, "CBT Court", QCSM_COURT)
        # data-ajax-state completes before cur_frm binds — wait for THIS doc.
        page.wait_for_function(
            f"() => window.cur_frm && cur_frm.doc && cur_frm.doc.name === '{QCSM_COURT}'",
            timeout=15000,
        )
        stamp = f"E2E gate check {datetime.now():%H%M%S}"
        gestures.fill(page, "description", stamp)
        outcome = _save_form(page)
        assert not outcome.get("is_new"), f"court save failed: {outcome}"
        saved = page.request.get(f"/api/resource/CBT Court/{QCSM_COURT}")
        assert saved.ok and saved.json()["data"]["description"] == stamp
        # Leave the fixture as we found it.
        gestures.fill(page, "description", "")
        outcome = _save_form(page)
        assert not outcome.get("is_new"), f"court revert failed: {outcome}"

    def test_platform_admin_bypasses_gate(
        self, page: Page, playwright: Playwright, platform_seat: str
    ):
        _delete_branch_if_exists(playwright, "QCSM-e2e-gate-pa")
        # The seeded CBT Platform Admin — not the Administrator context, which
        # bypasses DocPerms and would prove nothing about the role (section-27).
        login_as(page, platform_seat)
        _new_branch_form(page)
        _fill_branch(page, "qc-smash", "e2e-gate-pa", "Platform Made This")
        outcome = _save_form(page)
        assert not outcome.get("is_new"), f"platform branch save failed: {outcome}"
        assert outcome["name"] == "QCSM-e2e-gate-pa", outcome
        _delete_branch_if_exists(playwright, "QCSM-e2e-gate-pa")
