"""
E2E — A tenant's website and social links, shown while booking (Backlog B24,
2026-08-27).

The user's words, 2026-08-20: *"socmed should be shown while booking okay. It
should be visible as part of a section so they can check in a new tab the
amenities updated pics etc. We don;t need to manage it"* — and *"it should
combine"* with the photo gallery. So the promise has four parts, and each row
below owes one of them to a human's own gestures:

  1. a Company Admin TYPES the links into their own company form and saves —
     no `set_value`, no seed: the seed leaves every company's links empty;
  2. a LOGGED-OUT visitor sees them on `/book`, in the SAME strip as the Photos
     button (the combine ruling), in a fixed order;
  3. clicking one opens a NEW TAB and the booking page stays where it was;
  4. a bad link (the guest-exposure trap — `javascript:` on a page under our
     domain) is REFUSED on the form, with a sentence a human can act on.

Company choice: `qc-smash`, the seeded company WITH a gallery, so the combined
strip is exercised with both halves present; `e2e-fast` is the has-nothing
control (no photos, no links → no strip at all, not an empty one). Every write
is undone in `finally`, and the admin session is per-test (pytest-playwright's
`page` context is function-scoped), so nothing leaks into the next file.

Claims NO ledger date: nothing here books or blocks. The guest view waits on
the card head, not the grid, so QCSM-timog's Sunday closure is irrelevant.
"""
import json

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures
from helpers.auth import login_as
from helpers.navigation import goto_form

COMPANY = "qc-smash"
BRANCH_SLUG = "timog"
COMPANY_ADMIN = "admin.qcsm@example.com"  # QCSM Company Admin (seed COMPANY_USERS)
CLEAN_COMPANY = "e2e-fast"
CLEAN_BRANCH_SLUG = "main"

FACEBOOK = "https://www.facebook.com/qcsmash"
INSTAGRAM = "https://www.instagram.com/qcsmash"
# A link that must never reach a guest's browser as an href.
BAD_WEBSITE = "javascript:alert(1)"

LINK_FIELDS = ("website", "facebook_url", "instagram_url")


def _website(base_url: str) -> str:
    """The tenant's "own site". Pointed at THIS bench so the new tab really
    navigates and can be asserted on — a real facebook.com would only prove
    that the sandbox has no internet."""
    # Two query params on purpose: the `&` pins that the template escapes the
    # href exactly once (an autoescaping env plus `| e` would ship `&amp;amp;`
    # and the chip would open the wrong URL) — the href assertion below reads
    # the attribute back decoded, so a double escape shows up as a mismatch.
    return f"{base_url}/find-court?from=qcsm-site&x=1"


def _links_via_api(page: Page) -> dict:
    """What the server holds, read through the desk session the test drives."""
    resp = page.request.get(f"/api/resource/CBT Company/{COMPANY}")
    assert resp.ok, f"GET CBT Company/{COMPANY}: HTTP {resp.status} {resp.text()}"
    data = resp.json()["data"]
    # /api/resource DROPS NULL keys (S13 as-built 15) — read with .get so an
    # unset link is None rather than a KeyError.
    return {field: data.get(field) for field in LINK_FIELDS}


def _clear_links(page: Page):
    """Undo the test's own writes. The desk session is the company's own admin,
    who may write these fields — a PUT through /api/resource, CSRF-signed."""
    # `or ""`: a missing token must surface as the server's 4xx below, not as a
    # TypeError from a None header that would mask the assertion that got us here.
    csrf = page.evaluate("() => (window.frappe && frappe.csrf_token) || ''") or ""
    resp = page.request.put(
        f"/api/resource/CBT Company/{COMPANY}",
        headers={"X-Frappe-CSRF-Token": csrf, "Content-Type": "application/json"},
        data=json.dumps({field: None for field in LINK_FIELDS}),
    )
    assert resp.ok, f"cleanup PUT: HTTP {resp.status} {resp.text()}"


def _open_company_form(page: Page):
    login_as(page, COMPANY_ADMIN)
    goto_form(page, "CBT Company", COMPANY)
    page.wait_for_function(
        "(name) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === name",
        arg=COMPANY,
        timeout=30000,
    )
    # The links section must be a place a Company Admin can TYPE into — the
    # whole feature is self-service. A read-only or hidden control fails here,
    # at the cause, rather than as a save that silently changed nothing.
    for field in LINK_FIELDS:
        box = page.locator(f".frappe-control[data-fieldname='{field}'] input").first
        expect(box).to_be_visible(timeout=15000)
        expect(box).to_be_editable()


def _open_book_as_guest(browser, base_url: str, company: str, branch: str) -> Page:
    """A LOGGED-OUT visitor — a fresh context with no storage state at all."""
    context = browser.new_context(
        base_url=base_url,
        viewport={"width": 1400, "height": 960},
        ignore_https_errors=True,
    )
    page = context.new_page()
    page.goto(f"/book?c={company}&b={branch}", wait_until="domcontentloaded", timeout=60000)
    expect(page.locator("#cbt-company-name")).to_be_visible(timeout=20000)
    return page


@pytest.mark.e2e
class TestCompanyLinks:
    def test_admin_types_the_links_and_a_guest_opens_them_in_a_new_tab(
        self, page: Page, browser, base_url
    ):
        """Parts 1–3 of the promise, in the order a tenant would live them."""
        website = _website(base_url)
        guest = None
        try:
            # ---- 1. the Company Admin types them in ------------------------
            _open_company_form(page)
            before = _links_via_api(page)
            assert not any(before.values()), (
                f"{COMPANY} already carries links before the test typed any: "
                f"{before} — a previous run did not clean up"
            )
            gestures.fill(page, "website", website)
            gestures.fill(page, "facebook_url", FACEBOOK)
            gestures.fill(page, "instagram_url", INSTAGRAM)
            gestures.save_form(page)
            assert _links_via_api(page) == {
                "website": website,
                "facebook_url": FACEBOOK,
                "instagram_url": INSTAGRAM,
            }

            # ---- 2. a logged-out visitor sees them beside Photos ------------
            guest = _open_book_as_guest(browser, base_url, COMPANY, BRANCH_SLUG)
            strip = guest.locator("[data-testid='company-links']")
            expect(strip).to_be_visible(timeout=15000)
            # The strip is links only now; the hero opens the photos (section-29).
            expect(strip.locator("[data-testid='photos-button']")).to_have_count(0)
            expect(guest.locator("[data-testid='facility-photos']")).to_be_visible()

            chips = strip.locator("a.cbt-link")
            expect(chips).to_have_count(3)
            kinds = chips.evaluate_all("els => els.map((e) => e.dataset.kind)")
            assert kinds == ["website", "facebook", "instagram"], kinds
            labels = chips.evaluate_all("els => els.map((e) => e.textContent.trim())")
            # The label carries a trailing "↗" (this leaves the page); strip
            # exactly that, not "the first word" — a two-word label must not
            # silently pass.
            assert [label.replace("↗", "").strip() for label in labels] == [
                "Website", "Facebook", "Instagram",
            ], labels
            hrefs = chips.evaluate_all("els => els.map((e) => e.getAttribute('href'))")
            assert hrefs == [website, FACEBOOK, INSTAGRAM], hrefs
            for i in range(3):
                chip = chips.nth(i)
                assert chip.get_attribute("target") == "_blank", hrefs[i]
                rel = (chip.get_attribute("rel") or "").split()
                assert "noopener" in rel and "noreferrer" in rel, (
                    f"{hrefs[i]} opens a new tab without noopener/noreferrer: rel={rel}"
                )

            # ---- 3. clicking one opens a NEW tab; /book stays put -----------
            booking_url = guest.url
            with guest.context.expect_page(timeout=20000) as opened:
                strip.locator("[data-testid='company-link-website']").click()
            new_tab = opened.value
            new_tab.wait_for_load_state("domcontentloaded", timeout=30000)
            assert new_tab.url.startswith(website), (
                f"the new tab is at {new_tab.url!r}, not the tenant's site {website!r}"
            )
            assert guest.url == booking_url, (
                f"the booking page navigated away: {guest.url!r}"
            )
            # Two pages in the guest's context: the booking page and the new tab.
            assert len(guest.context.pages) == 2, [p.url for p in guest.context.pages]
            new_tab.close()
        finally:
            if guest is not None:
                guest.context.close()
            if "/desk" in page.url:
                _clear_links(page)

    def test_a_bad_link_is_refused_on_the_form(self, page: Page):
        """Part 4: the guest-exposure guard, seen from the form a human uses.

        `javascript:alert(1)` typed into Website must be refused by the SERVER
        with a sentence that says what a link has to look like — and nothing
        must be saved, so the guest page can never carry it as an href.
        """
        _open_company_form(page)
        try:
            gestures.fill(page, "website", BAD_WEBSITE)
            # Ctrl+S — the keystroke a human uses — then WAIT for the refusal to
            # render. Not `save_form_expecting_failure`: its fixed 4s settle read
            # an empty modal list once, on the first save after a dev-server
            # restart (the round trip took longer than the settle), and a row
            # that is green or red depending on server latency proves nothing.
            page.keyboard.press("Control+s")
            refusal = page.locator(".modal.show").filter(
                has_text="Website must be a full web address"
            )
            expect(refusal).to_be_visible(timeout=30000)
            # Still dirty on screen, and nothing on the server.
            assert page.evaluate("() => !!cur_frm.doc.__unsaved"), (
                "the form reports itself saved after a refused save"
            )
            assert _links_via_api(page)["website"] is None
            # Close the message the way a human does — retried, because
            # Bootstrap ignores hide() while the fade-in is still running and
            # a human simply clicks the X again (file 17's warning-modal lesson).
            for _attempt in range(3):
                refusal.locator(".modal-header .btn-modal-close").first.click()
                try:
                    expect(refusal).to_have_count(0, timeout=3000)
                    break
                except AssertionError:
                    continue
            expect(refusal).to_have_count(0, timeout=5000)
        finally:
            _clear_links(page)

    def test_a_company_with_neither_photos_nor_links_shows_no_strip(
        self, browser, base_url
    ):
        """The empty-state rule: no photos AND no links → NO strip. The
        section-22 control company, which every other portal file also relies
        on staying clean."""
        guest = _open_book_as_guest(browser, base_url, CLEAN_COMPANY, CLEAN_BRANCH_SLUG)
        try:
            assert guest.locator("[data-testid='company-links']").count() == 0
            assert guest.locator("a.cbt-link").count() == 0
            assert guest.locator("[data-testid='photos-button']").count() == 0
        finally:
            guest.context.close()
