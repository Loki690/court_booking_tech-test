"""
E2E — "Book now · <opening hours>" and "Opens 6 AM" (Backlog B48, 2026-09-09).

The user: *"'Open now' label should be changed. It should be 'Book now' then time
slot of it like … usually it just says 24/7 or Sun-Thu 8am-9pm, Fri-Sat
8am-12MN"*, and *"Book now → 'Opens 6 AM'"*. Two customer faces print the same
server strings — the marketplace card (`/find-court`, JS) and the `/book` branch
chooser (Jinja) — plus the selected branch's hours under its name on `/book`.

Every assertion here is the rendered STRING, at rest, on a card a person can read
(B47's rule: `.count("–") == 1` passed on the wrong format for months). The
strings are FIXED by the seed — AYALA-bgc/makati 06:00–22:00 all week, QCSM-timog
closed on Sundays, the e2e-fast rig 00:00–23:59 — so nothing here depends on the
day the suite runs, except timog's badge, which is computed from the pretend day.

LEDGER: THIS FILE CLAIMS NO DATE. It books nothing. It MOVES THE TEST CLOCK
(`testing.set_test_clock_offset`) to 12:00 and to 23:00 of TODAY — the
same-calendar-day rule — and clears it in teardown.
"""
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect

from helpers.worker_routing import bench_json

AYALA = "ayala-courts"
QCSM = "qc-smash"
E2EF = "e2e-fast"

BGC_HOURS = "Daily 6 AM – 10 PM"
TIMOG_HOURS = "Mon–Sat 6 AM – 10 PM · Sun closed"
E2EF_HOURS = "24/7"
SHOTS = Path(__file__).resolve().parent.parent / "screenshots"

STATUS = "[data-testid='branch-status']"
HOURS = "[data-testid='branch-hours']"


@pytest.fixture(scope="module", autouse=True)
def real_clock_afterwards():
    yield
    bench_json("court_booking_tech.testing.set_test_clock_offset", [0])


def _pin_clock_to(hour: int) -> datetime:
    """Pretend-now = <hour>:30 of the SERVER's today. Asserted, never trusted.
    The half hour, not the top of it: the offset is whole MINUTES, so a target
    of hh:00 rounds to hh−1:59 half the time (measured 2026-09-09)."""
    real = bench_json("court_booking_tech.testing.set_test_clock_offset", [0])
    now = datetime.fromisoformat(real["server_now"])
    target = now.replace(hour=hour, minute=30, second=0, microsecond=0)
    minutes = int(round((target - now).total_seconds() / 60))
    result = bench_json("court_booking_tech.testing.set_test_clock_offset", [minutes])
    pretend = datetime.fromisoformat(result["server_now"])
    assert pretend.date() == now.date() and pretend.hour == hour, result
    return pretend


def _new_context(browser, base_url):
    return browser.new_context(
        base_url=base_url, viewport={"width": 1400, "height": 960}, ignore_https_errors=True
    )


def _marketplace(context):
    page = context.new_page()
    page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
    return page


def _card(page, company, slug):
    """⚠ `branch_slug` is unique PER COMPANY, never globally — scope to both."""
    return page.locator(
        f"#cbt-results .cbt-card[data-company='{company}'][data-slug='{slug}']"
    )


def _readable(page, locator) -> dict:
    """At rest AND unoccluded: computed style plus a hit test at the centre.

    The marketplace card is ONE click target through `a.cbt-card-link::after`,
    a transparent stretched overlay (cbt_portal.css) — so on a card the hit is
    the card's own link, which hides nothing. Measured 2026-09-09: the first
    draft called that "covered". Anything ELSE over the badge still fails."""
    # elementFromPoint is viewport-relative: below the fold it returns null and
    # reads as "covered". A hit test is only meaningful on screen.
    locator.scroll_into_view_if_needed()
    return locator.evaluate(
        """(el) => {
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            const card = el.closest('.cbt-card');
            const ownLink = !!hit && !!card && hit.classList.contains('cbt-card-link')
                && card.contains(hit);
            return {opacity: cs.opacity, visibility: cs.visibility,
                    hit: !!hit && (el === hit || el.contains(hit) || ownLink),
                    hitTag: hit ? hit.tagName + '.' + hit.className : null,
                    text: el.innerText.trim()};
        }"""
    )


def test_at_noon_every_open_card_says_book_now_with_its_hours(browser, base_url):
    pretend = _pin_clock_to(12)
    context = _new_context(browser, base_url)
    try:
        page = _marketplace(context)
        page.screenshot(path=str(SHOTS / "book_now_marketplace.png"), full_page=True)
        for company, slug, hours in (
            (AYALA, "bgc", BGC_HOURS),
            (AYALA, "makati", BGC_HOURS),
            (E2EF, "main", E2EF_HOURS),
        ):
            card = _card(page, company, slug)
            expect(card.locator(STATUS)).to_have_text("Book now")
            expect(card.locator(HOURS)).to_have_text(hours)
            readable = _readable(page, card.locator(STATUS))
            assert readable["opacity"] == "1" and readable["visibility"] == "visible", readable
            assert readable["hit"], f"{company}/{slug}: badge not readable on screen: {readable}"
        timog = _card(page, QCSM, "timog")
        expect(timog.locator(HOURS)).to_have_text(TIMOG_HOURS)
        expect(timog.locator(STATUS)).to_have_text(
            "Opens tomorrow 6 AM" if pretend.weekday() == 6 else "Book now"
        )
        assert page.locator("#cbt-results").inner_text().count("Open now") == 0
    finally:
        context.close()


def test_at_eleven_pm_a_closed_branch_says_when_it_opens(browser, base_url):
    _pin_clock_to(23)
    context = _new_context(browser, base_url)
    try:
        page = _marketplace(context)
        bgc = _card(page, AYALA, "bgc")
        expect(bgc.locator(STATUS)).to_have_text("Opens tomorrow 6 AM")
        expect(bgc.locator(HOURS)).to_have_text(BGC_HOURS)
        assert bgc.locator(STATUS).evaluate("el => el.classList.contains('cbt-badge--closed')")
        # The rig never closes: 23:00 is inside 00:00–24:00.
        expect(_card(page, E2EF, "main").locator(STATUS)).to_have_text("Book now")
    finally:
        context.close()


def test_the_chooser_and_the_booking_page_print_the_same_strings(browser, base_url):
    _pin_clock_to(12)
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/book?c=ayala-courts", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-branch-chooser .cbt-card[data-slug]", timeout=30000)
        bgc = page.locator("#cbt-branch-chooser .cbt-card[data-slug='bgc']")
        expect(bgc.locator(STATUS)).to_have_text("Book now")
        expect(bgc.locator(HOURS)).to_have_text(BGC_HOURS)
        assert page.locator("#cbt-branch-chooser").inner_text().count("Open now") == 0

        page.goto("/book?c=ayala-courts&b=bgc", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        expect(page.locator("#cbt-branch-hours")).to_have_text(BGC_HOURS)
        readable = _readable(page, page.locator("#cbt-branch-hours"))
        assert readable["hit"] and readable["opacity"] == "1", readable
        page.screenshot(path=str(SHOTS / "book_now_branch_page.png"))
    finally:
        context.close()
