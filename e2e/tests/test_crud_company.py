"""CRUD SAFEGUARD — CBT Company: READ -> MODIFY(ALL FIELDS) -> READ.

User, 2026-09-11: *"SCOPE IS ALL FIELD. REMEMBER HUMAN READABLE IS THE KEY"*.
LEDGER: THIS FILE CLAIMS NO DATE. It books nothing.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright

from helpers import crud, gestures, schema
from helpers.board import api_csrf, platform_api
from helpers.navigation import goto_form, wait_for_page_load

DOCTYPE = "CBT Company"
SLUG = "crud-probe-company"
CODE = "CRUDCO"
FILES = Path(__file__).resolve().parents[2] / "court_booking_tech" / "seeds" / "files"
LOGO_A = FILES / "media_lounge.jpg"
LOGO_B = FILES / "media_showers.jpg"
BANNER_A = FILES / "media_banner.jpg"
BANNER_B = FILES / "media_court_1.jpg"

MEDIA_TAB = "[data-testid='cbt-media-tab']"
MEDIA_TILE = "[data-testid='cbt-media-tile']"
MEDIA_SAVE = "[data-testid='cbt-media-save']"

# An open day needs both times, or validate_business_hours refuses the save.
HOURS = {"opening_time": "06:00:00", "closing_time": "22:00:00"}

SLUG_CTRL = ".frappe-control[data-fieldname='slug']"

EXPECTED_DATA_FIELDS = 32

# Nothing on this DocType is untested; every name below says which test owns it.
COVERED_ELSEWHERE = {
    "slug": "test_the_slug_and_company_code_refuse_to_change_after_the_first_save",
    "company_code": "test_the_slug_and_company_code_refuse_to_change_after_the_first_save",
    "logo": "test_the_logo_goes_on_comes_off_and_goes_on_again",
    "banner": "test_the_banner_goes_on_comes_off_and_goes_on_again",
    "vat_percent": "test_vat_percent_appears_only_for_a_VAT_company",
    "subscription_fee": "test_each_billing_mode_shows_and_keeps_only_its_own_fields",
    "commission_percent": "test_each_billing_mode_shows_and_keeps_only_its_own_fields",
    "booking_fee_tiers": "test_each_billing_mode_shows_and_keeps_only_its_own_fields",
    "open_play_fee_per_participant": "test_each_billing_mode_shows_and_keeps_only_its_own_fields",
    "office_hours": "test_office_hours_goes_from_none_to_one_to_many",
    "photos": "test_the_media_tab_photo_count_goes_none_one_many",
}

EXCLUDED = {}
EXPECTED_EXCLUDED = frozenset()

SUPPLIED = {
    "website": "https://crud-probe.example.com",
    "facebook_url": "https://facebook.com/crudprobe",
    "instagram_url": "https://instagram.com/crudprobe",
}


def _purge(api, csrf):
    api.delete(
        f"/api/resource/{DOCTYPE}/{SLUG}",
        headers={"X-Frappe-CSRF-Token": csrf},
        timeout=30000,
    )


@pytest.fixture(scope="module", autouse=True)
def probe_company(playwright: Playwright):
    """A throwaway company this file owns outright — no seeded record is touched.

    Purged at setup as well as teardown: a killed run leaves this bench dirty and a
    duplicate slug on the next run reads exactly like a real product defect.
    """
    api = platform_api(playwright)
    csrf = api_csrf(api)
    _purge(api, csrf)
    made = api.post(
        f"/api/resource/{DOCTYPE}",
        headers={"X-Frappe-CSRF-Token": csrf},
        data={
            "company_name": "CRUD Probe Courts",
            "registered_name": "CRUD Probe Holdings Inc.",
            "slug": SLUG,
            "company_code": CODE,
            "vat_registration": "NON-VAT",
            "status": "Active",
        },
        timeout=30000,
    )
    assert made.ok, f"could not arrange the probe company: {made.status} {made.text()}"
    yield SLUG
    _purge(api, csrf)


def _details_tab(page: Page):
    """Force the Details tab forward: frappe remembers the last tab, across runs.

    The stored session state carries it, so a file that visits the Media tab makes
    every later Details field "not visible" — including in a different run.
    """
    tabs = page.locator("button.nav-link")
    if tabs.count():
        tabs.first.click()


def _open(page: Page):
    goto_form(page, DOCTYPE, SLUG)
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=SLUG,
        timeout=30000,
    )
    _details_tab(page)


def _reopen(page: Page):
    page.reload(wait_until="domcontentloaded")
    wait_for_page_load(page)
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=SLUG,
        timeout=30000,
    )
    _details_tab(page)


def _open_media_tab(page: Page):
    page.locator("button.nav-link[data-fieldname='media_tab'] >> visible=true").first.click()
    page.locator(f"{MEDIA_TAB} >> visible=true").first.wait_for(state="visible", timeout=20000)


def _drop_media(page: Page, files):
    page.locator(f"{MEDIA_TAB} input[type=file] >> visible=false").first.set_input_files(
        [str(f) for f in files]
    )


def _save_media(page: Page, expected_tiles: int):
    """Save the Media tab and wait for the tile count it should settle on."""
    page.locator(MEDIA_SAVE).first.click()
    page.wait_for_function(
        "(n) => document.querySelectorAll(\"[data-testid='cbt-media-tile']\").length === n",
        arg=expected_tiles,
        timeout=30000,
    )


@pytest.mark.e2e
class TestCompanyCrud:
    def test_the_schema_arithmetic_is_still_what_this_file_was_written_against(self):
        """A field added or deleted changes what ALL FIELDS means, so it goes RED here."""
        crud.assert_total_fields(DOCTYPE, EXPECTED_DATA_FIELDS)
        crud.assert_exclusions_are_pinned(DOCTYPE, EXCLUDED, EXPECTED_EXCLUDED)
        owned = set(COVERED_ELSEWHERE) | set(EXCLUDED)
        on_disk = {f["fieldname"] for f in schema.data_fields(DOCTYPE)}
        assert owned <= on_disk, (
            f"these names are claimed as covered but are not fields: {sorted(owned - on_disk)}"
        )
        for field, test_name in COVERED_ELSEWHERE.items():
            assert hasattr(TestCompanyCrud, test_name), (
                f"{field} claims to be covered by {test_name}, which does not exist — "
                "a covered-elsewhere note that names no test is a hole"
            )

    def test_every_field_on_disk_is_visible_to_this_seat_in_the_browser(self, page: Page):
        """The client meta is permission-filtered; the file on disk is not."""
        _open(page)
        crud.assert_client_meta_matches_disk(page, DOCTYPE)

    def test_every_company_field_survives_read_modify_read(self, page: Page):
        """READ -> MODIFY -> READ: after == typed AND after != before, for every field.

        The `!= before` half is the load-bearing one. Without it, any field whose new
        value happened to equal its old one or its default could not fail.
        """
        _open(page)
        fields = [
            f for f in crud.plan_fields(DOCTYPE, EXCLUDED)
            if f["fieldname"] not in COVERED_ELSEWHERE
            and gestures.field_is_on_screen(page, f["fieldname"])
        ]
        assert len(fields) >= 15, f"only {len(fields)} fields reachable — the form did not render"

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
                f"{name}: the value never moved off {before[name]!r} — this assertion "
                "could not have failed, so it proves nothing"
            )

    def test_the_logo_goes_on_comes_off_and_goes_on_again(self, page: Page):
        """User: *"IF YOU ATTACH and CLEAR and ATTACH IT SHOULD APPEAR READABLE"*.

        One uninterrupted sequence on one form instance, and the URL is read back
        AFTER a reload — the client renders optimistically, so an unreloaded read
        cannot see a server-side overwrite.
        """
        _open(page)
        gestures.attach_file(page, "logo", LOGO_A)
        _reopen(page)
        first = gestures.attached_url(page, "logo")
        assert first, "nothing readable on the form after attaching the first logo"

        gestures.clear_attachment(page, "logo")
        _reopen(page)
        assert gestures.attach_is_empty(page, "logo"), (
            "after Clear the Attach button is not back on screen"
        )
        assert gestures.attached_url(page, "logo") is None, (
            "after Clear the form still shows a file"
        )

        gestures.attach_file(page, "logo", LOGO_B)
        _reopen(page)
        second = gestures.attached_url(page, "logo")
        assert second, "nothing readable on the form after re-attaching"
        assert second != first, (
            f"the re-attached logo reads as the FIRST file ({second!r}) — the new "
            "upload never reached the field"
        )

    def test_the_banner_goes_on_comes_off_and_goes_on_again(self, page: Page):
        """The same three-step sequence on the second file field, uninterrupted."""
        _open(page)
        gestures.attach_file(page, "banner", BANNER_A)
        _reopen(page)
        first = gestures.attached_url(page, "banner")
        assert first, "nothing readable on the form after attaching the first banner"

        gestures.clear_attachment(page, "banner")
        _reopen(page)
        assert gestures.attach_is_empty(page, "banner"), (
            "after Clear the Attach button is not back on screen"
        )

        gestures.attach_file(page, "banner", BANNER_B)
        _reopen(page)
        second = gestures.attached_url(page, "banner")
        assert second and second != first, (
            f"the re-attached banner reads as {second!r}, the first was {first!r}"
        )

    def test_vat_percent_appears_only_for_a_VAT_company(self, page: Page):
        """A `depends_on` field: absent must be an ASSERTION, or hiding it passes."""
        _open(page)
        gestures.fill(page, "vat_registration", "NON-VAT")
        assert not gestures.field_is_on_screen(page, "vat_percent"), (
            "VAT (%) is on screen for a NON-VAT company"
        )
        gestures.fill(page, "vat_registration", "VAT")
        assert gestures.field_is_on_screen(page, "vat_percent"), (
            "VAT (%) never appeared after choosing VAT — the field is unreachable"
        )
        gestures.fill(page, "vat_percent", 8)
        gestures.save_form(page)
        _reopen(page)
        assert crud._as_number(gestures.read_value(page, "vat_percent")) == 8

    def test_each_billing_mode_shows_and_keeps_only_its_own_fields(self, page: Page):
        """Four mutually exclusive modes: each shows ITS field and hides the others.

        They cannot all be on screen at once, so one pass can never cover them —
        this is the test that stops `billing_mode` from narrowing to whichever
        mode the record happened to be in.
        """
        owned = {
            "Subscription": "subscription_fee",
            "Percentage": "commission_percent",
            "Per Booking": "open_play_fee_per_participant",
        }
        for mode, mine in owned.items():
            _open(page)
            gestures.fill(page, "billing_mode", mode)
            assert gestures.field_is_on_screen(page, mine), (
                f"{mode}: its own field {mine} is not on screen"
            )
            for other_mode, theirs in owned.items():
                if other_mode != mode:
                    assert not gestures.field_is_on_screen(page, theirs), (
                        f"{mode}: {theirs} belongs to {other_mode} and is on screen"
                    )
            if mode == "Per Booking":
                if not gestures.grid_row_count(page, "booking_fee_tiers"):
                    idx = gestures.grid_add_row(page, "booking_fee_tiers")
                    gestures.grid_fill(
                        page, "booking_fee_tiers", idx, {"from_count": 1, "fee": 25}
                    )
            gestures.fill(page, mine, 7)
            gestures.save_form(page)
            _reopen(page)
            assert crud._as_number(gestures.read_value(page, mine)) == 7, (
                f"{mode}: {mine} did not survive the reload"
            )

    def test_office_hours_goes_from_none_to_one_to_many(self, page: Page):
        """NONE, then ONE, then MANY — ONE is the state every new record is in."""
        _open(page)
        assert gestures.grid_row_count(page, "office_hours") == 0, (
            "the probe company did not start with an empty Office Hours grid"
        )
        first = gestures.grid_add_row(page, "office_hours")
        gestures.grid_fill(page, "office_hours", first, {"day": "Monday", **HOURS})
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "office_hours") == 1

        for day in ("Tuesday", "Wednesday"):
            idx = gestures.grid_add_row(page, "office_hours")
            gestures.grid_fill(page, "office_hours", idx, {"day": day, **HOURS})
        gestures.save_form(page)
        _reopen(page)
        assert gestures.grid_row_count(page, "office_hours") == 3, (
            "the grid did not keep all three days after a reload"
        )

    def test_the_slug_and_company_code_refuse_to_change_after_the_first_save(self, page: Page):
        """A refusal test must first prove the save path is ALIVE.

        Otherwise a form that silently stopped saving passes every "it did not
        change" assertion in this file.
        """
        _open(page)
        alive = "CRUD Probe Courts still saving"
        gestures.fill(page, "company_name", alive)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "company_name") == alive, (
            "the form is not saving at all, so no refusal below would mean anything"
        )

        assert not page.locator(f"{SLUG_CTRL} input").first.is_visible(), (
            "the slug is typeable on a saved company — it names the document"
        )
        assert gestures.read_value(page, "slug") == SLUG, (
            "the slug is not readable on the form at all"
        )

        gestures.fill(page, "company_code", "ZZTOP")
        page.keyboard.press("Control+s")
        modal = page.locator(".modal.show").first
        modal.wait_for(state="visible", timeout=30000)
        gestures.dismiss_modals(page)
        gestures.fill(page, "company_code", CODE)
        gestures.save_form(page)
        _reopen(page)
        assert gestures.read_value(page, "company_code") == CODE, (
            "the company code moved despite the controller calling it immutable"
        )

    def test_the_media_tab_photo_count_goes_none_one_many(self, page: Page):
        """The photo COUNT boundary. ONE is the state B56 shipped broken.

        Captions, Remove, Make cover and the banner seam are already owned by
        test_media_tab.py and are deliberately not rewritten here.
        """
        _open(page)
        gestures.check(page, "allow_company_gallery_management", True)
        gestures.save_form(page)
        _open_media_tab(page)
        assert page.locator(f"{MEDIA_TAB} {MEDIA_TILE}").count() == 0

        _drop_media(page, [LOGO_A])
        _save_media(page, 1)
        _reopen(page)
        _open_media_tab(page)
        assert page.locator(f"{MEDIA_TAB} {MEDIA_TILE}").count() == 1, (
            "one photo is not one tile — the state every facility is in first"
        )

        _drop_media(page, [LOGO_B, BANNER_A])
        _save_media(page, 3)
        _reopen(page)
        _open_media_tab(page)
        assert page.locator(f"{MEDIA_TAB} {MEDIA_TILE}").count() == 3

    def test_the_onboarding_and_branches_tabs_render(self, page: Page):
        """Hand-written, non-field assertions: these two tabs carry no fields."""
        _open(page)
        for tab in ("onboarding_tab", "branches_tab"):
            link = page.locator(f"button.nav-link[data-fieldname='{tab}']")
            assert link.count(), f"{tab} is not on the form for the platform seat"
            link.first.click()
        assert page.locator("[data-fieldname='branches_html']").first.is_visible()
