"""
E2E — the customer's half of a facility's photographs. Design: docs/sections/section-29.

The user's side quest (2026-09-11): *"when you click a banner a gallery type view
should appear to browse more pictures"*, *"make a hi[n]t that the banner is
clickable"*, and the override: *"if you uploaded in that Branch then that is what
it should show"*.

The override is what this file exists to prove, and it is proved the only way
that cannot go green by accident: the two cards of the SAME company must render
DIFFERENT covers. Before this row they rendered the same one, because every
branch showed head office's banner.

LEDGER: THIS FILE CLAIMS NO DATE. It books nothing and signs in as nobody.
"""
import json

from playwright.sync_api import Playwright, expect

from helpers.board import api_csrf, platform_api

QCSM = "qc-smash"
AYALA = "ayala-courts"
BGC = "bgc"
ONE_SHOT = "/files/media_lounge.jpg"
MORE = ["/files/media_showers.jpg", "/files/media_court_1.jpg", "/files/media_banner.jpg"]
ANNEX = "annex"
TIMOG = "timog"

VIEWER = "#cbt-photo-viewer"
THUMB = "[data-testid='viewer-thumb']"
CARD = "#cbt-results .cbt-card[data-slug='%s']"
PHOTO_BTN = "[data-testid='branch-photos']"
PILL = "[data-testid='branch-photo-count']"


def _guest(browser, base_url):
    return browser.new_context(
        base_url=base_url, viewport={"width": 1400, "height": 960}, ignore_https_errors=True
    )


def _readable(locator) -> dict:
    """Unoccluded at rest. elementFromPoint is viewport-relative — scroll first."""
    locator.scroll_into_view_if_needed()
    return locator.evaluate(
        """(el) => {
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return {opacity: cs.opacity, visibility: cs.visibility,
                    width: r.width, height: r.height,
                    hit: !!hit && (el === hit || el.contains(hit) || hit.contains(el)),
                    hitTag: hit ? hit.tagName + '.' + hit.className : null,
                    text: el.innerText.trim()};
        }"""
    )


def _open_marketplace(browser, base_url):
    context = _guest(browser, base_url)
    page = context.new_page()
    page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
    return context, page


def _set_banner(playwright: Playwright, company: str, url):
    """The banner is half of what a customer sees, so EMPTY has to clear it too.

    Clearing only the photos leaves the seeded banner behind, and the facility
    that is supposed to have nothing still renders one picture.
    """
    api = platform_api(playwright)
    api.put(
        f"/api/resource/CBT Company/{company}",
        headers={"X-Frappe-CSRF-Token": api_csrf(api)},
        data={"banner": url or ""},
        timeout=30000,
    )


def _cover(page, slug) -> str:
    return page.locator(f"{CARD % slug} img.cbt-card-photo").first.get_attribute("src")


def _thumbs(page) -> list:
    return page.eval_on_selector_all(
        f"{THUMB} img", "els => els.map(e => e.getAttribute('src'))"
    )


def test_two_branches_of_one_company_show_different_pictures(browser, base_url):
    """The defect this row exists for: the annex used to advertise head office."""
    context, page = _open_marketplace(browser, base_url)
    try:
        annex, timog = _cover(page, ANNEX), _cover(page, TIMOG)
        assert annex and timog, f"a card rendered no photo: annex={annex!r} timog={timog!r}"
        assert annex != timog, (
            f"both branches of {QCSM} render the SAME picture ({annex}) — the "
            "branch override is not reaching the marketplace card"
        )
    finally:
        context.close()


def test_the_picture_says_how_many_and_is_a_real_tap_target(browser, base_url):
    """The count rides INSIDE the button, and a finger lands on it."""
    context, page = _open_marketplace(browser, base_url)
    try:
        button = page.locator(f"{CARD % ANNEX} {PHOTO_BTN}").first
        expect(button).to_be_visible()
        pill = page.locator(f"{CARD % ANNEX} {PILL}").first
        expect(pill).to_contain_text("photos")

        seen = _readable(pill)
        assert seen["hit"], (
            f"the photo count is not tappable — the point at its centre hits "
            f"{seen['hitTag']}, which is how a delegated click path passes on a "
            f"desktop and fails on a phone"
        )
        assert seen["opacity"] == "1" and seen["visibility"] == "visible", seen
        assert seen["width"] >= 44 or seen["height"] >= 24, seen
    finally:
        context.close()


def test_tapping_the_picture_opens_that_branchs_photos(browser, base_url):
    """The side quest, end to end, and the branch's own set — not the company's."""
    context, page = _open_marketplace(browser, base_url)
    try:
        expect(page.locator(VIEWER)).to_be_hidden()
        page.locator(f"{CARD % ANNEX} {PHOTO_BTN}").first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        expect(page.locator("[data-testid='viewer-image']")).to_be_visible()

        annex_shots = _thumbs(page)
        assert annex_shots, "the viewer opened on nothing"
        assert _cover(page, ANNEX) in annex_shots, (
            "the gallery does not contain the picture the customer tapped"
        )
        page.locator("[data-testid='viewer-close']").click()
        expect(page.locator(VIEWER)).to_be_hidden()

        page.locator(f"{CARD % TIMOG} {PHOTO_BTN}").first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        timog_shots = _thumbs(page)
        assert set(annex_shots) != set(timog_shots), (
            "both branches opened the SAME gallery — compared as SETS, because "
            "equal counts would pass a count check either way"
        )
    finally:
        context.close()


def _set_photos(playwright: Playwright, doctype, name, images):
    api = platform_api(playwright)
    try:
        rows = json.dumps([{"image": url, "caption": None} for url in images])
        res = api.post(
            "/api/method/court_booking_tech.api.facilities.save_media",
            headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            data={"doctype": doctype, "name": name, "rows": rows},
        )
        assert res.ok, f"save_media {name}: HTTP {res.status} {res.text()}"
    finally:
        api.dispose()


def _pill_text(page, where):
    return page.locator(where).first.inner_text().strip()


def test_the_users_story_walked_from_an_empty_facility(browser, base_url, playwright):
    """THE STORY, in the user's words (2026-09-11):

        "In Company you can upload(optional) 1 to many images ... (when you click
         a banner) a gallery type view should appear to browse more pictures ...
         Also make a hi[n]t that the banner is clickable."

    Walked from NOTHING, through ONE, to MANY — on BOTH customer surfaces. Every
    earlier test in this file started at 3 or 4 photos, so `photo_count > 1`
    shipped and one photo opened the MAP MODAL instead of the gallery.
    """
    _set_photos(playwright, "CBT Company", AYALA, [])
    _set_banner(playwright, AYALA, None)
    context = _guest(browser, base_url)
    try:
        page = context.new_page()

        # --- nothing: no count, nothing to tap -------------------------------
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
        assert page.locator(f"{CARD % BGC} {PHOTO_BTN}").count() == 0
        page.goto(f"/book?c={AYALA}&b={BGC}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        assert page.locator("[data-testid='facility-photos']").count() == 0

        # --- ONE: the spec's own lower bound ---------------------------------
        _set_photos(playwright, "CBT Company", AYALA, [ONE_SHOT])
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
        expect(page.locator(f"{CARD % BGC} {PHOTO_BTN}").first).to_be_visible()
        assert _pill_text(page, f"{CARD % BGC} {PILL}") == "1 photo"
        page.locator(f"{CARD % BGC} {PHOTO_BTN}").first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        expect(page.locator("#cbt-branch-modal")).to_be_hidden()
        assert _thumbs(page) == [ONE_SHOT]
        page.locator("[data-testid='viewer-close']").click()

        page.goto(f"/book?c={AYALA}&b={BGC}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        opener = page.locator("[data-testid='facility-photos']").first
        expect(opener).to_be_visible()
        assert _pill_text(page, "[data-testid='facility-photo-count']") == "1 photo"
        opener.click()
        expect(page.locator(VIEWER)).to_be_visible()
        assert _thumbs(page) == [ONE_SHOT]

        # --- a banner AND its photos coexist: 1 + 3 = FOUR --------------------
        four = [ONE_SHOT] + MORE
        _set_banner(playwright, AYALA, ONE_SHOT)
        _set_photos(playwright, "CBT Company", AYALA, MORE)
        page.goto(f"/book?c={AYALA}&b={BGC}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        assert _pill_text(page, "[data-testid='facility-photo-count']") == "4 photos"
        page.locator("[data-testid='facility-photos']").first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        assert _thumbs(page) == four, _thumbs(page)
    finally:
        context.close()
        _set_photos(playwright, "CBT Company", AYALA, [])
        _set_banner(playwright, AYALA, None)


def test_a_facility_with_exactly_one_picture_still_opens(browser, base_url, playwright):
    """A single photo is not an empty room. The card CROPS it, so tapping is how a
    customer sees the whole of it — and the person likeliest to tap is the one who
    just uploaded it. Gating the button on >1 made that the one case that did
    nothing, and worse: the tap fell through to the whole-card handler and opened
    the MAP modal instead."""
    _set_banner(playwright, AYALA, None)
    _set_photos(playwright, "CBT Company", AYALA, [ONE_SHOT])
    context, page = _open_marketplace(browser, base_url)
    try:
        card = page.locator(CARD % BGC)
        expect(card.locator(PHOTO_BTN).first).to_be_visible()
        expect(card.locator(PILL).first).to_have_text("1 photo")

        card.locator(PHOTO_BTN).first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        # The map modal is what a missing button used to open by mistake.
        expect(page.locator("#cbt-branch-modal")).to_be_hidden()
        assert _thumbs(page) == [ONE_SHOT], _thumbs(page)
    finally:
        context.close()
        _set_photos(playwright, "CBT Company", AYALA, [])


def test_the_rest_of_the_card_still_opens_the_map(browser, base_url):
    """The photo is a THIRD click layer on a card that already had two."""
    context, page = _open_marketplace(browser, base_url)
    try:
        page.locator(f"{CARD % TIMOG} .cbt-card-title").first.click()
        expect(page.locator("#cbt-branch-modal")).to_be_visible()
        expect(page.locator(VIEWER)).to_be_hidden()
    finally:
        context.close()


def test_the_branch_chooser_shows_each_branch_its_own_photo(browser, base_url):
    """/book?c= lists branches to choose between, and a chooser with no pictures
    is the same defect one page over."""
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        page.goto(f"/book?c={QCSM}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-branch-chooser .cbt-card[data-slug]", timeout=30000)

        rows = page.locator("#cbt-branch-chooser .cbt-card[data-slug]")
        expect(rows).to_have_count(2)
        covers = page.eval_on_selector_all(
            "#cbt-branch-chooser img.cbt-card-photo", "els => els.map(e => e.getAttribute('src'))"
        )
        assert len(covers) == 2, f"a chooser row rendered no photo: {covers}"
        assert covers[0] != covers[1], f"both chooser rows show the same picture: {covers}"

        page.locator(f"[data-testid='branch-photos-{ANNEX}']").first.click()
        expect(page.locator(VIEWER)).to_be_visible()
        page.locator("[data-testid='viewer-close']").click()
        expect(page.locator(VIEWER)).to_be_hidden()
    finally:
        context.close()


def test_the_chooser_row_still_navigates_after_the_photo_was_added(browser, base_url):
    """The row was an <a>; a button cannot nest in one, so it became a div with
    an inner link. That is a navigation path customers use."""
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        page.goto(f"/book?c={QCSM}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-branch-chooser .cbt-card[data-slug]", timeout=30000)

        page.locator(f"#cbt-branch-chooser .cbt-card[data-slug='{ANNEX}'] .cbt-card-title").click()
        page.wait_for_url(f"**/book?c={QCSM}&b={ANNEX}**", timeout=30000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
        expect(page.locator(VIEWER)).to_be_hidden()
    finally:
        context.close()
