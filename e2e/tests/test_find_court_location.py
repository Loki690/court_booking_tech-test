"""E2E — /find-court lists every court without waiting on the location prompt.

Playwright cannot draw the browser's permission bubble, so an init script stands in for it
(helpers/location_prompt.py): it counts every ask, holds the callbacks and reports the permission
state. Visitors are guest contexts built here. LEDGER: THIS FILE CLAIMS NO DATE.
"""
import pytest
from playwright.sync_api import expect

from helpers import gestures
from helpers.auth import portal_login_as
from helpers.location_prompt import (
    CARDS,
    PROMPT_NOTE,
    asks,
    branch_rows,
    card_slugs,
    prompt_stub,
    set_bypass_gps_request,
    stub_installed,
)
from helpers.navigation import goto_single, wait_for_page_load

NOTE = "#cbt-geo-note"
SETTINGS = "CBT Platform Settings"
FIELD = "bypass_gps_request"
BLOCKED_NOTE = "Location off — showing all courts. Log in and save your home pin for nearest-first results."
EMPTY_TEXT = "No courts match your search yet."
MY_CARDS = "#cbt-open a.cbt-card[data-booking], #cbt-past a.cbt-card[data-booking]"
PROFILE_API = "/api/method/court_booking_tech.api.profile.get_my_profile"
CARLA = "cust.carla@example.com"
PIA = "cust.pia@example.com"
NO_GEOLOCATION = (
    "Object.defineProperty(Navigator.prototype, 'geolocation', {get: () => undefined, configurable: true});"
)


@pytest.fixture(autouse=True)
def _switch_off():
    set_bypass_gps_request(0)


def _context(browser, base_url, script):
    context = browser.new_context(
        base_url=base_url, viewport={"width": 1400, "height": 960}, ignore_https_errors=True
    )
    context.add_init_script(script)
    return context


def _guest(browser, base_url, permission="prompt"):
    return _context(browser, base_url, prompt_stub(permission))


def _open(page):
    page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
    assert stub_installed(page), "the prompt stub did not install"
    page.wait_for_selector(CARDS, timeout=15000)


def _loaded_without_asking(page, ready):
    assert stub_installed(page), f"{page.url}: the prompt stub did not install"
    page.wait_for_selector(ready, timeout=30000)
    page.wait_for_load_state("load")
    assert asks(page) == 0, f"{page.url} asked for the visitor's location on load"


def _visit(page, path, ready):
    resp = page.goto(path, wait_until="domcontentloaded", timeout=60000)
    assert resp is not None and resp.ok, f"{path}: HTTP {resp.status if resp else 'no response'}"
    _loaded_without_asking(page, ready)


def _saved_pin(page) -> tuple:
    resp = page.request.get(PROFILE_API, timeout=15000)
    assert resp.ok, f"get_my_profile: HTTP {resp.status}"
    profile = resp.json()["message"]
    return profile.get("home_latitude"), profile.get("home_longitude")


def _matching(rows, needle):
    needle = needle.strip().lower()
    return [
        row["branch_slug"]
        for row in rows
        if needle in " ".join(str(row.get(key) or "") for key in ("branch_name", "company_name", "address_text")).lower()
    ]


def test_an_unanswered_location_prompt_still_lists_every_court(browser, base_url):
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        rows = branch_rows(context.request)
        _open(page)
        assert card_slugs(page) == [row["branch_slug"] for row in rows]
        expect(page.locator(CARDS).first.locator(".cbt-card-title")).to_have_text(rows[0]["branch_name"])
        expect(page.locator(NOTE)).to_have_text(PROMPT_NOTE)
        assert asks(page) == 1, "the visitor was never asked: is the platform switch left on?"
    finally:
        context.close()


def test_allowing_location_later_reorders_the_list_nearest_first(browser, base_url):
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        rows = branch_rows(context.request)
        pinned = [row for row in rows if row.get("latitude") and row.get("longitude")]
        assert len(pinned) >= 2, f"a reorder needs two branches with coordinates: {rows}"
        lat, lng = pinned[-1]["latitude"], pinned[-1]["longitude"]
        nearest = branch_rows(context.request, lat=lat, lng=lng)
        expected = [row["branch_slug"] for row in nearest]
        before = [row["branch_slug"] for row in rows]
        assert expected != before, f"the two orders match, the reorder would prove nothing: {before}"

        _open(page)
        assert card_slugs(page) == before
        page.evaluate(
            "([lat, lng]) => window.__geo.success("
            "{coords: {latitude: lat, longitude: lng, accuracy: 20}, timestamp: Date.now()})",
            [lat, lng],
        )
        page.wait_for_function(
            "(want) => JSON.stringify(Array.from(document.querySelectorAll("
            "'#cbt-results .cbt-card[data-slug]'), (e) => e.dataset.slug)) === JSON.stringify(want)",
            arg=expected,
            timeout=15000,
        )
        expect(page.locator(NOTE)).to_be_hidden()
        expect(page.locator(CARDS).first.locator(".cbt-distance")).to_have_text(
            f"{nearest[0]['distance_km']:.1f} km"
        )
    finally:
        context.close()


def test_blocking_location_later_keeps_the_list_and_says_so(browser, base_url):
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        _open(page)
        before = card_slugs(page)
        expect(page.locator(NOTE)).to_have_text(PROMPT_NOTE)
        page.evaluate(
            "() => window.__geo.error({code: 1, message: 'User denied Geolocation', "
            "PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3})"
        )
        expect(page.locator(NOTE)).to_have_text(BLOCKED_NOTE)
        assert card_slugs(page) == before
    finally:
        context.close()


def test_a_visitor_who_already_allowed_location_is_not_left_waiting(browser, base_url):
    context = _guest(browser, base_url, "granted")
    try:
        page = context.new_page()
        rows = branch_rows(context.request)
        _open(page)
        assert card_slugs(page) == [row["branch_slug"] for row in rows]
        assert asks(page) == 1
    finally:
        context.close()


def test_a_browser_without_geolocation_lists_every_court(browser, base_url):
    context = _context(browser, base_url, NO_GEOLOCATION)
    try:
        page = context.new_page()
        rows = branch_rows(context.request)
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        assert page.evaluate("() => navigator.geolocation === undefined"), "geolocation is still present"
        page.wait_for_selector(CARDS, timeout=15000)
        assert card_slugs(page) == [row["branch_slug"] for row in rows]
        expect(page.locator(NOTE)).to_have_count(1)
        expect(page.locator(NOTE)).to_be_hidden()
    finally:
        context.close()


def test_searching_while_the_location_prompt_waits(browser, base_url):
    """None, one, many: the search narrows the list, restores it and empties it while the bubble waits."""
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        rows = branch_rows(context.request)
        every = [row["branch_slug"] for row in rows]
        assert len(rows) >= 2, f"the many case needs two courts: {rows}"
        one = next((row["branch_name"] for row in rows if len(_matching(rows, row["branch_name"])) == 1), None)
        assert one, f"no branch name matches exactly one court: {rows}"
        _open(page)
        assert card_slugs(page) == every
        box = page.locator("#cbt-search")

        box.click()
        box.press_sequentially(one)
        expect(page.locator(CARDS)).to_have_count(1)
        assert card_slugs(page) == _matching(rows, one)

        box.press("Control+A")
        box.press("Backspace")
        expect(box).to_have_value("")
        expect(page.locator(CARDS)).to_have_count(len(rows))
        assert card_slugs(page) == every

        box.press_sequentially("zzqx no such court")
        expect(page.locator("#cbt-results .cbt-empty")).to_have_text(EMPTY_TEXT)
        expect(page.locator(CARDS)).to_have_count(0)
        expect(page.locator(NOTE)).to_have_text(PROMPT_NOTE)
        assert asks(page) == 1
    finally:
        context.close()


def test_no_other_portal_page_asks_for_location_on_load(browser, base_url):
    """With asking allowed, only /find-court asks, and only a visitor without a saved pin:
    guest pages, a customer's own pages, then /find-court for Carla (no pin) and Pia (pin)."""
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        _visit(page, "/signup", "#cbt-signup-form")
        _visit(page, "/login", "#login_email")
        _visit(page, "/privacy-policy", ".cbt-legal-page")
        _visit(page, "/terms-of-service", ".cbt-legal-page")
        _visit(page, "/book?c=ayala-courts", "#cbt-branch-chooser .cbt-chooser-card")
        _visit(page, "/book?c=ayala-courts&b=bgc", "#cbt-grid .cbt-matrix, #cbt-grid .cbt-empty")

        portal_login_as(page, CARLA)
        pin = _saved_pin(page)
        assert not any(pin), f"{CARLA} carries a saved pin {pin}; the seed keeps her pin-less, so this bench is not the seeded one"
        _visit(page, "/my-profile", "#cbt-profile-map.leaflet-container")
        _visit(page, "/my-bookings", MY_CARDS)
        first = page.locator(MY_CARDS).first
        booking = first.get_attribute("data-booking")
        first.click()
        page.wait_for_url(f"**/my-bookings/{booking}", timeout=30000)
        expect(page.locator("#cbt-billing-card"), f"booking {booking} shows no billing card").to_be_visible(timeout=30000)
        _loaded_without_asking(page, "#cbt-status-card")
        with context.expect_page() as opened:
            page.locator("#cbt-print-link").click()
        statement = opened.value
        statement.wait_for_load_state("domcontentloaded")
        _loaded_without_asking(statement, ".cbt-statement")
        statement.close()

        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        assert stub_installed(page), "the prompt stub did not install"
        page.wait_for_selector(CARDS, timeout=30000)
        expect(page.locator(NOTE)).to_have_text(PROMPT_NOTE)
        assert asks(page) == 1, f"{CARLA} has no saved pin and was not asked"

        portal_login_as(page, PIA)
        pin = _saved_pin(page)
        assert all(pin), f"{PIA} has no saved pin {pin}; the seed pins her home, so this bench is not the seeded one"
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        assert stub_installed(page), "the prompt stub did not install"
        expect(page.locator(CARDS).first.locator(".cbt-distance")).to_be_visible(timeout=30000)
        page.wait_for_load_state("load")
        assert asks(page) == 0, f"{PIA} has a saved pin and was still asked"
    finally:
        context.close()


def test_the_platform_switch_never_asks_for_location(page, browser, base_url):
    """The platform seat ticks Bypass GPS Request on the settings form; then neither
    the seat itself (logged in, no pin) nor a guest is ever asked."""
    try:
        goto_single(page, SETTINGS)
        page.wait_for_function(
            "() => window.cur_frm && cur_frm.doc && cur_frm.doc.doctype === 'CBT Platform Settings'",
            timeout=30000,
        )
        control = page.locator(f'.frappe-control[data-fieldname="{FIELD}"]')
        expect(control).to_contain_text("Bypass GPS Request")
        box = control.get_by_role("checkbox", name="Bypass GPS Request")
        expect(box).not_to_be_checked()
        gestures.fill(page, FIELD, True)
        gestures.save_form(page)
        page.reload(wait_until="domcontentloaded")
        wait_for_page_load(page)
        expect(box).to_be_checked(timeout=30000)

        page.context.add_init_script(prompt_stub("prompt"))
        rows = branch_rows(page.request)
        _open(page)
        assert card_slugs(page) == [row["branch_slug"] for row in rows]
        expect(page.locator(NOTE)).to_have_text(
            "Showing all courts. Set your home pin in My profile for nearest-first results."
        )
        assert asks(page) == 0, "the switch is on and the browser was still asked"

        context = _guest(browser, base_url)
        try:
            guest = context.new_page()
            rows = branch_rows(context.request)
            _open(guest)
            assert card_slugs(guest) == [row["branch_slug"] for row in rows]
            expect(guest.locator(NOTE)).to_have_text(
                "Showing all courts. Log in and save your home pin for nearest-first results."
            )
            assert asks(guest) == 0, "the switch is on and the guest's browser was still asked"
        finally:
            context.close()
    finally:
        set_bypass_gps_request(0)
