"""CRUD SAFEGUARD — CBT Platform Settings: READ -> MODIFY(ALL FIELDS) -> READ.

⛔ A SINGLE: this is site-global state every other file on this worker reads.
Each test restores immediately and ASSERTS the restore; see section-30.
"""

import pytest
from playwright.sync_api import Page, Playwright

from helpers import crud, gestures, schema
from helpers.board import api_csrf, platform_api
from helpers.navigation import goto_single, wait_for_page_load

DOCTYPE = "CBT Platform Settings"

EXPECTED_DATA_FIELDS = 24

COVERED_ELSEWHERE = {}

EXCLUDED = {
    "enable_turnstile": (
        "Turning it on makes a LIVE Cloudflare round trip in _validate_turnstile, "
        "so a test that ticks it depends on the network. User ruling 2026-09-11."
    ),
    "turnstile_site_key": (
        "Only reachable while enable_turnstile is on, which this file refuses to "
        "switch on. User ruling 2026-09-11."
    ),
    "turnstile_secret_key": (
        "A Password field: it reads back masked by design, so type-X-read-X is "
        "impossible on it. User ruling 2026-09-11."
    ),
}
EXPECTED_EXCLUDED = frozenset(
    {"enable_turnstile", "turnstile_site_key", "turnstile_secret_key"}
)

SUPPLIED = {}


def _tracked():
    return [
        f["fieldname"] for f in schema.data_fields(DOCTYPE)
        if f["fieldname"] not in EXCLUDED
    ]


def _write(api, csrf, values: dict):
    return api.put(
        f"/api/resource/{DOCTYPE}/{DOCTYPE}",
        headers={"X-Frappe-CSRF-Token": csrf},
        data=values,
        timeout=30000,
    )


@pytest.fixture(scope="module", autouse=True)
def settings_snapshot(playwright: Playwright):
    """Snapshot every tracked field and put it back, whatever the tests did.

    Registered at SETUP so a killed run still restores; the in-test restore is the
    real isolation, this is only the crash net.
    """
    api = platform_api(playwright)
    csrf = api_csrf(api)
    got = api.get(f"/api/resource/{DOCTYPE}/{DOCTYPE}", timeout=30000)
    assert got.ok, f"could not read the platform settings: {got.status}"
    doc = got.json()["data"]
    saved = {name: doc.get(name) for name in _tracked()}
    yield saved
    _write(api, csrf, saved)


def _open(page: Page):
    goto_single(page, DOCTYPE)
    page.wait_for_function(
        "() => window.cur_frm && cur_frm.doc && cur_frm.doc.doctype === 'CBT Platform Settings'",
        timeout=30000,
    )


def _reopen(page: Page):
    page.reload(wait_until="domcontentloaded")
    wait_for_page_load(page)
    page.wait_for_function(
        "() => window.cur_frm && cur_frm.doc && cur_frm.doc.doctype === 'CBT Platform Settings'",
        timeout=30000,
    )


@pytest.mark.e2e
class TestPlatformSettingsCrud:
    def test_the_schema_arithmetic_is_still_what_this_file_was_written_against(self):
        """Exclusions pin NAMES and reasons — a count alone permits a swap."""
        crud.assert_total_fields(DOCTYPE, EXPECTED_DATA_FIELDS)
        crud.assert_exclusions_are_pinned(DOCTYPE, EXCLUDED, EXPECTED_EXCLUDED)
        assert len(_tracked()) == 21, (
            f"this file claims to cover 21 of 24 fields, it now covers {len(_tracked())}"
        )

    def test_every_field_on_disk_is_visible_to_this_seat_in_the_browser(self, page: Page):
        """The client meta is permission-filtered; the file on disk is not."""
        _open(page)
        crud.assert_client_meta_matches_disk(page, DOCTYPE)

    def test_every_platform_setting_survives_read_modify_read_and_is_put_back(
        self, page: Page, playwright: Playwright, settings_snapshot
    ):
        """READ -> MODIFY -> READ on a SINGLE, then restore and prove the restore.

        Leaving these mutated would poison every later file on this worker's site:
        six numeric fields on Company and Branch treat 0 as "inherit from here".
        """
        _open(page)
        fields = [
            f for f in crud.plan_fields(DOCTYPE, EXCLUDED)
            if gestures.field_is_on_screen(page, f["fieldname"])
        ]
        assert len(fields) >= 18, f"only {len(fields)} fields reachable — the form did not render"

        before = {f["fieldname"]: gestures.read_value(page, f["fieldname"]) for f in fields}
        typed = {}
        try:
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
        finally:
            api = platform_api(playwright)
            _write(api, api_csrf(api), settings_snapshot)

        _reopen(page)
        for field in fields:
            name = field["fieldname"]
            assert crud.rendered_matches(field, before[name], gestures.read_value(page, name)), (
                f"{name} was NOT put back: the rest of the lane would read a value "
                "this file left behind"
            )

    def test_the_turnstile_fields_stay_hidden_while_it_is_off(self, page: Page):
        """The three excluded fields are still ACCOUNTED FOR, not merely skipped."""
        _open(page)
        assert gestures.field_is_on_screen(page, "enable_turnstile"), (
            "the Turnstile switch is not on the settings form at all"
        )
        if not gestures.read_value(page, "enable_turnstile"):
            for hidden in ("turnstile_site_key", "turnstile_secret_key"):
                assert not gestures.field_is_on_screen(page, hidden), (
                    f"{hidden} is on screen while Turnstile is switched off"
                )
