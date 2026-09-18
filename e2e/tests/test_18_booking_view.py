"""
E2E file 18 — Show the place: banner, gallery, court-layout sketch.

A customer deciding where to book used to see TEXT. This file drives the
surfaces that changed it, against the SEEDED fixture rather than in-test
uploads: the upload path is browser-tested in `test_media_tab.py`, and what this
file owes is the customer-visible half — that the pictures actually arrive in a
browser, that the banner OPENS them, and that the floor plan is wired to the
grid beneath it.

**The banner assertion checks `naturalWidth`, not just `src`.** The whole
feature turns on a File permission subtlety — an attach defaults to PRIVATE and
File permission follows the attached document, and customers hold no DocPerm on
a facility — so a broken gallery looks perfectly fine to the logged-in staff
member who uploaded it and 403s every customer. An `src` assertion would pass
against exactly that bug. Only a decoded image proves it.

Runs as Administrator, admin-driven like files 06/11/12/13/14/16/17. This file
BOOKS NOTHING and BLOCKS NOTHING, so it consumes no court x hour and cleans
nothing. Every row is a page load; the one row that clicks a slot (B33's sticky
time column) goes no further than the quote GET behind the checkout bar, which
mints no hold — the same stopping point the mobile lane uses.

Date map (all relative to the run day). THE FULL E2E LEDGER, re-verified this
session by reading every `timedelta(days=…)` in `e2e/tests` rather than copying
a recital forward — which is how Backlog B16 happened:

  +0 TODAY  file 14 (E2EF-main-court-1) and file 15 (AYALA-bgc-court-1)
  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              **+45      file 02** (see below)
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   file 13
  **+60/+61 files 08 AND 10 both**          +62       file 10
  +63/+64   file 11                         +65       file 14
  +66       reserved in prose for section-16, never used
  +67/+68   file 15                         +69/+70   file 16
  +71/+72   file 17

  file 05 also carries **+200**, a beyond-the-horizon REJECTION constant that
  persists nothing. It is not a ledger slot.

**THIS FILE CLAIMS +73 AND +74**, on `qc-smash` / `QCSM-timog`, and it claims
BOTH on purpose. QCSM-timog is seeded CLOSED ON SUNDAYS, so the date slides +1
whenever the run day makes +73 a Sunday — the same shape file 02's
`_qcsm_open_date()` uses for the same branch. Declaring only the unslid date is
precisely the defect Backlog B16 (b) records against file 02, so both are
declared here. Only ONE is used per run, and neither is booked.

  The slide is LOAD-BEARING, not defensive: on a closed day
  `get_public_availability` returns no courts, `/book` renders "This branch is
  closed on the selected day" and emits ZERO `.cbt-card[data-court]` — so the
  sketch-taps-the-grid row would fail one run day in seven without it.

  +73 (or +74)  QCSM-timog — banner, gallery, sketch. Read-only.
  The B37 map rows (2026-09-04) also LOAD ayala-courts/makati and qc-smash/annex
  on the same slid day — page loads only, nothing booked, no new ledger slot.

**Company choice is the load-bearing decision of this file, and it was swept
rather than guessed.** A layout changes a branch's DESK board DOM and media
changes its `/book` DOM. `qc-smash` is the ONLY seeded company with neither
asserted anywhere in the suite: no E2E file loads a QCSM board (file 02's single
QCSM board reference asserts HTTP 403 — payload-blind, as does
`tests/test_isolation.py`), and no E2E file loads `/book?c=qc-smash`.

`?d=` resolving at all depends on `SEED_ADVANCE_BOOKING_DAYS = 90` being applied
to qc-smash: the PLATFORM default is 30, and the date strip clamps to that
horizon (`cbt-book.html` HORIZON), so on a 30-day company every row below would
have loaded a different date than it asked for.

No sleeps. Every assertion waits on a rendered node or a decoded image.
"""
import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

# The seeded media/layout fixture — see the company-choice note above.
MEDIA_COMPANY = "qc-smash"
MEDIA_BRANCH_SLUG = "timog"
MEDIA_BRANCH = "QCSM-timog"
COURT_1 = "QCSM-timog-court-1"
COURT_2 = "QCSM-timog-court-2"
CENTER_COURT = "QCSM-timog-center-court"

# The company that has NEITHER media NOR a layout — the "stays clean" control.
CLEAN_COMPANY = "e2e-fast"
CLEAN_BRANCH_SLUG = "main"

# 3 courts on a 2x3 plan => 3 walkway cells.
SKETCH_COURTS = 3
SKETCH_GAPS = 3

# Backlog B37 — the seeded pins and addresses (seeds.BRANCHES). A map centred
# on the wrong venue fails silently, so the rows assert the exact coordinates.
TIMOG_ADDRESS = "Timog Ave cor Tomas Morato"
TIMOG_PIN = "14.6349,121.0388"
ANNEX_ADDRESS = "Annex Bldg, Kamuning"  # seeded WITHOUT a pin
CLEAN_ADDRESS = "Test Grid, Nowhere"  # e2e-fast: address, no pin


def _timog_open_date() -> str:
    """+73, slid off a Sunday. See the ledger note in the module docstring."""
    day = date.today() + timedelta(days=73)
    if day.weekday() == 6:  # QCSM-timog is seeded closed on Sundays
        day += timedelta(days=1)
    return day.isoformat()


D_VIEW = _timog_open_date()


def _open_book(page: Page, company: str, branch: str, day: str | None = None):
    """Load /book and wait for the availability grid to have really rendered."""
    url = f"/book?c={company}&b={branch}"
    if day:
        url += f"&d={day}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # The grid is fetched after load; every row below reads DOM that only makes
    # sense once it is there.
    page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)


# B33's matrix IS the grid now, so opening the matrix is opening the page. The
# alias survives the flip because the matrix rows read better naming what they
# are about; it went in with the dev-only ?grid=matrix switch, which is gone.
_open_matrix = _open_book






@pytest.mark.e2e
class TestBookingView:
    def test_book_page_shows_banner_and_photos_button(self, page: Page):
        """The hero renders, and the browser could actually DECODE it.

        `naturalWidth > 0` is the whole point: a private File 403s the customer
        while the `src` attribute reads perfectly, so any assertion short of a
        decoded image passes against the exact bug the force-public controller
        exists to prevent.
        """
        _open_book(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        banner = page.locator("#cbt-banner")
        expect(banner).to_be_visible()
        src = banner.get_attribute("src")
        assert src and src.startswith("/files/"), (
            f"the banner is not a PUBLIC file url: {src!r} — a /private/files "
            "url is exactly what 403s every customer"
        )
        loaded = page.evaluate(
            """() => {
                const img = document.getElementById('cbt-banner');
                return {complete: img.complete, width: img.naturalWidth};
            }"""
        )
        assert loaded["width"] > 0, (
            f"the banner element exists but the browser could not decode it: {loaded}"
        )

        # The picture IS the door now, and the count rides inside it.
        opener = page.locator("[data-testid='facility-photos']")
        expect(opener).to_be_visible()
        expect(page.locator("[data-testid='facility-photo-count']")).to_contain_text("photos")
        assert opener.locator("#cbt-banner").count() == 1, (
            "the banner must sit INSIDE its own button — a tappable word outside "
            "the bound element is the click path that passes on desktop and "
            "fails on a phone"
        )
        # The viewer ships shut and opens only when asked.
        assert page.locator("#cbt-photo-viewer").count() == 1
        assert page.locator("#cbt-photo-viewer").is_hidden()

        opener.click()
        expect(page.locator("#cbt-photo-viewer")).to_be_visible()
        expect(page.locator("[data-testid='viewer-image']")).to_be_visible()
        page.locator("[data-testid='viewer-close']").click()
        expect(page.locator("#cbt-photo-viewer")).to_be_hidden()


    def test_page_carries_the_app_version_and_versioned_assets(self, page: Page):
        """The version footer is on the page ONCE, and the stylesheet the page
        actually loaded is the versioned one.

        The 2026-09-03 iPhone screenshot was new markup styled by a cached,
        older sheet — which a bare link permits. The footer's data attribute
        carries the full stamp so the next such screenshot names its own cause.
        """
        _open_book(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        footer = page.locator("[data-testid='app-version']")
        expect(footer).to_have_count(1)
        shown = footer.inner_text().strip()
        assert re.match(r"^v\d+\.\d+\.\d+$", shown), f"footer reads {shown!r}"
        stamp = footer.get_attribute("data-asset-version") or ""
        assert re.match(r"^\d+\.\d+\.\d+-[0-9a-f]{8}$", stamp), f"stamp is {stamp!r}"
        assert stamp.startswith(shown[1:] + "-"), (shown, stamp)

        href = page.get_attribute("link[href*='cbt_portal.css']", "href") or ""
        assert href.endswith("?v=" + stamp), (
            f"the stylesheet the page loaded is {href!r}, not the stamped one"
        )

    def test_layout_sketch_matches_seeded_arrangement(self, page: Page):
        """The floor plan renders WITH its walkways, and taps through to the grid.

        The gap cells are the reason this reuses CBT Branch Court Cell instead
        of a courts-per-row number: 4-3-4 (and the seeded 2x3 with three
        walkways) can only be said with empty cells.
        """
        _open_book(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        # Backlog B32: the sketch ships CLOSED. A closed <details> is visible
        # (its summary renders), so the closed-state assertion is on the CONTENT
        # and the `open` attribute — then the row opens it the way a customer
        # does, and everything below runs against the opened plan.
        sketch = page.locator("[data-testid='court-layout']")
        grid = page.locator("#cbt-sketch .cbt-sketch-grid")
        expect(sketch).to_be_visible()
        assert sketch.get_attribute("open") is None, "the sketch must ship closed"
        expect(grid).to_be_hidden()
        expect(sketch.locator("summary")).to_contain_text(f"{SKETCH_COURTS} courts")
        sketch.locator("summary").click()
        expect(grid).to_be_visible()
        assert sketch.get_attribute("open") is not None
        style = page.get_attribute("#cbt-sketch .cbt-sketch-grid", "style") or ""
        assert "repeat(3" in style, f"sketch is not 3 columns wide: {style!r}"
        assert page.locator("#cbt-sketch .cbt-sketch-court").count() == SKETCH_COURTS
        assert page.locator("#cbt-sketch .cbt-sketch-gap").count() == SKETCH_GAPS

        # Each cell is labelled with the court's HUMAN name, not its document id.
        names = page.eval_on_selector_all(
            "#cbt-sketch .cbt-sketch-court",
            "els => els.map(e => e.textContent.trim())",
        )
        assert "Court 1" in names, names
        assert "Center Court" in names, names
        for name in names:
            assert not re.match(r"^QCSM-", name), (
                f"a sketch cell is labelled with a document id: {name!r}"
            )

        # ...and tapping one takes you to that court's COLUMN in the grid
        # below — B33 turned the per-court card into a column, so the tap
        # target moved with it. The full highlight is asserted in
        # test_the_sketch_tap_highlights_a_whole_column; here it is the wiring.
        head = page.locator(f"th.cbt-colhead[data-court='{CENTER_COURT}']")
        expect(head).to_have_count(1)
        assert "cbt-col--focus" not in (head.get_attribute("class") or "")

        page.locator(
            f"#cbt-sketch .cbt-sketch-court[data-court='{CENTER_COURT}']"
        ).click()
        page.wait_for_function(
            """(court) => {
                const th = document.querySelector(`th.cbt-colhead[data-court="${court}"]`);
                return th && th.classList.contains('cbt-col--focus');
            }""",
            arg=CENTER_COURT,
            timeout=15000,
        )
        pressed = page.locator("#cbt-sketch .cbt-sketch-court[aria-pressed='true']")
        assert pressed.count() == 1
        assert pressed.get_attribute("data-court") == CENTER_COURT

    def test_map_is_folded_under_the_address_and_opens_on_demand(self, page: Page):
        """Backlog B37. The address is the always-visible cheap answer and
        Directions opens the phone's own maps app; Google's frame costs nothing
        until asked — the iframe has NO src until the disclosure is opened, and
        the src it then gets carries the branch's ACTUAL coordinates.
        """
        _open_book(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        expect(page.locator("#cbt-where[data-pinned='1']")).to_have_count(1)
        expect(page.locator("#cbt-where-address")).to_contain_text(TIMOG_ADDRESS)
        directions = page.locator("#cbt-where-directions")
        expect(directions).to_be_visible()
        href = directions.get_attribute("href") or ""
        assert f"destination={TIMOG_PIN}" in href, href

        details = page.locator("#cbt-where-details")
        frame = page.locator("#cbt-where-map")
        expect(frame).to_have_count(1)
        assert details.get_attribute("open") is None, "the map must ship folded"
        assert frame.get_attribute("src") is None, "the map must not load before it is opened"

        details.locator("summary").click()
        expect(frame).to_have_attribute(
            "src", re.compile(r"^https://maps\.google\.com/maps\?q=14\.6349,121\.0388&output=embed$")
        )
        assert details.get_attribute("open") is not None
        expect(frame).to_be_visible()

    def test_map_states_follow_the_pin_and_the_plan(self, page: Page):
        """The COMMON case is a pinned branch with no floor plan (AYALA-makati):
        the address row with the map folded under it, and no sketch at all. A
        pin-less branch (QCSM-annex) gets its address line only — no expander,
        no frame, no Directions to a place nobody has located.
        """
        _open_book(page, "ayala-courts", "makati", D_VIEW)
        expect(page.locator("#cbt-where[data-pinned='1']")).to_have_count(1)
        expect(page.locator("#cbt-where-directions")).to_be_visible()
        assert page.locator("#cbt-where-map").count() == 1
        assert page.locator("#cbt-sketch").count() == 0

        _open_book(page, MEDIA_COMPANY, "annex", D_VIEW)
        where = page.locator("#cbt-where[data-pinned='0']")
        expect(where).to_have_count(1)
        expect(where).to_contain_text(ANNEX_ADDRESS)
        assert where.locator("details").count() == 0
        assert page.locator("#cbt-where-map").count() == 0
        assert page.locator("#cbt-where-directions").count() == 0

    def test_matrix_puts_each_court_in_a_column_and_states_every_cell(self, page: Page):
        """Backlog B33. One row per grid slot, one column per court, and the
        user's own rule on every cell: *"always show amount and status"*.

        The per-cell loop is deliberate. Counting how many cells read
        "Available" would be a wall-clock assertion — the seeded day slides and
        the past line moves — so every cell is asserted to carry BOTH a price
        and a word, whatever that word is.
        """
        _open_matrix(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        expect(page.locator("table.cbt-matrix")).to_have_count(1)
        courts = page.eval_on_selector_all(
            ".cbt-matrix th.cbt-colhead", "els => els.map(e => e.dataset.court)"
        )
        assert set(courts) == {COURT_1, COURT_2, CENTER_COURT}, courts

        # The time left the cells and became the row header, as a RANGE (user
        # ruling 2026-09-04): "6 AM – 7 AM", the :00 dropped, midnight as
        # "12 MN" at either end of the day and noon as "12 NN" (ruled the same
        # day). MN and NN are a pair: "12 AM" and "12 PM" are the two labels
        # people actually misread, so a column that says one without the other
        # invites the mistake at the other end of the day. The dash is real DOM
        # text, not a CSS ::before — textContent cannot see a pseudo-element, so
        # a separator drawn in CSS would leave the one thing the user asked for
        # untested while this row stayed green.
        times = page.eval_on_selector_all(
            ".cbt-matrix tbody th.cbt-timecell", "els => els.map(e => e.textContent.trim())"
        )
        assert times, "the matrix has no time column"
        part = r"(?:\d{1,2}(?::\d{2})? (?:AM|PM)|12 MN|12 NN)"
        for shown in times:
            assert re.match(rf"^{part} – {part}$", shown), (shown, times)
            start, end = [half.strip() for half in shown.split("–")]
            assert start != end, shown
        # Noon is provable on EVERY seeded branch (they all span 06:00–22:00),
        # unlike 12 MN which only E2EF-main can render — so it is asserted here
        # rather than parked in the clean-branch row. "12 PM" must be gone.
        assert any("12 NN" in shown for shown in times), times
        assert not any("12 PM" in shown for shown in times), times
        # The end really is THIS slot's end, not the next row's start dressed up:
        # every seeded branch carries buffer_minutes 0, so the grid is
        # contiguous and one row's end must be the next row's start. A branch
        # that grows a buffer makes this red on purpose — read it as "look
        # here", not as a flake. The LAST row has no successor, which is what
        # keeps a 24:00 end ("12 MN", numerically smaller than its own start)
        # out of the chain.
        for above, below in zip(times, times[1:]):
            assert above.split("–")[1].strip() == below.split("–")[0].strip(), (
                f"the grid is not contiguous: {above!r} then {below!r} — either the "
                "row header shows the wrong end, or this branch now has a buffer"
            )

        # Every row is the time header plus one cell per court — a short column
        # would otherwise shift the hours below it out of line.
        widths = page.eval_on_selector_all(
            ".cbt-matrix tbody tr", "els => els.map(e => e.children.length)"
        )
        assert set(widths) == {len(courts) + 1}, widths

        cells = page.eval_on_selector_all(
            ".cbt-matrix .cbt-slot",
            """els => els.map(e => ({
                court: e.dataset.court,
                start: e.dataset.start,
                status: e.dataset.status,
                rate: ((e.querySelector('.cbt-slot-rate') || {}).textContent || '').trim(),
                word: ((e.querySelector('.cbt-slot-state') || {}).textContent || '').trim(),
            }))""",
        )
        assert len(cells) == len(times) * len(courts), (len(cells), len(times), len(courts))
        for cell in cells:
            assert cell["rate"].startswith("₱"), cell
            assert cell["word"], cell
            assert cell["court"] in courts and cell["start"], cell

    def test_the_matrix_scrolls_in_its_own_box_and_no_cell_hides_under_the_time_column(
        self, page: Page
    ):
        """The sticky time column is this stylesheet's ONE exception to its own
        no-new-sticky rule, and this is the failure that rule exists to prevent:
        a cell scrolled hard against the left edge sitting UNDER the column,
        where the click lands on the column instead of the slot.

        Selecting a slot only GETs a quote — this file still books nothing.
        """
        _open_matrix(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        # The table scrolls INSIDE its wrapper; the page never moves sideways.
        page.eval_on_selector("#cbt-matrix-wrap", "el => { el.scrollLeft = el.scrollWidth; }")
        page_overflow = page.evaluate(
            """() => {
                const root = document.scrollingElement;
                return {scroll: root.scrollWidth, view: window.innerWidth};
            }"""
        )
        assert page_overflow["scroll"] <= page_overflow["view"] + 1, page_overflow

        # Scrolled fully right, the FIRST court's column is the one at risk.
        cell = page.locator(f".cbt-matrix .cbt-slot[data-court='{COURT_1}'][data-status='available']").first
        expect(cell).to_have_count(1)
        cell.click()  # times out with "intercepts pointer events" if it is covered
        expect(cell).to_have_attribute("aria-pressed", "true", timeout=15000)
        expect(page.locator("#cbt-checkoutbar")).to_be_visible(timeout=20000)

    def test_the_sketch_tap_highlights_a_whole_column(self, page: Page):
        """Backlog B32's tap target under B33. A court is a COLUMN now, so the
        highlight the sketch used to put on one card lands on the header and
        every cell beneath it — and stays exclusive."""
        _open_matrix(page, MEDIA_COMPANY, MEDIA_BRANCH_SLUG, D_VIEW)

        head = page.locator(f".cbt-matrix th.cbt-colhead[data-court='{CENTER_COURT}']")
        expect(head).to_have_count(1)
        assert "cbt-col--focus" not in (head.get_attribute("class") or "")

        page.locator("#cbt-sketch summary").click()
        page.locator(f"#cbt-sketch .cbt-sketch-court[data-court='{CENTER_COURT}']").click()
        page.wait_for_function(
            """(court) => {
                const th = document.querySelector(`th.cbt-colhead[data-court="${court}"]`);
                return th && th.classList.contains('cbt-col--focus');
            }""",
            arg=CENTER_COURT,
            timeout=15000,
        )
        # Exclusive: one court is "the one you asked about".
        assert page.locator("th.cbt-col--focus").count() == 1
        lit = page.eval_on_selector_all(
            ".cbt-matrix td.cbt-col--focus", "els => [...new Set(els.map(e => e.dataset.court))]"
        )
        assert lit == [CENTER_COURT], lit
        pressed = page.locator("#cbt-sketch .cbt-sketch-court[aria-pressed='true']")
        assert pressed.count() == 1
        assert pressed.get_attribute("data-court") == CENTER_COURT


    def test_company_without_media_or_layout_is_clean(self, page: Page):
        """A tenant that uploaded nothing gets NOTHING — no button, no empty box.

        Most tenants have never configured a floor plan, and a "Photos" button
        that opens an empty modal is worse than no button. The last block is the
        additive-DOM promise: this section touched the page every other portal
        file selects on, so the section-9 contract is spot-checked here.
        """
        _open_book(page, CLEAN_COMPANY, CLEAN_BRANCH_SLUG)

        assert page.locator("[data-testid='facility-photos']").count() == 0
        assert page.locator("[data-testid='court-layout']").count() == 0
        assert page.locator("#cbt-sketch").count() == 0
        assert page.locator("#cbt-banner").count() == 0
        # The viewer SHELL ships on every portal page, but it must be shut and
        # hold nothing: a facility with no pictures has no door to open.
        assert page.locator("#cbt-photo-viewer").is_hidden()
        assert page.locator("[data-testid='viewer-thumb']").count() == 0
        # B37: an address without a pin is the address line and nothing else.
        where = page.locator("#cbt-where[data-pinned='0']")
        expect(where).to_have_count(1)
        expect(where).to_contain_text(CLEAN_ADDRESS)
        assert where.locator("details, iframe, a").count() == 0

        # The section-9 booking DOM that files 05/07/10/12/15 select on.
        expect(page.locator("#cbt-company-name")).to_have_text("E2E Fast Courts")
        expect(page.locator("#cbt-branch-name")).to_contain_text("E2E Fast Main")
        assert page.locator(".cbt-matrix .cbt-slot").count() > 0
        assert page.locator("#cbt-grid").count() == 1
        # 2026-09-04 ruling: midnight reads "12 MN", not "12 AM". This branch is
        # where that is provable — E2EF-main closes at 23:59, which the slot
        # engine turns into a 24:00:00 end (section-4 addendum). Every other
        # seeded branch closes at 22:00 and would never show it.
        column = page.eval_on_selector_all(
            ".cbt-matrix tbody th.cbt-timecell", "els => els.map(e => e.textContent.trim())"
        )
        assert column, "the clean branch rendered no time column"
        assert column[-1].endswith("– 12 MN"), (
            f"the last row reads {column[-1]!r}: a 23:59 close is midnight, and "
            "midnight prints as 12 MN"
        )
        assert page.locator("#cbt-checkoutbar").count() == 1
        assert page.locator(".cbt-date[data-date]").count() > 0
