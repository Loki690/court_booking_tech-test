"""
E2E — Mobile lane (Backlog B31, batch 1: the instrument, 2026-09-04).

Two phone-shaped browser contexts, on both engines the customer is actually on:

  iphone13  WebKit   390x664 @3x   = Safari, Chrome, Edge AND Firefox on any iPhone
                                     (every iOS browser runs Apple's engine)
  pixel7    Chromium 412x839 @2.6x = Chrome and Edge on Android (both Blink)

Firefox is deliberately NOT run: Playwright's Firefox has no mobile emulation, so a
green there would be a control that cannot fail.

WHAT THIS LANE IS: a regression net for viewport-shaped CSS on the two engines —
page overflow, tap-target size, text size, the checkout reachable — with a
viewport AND a full-page screenshot per page into e2e/screenshots/mobile_<ts>/
(gitignored). The pictures are the deliverable; the assertions are the diagnosis.

WHAT IT IS NOT: iOS Safari's URL-bar `vh` and safe areas; Android font inflation;
content CLIPPED by an overflow:hidden ancestor (scrollWidth cannot see it);
position:fixed elements running off-screen; and the board rows prove the boards
LAY OUT on a phone, not that a human can REACH them through the desk sidebar at
390 px — they are driven through the boards' public show() entry like every other
board row.

Discipline, each learned from a review before the first run:
- Screenshots are taken BEFORE the assertions, so a red row still leaves its picture.
- Every helper asserts its selector matched >= 1 VISIBLE element — a zero-match
  selector is a red, never a silent green — and every row first proves the layout
  viewport really is the phone's width: a page without <meta name="viewport"> lays
  out at ~980 px under emulation and would pass every check by looking tiny.
- Console errors, pageerrors and non-2xx /api bodies are captured per context and
  land in the failure message: the likeliest two-engine failure is WebKit-only JS,
  and a selector timeout without them would be misdiagnosed as CSS.

LEDGER: THIS FILE CLAIMS **+85** (Backlog B50, 2026-09-09): the booking-page QR
row mints a GCash hold as Carla on AYALA-bgc-court-3 by API — one hour per device —
and she cancels it in `finally`. ⚠ A CUSTOMER-path claim must sit at or below **+90**:
the seeded `advance_booking_days` is 90 and `reserve_booking` refuses beyond it
(measured 2026-09-09 — +93 was refused with "up to 90 days ahead"; only the desk
may book further out). Every other row is read-only: the /book rows reuse
file 18's +73/+74 on qc-smash/timog (Sunday-slid, never booked); the checkout row
stops at the GUEST call-to-action — selecting a slot only GETs a quote, no hold is
minted; the board rows load today and write nothing. The /my-bookings rows'
portal_login_as DOES write: a server session for Carla and
auth_state/<worker>/cust_carla_at_example_com.json, exactly as the suite's
customer_page fixture does.

Timing: the suite's autouse verify_session requests the desktop `page` and, on a
dead admin session, performs a full login inside a row's clock. Harmless.
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Page, Playwright, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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

DEVICES = {"iphone13": ("webkit", "iPhone 13"), "pixel7": ("chromium", "Pixel 7")}
OUT = Path(__file__).resolve().parent.parent / "screenshots" / (
    "mobile_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)

# Owns the seeded bookings (seed_test_data.CUSTOMER_EMAIL). Pia has none.
CARLA = "cust.carla@example.com"
OPEN_PLAY_SESSION = "OPS-AYALA-00001"
# Backlog B50: the booking-page QR row's own court × date (see LEDGER above).
QR_COURT = "AYALA-bgc-court-3"
D_QR = (date.today() + timedelta(days=85)).isoformat()
QR_HOUR_BY_DEVICE = {"iphone13": "10:00:00", "pixel7": "11:00:00"}

# Apple HIG: 44 pt. Material's 48 dp is the stricter follow-up — stated, not enforced.
TAP_MIN_PX = 44
# The smallest size the design uses ON PURPOSE is .85rem = 13.6 px (.cbt-small);
# anything under 13 px on a phone is an accident, not a choice.
TEXT_MIN_PX = 13


def _timog_open_date() -> str:
    """File 18's +73, slid off a Sunday (QCSM-timog is seeded closed on Sundays)."""
    day = date.today() + timedelta(days=73)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


D_VIEW = _timog_open_date()
BOOK_URL = f"/book?c=qc-smash&b=timog&d={D_VIEW}"


# ---- browser-side measurement --------------------------------------------
_JS_LABEL = """
    const label = (el) => el.tagName.toLowerCase()
        + (el.id ? '#' + el.id : '')
        + (typeof el.className === 'string' && el.className.trim()
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
    const visible = (el) => el.checkVisibility({visibilityProperty: true, opacityProperty: true});
"""

JS_OVERFLOW = "([rootSel]) => {" + _JS_LABEL + """
    const root = rootSel ? document.querySelector(rootSel) : document.scrollingElement;
    if (!root) return {missing: rootSel};
    const limit = rootSel ? root.getBoundingClientRect().right + 1 : window.innerWidth + 1;
    const scrolls = (el) => {
        for (let a = el.parentElement; a && a !== root.parentElement; a = a.parentElement) {
            const o = getComputedStyle(a).overflowX;
            if (o === 'auto' || o === 'scroll') return true;
        }
        return false;
    };
    const offenders = [];
    for (const el of root.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > limit && !scrolls(el)) {
            offenders.push(label(el) + ' right=' + Math.round(r.right));
            if (offenders.length >= 8) break;
        }
    }
    return {scrollWidth: root.scrollWidth,
            clientWidth: rootSel ? root.clientWidth : window.innerWidth,
            offenders};
}"""

JS_TAP = "([sel, min]) => {" + _JS_LABEL + """
    const els = Array.from(document.querySelectorAll(sel)).filter(visible);
    const small = [];
    for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.width < min || r.height < min)
            small.push({w: Math.round(r.width), h: Math.round(r.height), el: label(el)});
    }
    small.sort((a, b) => a.w * a.h - b.w * b.h);
    return {visible: els.length, below: small.length, smallest: small.slice(0, 3)};
}"""

JS_TEXT = "([sel, min]) => {" + _JS_LABEL + """
    const els = Array.from(document.querySelectorAll(sel)).filter(visible);
    const small = els
        .map((el) => ({px: parseFloat(getComputedStyle(el).fontSize), el: label(el)}))
        .filter((x) => x.px < min)
        .sort((a, b) => a.px - b.px);
    return {visible: els.length, below: small.length, smallest: small.slice(0, 3)};
}"""

JS_INVIEW = "([sel, scrollFirst]) => {" + _JS_LABEL + """
    const el = document.querySelector(sel);
    if (!el) return {missing: true};
    if (scrollFirst) el.scrollIntoView({block: 'nearest', inline: 'nearest'});
    const r = el.getBoundingClientRect();
    return {visible: visible(el), area: r.width * r.height,
            top: Math.round(r.top), bottom: Math.round(r.bottom),
            left: Math.round(r.left), right: Math.round(r.right),
            iw: window.innerWidth, ih: window.innerHeight};
}"""


class _Mobile:
    """One device: contexts, screenshots, the captured log, and the checks."""

    def __init__(self, spec, base_url: str):
        self.key = spec.key
        self.engine = spec.engine
        self.device = spec.device
        self.browser = spec.browser
        self.base_url = base_url
        self.width = spec.device["viewport"]["width"]
        self.log: list[str] = []
        self._contexts = []

    # -- contexts -----------------------------------------------------------
    def new_context(self, **extra):
        ctx = self.browser.new_context(
            **self.device, base_url=self.base_url, ignore_https_errors=True, **extra
        )
        self._contexts.append(ctx)
        return ctx

    def new_page(self, **extra) -> Page:
        page = self.new_context(**extra).new_page()
        page.on(
            "console",
            lambda m: self.log.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning")
            else None,
        )
        page.on("pageerror", lambda e: self.log.append(f"pageerror: {e}"))

        def _response(resp):
            if "/api/" in resp.url and not resp.ok:
                try:
                    body = resp.text()[:600]
                except Exception:
                    body = "<unreadable>"
                self.log.append(f"HTTP {resp.status} {resp.url}: {body}")

        page.on("response", _response)
        return page

    def close(self):
        for ctx in self._contexts:
            try:
                ctx.close()
            except Exception:
                pass

    # -- evidence -----------------------------------------------------------
    def shot(self, page: Page, name: str):
        OUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT / f"{self.key}_{name}_viewport.png"))
        page.screenshot(path=str(OUT / f"{self.key}_{name}_full.png"), full_page=True)

    def _why(self, text: str) -> str:
        tail = "\n  ".join(self.log[-12:]) if self.log else "(no console/pageerror/api errors captured)"
        return f"[{self.key}/{self.engine}] {text}\n  captured: {tail}"

    # -- checks (each asserts >= 1 visible match; a miss is a red) ----------
    def check_viewport(self, page: Page):
        inner = page.evaluate("() => window.innerWidth")
        assert inner == self.width, self._why(
            f"layout viewport is {inner} px, device is {self.width} px — is "
            "<meta name=\"viewport\"> missing on this page? Every other check "
            "would pass by looking tiny."
        )

    def no_overflow(self, page: Page, root: str | None = None):
        result = page.evaluate(JS_OVERFLOW, [root])
        assert "missing" not in result, self._why(f"overflow root {root!r} not on the page")
        assert result["scrollWidth"] <= result["clientWidth"] + 1, self._why(
            f"{root or 'the page'} scrolls sideways: scrollWidth "
            f"{result['scrollWidth']} > width {result['clientWidth']}; widest "
            f"non-scrolling elements: {result['offenders']}"
        )

    def tap_targets(self, page: Page, selector: str, min_px: int = TAP_MIN_PX):
        result = page.evaluate(JS_TAP, [selector, min_px])
        assert result["visible"] >= 1, self._why(f"no visible element matches {selector!r}")
        assert result["below"] == 0, self._why(
            f"{result['below']} of {result['visible']} {selector!r} are under "
            f"{min_px}x{min_px} px; smallest: {result['smallest']}"
        )

    def text_floor(self, page: Page, selector: str, min_px: int = TEXT_MIN_PX):
        result = page.evaluate(JS_TEXT, [selector, min_px])
        assert result["visible"] >= 1, self._why(f"no visible element matches {selector!r}")
        assert result["below"] == 0, self._why(
            f"{result['below']} of {result['visible']} {selector!r} render under "
            f"{min_px} px; smallest: {result['smallest']}"
        )

    def image_decoded(self, page: Page, selector: str, timeout_ms: int = 10000):
        """None when the first match is a decoded image (complete, naturalWidth
        > 0) within the timeout; otherwise the diagnosis string — what the
        element reports plus every captured network/console line, so a blank
        tile on one engine names its own cause."""
        try:
            page.wait_for_function(
                """(sel) => {
                    const img = document.querySelector(sel);
                    return !!img && img.complete && img.naturalWidth > 0;
                }""",
                arg=selector,
                timeout=timeout_ms,
            )
            return None
        except PlaywrightTimeoutError:
            state = page.evaluate(
                """(sel) => {
                    const img = document.querySelector(sel);
                    if (!img) return {missing: true};
                    const r = img.getBoundingClientRect();
                    return {complete: img.complete, naturalWidth: img.naturalWidth,
                            naturalHeight: img.naturalHeight, currentSrc: img.currentSrc,
                            src: img.getAttribute('src'), loading: img.getAttribute('loading'),
                            box: [Math.round(r.width), Math.round(r.height)]};
                }""",
                selector,
            )
            return self._why(f"{selector!r} never decoded within {timeout_ms} ms: {state}")

    def portal_header_only(self, page: Page):
        """B31 batch 2: frappe's website navbar is suppressed on the portal
        pages, and the portal topbar is the one header. Both halves — a bare
        `count == 0` would also pass on an error page."""
        assert page.locator("nav.navbar").count() == 0, self._why(
            "frappe's website navbar is rendering above the portal topbar"
        )
        assert page.locator(".cbt-topbar").is_visible(), self._why("the portal topbar is missing")

    def visible_in_strip(self, page: Page, strip: str, item: str) -> int:
        """How many `item`s are fully inside `strip`'s own scroll box."""
        return page.evaluate(
            """([strip, item]) => {
                const box = document.querySelector(strip);
                if (!box) return -1;
                const b = box.getBoundingClientRect();
                return Array.from(box.querySelectorAll(item)).filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.left >= b.left - 1 && r.right <= b.right + 1 && r.width > 0;
                }).length;
            }""",
            [strip, item],
        )

    def in_viewport(self, page: Page, selector: str, scroll_first: bool = False):
        """The element is visible, has area, and lies inside the viewport —
        without scrolling (a dismiss control) or after scrolling it into view
        inside its own scroller (a call-to-action in a tall modal)."""
        r = page.evaluate(JS_INVIEW, [selector, scroll_first])
        assert not r.get("missing"), self._why(f"{selector!r} is not on the page")
        assert r["visible"] and r["area"] > 0, self._why(
            f"{selector!r} is on the page but not visible (area {r['area']})"
        )
        inside = 0 <= r["top"] and r["bottom"] <= r["ih"] and 0 <= r["left"] and r["right"] <= r["iw"]
        assert inside, self._why(
            f"{selector!r} is off-screen: box top={r['top']} bottom={r['bottom']} "
            f"left={r['left']} right={r['right']} vs viewport {r['iw']}x{r['ih']}"
            + ("" if scroll_first else " (no scrolling allowed for this control)")
        )


@pytest.fixture(scope="module", params=list(DEVICES), ids=list(DEVICES))
def mobile_browser(playwright: Playwright, request):
    """One engine launch per device for the module — pytest-playwright's own
    session `playwright`, never a second sync_playwright().start()."""
    engine, name = DEVICES[request.param]
    browser = getattr(playwright, engine).launch()
    yield SimpleNamespace(
        key=request.param, engine=engine, device=playwright.devices[name], browser=browser
    )
    browser.close()


@pytest.fixture
def mobile(mobile_browser, base_url) -> _Mobile:
    m = _Mobile(mobile_browser, base_url)
    yield m
    m.close()


@pytest.mark.e2e
class TestMobilePortal:
    def test_marketplace_fits_the_phone(self, mobile: _Mobile):
        """`/find-court` as a guest: the front door, on a phone."""
        page = mobile.new_page()
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
        mobile.shot(page, "find_court")

        mobile.check_viewport(page)
        mobile.portal_header_only(page)
        mobile.no_overflow(page)
        mobile.tap_targets(page, "#cbt-type-filters button")
        mobile.tap_targets(page, ".cbt-navlink")
        mobile.text_floor(page, ".cbt-card-title, .cbt-card-sub, .cbt-small:not(.cbt-version)")
        # The title owns its row on a phone: it must not wrap to more than one line.
        title_lines = page.evaluate(
            """() => { const h = document.querySelector('.cbt-topbar h1');
                       const r = h.getBoundingClientRect();
                       return Math.round(r.height / parseFloat(getComputedStyle(h).lineHeight)); }"""
        )
        assert title_lines == 1, mobile._why(f"the page title wraps to {title_lines} lines")

    def test_marketplace_lists_courts_while_the_location_prompt_waits(self, mobile: _Mobile):
        """`/find-court` as a guest who leaves the browser's location bubble unanswered."""
        set_bypass_gps_request(0)
        page = mobile.new_page()
        page.add_init_script(prompt_stub("prompt"))
        expected = [row["branch_slug"] for row in branch_rows(page.request)]
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        assert stub_installed(page), mobile._why("the location-prompt stand-in did not install")
        try:
            page.wait_for_selector(CARDS, timeout=30000)
        finally:
            mobile.shot(page, "find_court_prompt_waiting")

        mobile.check_viewport(page)
        mobile.no_overflow(page)
        shown = card_slugs(page)
        assert shown == expected, mobile._why(f"cards {shown} are not every court in API order {expected}")
        expect(page.locator("#cbt-geo-note")).to_have_text(PROMPT_NOTE)
        mobile.text_floor(page, "#cbt-geo-note")
        count = asks(page)
        assert count == 1, mobile._why(f"the page asked for location {count} times, expected once")

    def test_booking_grid_fits_the_phone(self, mobile: _Mobile):
        """`/book` as a guest, from the poster URL: the date strip and the grid.

        The strip is the defect no assertion caught on the first run: it scrolls
        inside its own box, so page-level overflow was green while ONE chip
        showed and the pressed day sat out of view. Now: at least four chips
        inside the strip's box, and the pressed one among them.
        """
        page = mobile.new_page()
        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        mobile.shot(page, "book_grid")

        mobile.check_viewport(page)
        mobile.portal_header_only(page)
        mobile.no_overflow(page)
        shown = mobile.visible_in_strip(page, "#cbt-dates", ".cbt-date")
        assert shown >= 4, mobile._why(f"only {shown} date chip(s) are inside the strip's box")
        pressed_shown = mobile.visible_in_strip(page, "#cbt-dates", ".cbt-date[aria-pressed='true']")
        assert pressed_shown == 1, mobile._why("the pressed date chip is scrolled out of the strip")
        mobile.tap_targets(page, ".cbt-date")
        mobile.tap_targets(page, ".cbt-datenav, .cbt-datejump")
        mobile.tap_targets(page, ".cbt-slot:not([disabled])")
        mobile.text_floor(page, ".cbt-card-title, .cbt-card-sub, .cbt-small:not(.cbt-version)")

        # B33: the matrix is the one thing on this page allowed to be wider
        # than the phone, and it must carry that width INSIDE its own box —
        # which is the whole reason the page-level check above stays green with
        # three courts on a 390px screen. The wrapper is the scroller; the page
        # never is.
        box = page.evaluate(
            """() => {
                const el = document.getElementById('cbt-matrix-wrap');
                if (!el) return null;
                return {scroll: el.scrollWidth, client: el.clientWidth,
                        overflow: getComputedStyle(el).overflowX,
                        right: Math.round(el.getBoundingClientRect().right)};
            }"""
        )
        assert box, mobile._why("the matrix wrapper is not on the page")
        assert box["overflow"] in ("auto", "scroll"), mobile._why(
            f"the matrix wrapper does not scroll: overflow-x is {box['overflow']!r}"
        )
        assert box["right"] <= page.evaluate("() => window.innerWidth") + 1, mobile._why(
            f"the matrix wrapper itself overhangs the phone: {box}"
        )

        # B32 + B37 (batch 2): both disclosures ship CLOSED — the plan's grid is
        # hidden and the map iframe has no src — and each is a thumb target on
        # its own (one selector per call: a grouped selector is green as soon as
        # ANY member renders). Then open both, the way a customer does, and
        # measure the page again: a Google frame ignoring its 100% width is the
        # one thing in B37 that can break the phone, and a closed <details>
        # hides it from the overflow check.
        sketch = page.locator("#cbt-sketch")
        sketch_grid = page.locator("#cbt-sketch .cbt-sketch-grid")
        expect(sketch_grid).to_be_hidden()
        assert page.locator("#cbt-where-map").get_attribute("src") is None, mobile._why(
            "the map loaded before anyone asked for it"
        )
        mobile.tap_targets(page, "#cbt-sketch summary")
        mobile.tap_targets(page, "#cbt-where-details summary")
        mobile.tap_targets(page, "#cbt-where-directions")

        closed_height = sketch.bounding_box()["height"]
        sketch.locator("summary").click()
        expect(sketch_grid).to_be_visible()
        assert sketch.bounding_box()["height"] > closed_height, mobile._why(
            "opening the sketch did not grow it"
        )
        page.locator("#cbt-where-details summary").click()
        expect(page.locator("#cbt-where-map")).to_have_attribute("src", re.compile(r"output=embed"))
        mobile.shot(page, "book_disclosures_open")
        mobile.no_overflow(page)

    def test_checkout_reachable_on_the_phone(self, mobile: _Mobile):
        """Tap a slot, reach the bar, open the checkout, find the way in AND out.

        Stops at the GUEST call-to-action: selecting a slot only GETs a quote,
        nothing is reserved. The close button must be inside the viewport with
        NO scrolling (a customer must always be able to dismiss); the CTA may
        sit lower in a tall modal that scrolls internally, so it is asserted
        reachable after scrolling into view.
        """
        page = mobile.new_page()
        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot:not([disabled])", timeout=30000)
        mobile.check_viewport(page)

        page.locator(".cbt-matrix .cbt-slot:not([disabled])").first.click()
        bar = page.locator("#cbt-checkoutbar")
        bar.wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.getElementById('cbt-selection-total').textContent.trim() !== '₱0'",
            timeout=15000,
        )
        mobile.shot(page, "book_selected")
        mobile.in_viewport(page, "#cbt-review")

        page.locator("#cbt-review").click()
        page.locator("#cbt-checkout").wait_for(state="visible", timeout=15000)
        page.wait_for_selector("#cbt-quote-lines tr", timeout=15000)
        # The GCash QR tile (seeded 2026-09-04) must actually DECODE — the first
        # full run showed the tile as a blank square on WebKit while Blink drew
        # it. A `src` alone proves nothing (file 18's banner lesson).
        tile_problem = mobile.image_decoded(page, "#cbt-checkout img.cbt-channel-qr")
        mobile.shot(page, "book_checkout")
        assert tile_problem is None, tile_problem
        # 2026-09-04 ruling: QC Smash's GCash carries its own note, so the
        # company-level text must NOT print under the cards (its fallback is
        # pinned in the next row, on a channel without a note).
        note = page.locator("#cbt-channels .cbt-channel-note").first
        assert note.inner_text().strip(), mobile._why("the seeded GCash note is missing from the card")
        assert page.locator("#cbt-instructions").inner_text().strip() == "", mobile._why(
            "the company-level payment text prints beside a channel that carries its own note"
        )
        mobile.in_viewport(page, "#cbt-checkout-close")
        mobile.in_viewport(page, "#cbt-login-cta", scroll_first=True)

        # Tap the WORDS under the tile (Backlog B50: the thumb lands there, and
        # until 2026-09-09 they were outside the control): the lightbox opens
        # with the same image, and can be closed.
        page.locator("#cbt-checkout .cbt-channel-qr-hint").first.click()
        page.locator("#cbt-qr-modal").wait_for(state="visible", timeout=10000)
        large_problem = mobile.image_decoded(page, "#cbt-qr-image")
        mobile.shot(page, "book_qr_lightbox")
        assert large_problem is None, large_problem
        mobile.in_viewport(page, "#cbt-qr-close")
        page.locator("#cbt-qr-close").click()
        page.locator("#cbt-qr-modal").wait_for(state="hidden", timeout=10000)

        mobile.tap_targets(
            page,
            "#cbt-login-cta, #cbt-checkout-close, #cbt-checkout .cbt-channel-qr-wrap[data-qr-zoom]",
        )

    def test_checkout_falls_back_to_the_company_note(self, mobile: _Mobile):
        """The negative control for the note rule: the e2e-fast rig's GCash
        carries NO note, so the company-level text must still print — a rule
        that blanked every company note would pass the row above and fail here.
        Guest, no reserve. The date is PINNED — date-free lands on today and
        fails by the clock once the rig's last slot has started."""
        page = mobile.new_page()
        day = (date.today() + timedelta(days=73)).isoformat()
        page.goto(
            f"/book?c=e2e-fast&b=main&d={day}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_selector(".cbt-matrix .cbt-slot:not([disabled])", timeout=30000)
        page.locator(".cbt-matrix .cbt-slot:not([disabled])").first.click()
        page.locator("#cbt-checkoutbar").wait_for(state="visible", timeout=15000)
        page.locator("#cbt-review").click()
        page.locator("#cbt-checkout").wait_for(state="visible", timeout=15000)
        page.wait_for_selector("#cbt-quote-lines tr", timeout=15000)
        mobile.shot(page, "book_checkout_fallback")

        assert page.locator("#cbt-channels .cbt-channel").count() >= 1, mobile._why(
            "the rig has no transfer channel card at checkout"
        )
        assert page.locator("#cbt-channels .cbt-channel-note").count() == 0, mobile._why(
            "the rig's channel now carries a note — this row needs a channel WITHOUT one"
        )
        text = page.locator("#cbt-instructions").inner_text().strip()
        assert "pay at the desk" in text, mobile._why(
            f"the company-level fallback text is missing at checkout: {text!r}"
        )

    def _login_carla(self, mobile: _Mobile) -> Page:
        """Carla owns the seeded bookings. Her password is seeded since
        2026-09-04 (before that the login 401'd on both engines). A worker CLONE
        is restored without the seed step, so a parallel run of these rows
        would need a `portal_customer_seat` fixture in the `platform_seat`
        shape — not added until a clone run needs it."""
        from helpers.auth import portal_login_as

        page = mobile.new_page()
        portal_login_as(page, CARLA)
        return page

    def _open_my_bookings(self, mobile: _Mobile, page: Page):
        page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
        cards = page.locator("#cbt-open .cbt-card[data-status], #cbt-past .cbt-card[data-status]")
        cards.first.wait_for(state="visible", timeout=30000)
        assert cards.count() >= 1, mobile._why(
            f"{CARLA} has no bookings on /my-bookings — the seed no longer gives her any"
        )
        return cards

    def test_my_bookings_fits_the_phone(self, mobile: _Mobile):
        """The customer's list, as Carla."""
        page = self._login_carla(mobile)
        self._open_my_bookings(mobile, page)
        mobile.shot(page, "my_bookings")
        mobile.check_viewport(page)
        mobile.portal_header_only(page)
        mobile.no_overflow(page)
        mobile.tap_targets(page, ".cbt-navlink")
        mobile.text_floor(page, ".cbt-card-title, .cbt-card-sub, .cbt-badge")

    def test_booking_detail_and_log_out_on_the_phone(self, mobile: _Mobile):
        """One booking's page, then the way OUT: frappe's navbar (and its logout
        menu) is gone from the portal pages, so the topbar's Log out must really
        end the session — asserted server-side, not by a link being visible."""
        from helpers.auth import get_logged_user

        page = self._login_carla(mobile)
        cards = self._open_my_bookings(mobile, page)
        cards.first.click()
        page.wait_for_url("**/my-bookings/**", timeout=30000)
        page.wait_for_function(
            "() => (document.getElementById('cbt-court') || {}).textContent.trim() !== ''",
            timeout=30000,
        )
        mobile.shot(page, "booking_detail")
        mobile.check_viewport(page)
        mobile.portal_header_only(page)
        mobile.no_overflow(page)

        mobile.in_viewport(page, "#cbt-logout")
        page.locator("#cbt-logout").click()
        page.wait_for_url("**/find-court**", timeout=30000)
        page.wait_for_selector(".cbt-topbar", timeout=30000)
        mobile.shot(page, "logged_out")
        assert get_logged_user(page) == "Guest", mobile._why("Log out did not end the session")
        assert page.locator("#cbt-logout").count() == 0, mobile._why("Log out still renders for a guest")
        assert page.locator(".cbt-navlink[href='/login']").count() == 1, mobile._why(
            "the logged-out topbar does not offer Log in"
        )

    def test_the_booking_page_qr_opens_on_the_phone(self, mobile: _Mobile):
        """Backlog B50 (2026-09-09). The user, on an iPhone: *"Booking on Safari
        Mobile could not open QR Code"* — a tap did nothing. The page a customer
        actually scans from is /my-bookings/<name>, whose card has NO <label>
        around it, so nothing but the control itself can open the lightbox there.
        The tap lands on the WORDS under the tile — the target a thumb finds —
        on both engines. Claims **+85** (LEDGER above — a customer hold cannot sit
        past +90); the hold is Carla's own and she cancels it herself in finally,
        as a customer can.
        """
        from helpers import portal

        page = self._login_carla(mobile)
        booking = portal.reserve(page, QR_COURT, D_QR, QR_HOUR_BY_DEVICE[mobile.key])["booking"]
        try:
            page.goto(f"/my-bookings/{booking}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(
                "#cbt-pay-channel .cbt-channel-qr-wrap[data-qr-zoom]", timeout=30000
            )
            mobile.check_viewport(page)
            tile_problem = mobile.image_decoded(page, "#cbt-pay-channel img.cbt-channel-qr")
            mobile.shot(page, "booking_detail_qr")
            assert tile_problem is None, tile_problem
            mobile.tap_targets(page, "#cbt-pay-channel .cbt-channel-qr-wrap[data-qr-zoom]")

            page.locator("#cbt-pay-channel .cbt-channel-qr-hint").click()
            page.locator("#cbt-qr-modal").wait_for(state="visible", timeout=10000)
            expect(page.locator("#cbt-qr-title")).to_have_text("GCash")
            large_problem = mobile.image_decoded(page, "#cbt-qr-image")
            mobile.shot(page, "booking_detail_qr_lightbox")
            assert large_problem is None, large_problem
            mobile.in_viewport(page, "#cbt-qr-close")
            page.locator("#cbt-qr-close").click()
            page.locator("#cbt-qr-modal").wait_for(state="hidden", timeout=10000)
        finally:
            portal.post(page, "court_booking_tech.api.portal.cancel_my_booking", {"name": booking})

    def test_court_board_lays_out_on_the_phone(self, mobile: _Mobile):
        """Staff Court Board (ruled into B31's scope). Overflow is measured on the
        BOARD (`.cbt-board`), not the document: the desk shell around it is
        frappe's, and a document-level red there is not ours to fix. The desk
        shell's own overflow is recorded as an observation in the log only."""
        from helpers.board import BGC_BRANCH, load_board
        from helpers.auth import PLATFORM_STATE
        from helpers.worker_routing import auth_state_file

        page = mobile.new_page(storage_state=str(auth_state_file(PLATFORM_STATE)))
        load_board(page, BGC_BRANCH, date.today().isoformat())
        mobile.shot(page, "court_board")
        mobile.check_viewport(page)
        shell = page.evaluate(JS_OVERFLOW, [None])
        mobile.log.append(f"desk shell (observation): {shell}")
        mobile.no_overflow(page, root=".cbt-board")

    def test_the_court_board_can_be_OPERATED_on_the_phone(self, mobile: _Mobile):
        """2026-09-05, the user: *"desk/cbt-court-board is not mobile friendly"*.

        The row above proved the board LAYS OUT — no sideways scroll — and
        stopped there, which is why the board could stay unusable while it
        stayed green. MEASURED before a line was changed, on both engines:

          * 21 of 48 slot cells were 124x**42**, under Apple's 44 pt floor;
          * the date nav's prev/next arrows were **22x29**;
          * ALL NINE Pending Payments buttons — Accept / Reject / Open, the
            money action on this screen — were 52x**18**;
          * 75 elements rendered at **12 px**, under the 13 px floor this very
            file defines;
          * the Pending Payments panel started at y=**1370**, two screens below
            the fold, reachable only by scrolling past the whole day.

        ⚠ EVERY CHECK ASSERTS ITS COUNT FIRST. `tap_targets` and `text_floor`
        already refuse a zero-match selector, and the explicit floors below say
        how many there must be — a seed that stopped producing pending payments
        would otherwise turn the sharpest assertion here into a no-op.

        `.page-head button` is deliberately NOT asserted: it matches frappe's
        own sidebar toggle (20x20), which is the desk SHELL, on the same
        reasoning as the overflow scoping above. This board's own actions are
        asserted through `.page-actions .btn`.
        """
        from helpers.board import BGC_BRANCH, load_board
        from helpers.auth import PLATFORM_STATE
        from helpers.worker_routing import auth_state_file

        page = mobile.new_page(storage_state=str(auth_state_file(PLATFORM_STATE)))
        load_board(page, BGC_BRANCH, date.today().isoformat())
        mobile.shot(page, "court_board_operable")
        mobile.check_viewport(page)

        counts = page.evaluate(
            """() => ({
                slots: document.querySelectorAll('.cbt-matrix .cbt-slot').length,
                dateNav: document.querySelectorAll('.cbt-date-nav .btn').length,
                pending: document.querySelectorAll('.cbt-pending-actions .btn').length,
                actions: document.querySelectorAll('.page-actions .btn').length,
            })"""
        )
        assert counts["slots"] >= 40, mobile._why(f"the grid shrank: {counts}")
        assert counts["dateNav"] == 3, mobile._why(f"date nav changed: {counts}")
        assert counts["pending"] >= 3, mobile._why(
            f"no Pending Payments buttons to measure — the seed changed: {counts}"
        )
        assert counts["actions"] >= 1, mobile._why(f"no page actions: {counts}")

        # THE THUMB. One call per selector: a grouped selector is green as soon
        # as ANY member passes.
        mobile.tap_targets(page, ".cbt-matrix .cbt-slot")
        mobile.tap_targets(page, ".cbt-date-nav .btn")
        mobile.tap_targets(page, ".cbt-pending-actions .btn")
        mobile.tap_targets(page, ".cbt-layout-details > summary")
        mobile.tap_targets(page, ".page-actions .btn")

        # THE EYE.
        mobile.text_floor(page, ".cbt-matrix .cbt-timecell")
        mobile.text_floor(page, ".cbt-matrix .cbt-slot-rate")
        mobile.text_floor(page, ".cbt-colhead-name")
        mobile.text_floor(page, ".cbt-pending-ref")
        mobile.text_floor(page, ".cbt-pending-where")

        # THE MONEY, REACHABLE. The panel is below the grid on a phone, so the
        # board offers a jump — and the chip has to be a real target, and its
        # tap has to actually bring the panel into view.
        chip = page.locator("[data-testid='pending-jump']")
        assert chip.is_visible(), mobile._why(
            "the Pending Payments jump is missing on a phone — the panel is "
            "two screens below the grid without it"
        )
        assert "awaiting payment" in chip.inner_text(), chip.inner_text()
        # It must not say a PRICE: "₱ 4 awaiting payment" reads as four pesos.
        assert "₱" not in chip.inner_text(), chip.inner_text()
        mobile.tap_targets(page, "[data-testid='pending-jump']")
        chip.click()
        page.wait_for_timeout(700)  # smooth scroll
        panel = page.evaluate(
            """() => {
                const el = document.querySelector('.cbt-pending-panel');
                const r = el.getBoundingClientRect();
                return {top: Math.round(r.top), ih: window.innerHeight};
            }"""
        )
        assert panel["top"] < panel["ih"], mobile._why(
            f"tapping the jump did not bring the panel into view: {panel}"
        )
        mobile.shot(page, "court_board_pending_jumped")

    def test_the_desk_cart_is_usable_on_the_phone(self, mobile: _Mobile):
        """Backlog B46 on a hand-sized screen. READ-ONLY: it selects two cells,
        opens the dialog, measures it and closes it — nothing is booked, so this
        row claims no ledger date.

        The assertion that matters is B42's, re-run at 390 px: the TOTAL must be
        on screen, at rest, with nothing on top of it. A bounding box inside the
        viewport is not a figure a human can read.
        """
        from helpers.board import (
            BGC_BRANCH,
            BGC_COURTS,
            load_board,
            select_slots,
        )
        from helpers.auth import PLATFORM_STATE
        from helpers.worker_routing import auth_state_file

        page = mobile.new_page(storage_state=str(auth_state_file(PLATFORM_STATE)))
        # A future date, so every cell is free and nothing here depends on the
        # hour the suite happens to run at.
        day = (date.today() + timedelta(days=7)).isoformat()
        load_board(page, BGC_BRANCH, day)

        select_slots(page, BGC_COURTS[0], "10:00:00", 2)
        bar = page.locator("[data-testid='cart-bar']")
        assert bar.is_visible(), mobile._why("the cart bar did not appear")
        mobile.in_viewport(page, "[data-testid='cart-book']")
        mobile.tap_targets(page, ".cbt-cart-bar .btn")
        mobile.shot(page, "court_board_cart")

        page.locator("[data-testid='cart-book']").click()
        page.wait_for_function(
            "() => window.cur_dialog && cur_dialog._quote_settled === true",
            timeout=30000,
        )
        mobile.shot(page, "court_board_cart_dialog")

        geometry = page.evaluate(
            """() => {
                const wrap = cur_dialog.$wrapper[0];
                const node = cur_dialog.fields_dict.estimate.$wrapper[0];
                const total = node.querySelector("[data-testid='quote-total']");
                const box = total ? total.getBoundingClientRect() : null;
                let hit = null, occludedBy = null;
                if (box && box.height) {
                    const el = document.elementFromPoint(
                        Math.round(box.left + box.width / 2),
                        Math.round(box.top + box.height / 2));
                    hit = !!el && (total.contains(el) || el.contains(total));
                    if (!hit && el) occludedBy = el.className || el.tagName;
                }
                const primary = wrap.querySelector('.modal-footer .btn-primary');
                const pbox = primary ? primary.getBoundingClientRect() : null;
                return {
                    lines: wrap.querySelectorAll("[data-testid='cart-line']").length,
                    inFooter: node.parentElement.classList.contains('modal-footer'),
                    hasSlotsField: !!cur_dialog.fields_dict.number_of_slots,
                    totalBottom: box ? Math.round(box.bottom) : null,
                    totalText: total ? total.innerText.trim() : null,
                    hit: hit, occludedBy: occludedBy,
                    viewport: window.innerHeight,
                    docScrollWidth: document.scrollingElement.scrollWidth,
                    innerWidth: window.innerWidth,
                    primary: pbox
                        ? {w: Math.round(pbox.width), h: Math.round(pbox.height)}
                        : null,
                };
            }"""
        )
        assert geometry["lines"] == 1, mobile._why(
            f"two adjacent hours must read as ONE booking: {geometry}"
        )
        assert not geometry["hasSlotsField"], mobile._why(
            "the Slots integer is back — B46 removed it, the board says the "
            "length now"
        )
        assert geometry["inFooter"], mobile._why(str(geometry))
        assert geometry["totalBottom"] <= geometry["viewport"], mobile._why(
            f"the total is below the fold on a phone: {geometry}"
        )
        assert geometry["hit"], mobile._why(
            f"something is covering the total: {geometry}"
        )
        assert "₱" in (geometry["totalText"] or ""), mobile._why(str(geometry))
        assert geometry["primary"] and geometry["primary"]["h"] >= TAP_MIN_PX, (
            mobile._why(f"the Book button is under {TAP_MIN_PX}px: {geometry}")
        )
        assert geometry["docScrollWidth"] <= geometry["innerWidth"] + 1, mobile._why(
            f"the dialog made the page scroll sideways: {geometry}"
        )
        page.evaluate("() => cur_dialog.hide()")

    def test_open_play_board_lays_out_on_the_phone(self, mobile: _Mobile):
        """Staff Open Play Board on the seeded session. Same scoping as above."""
        from helpers.open_play import show_session
        from helpers.auth import PLATFORM_STATE
        from helpers.worker_routing import auth_state_file

        page = mobile.new_page(storage_state=str(auth_state_file(PLATFORM_STATE)))
        show_session(page, OPEN_PLAY_SESSION)
        mobile.shot(page, "open_play_board")
        mobile.check_viewport(page)
        shell = page.evaluate(JS_OVERFLOW, [None])
        mobile.log.append(f"desk shell (observation): {shell}")
        mobile.no_overflow(page, root=".cbt-op")
