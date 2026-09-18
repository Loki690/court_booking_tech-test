"""CRUD SAFEGUARD — CBT Court: READ -> MODIFY(ALL FIELDS) -> READ.

User, 2026-09-11: *"SCOPE IS ALL FIELD. REMEMBER HUMAN READABLE IS THE KEY"*.
LEDGER: THIS FILE CLAIMS NO DATE. It books nothing.
"""

import pytest
from playwright.sync_api import Page, Playwright

from helpers import crud, gestures, schema
from helpers.board import api_csrf, platform_api
from helpers.navigation import goto_form, wait_for_page_load

DOCTYPE = "CBT Court"
COMPANY = "crud-probe-courtco"
CODE = "CRUDCT"
BRANCH_SLUG = "main"
BRANCH = f"{CODE}-{BRANCH_SLUG}"
COURT_NAME = "Center Court"
COURT = f"{BRANCH}-center-court"

# Both parents set show_title_field_in_link, so the control shows the TITLE.
COMPANY_TITLE = "CRUD Probe Court Co"
BRANCH_TITLE = "CRUD Probe Main"

EXPECTED_DATA_FIELDS = 10

COVERED_ELSEWHERE = {
    "branch": "test_the_branch_refuses_to_change_after_the_first_save",
    "company": "test_the_company_follows_the_branch_and_is_not_typeable",
    "court_slug": "test_renaming_the_court_does_not_move_its_slug_or_its_document_name",
    "rate_rules": "test_rate_rules_go_none_one_many",
}

EXCLUDED = {}
EXPECTED_EXCLUDED = frozenset()
SUPPLIED = {}


def _purge(api, csrf):
    head = {"X-Frappe-CSRF-Token": csrf}
    api.delete(f"/api/resource/CBT Court/{COURT}", headers=head, timeout=30000)
    api.delete(f"/api/resource/CBT Branch/{BRANCH}", headers=head, timeout=30000)
    api.delete(f"/api/resource/CBT Company/{COMPANY}", headers=head, timeout=30000)


@pytest.fixture(scope="module", autouse=True)
def probe_court(playwright: Playwright):
    """A company, branch and court this file owns outright."""
    api = platform_api(playwright)
    csrf = api_csrf(api)
    _purge(api, csrf)
    head = {"X-Frappe-CSRF-Token": csrf}
    for path, payload, what in (
        (
            "/api/resource/CBT Company",
            {
                "company_name": "CRUD Probe Court Co",
                "registered_name": "CRUD Probe Court Holdings Inc.",
                "slug": COMPANY,
                "company_code": CODE,
                "vat_registration": "NON-VAT",
                "status": "Active",
            },
            "company",
        ),
        (
            "/api/resource/CBT Branch",
            {"company": COMPANY, "branch_name": "CRUD Probe Main", "slug": BRANCH_SLUG},
            "branch",
        ),
        (
            "/api/resource/CBT Court",
            {
                "branch": BRANCH,
                "court_name": COURT_NAME,
                "court_type": "Pickleball",
                "hourly_rate": 500,
            },
            "court",
        ),
    ):
        made = api.post(path, headers=head, data=payload, timeout=30000)
        assert made.ok, f"could not arrange the {what}: {made.status} {made.text()}"
    yield COURT
    _purge(api, csrf)


def _settle(page: Page):
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=COURT,
        timeout=30000,
    )


def _open(page: Page):
    goto_form(page, DOCTYPE, COURT)
    _settle(page)


def _reopen(page: Page):
    page.reload(wait_until="domcontentloaded")
    wait_for_page_load(page)
    _settle(page)


@pytest.mark.e2e
class TestCourtCrud:
    def test_the_schema_arithmetic_is_still_what_this_file_was_written_against(self):
        """A field added or deleted changes what ALL FIELDS means, so it goes RED here."""
        crud.assert_total_fields(DOCTYPE, EXPECTED_DATA_FIELDS)
        crud.assert_exclusions_are_pinned(DOCTYPE, EXCLUDED, EXPECTED_EXCLUDED)
        on_disk = {f["fieldname"] for f in schema.data_fields(DOCTYPE)}
        owned = set(COVERED_ELSEWHERE) | set(EXCLUDED)
        assert owned <= on_disk, f"claimed but not fields: {sorted(owned - on_disk)}"
        for field, test_name in COVERED_ELSEWHERE.items():
            assert hasattr(TestCourtCrud, test_name), (
                f"{field} claims to be covered by {test_name}, which does not exist"
            )

    def test_every_field_on_disk_is_visible_to_this_seat_in_the_browser(self, page: Page):
        """The client meta is permission-filtered; the file on disk is not."""
        _open(page)
        crud.assert_client_meta_matches_disk(page, DOCTYPE)

    def test_every_court_field_survives_read_modify_read(self, page: Page):
        """READ -> MODIFY -> READ, every reachable field, read back from the screen."""
        _open(page)
        fields = [
            f for f in crud.plan_fields(DOCTYPE, EXCLUDED)
            if f["fieldname"] not in COVERED_ELSEWHERE
            and gestures.field_is_on_screen(page, f["fieldname"])
        ]
        assert len(fields) >= 5, f"only {len(fields)} fields reachable — the form did not render"

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

    def test_the_company_follows_the_branch_and_is_not_typeable(self, page: Page):
        """A read_only field is not skipped — it is read and asserted DERIVED."""
        _open(page)
        assert gestures.read_value(page, "company") == COMPANY_TITLE, (
            "the court is not showing its branch's company"
        )
        assert not page.locator(
            ".frappe-control[data-fieldname='company'] input"
        ).first.is_visible(), "the company is typeable on a court"

    def test_the_branch_refuses_to_change_after_the_first_save(self, page: Page):
        """A refusal test must first prove the save path is ALIVE."""
        _open(page)
        alive = "Center Court still saving"
        gestures.fill(page, "court_name", alive)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "court_name") == alive, (
            "the form is not saving at all, so no refusal below would mean anything"
        )
        assert gestures.read_value(page, "branch") == BRANCH_TITLE, (
            "the court drifted off its branch without anyone asking"
        )

    def test_renaming_the_court_does_not_move_its_slug_or_its_document_name(self, page: Page):
        """The slug is computed at INSERT only, so a rename must not move the name."""
        _open(page)
        gestures.fill(page, "court_name", "Renamed Show Court")
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "court_name") == "Renamed Show Court"
        assert page.evaluate("() => cur_frm.doc.name") == COURT, (
            "renaming the court moved its document name — every link to it would break"
        )

    def test_a_zero_hourly_rate_is_refused(self, page: Page):
        """`hourly_rate <= 0` throws, and the person must SEE that it was refused."""
        _open(page)
        gestures.fill(page, "hourly_rate", 0)
        page.keyboard.press("Control+s")
        page.locator(".modal.show").first.wait_for(state="visible", timeout=30000)
        gestures.dismiss_modals(page)
        gestures.fill(page, "hourly_rate", 750)
        gestures.save_form(page)
        _reopen(page)
        assert crud._as_number(gestures.read_value(page, "hourly_rate")) == 750

    def test_rate_rules_go_none_one_many(self, page: Page):
        """NONE, ONE, MANY — two rules in one tier may not overlap in time."""
        _open(page)
        assert gestures.grid_row_count(page, "rate_rules") == 0
        first = gestures.grid_add_row(page, "rate_rules")
        gestures.grid_fill_row_form(
            page, "rate_rules", first,
            {"day_scope": "All Days", "start_time": "06:00:00",
             "end_time": "12:00:00", "hourly_rate": 600},
        )
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "rate_rules") == 1

        second = gestures.grid_add_row(page, "rate_rules")
        gestures.grid_fill_row_form(
            page, "rate_rules", second,
            {"day_scope": "All Days", "start_time": "12:00:00",
             "end_time": "18:00:00", "hourly_rate": 800},
        )
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "rate_rules") == 2, (
            "the second rate rule did not survive the reload"
        )
