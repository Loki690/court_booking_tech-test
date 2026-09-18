"""
E2E — Company → Branches → Courts without leaving the form (section-27,
Backlog B30).

The user's words (2026-08-27): *"You should not go out of `CBT Company Page`
to read and modify your `CBT Branch` … you should not go out of that specific
CBT Branch in order to read and modify your `CBT Court`."* And on the editor
(2026-08-28): *"everything … it would look like the same but inside a tab …
CBT Company field is readonly and disabled since it is presumed"*.

So one seat, one route each, driven by a human's gestures — every value is
TYPED or CLICKED into the in-place editor (helpers.gestures), scoped to the
editor because CBT Company owns `slug` / `company_code` / `is_active` too and
an unscoped fill would land on the parent's field and go green for the wrong
reason. Saves are the editor's own Save button; the parent form's Ctrl+S is
never pressed. The anchor for "the save landed" is the editor's `data-state`
flipping to `saved` under the saved document's name, then the list row's own
cell text — never `cur_frm.doc.__unsaved`, which belongs to the PARENT and
never moves.

Rows:
  1. Alona (AYALA admin, self-management ON) creates a branch from the
     company's Branches tab, edits it in place (the hours grid is there, all
     seven rows), opens it, creates and edits a court from the branch's
     Courts tab, and walks BACK up through the tab's own link to find the
     court counted on the company form. The URL proves she never left.
  2. Quintin (QCSM admin, self-management OFF) reads branches with no
     New/Edit and the platform-managed sentence; courts are still his.
  3. Stella (AYALA staff) reads branches with no New/Edit and no gate
     sentence — her refusal is her role, not the platform.
  4. The PLATFORM seat opens a NEW company form: it carries no Branches tab
     (nothing to list yet). Creating a company is the platform's act, so the
     row also proves the role's `create` on CBT Company — the form does not
     render without it.
  5. The PLATFORM seat on the gate-OFF tenant (QCSM): the Onboarding tab IS
     visible (the checklist Quintin never sees), the Branches tab offers New
     with NO gate sentence (the exact contrast to row 2), and a branch typed
     into the editor saves under the role's own DocPerms.

The platform seat is `cbt.admin@example.com` — the CBT Platform Admin role and
nothing else. Rows 4–5 log in as it through the real form instead of riding
the Administrator context every test starts from: Administrator bypasses
DocPerms, so a row run as it proves nothing about the role (user ruling,
2026-08-28). The `platform_seat` fixture guarantees its password first.

Cleanup: created courts and branches are deleted through the admin API up
front AND in `finally` (a killed run must not poison the next one). Claims no
ledger date: nothing here books or blocks.
"""
import re

import pytest
from playwright.sync_api import Page, Playwright, expect

from helpers import gestures
from helpers.auth import login_as
from helpers.board import platform_api, api_csrf
from helpers.navigation import goto_form, wait_for_page_load

ALONA = "admin.ayala@example.com"
STELLA = "staff.ayala@example.com"
QUINTIN = "admin.qcsm@example.com"

AYALA = "ayala-courts"
QCSM = "qc-smash"

NEW_SLUG = "e2e-tree-branch"
NEW_BRANCH = "AYALA-e2e-tree-branch"
NEW_COURT = "AYALA-e2e-tree-branch-court-a"
PLATFORM_SLUG = "e2e-platform-branch"
PLATFORM_BRANCH = "QCSM-e2e-platform-branch"

LIST = "[data-testid='cbt-facility-list']"
BRANCH_LIST = f"{LIST}[data-child='CBT Branch']"
COURT_LIST = f"{LIST}[data-child='CBT Court']"
# The desk is an SPA that keeps every visited page container in the DOM, so
# each editor/list locator says which child it means and asks for the VISIBLE one.
BRANCH_EDITOR = "[data-testid='cbt-facility-editor'][data-child='CBT Branch']"
COURT_EDITOR = "[data-testid='cbt-facility-editor'][data-child='CBT Court']"
GATE_WORDS = "managed by the platform"


def _delete_if_exists(playwright: Playwright, doctype: str, name: str):
    api = platform_api(playwright)
    try:
        if api.get(f"/api/resource/{doctype}/{name}").ok:
            deleted = api.delete(
                f"/api/resource/{doctype}/{name}",
                headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            )
            assert deleted.ok, f"delete {doctype} {name}: HTTP {deleted.status} {deleted.text()}"
    finally:
        api.dispose()


def _open_form(page: Page, doctype: str, name: str):
    goto_form(page, doctype, name)
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=name,
        timeout=30000,
    )


def _click_tab(page: Page, fieldname: str):
    page.locator(f"button.nav-link[data-fieldname='{fieldname}'] >> visible=true").first.click()


def _visible(page: Page, selector: str):
    loc = page.locator(f"{selector} >> visible=true").first
    loc.wait_for(state="visible", timeout=20000)
    return loc


def _row(page: Page, name: str):
    return page.locator(f"[data-testid='cbt-facility-row'][data-name='{name}'] >> visible=true").first


def _cell(page: Page, name: str, col: str):
    return _row(page, name).locator(f"[data-col='{col}']")


def _wait_editor(page: Page, editor: str, state: str, name: str = ""):
    sel = f"{editor}[data-state='{state}']" + (f"[data-name='{name}']" if name else "")
    page.locator(f"{sel} >> visible=true").first.wait_for(state="visible", timeout=30000)


def _save_editor(page: Page, editor: str, expect_name: str):
    _visible(page, f"{editor} [data-testid='cbt-facility-save']").click()
    _wait_editor(page, editor, "saved", expect_name)


def _assert_onboarding_tab_hidden(page: Page):
    """Section-27 found and fixed a latent no-op: `frm.get_field('<tab>')` is
    undefined for a Tab Break, so the Onboarding tab's hide never ran and every
    Company Admin saw the platform's checklist tab ("Could not load the
    checklist."). Pinned here on a tenant seat."""
    page.wait_for_function(
        """() => {
            const t = (cur_frm.layout.tabs || []).find((t) => t.df && t.df.fieldname === 'onboarding_tab');
            return !!(t && t.hidden === true);
        }""",
        timeout=15000,
    )
    expect(
        page.locator("button.nav-link[data-fieldname='onboarding_tab'] >> visible=true")
    ).to_have_count(0)


def _read_only_value(page: Page, editor: str, fieldname: str) -> str:
    """A preset link is rendered, not editable: no <input>, a value block."""
    control = page.locator(f"{editor} .frappe-control[data-fieldname='{fieldname}'] >> visible=true").first
    control.wait_for(state="visible", timeout=15000)
    assert control.locator("input").count() == 0, f"{fieldname} is editable in the editor"
    return (control.locator(".control-value").first.inner_text() or "").strip()


@pytest.mark.e2e
class TestFacilityTree:
    def test_admin_builds_branch_and_court_without_leaving(self, page: Page, playwright: Playwright):
        _delete_if_exists(playwright, "CBT Court", NEW_COURT)
        _delete_if_exists(playwright, "CBT Branch", NEW_BRANCH)
        try:
            login_as(page, ALONA)
            _open_form(page, "CBT Company", AYALA)
            _click_tab(page, "branches_tab")
            _visible(page, BRANCH_LIST)
            expect(_row(page, "AYALA-bgc")).to_be_visible()
            expect(_row(page, "AYALA-makati")).to_be_visible()
            expect(_cell(page, "AYALA-bgc", "courts")).to_contain_text("3 courts")
            expect(_cell(page, "AYALA-bgc", "rate")).to_contain_text("400.00")

            # --- New branch, typed into the child's own layout in place ---
            _visible(page, f"{BRANCH_LIST} [data-testid='cbt-facility-new']").click()
            _wait_editor(page, BRANCH_EDITOR, "editing")
            assert re.search("ayala", _read_only_value(page, BRANCH_EDITOR, "company"), re.I)
            gestures.fill(page, "branch_name", "E2E Tree Branch", scope=BRANCH_EDITOR)
            slug_input = page.locator(
                f"{BRANCH_EDITOR} .frappe-control[data-fieldname='slug'] input >> visible=true"
            ).first
            expect(slug_input).to_have_value(NEW_SLUG, timeout=10000)
            gestures.fill(page, "phone", "0917 111 2222", scope=BRANCH_EDITOR)
            _save_editor(page, BRANCH_EDITOR, NEW_BRANCH)
            assert f"/desk/cbt-company/{AYALA}" in page.url, page.url
            expect(_row(page, NEW_BRANCH)).to_be_visible()
            expect(_cell(page, NEW_BRANCH, "phone")).to_contain_text("0917 111 2222")
            expect(_cell(page, NEW_BRANCH, "courts")).to_contain_text("No courts yet")

            # --- Edit it in place: the whole form is there, hours grid included ---
            _row(page, NEW_BRANCH).locator("[data-testid='cbt-facility-edit']").click()
            _wait_editor(page, BRANCH_EDITOR, "editing", NEW_BRANCH)
            assert gestures.grid_row_count(page, "business_hours", scope=BRANCH_EDITOR) == 7
            gestures.fill(page, "phone", "0917 333 4444", scope=BRANCH_EDITOR)
            _save_editor(page, BRANCH_EDITOR, NEW_BRANCH)
            expect(_cell(page, NEW_BRANCH, "phone")).to_contain_text("0917 333 4444")
            assert f"/desk/cbt-company/{AYALA}" in page.url, page.url

            # --- Down one level: the branch's own form, its Courts tab ---
            _row(page, NEW_BRANCH).locator("[data-testid='cbt-facility-open']").click()
            page.wait_for_url(f"**/desk/cbt-branch/{NEW_BRANCH}", timeout=30000)
            wait_for_page_load(page)
            page.wait_for_function(
                "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
                arg=NEW_BRANCH,
                timeout=30000,
            )
            _click_tab(page, "courts_tab")
            _visible(page, COURT_LIST)
            expect(_visible(page, f"{COURT_LIST} [data-testid='cbt-facility-empty']")).to_contain_text(
                "No courts yet"
            )

            _visible(page, f"{COURT_LIST} [data-testid='cbt-facility-new']").click()
            _wait_editor(page, COURT_EDITOR, "editing")
            assert NEW_BRANCH in _read_only_value(page, COURT_EDITOR, "branch")
            gestures.fill(page, "court_name", "Court A", scope=COURT_EDITOR)
            gestures.select(page, "court_type", "Pickleball", scope=COURT_EDITOR)
            gestures.fill(page, "hourly_rate", 400, scope=COURT_EDITOR)
            _save_editor(page, COURT_EDITOR, NEW_COURT)
            assert f"/desk/cbt-branch/{NEW_BRANCH}" in page.url, page.url
            expect(_cell(page, NEW_COURT, "type")).to_contain_text("Pickleball")
            expect(_cell(page, NEW_COURT, "rate")).to_contain_text("400.00")
            expect(_cell(page, NEW_COURT, "status")).to_contain_text("Active")

            _row(page, NEW_COURT).locator("[data-testid='cbt-facility-edit']").click()
            _wait_editor(page, COURT_EDITOR, "editing", NEW_COURT)
            gestures.fill(page, "hourly_rate", 450, scope=COURT_EDITOR)
            _save_editor(page, COURT_EDITOR, NEW_COURT)
            expect(_cell(page, NEW_COURT, "rate")).to_contain_text("450.00")

            # --- Back UP through the tab's own link, and the company knows ---
            _visible(page, f"{COURT_LIST} [data-testid='cbt-facility-parent']").click()
            page.wait_for_url(f"**/desk/cbt-company/{AYALA}", timeout=30000)
            wait_for_page_load(page)
            _click_tab(page, "branches_tab")
            expect(_cell(page, NEW_BRANCH, "courts")).to_contain_text("1 court", timeout=20000)
            expect(_cell(page, NEW_BRANCH, "rate")).to_contain_text("450.00")
        finally:
            _delete_if_exists(playwright, "CBT Court", NEW_COURT)
            _delete_if_exists(playwright, "CBT Branch", NEW_BRANCH)

    def test_gate_off_admin_reads_branches_but_manages_courts(self, page: Page):
        login_as(page, QUINTIN)
        _open_form(page, "CBT Company", QCSM)
        _assert_onboarding_tab_hidden(page)
        _click_tab(page, "branches_tab")
        branches = _visible(page, BRANCH_LIST)
        expect(_row(page, "QCSM-timog")).to_be_visible()
        expect(_row(page, "QCSM-annex")).to_be_visible()
        expect(branches.locator("[data-testid='cbt-facility-new']")).to_have_count(0)
        expect(branches.locator("[data-testid='cbt-facility-edit']")).to_have_count(0)
        expect(branches.locator("[data-testid='cbt-facility-gate']")).to_contain_text(GATE_WORDS)

        _row(page, "QCSM-timog").locator("[data-testid='cbt-facility-open']").click()
        page.wait_for_url("**/desk/cbt-branch/QCSM-timog", timeout=30000)
        wait_for_page_load(page)
        _click_tab(page, "courts_tab")
        courts = _visible(page, COURT_LIST)
        expect(courts.locator("[data-testid='cbt-facility-row']")).to_have_count(3)
        expect(courts.locator("[data-testid='cbt-facility-new']")).to_have_count(1)
        expect(courts.locator("[data-testid='cbt-facility-parent']")).to_be_visible()

    def test_staff_reads_branches_only(self, page: Page):
        login_as(page, STELLA)
        _open_form(page, "CBT Company", AYALA)
        _assert_onboarding_tab_hidden(page)
        _click_tab(page, "branches_tab")
        branches = _visible(page, BRANCH_LIST)
        expect(branches.locator("[data-testid='cbt-facility-row']")).to_have_count(2)
        expect(branches.locator("[data-testid='cbt-facility-new']")).to_have_count(0)
        expect(branches.locator("[data-testid='cbt-facility-edit']")).to_have_count(0)
        expect(branches.locator("[data-testid='cbt-facility-gate']")).to_have_count(0)

    def test_new_company_has_no_branches_tab(self, page: Page, platform_seat: str):
        login_as(page, platform_seat)
        page.goto("/desk/cbt-company/new", wait_until="domcontentloaded", timeout=60000)
        wait_for_page_load(page)
        page.wait_for_function(
            "() => window.cur_frm && cur_frm.doc && cur_frm.doctype === 'CBT Company'",
            timeout=30000,
        )
        page.wait_for_function(
            """() => {
                const t = (cur_frm.layout.tabs || []).find((t) => t.df && t.df.fieldname === 'branches_tab');
                return !!(t && t.hidden === true);
            }""",
            timeout=15000,
        )
        expect(
            page.locator("button.nav-link[data-fieldname='branches_tab'] >> visible=true")
        ).to_have_count(0)

    def test_platform_seat_manages_a_gated_tenant(
        self, page: Page, playwright: Playwright, platform_seat: str
    ):
        _delete_if_exists(playwright, "CBT Branch", PLATFORM_BRANCH)
        try:
            login_as(page, platform_seat)
            _open_form(page, "CBT Company", QCSM)
            # The platform sees the checklist tab a tenant never does.
            expect(
                page.locator("button.nav-link[data-fieldname='onboarding_tab'] >> visible=true")
            ).to_have_count(1)
            _click_tab(page, "branches_tab")
            branches = _visible(page, BRANCH_LIST)
            expect(_row(page, "QCSM-timog")).to_be_visible()
            # Gate OFF for QCSM, but the platform manages — New, and no gate sentence.
            expect(branches.locator("[data-testid='cbt-facility-new']")).to_have_count(1)
            expect(branches.locator("[data-testid='cbt-facility-gate']")).to_have_count(0)

            branches.locator("[data-testid='cbt-facility-new']").click()
            _wait_editor(page, BRANCH_EDITOR, "editing")
            assert re.search("smash", _read_only_value(page, BRANCH_EDITOR, "company"), re.I)
            gestures.fill(page, "branch_name", "E2E Platform Branch", scope=BRANCH_EDITOR)
            slug_input = page.locator(
                f"{BRANCH_EDITOR} .frappe-control[data-fieldname='slug'] input >> visible=true"
            ).first
            expect(slug_input).to_have_value(PLATFORM_SLUG, timeout=10000)
            _save_editor(page, BRANCH_EDITOR, PLATFORM_BRANCH)
            assert f"/desk/cbt-company/{QCSM}" in page.url, page.url
            expect(_row(page, PLATFORM_BRANCH)).to_be_visible()
            expect(_cell(page, PLATFORM_BRANCH, "courts")).to_contain_text("No courts yet")
            owner = page.request.get(f"/api/resource/CBT Branch/{PLATFORM_BRANCH}")
            assert owner.ok, owner.text()
            assert owner.json()["data"]["owner"] == platform_seat, owner.json()["data"]["owner"]
        finally:
            _delete_if_exists(playwright, "CBT Branch", PLATFORM_BRANCH)
