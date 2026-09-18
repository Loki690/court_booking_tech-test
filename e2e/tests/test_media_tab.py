"""
E2E — the Media tab on CBT Branch and CBT Company.

The user's words (2026-09-11): *"In Company you can upload (optional) 1 to many
images. In Branches you can upload (optional) 1 to many images … if you uploaded
in that Branch then that is what it should show (something like override)."*

The seat is deliberately QUINTIN, whose company has self branch management OFF —
so his Branch form is DISABLED by `frm.disable_form()`. Photos are NOT gated on
that switch, so the tab must stay live inside a switched-off form. That is the
whole point of the checkbox, and a test that used an ungated admin would pass
without ever touching it.

Every assertion is SAVE -> RELOAD -> READ the rendered `src`, never the count
and never `cur_frm.doc`: a branch with 3 of its own and a company with 3 would
go green on a count either way.
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright, expect

from helpers import gestures
from helpers.auth import login_as
from helpers.board import api_csrf, platform_api
from helpers.navigation import goto_form, wait_for_page_load

QUINTIN = "admin.qcsm@example.com"
QCSM_STAFF = "staff.qcsm@example.com"
QCSM = "qc-smash"
TIMOG = "QCSM-timog"

BANNER_URL = "/files/media_banner.jpg"
# The seeded company gallery, restored verbatim by any test that disturbs it.
SEEDED_COMPANY_PHOTOS = [
    BANNER_URL,
    "/files/media_court_1.jpg",
    "/files/media_court_center.jpg",
    "/files/media_courts_night.jpg",
]
SEEDS = Path(__file__).resolve().parents[2] / "court_booking_tech" / "seeds" / "files"
SHOTS = [SEEDS / "media_court_1.jpg", SEEDS / "media_lounge.jpg", SEEDS / "media_showers.jpg"]

TAB = "[data-testid='cbt-media-tab']"
TILE = "[data-testid='cbt-media-tile']"
DROP = "[data-testid='cbt-media-drop']"
SAVE = "[data-testid='cbt-media-save']"
COVER = "[data-testid='cbt-media-cover']"
RESOLUTION = "[data-testid='cbt-media-resolution']"


def _clear(playwright: Playwright, doctype: str, name: str):
    api = platform_api(playwright)
    try:
        res = api.post(
            "/api/method/court_booking_tech.api.facilities.save_media",
            headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            data={"doctype": doctype, "name": name, "rows": "[]"},
        )
        assert res.ok, f"clear {doctype} {name}: HTTP {res.status} {res.text()}"
    finally:
        api.dispose()


def _open(page: Page, doctype: str, name: str):
    goto_form(page, doctype, name)
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n", arg=name, timeout=30000
    )
    page.locator(f"button.nav-link[data-fieldname='media_tab'] >> visible=true").first.click()
    page.locator(f"{TAB} >> visible=true").first.wait_for(state="visible", timeout=20000)


def _set_gate(playwright: Playwright, on: int):
    api = platform_api(playwright)
    try:
        res = api.put(
            f"/api/resource/CBT Company/{QCSM}",
            headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            data={"allow_company_gallery_management": on},
        )
        assert res.ok, f"gate {on}: HTTP {res.status} {res.text()}"
    finally:
        api.dispose()


def _restore_seeded_company(playwright: Playwright):
    """Put qc-smash back exactly as the seed left it — file 18 asserts against it.

    The BANNER is half of that, and the seed DOES set one, so a test that clears
    it must put it back — not leave the company bannerless for whatever runs next.
    """
    api = platform_api(playwright)
    try:
        rows = json.dumps([{"image": u, "caption": None} for u in SEEDED_COMPANY_PHOTOS])
        res = api.post(
            "/api/method/court_booking_tech.api.facilities.save_media",
            headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            data={"doctype": "CBT Company", "name": QCSM, "rows": rows},
        )
        assert res.ok, f"restore {QCSM}: HTTP {res.status} {res.text()}"
    finally:
        api.dispose()
    _set_banner(playwright, QCSM, BANNER_URL)


def _set_banner(playwright: Playwright, company: str, url):
    api = platform_api(playwright)
    try:
        res = api.put(
            f"/api/resource/CBT Company/{company}",
            headers={"X-Frappe-CSRF-Token": api_csrf(api)},
            data={"banner": url},
        )
        assert res.ok, f"banner {url}: HTTP {res.status} {res.text()}"
    finally:
        api.dispose()


def _attach_banner_on_details(page: Page, company: str, url: str):
    """Set the banner the way a person does — the Attach control's Link tab, then
    the form's own Save. Never save_media, which is the path that already worked."""
    goto_form(page, "CBT Company", company)
    page.wait_for_function(
        "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
        arg=company,
        timeout=30000,
    )
    page.locator("[data-fieldname='banner'] .btn-attach, [data-fieldname='banner'] .attach-btn").first.click()
    dialog = page.locator(".modal.show").last
    dialog.wait_for(state="visible", timeout=20000)
    dialog.get_by_text("Link", exact=True).first.click()
    dialog.locator("input[type='text'], input[type='url']").first.fill(url)
    dialog.get_by_role("button", name="Upload").first.click()
    page.wait_for_function(
        "(u) => window.cur_frm && cur_frm.doc && cur_frm.doc.banner === u",
        arg=url,
        timeout=30000,
    )
    page.keyboard.press("Control+s")
    page.wait_for_function(
        "() => window.cur_frm && !cur_frm.doc.__unsaved", timeout=30000
    )


def _drop(page: Page, files):
    page.locator(f"{TAB} input[type=file] >> visible=false").first.set_input_files(
        [str(p) for p in files]
    )


def _shown(page: Page) -> list[str]:
    """The file names actually rendered in the grid, in the order a human reads."""
    return page.eval_on_selector_all(
        f"{TAB}:not([hidden]) {TILE} img",
        "els => els.map(e => e.getAttribute('src').split('/').pop())",
    )


@pytest.fixture(scope="module", autouse=True)
def leave_qcsm_as_the_seed_left_it(playwright: Playwright):
    """This file mutates a SEEDED company, so it hands it back at the FILE level.

    Per-test teardowns each restored what they personally knew about and the
    banner leaked for good, which broke test_facility_photos on the backend lane
    with `4 != 3` — a red that looks exactly like a product defect.
    """
    yield
    _restore_seeded_company(playwright)


@pytest.mark.e2e
class TestMediaTab:
    def test_a_gated_branch_admin_can_still_put_photos_on_a_branch(
        self, page: Page, playwright: Playwright
    ):
        """The Branch form is disabled for this seat; the Media tab is not."""
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)

            # The form really is switched off for him — the contrast this row rests on.
            assert page.evaluate("() => !!(cur_frm && cur_frm.page.btn_primary.is(':hidden'))"), (
                "the branch form is NOT disabled for this seat, so this row proves nothing"
            )
            expect(page.locator(f"{RESOLUTION} >> visible=true").first).to_contain_text(
                "showing the company's"
            )

            # One gesture, three files.
            page.locator(f"{TAB} input[type=file] >> visible=false").first.set_input_files(
                [str(p) for p in SHOTS]
            )
            expect(page.locator(f"{TAB}:not([hidden]) {TILE} >> visible=true")).to_have_count(3)
            page.locator(f"{SAVE} >> visible=true").first.click()

            # SAVE -> RELOAD -> READ. Never cur_frm.doc, never a count.
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)
            assert _shown(page) == [p.name for p in SHOTS], (
                f"the branch did not keep the photos in the order they were dropped: {_shown(page)}"
            )
            expect(page.locator(f"{RESOLUTION} >> visible=true").first).to_contain_text(
                "shows its own 3"
            )
        finally:
            _clear(playwright, "CBT Branch", TIMOG)

    def test_make_cover_moves_the_cover_and_survives_a_reload(
        self, page: Page, playwright: Playwright
    ):
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)
            page.locator(f"{TAB} input[type=file] >> visible=false").first.set_input_files(
                [str(p) for p in SHOTS]
            )
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)

            # The cover badge sits on the first tile and nowhere else.
            expect(page.locator(f"{TAB}:not([hidden]) {COVER} >> visible=true")).to_have_count(1)
            assert _shown(page)[0] == SHOTS[0].name

            # Third tile becomes the cover — by CLICKING the button a person clicks.
            page.locator(
                f"{TAB}:not([hidden]) {TILE}[data-i='2'] [data-testid='cbt-media-make-cover']"
            ).first.click()
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)

            assert _shown(page)[0] == SHOTS[2].name, (
                f"'Make cover' did not survive the save: {_shown(page)}"
            )
        finally:
            _clear(playwright, "CBT Branch", TIMOG)

    def test_the_CLEAR_button_on_the_banner_actually_clears_it(
        self, page: Page, playwright: Playwright
    ):
        """User, 2026-09-11: *"Everytime I clear the Banner it is being set as
        /files/xDkhIzHc.jpg ... what the fuck did you do?"*

        A re-mirror in the parent's validate() put the banner straight back, so
        the field could not be cleared. THE CONTROL IS THE TEST — pressing Clear
        and saving, not assigning None on the server.
        """
        _restore_seeded_company(playwright)
        _set_banner(playwright, QCSM, BANNER_URL)
        login_as(page, QUINTIN)
        try:
            goto_form(page, "CBT Company", QCSM)
            page.wait_for_function(
                "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
                arg=QCSM,
                timeout=30000,
            )
            assert page.evaluate("() => cur_frm.doc.banner") == BANNER_URL

            gestures.clear_attachment(page, "banner")

            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            page.wait_for_function(
                "(n) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === n",
                arg=QCSM,
                timeout=30000,
            )
            assert not page.evaluate("() => cur_frm.doc.banner"), (
                "the banner came back after Clear + Save — something the user "
                "never asked for is writing it"
            )
        finally:
            _restore_seeded_company(playwright)

    def test_a_banner_uploaded_on_details_shows_up_in_the_media_tab(
        self, page: Page, playwright: Playwright
    ):
        """ITEM #4, the user's own journey (2026-09-11):

            "WHEN I UPLOADED A BANNER IN CBT COMPANY AND UPLOAD 3 PHOTOS IN ITS
             MEDIA ... RIGHT NOW BANNER AND ITS MEDIA COULD NOT EXIST WITH EACH
             OTHER."

        The Details tab owns the banner; the Media tab owns the photos. The tab
        must show the PHOTOS only, and saving them must leave the banner alone.
        """
        _clear(playwright, "CBT Company", QCSM)
        _set_banner(playwright, QCSM, None)
        login_as(page, QUINTIN)
        try:
            _attach_banner_on_details(page, QCSM, BANNER_URL)

            _open(page, "CBT Company", QCSM)
            assert _shown(page) == [], (
                f"the banner is being rendered as a photo tile it does not own: {_shown(page)}"
            )

            _drop(page, SHOTS[:2])
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Company", QCSM)

            assert _shown(page) == [SHOTS[0].name, SHOTS[1].name], _shown(page)
            assert page.evaluate("() => cur_frm.doc.banner") == BANNER_URL, (
                "saving photos overwrote the banner the user chose"
            )
        finally:
            _restore_seeded_company(playwright)

    def test_the_remove_button_takes_a_photo_off(self, page: Page, playwright: Playwright):
        """The × was built and never clicked. save_media([]) passing proves the
        ENDPOINT deletes rows; it proves nothing about the control."""
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)
            _drop(page, SHOTS)
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)
            assert _shown(page) == [p.name for p in SHOTS]

            page.locator(
                f"{TAB}:not([hidden]) {TILE}[data-i='1'] [data-testid='cbt-media-remove']"
            ).first.click()
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)
            assert _shown(page) == [SHOTS[0].name, SHOTS[2].name], _shown(page)
        finally:
            _clear(playwright, "CBT Branch", TIMOG)

    def test_a_typed_caption_survives_the_save(self, page: Page, playwright: Playwright):
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)
            _drop(page, [SHOTS[0]])
            caption = page.locator(
                f"{TAB}:not([hidden]) {TILE}[data-i='0'] [data-testid='cbt-media-caption']"
            ).first
            caption.fill("Court 1 under lights")
            caption.blur()
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)
            assert page.locator(
                f"{TAB}:not([hidden]) {TILE}[data-i='0'] [data-testid='cbt-media-caption']"
            ).first.input_value() == "Court 1 under lights"
        finally:
            _clear(playwright, "CBT Branch", TIMOG)

    def test_staff_see_the_photos_and_no_controls_at_all(self, page: Page):
        login_as(page, QCSM_STAFF)
        _open(page, "CBT Company", QCSM)
        assert page.locator(f"{TAB}:not([hidden]) {TILE}").count() > 0
        for control in (DROP, SAVE, "[data-testid='cbt-media-remove']",
                        "[data-testid='cbt-media-make-cover']",
                        "[data-testid='cbt-media-caption']"):
            assert page.locator(f"{TAB}:not([hidden]) {control}").count() == 0, control

    def test_the_gate_off_shows_the_admin_a_sentence_not_a_dead_button(
        self, page: Page, playwright: Playwright
    ):
        _set_gate(playwright, 0)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Company", QCSM)
            expect(
                page.locator(f"{TAB}:not([hidden]) [data-testid='cbt-media-gate']").first
            ).to_contain_text("managed by the platform")
            assert page.locator(f"{TAB}:not([hidden]) {DROP}").count() == 0
            assert page.locator(f"{TAB}:not([hidden]) {SAVE}").count() == 0
        finally:
            _set_gate(playwright, 1)

    def test_the_company_tab_says_what_its_photos_are_for(self, page: Page):
        login_as(page, QUINTIN)
        _open(page, "CBT Company", QCSM)
        expect(page.locator(f"{RESOLUTION} >> visible=true").first).to_contain_text(
            "every branch that has none of its own"
        )

    def test_a_refused_upload_says_which_file_and_why(
        self, page: Page, playwright: Playwright, tmp_path
    ):
        """The first error a real tenant hits. A generic 'could not be uploaded'
        leaves them guessing which file and what to do about it."""
        bad = tmp_path / "notes.txt"
        bad.write_text("this is not a photograph", encoding="utf-8")
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)
            _drop(page, [bad])
            error = page.locator("[data-testid='cbt-media-error']").first
            error.wait_for(state="visible", timeout=20000)
            text = error.inner_text()
            assert "notes.txt" in text, text
            assert "JPEG" in text or "PNG" in text, text
            assert page.locator(f"{TAB}:not([hidden]) {TILE}").count() == 0
        finally:
            _clear(playwright, "CBT Branch", TIMOG)

    def test_the_photos_a_customer_would_load_are_public_files(
        self, page: Page, playwright: Playwright
    ):
        """A private file 403s every customer while the src attribute reads fine."""
        _clear(playwright, "CBT Branch", TIMOG)
        login_as(page, QUINTIN)
        try:
            _open(page, "CBT Branch", TIMOG)
            page.locator(f"{TAB} input[type=file] >> visible=false").first.set_input_files(
                [str(SHOTS[0])]
            )
            page.locator(f"{SAVE} >> visible=true").first.click()
            page.reload(wait_until="domcontentloaded")
            wait_for_page_load(page)
            _open(page, "CBT Branch", TIMOG)

            src = page.locator(f"{TAB}:not([hidden]) {TILE} img").first.get_attribute("src")
            assert src and src.startswith("/files/"), (
                f"the photo is not a PUBLIC file url: {src!r} — /private/files 403s every customer"
            )
            decoded = page.evaluate(
                """() => {
                    const img = document.querySelector(
                        "[data-testid='cbt-media-tab']:not([hidden]) [data-testid='cbt-media-tile'] img"
                    );
                    return img && img.naturalWidth;
                }"""
            )
            assert decoded and decoded > 0, "the browser could not DECODE the photo it was served"
        finally:
            _clear(playwright, "CBT Branch", TIMOG)
