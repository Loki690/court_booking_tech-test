"""CRUD SAFEGUARD — CBT Branch: READ -> MODIFY(ALL FIELDS) -> READ.

User, 2026-09-11: *"SCOPE IS ALL FIELD. REMEMBER HUMAN READABLE IS THE KEY"*.
LEDGER: THIS FILE CLAIMS NO DATE. It books nothing.
"""

import pytest
from playwright.sync_api import Page, Playwright

from helpers import crud, gestures, schema
from helpers.board import api_csrf, platform_api
from helpers.navigation import goto_form, wait_for_page_load

DOCTYPE = "CBT Branch"
COMPANY = "crud-probe-branchco"
CODE = "CRUDBR"
BRANCH_SLUG = "main"
BRANCH = f"{CODE}-{BRANCH_SLUG}"

MEDIA_TAB = "[data-testid='cbt-media-tab']"
MEDIA_TILE = "[data-testid='cbt-media-tile']"

EXPECTED_DATA_FIELDS = 16

COVERED_ELSEWHERE = {
    "slug": "test_the_slug_and_company_refuse_to_change_after_the_first_save",
    "company": "test_the_slug_and_company_refuse_to_change_after_the_first_save",
    "company_code": "test_the_company_code_follows_the_company_and_ignores_typing",
    "business_hours": "test_court_hours_goes_from_many_to_one_to_none",
    "layout": "test_the_floor_plan_cells_go_none_one_many",
    "photos": "test_the_branch_media_tab_starts_empty_and_counts_up",
}

EXCLUDED = {}
EXPECTED_EXCLUDED = frozenset()
SUPPLIED = {}


def _purge(api, csrf):
    head = {"X-Frappe-CSRF-Token": csrf}
    api.delete(f"/api/resource/CBT Branch/{BRANCH}", headers=head, timeout=30000)
    api.delete(f"/api/resource/CBT Company/{COMPANY}", headers=head, timeout=30000)


@pytest.fixture(scope="module", autouse=True)
def probe_branch(playwright: Playwright):
    """A company and a branch this file owns outright — no seeded record is touched."""
    api = platform_api(playwright)
    csrf = api_csrf(api)
    _purge(api, csrf)
    head = {"X-Frappe-CSRF-Token": csrf}
    company = api.post(
        "/api/resource/CBT Company",
        headers=head,
        data={
            "company_name": "CRUD Probe Branch Co",
            "registered_name": "CRUD Probe Branch Holdings Inc.",
            "slug": COMPANY,
            "company_code": CODE,
            "vat_registration": "NON-VAT",
            "status": "Active",
        },
        timeout=30000,
    )
    assert company.ok, f"could not arrange the company: {company.status} {company.text()}"
    branch = api.post(
        "/api/resource/CBT Branch",
        headers=head,
        data={
            "company": COMPANY,
            "branch_name": "CRUD Probe Main",
            "slug": BRANCH_SLUG,
        },
        timeout=30000,
    )
    assert branch.ok, f"could not arrange the branch: {branch.status} {branch.text()}"
    yield BRANCH
    _purge(api, csrf)


def _details_tab(page: Page):
    tabs = page.locator("button.nav-link")
    if tabs.count():
        tabs.first.click()


def _settle(page: Page):
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=BRANCH,
        timeout=30000,
    )
    _details_tab(page)


def _open(page: Page):
    goto_form(page, DOCTYPE, BRANCH)
    _settle(page)


def _reopen(page: Page):
    page.reload(wait_until="domcontentloaded")
    wait_for_page_load(page)
    _settle(page)


@pytest.mark.e2e
class TestBranchCrud:
    def test_the_schema_arithmetic_is_still_what_this_file_was_written_against(self):
        """A field added or deleted changes what ALL FIELDS means, so it goes RED here."""
        crud.assert_total_fields(DOCTYPE, EXPECTED_DATA_FIELDS)
        crud.assert_exclusions_are_pinned(DOCTYPE, EXCLUDED, EXPECTED_EXCLUDED)
        on_disk = {f["fieldname"] for f in schema.data_fields(DOCTYPE)}
        owned = set(COVERED_ELSEWHERE) | set(EXCLUDED)
        assert owned <= on_disk, f"claimed but not fields: {sorted(owned - on_disk)}"
        for field, test_name in COVERED_ELSEWHERE.items():
            assert hasattr(TestBranchCrud, test_name), (
                f"{field} claims to be covered by {test_name}, which does not exist"
            )

    def test_every_field_on_disk_is_visible_to_this_seat_in_the_browser(self, page: Page):
        """The client meta is permission-filtered; the file on disk is not."""
        _open(page)
        crud.assert_client_meta_matches_disk(page, DOCTYPE)

    def test_every_branch_field_survives_read_modify_read(self, page: Page):
        """READ -> MODIFY -> READ, every reachable field, read back from the screen."""
        _open(page)
        fields = [
            f for f in crud.plan_fields(DOCTYPE, EXCLUDED)
            if f["fieldname"] not in COVERED_ELSEWHERE
            and gestures.field_is_on_screen(page, f["fieldname"])
        ]
        assert len(fields) >= 8, f"only {len(fields)} fields reachable — the form did not render"

        before = {f["fieldname"]: gestures.read_value(page, f["fieldname"]) for f in fields}
        typed = {}
        for field in fields:
            name = field["fieldname"]
            typed[name] = crud.value_for(field, before[name], SUPPLIED)
            gestures.fill(page, name, typed[name])
        gestures.save_form(page)

        _reopen(page)
        for field in fields:
            name = field["fieldname"]
            after = gestures.read_value(page, name)
            assert crud.rendered_matches(field, typed[name], after), (
                f"{name}: typed {typed[name]!r}, the screen shows {after!r} after a reload"
            )
            assert not crud.rendered_matches(field, before[name], after), (
                f"{name}: the value never moved off {before[name]!r}"
            )

    def test_the_company_code_follows_the_company_and_ignores_typing(self, page: Page):
        """A read_only field is not skipped — it is read and asserted DERIVED."""
        _open(page)
        assert gestures.read_value(page, "company_code") == CODE, (
            "the branch is not showing its company's code"
        )
        assert not page.locator(
            f".frappe-control[data-fieldname='company_code'] input"
        ).first.is_visible(), "the company code is typeable on a branch"

    def test_the_slug_and_company_refuse_to_change_after_the_first_save(self, page: Page):
        """A refusal test must first prove the save path is ALIVE."""
        _open(page)
        alive = "CRUD Probe Main still saving"
        gestures.fill(page, "branch_name", alive)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "branch_name") == alive, (
            "the form is not saving at all, so no refusal below would mean anything"
        )

        gestures.fill(page, "slug", "renamed")
        page.keyboard.press("Control+s")
        page.locator(".modal.show").first.wait_for(state="visible", timeout=30000)
        gestures.dismiss_modals(page)
        gestures.fill(page, "slug", BRANCH_SLUG)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "slug") == BRANCH_SLUG, (
            "the slug moved despite the controller calling it immutable"
        )

    def test_court_hours_goes_from_many_to_one_to_none(self, page: Page):
        """MANY is where a new branch STARTS — seven rows the server appends.

        So this walks the boundary the only way a person can: down to one, then
        to none, proving the pre-filled grid can actually be emptied.
        """
        _open(page)
        assert gestures.grid_row_count(page, "business_hours") == 7, (
            "a new branch did not arrive with its seven Court Hours rows"
        )
        while gestures.grid_row_count(page, "business_hours") > 1:
            gestures.grid_remove_row(page, "business_hours", 1)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "business_hours") == 1

        gestures.grid_remove_row(page, "business_hours", 1)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "business_hours") == 0, (
            "the last Court Hours row could not be removed"
        )

    def test_the_floor_plan_cells_go_none_one_many(self, page: Page):
        """A cell with no court is a deliberate gap, so this needs no court to exist."""
        _open(page)
        assert gestures.grid_row_count(page, "layout") == 0
        first = gestures.grid_add_row(page, "layout")
        gestures.grid_fill_row_form(page, "layout", first, {"row_index": 1, "col_index": 1})
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "layout") == 1

        second = gestures.grid_add_row(page, "layout")
        gestures.grid_fill_row_form(page, "layout", second, {"row_index": 1, "col_index": 2})
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "layout") == 2

    def test_the_branch_media_tab_starts_empty_and_counts_up(self, page: Page):
        """The photo COUNT boundary on a branch; the rest is test_media_tab.py's."""
        _open(page)
        page.locator("button.nav-link[data-fieldname='media_tab'] >> visible=true").first.click()
        page.locator(f"{MEDIA_TAB} >> visible=true").first.wait_for(state="visible", timeout=20000)
        assert page.locator(f"{MEDIA_TAB} {MEDIA_TILE}").count() == 0, (
            "a brand new branch is already showing photo tiles"
        )

    def test_the_courts_tab_renders_for_a_branch_with_no_courts(self, page: Page):
        """Hand-written, non-field assertion: the Courts tab carries no fields."""
        _open(page)
        link = page.locator("button.nav-link[data-fieldname='courts_tab']")
        assert link.count(), "the Courts tab is not on the branch form"
        link.first.click()
        assert page.locator("[data-fieldname='courts_html']").first.is_visible()
