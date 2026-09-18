"""
E2E file 06 — Board operations (section-7; PLAN §10 row 06).

The Court Board page end-to-end: the B33 time x court matrix and its collapsed
Court Layout sketch, walk-in Cash quick-book
(instant green + Paid & Verified invoice), extend (original turns blue),
court-level block + the spanning-book rejection, whole-branch closure, and a
Fund-Transfer hold's countdown on the board and in the Pending Payments panel.

DRIVEN BY THE FRONT DESK, per branch (Backlog B43, 2026-09-05). Every row logs
in as the STAFF seat of the company whose board it opens — Elsa for E2E Fast,
Stella for Ayala — because a company seat may only act on its own company, so a
file that drives two branches logs in twice, exactly as two people would. Until
this row the whole file ran as Administrator on the stated grounds that "E2EF
has no staff user"; the seed now ships one, and an Administrator arrange can
build a world no real seat could, which is the reason the ruling exists.

The BRANCH split is unchanged and is not about seats: the E2EF rows want its
24-hour grid, and the countdown row needs AYALA's 30-minute base clock (E2EF's
1-minute expiry would kill it mid-test).

Date map (all relative to the run day): +40 BGC floor render (read-only) AND
E2EF Cash quick-book; +41 E2EF extend; +42 E2EF court block; +43 BGC
branch-wide block (bgc's 3 courts make "every court red" a real assertion —
ducky-approved deviation); +44 BGC Fund-Transfer countdown (30-min clock).
Same _cancel/_clear re-runnability treatment on both branches.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright, expect

from helpers import gestures
from helpers.auth import login_as
from helpers.board import (
    AYALA_STAFF,
    BGC_BRANCH,
    BGC_COURTS,
    CUSTOMER,
    E2EF_BRANCH,
    E2EF_COURT,
    E2EF_COURT_2,
    E2EF_STAFF,
    _submit_cart,
    book_slot_via_api,
    cart_count,
    platform_api,
    cancel_active_bookings,
    clear_blocks,
    load_board,
    open_cart_dialog,
    quick_book,
    select_slots,
    slot,
    wait_block_grid,
    wait_channels_settled,
    wait_dialog,
    wait_money_settled,
)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

D_FLOOR = (date.today() + timedelta(days=40)).isoformat()
D_CASH = (date.today() + timedelta(days=40)).isoformat()
D_EXTEND = (date.today() + timedelta(days=41)).isoformat()
D_BLOCK = (date.today() + timedelta(days=42)).isoformat()
D_BRANCH_BLOCK = (date.today() + timedelta(days=43)).isoformat()
D_RESERVED = (date.today() + timedelta(days=44)).isoformat()
# Section-11 membership addendum (dates used by no other file).
D_MEMBER = (date.today() + timedelta(days=46)).isoformat()
D_NONMEMBER = (date.today() + timedelta(days=47)).isoformat()
# Backlog B42. Claims +88 from the ledger — the first free day. Read-only: the
# row opens the quick-book dialog, measures it and closes it without booking.
D_TOTAL_VISIBLE = (date.today() + timedelta(days=88)).isoformat()
# Backlog B46, claimed from the ledger (+88 was the last taken):
#   +89  E2EF — two courts in ONE cart, one payment, one statement
#   +90  E2EF — a slot taken while it sat in the cart
D_CART = (date.today() + timedelta(days=89)).isoformat()
D_CART_RACE = (date.today() + timedelta(days=90)).isoformat()

RESERVED_COURT = "AYALA-bgc-court-2"
MEMBER_COURT = "AYALA-bgc-court-2"  # ₱450/hr
MIA = "cust.mia@example.com"  # AYALA VIP 20% (section-11 seeds)


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


@pytest.mark.e2e
class TestBoardOps:
    def test_the_board_reads_across_one_row_per_hour(self, page: Page):
        """Backlog B33. The front desk's question is "what is free at 3pm?", so
        the board is a time x court MATRIX: one row per grid slot, one column
        per court, and every cell still the same `.cbt-slot` contract."""
        login_as(page, AYALA_STAFF)
        load_board(page, BGC_BRANCH, D_FLOOR)
        matrix = page.locator(".cbt-matrix")
        assert matrix.count() == 1, "the board did not render its matrix"
        assert page.locator("th.cbt-colhead").count() == 3, "BGC has 3 courts"

        rows = page.locator(".cbt-matrix tbody tr")
        assert rows.count() > 0, "the matrix has no hours"
        # Every row offers a cell per court — that is what "read across" means.
        first = rows.first
        assert first.locator("td.cbt-cell").count() == 3
        # Backlog B45. THIS ASSERTION USED TO BE `.count("–") == 1`, which passes
        # on `06:00 – 07:00` AND on `6 AM – 7 AM` — it could not fail on a
        # format, and for three days it did not, while the desk printed machine
        # time at a customer looking at "6 AM" on their phone. It is the STRING
        # now. AYALA-bgc opens 06:00 every day of the week, so this is stable
        # whatever weekday the run lands on.
        assert first.locator("th.cbt-timecell").inner_text().strip() == "6 AM – 7 AM", (
            "the time column must show the SPAN in the app's one time language "
            f"(got {first.locator('th.cbt-timecell').inner_text()!r})"
        )
        assert page.locator(".cbt-slot").count() > 0

        # The user's own words: "always show amount and status of that time
        # slot". A cell whose price and state only appear on HOVER is a blank
        # cell to the person standing at the desk — this pins both AT REST.
        free = page.locator(".cbt-slot[data-status='available']").first
        rate = free.locator("[data-testid='slot-rate']")
        expect(rate).to_be_visible()
        assert "₱" in rate.inner_text(), rate.inner_text()
        hint = free.locator(".cbt-slot-hint")
        expect(hint).to_be_visible()
        assert (
            hint.evaluate("node => window.getComputedStyle(node).opacity") == "1"
        ), "the Book hint is invisible until hover — the cell reads as empty"

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / "board_matrix.png"), full_page=True)

    def test_the_court_layout_ships_collapsed_and_focuses_a_column(self, page: Page):
        """Backlog B33 (user ruling 2026-09-05). The sketch is the "which one is
        by the door?" answer, not the working grid — so it is folded away, and
        opening it and tapping a court brings that column into view."""
        login_as(page, AYALA_STAFF)
        load_board(page, BGC_BRANCH, D_FLOOR)
        details = page.locator("details[data-testid='court-layout']")
        assert details.count() == 1, "BGC's seeded floor plan rendered no sketch"
        assert details.evaluate("node => node.open") is False, (
            "the Court Layout is open on arrival — the ruling says collapsed"
        )
        # A human opens it by clicking the summary, then taps a court.
        details.locator("summary").click()
        assert details.evaluate("node => node.open") is True

        target = BGC_COURTS[2]
        page.locator(f".cbt-sketch-court[data-court='{target}']").click()
        focused = page.locator(f"th.cbt-colhead[data-court='{target}']")
        expect(focused).to_have_class(re.compile(r"cbt-col--focus"))
        page.screenshot(
            path=str(SCREENSHOT_DIR / "board_layout_collapsed.png"), full_page=True
        )

    def test_the_total_is_on_screen_before_you_can_book(self, page: Page):
        """Backlog B42. WYSIWYG money (S18/B4) is a promise to the person at the
        desk: never charge a number the screen has not caught up to. It was
        being broken by LAYOUT, silently.

        MEASURED on the build before this row (2026-09-05): the dialog's
        .modal-body had max-height:none and overflow-y:visible, so it had no
        height budget. At one Cash slot it filled 959 of a 960px viewport; add
        walk-in's two rows, or Fund Transfer's channel row, and the total went
        off the bottom of the screen while every existing assertion stayed
        green — `[data-testid='quote-total']` was in the DOM the whole time.

        So this row asserts GEOMETRY, not presence, and it asserts it in the
        state that actually broke: walk-in + Fund Transfer, the fullest the form
        gets. B33 taught the same lesson three days earlier with a matrix whose
        every cell was blank.
        """
        login_as(page, E2EF_STAFF)
        load_board(page, E2EF_BRANCH, D_TOTAL_VISIBLE)

        # B46: the two slots are SELECTED on the board, not typed into a box.
        select_slots(page, E2EF_COURT, "10:00:00", 2)
        open_cart_dialog(page)
        gestures.check(page, "walk_in", True, scope=gestures.DIALOG)
        gestures.fill(page, "customer_name", "Walk-in Wilma", scope=gestures.DIALOG)
        gestures.fill(page, "customer_phone", "0917 555 0142", scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Fund Transfer", scope=gestures.DIALOG)
        page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
        wait_channels_settled(page)
        wait_money_settled(page)

        # B42's named groups, in the order the desk works in. B46 retired the
        # middle one: "How long" held the `number_of_slots` Int, and length is
        # now said on the board.
        legends = page.locator(".modal.show .form-section .section-head").all_inner_texts()
        assert [t.strip() for t in legends if t.strip()][:3] == [
            "Who is playing", "Price", "How they pay"
        ], legends

        geometry = page.evaluate(
            """() => {
                const wrap = cur_dialog.$wrapper[0];
                const node = cur_dialog.fields_dict.estimate.$wrapper[0];
                const total = node.querySelector("[data-testid='quote-total']");
                const body = wrap.querySelector('.modal-body');
                const box = total ? total.getBoundingClientRect() : null;
                // What a THUMB would actually hit at the total's centre. A box
                // that fits the viewport can still be behind the footer — the
                // build this row was written against rendered the total at
                // y=901..943 on a 960px screen, inside the viewport by the
                // numbers and invisible to the human. Only the hit test knows.
                let hit = null, occludedBy = null;
                if (box && box.height) {
                    const el = document.elementFromPoint(
                        Math.round(box.left + box.width / 2),
                        Math.round(box.top + box.height / 2));
                    hit = !!el && (total.contains(el) || el.contains(total));
                    if (!hit && el) occludedBy = el.className || el.tagName;
                }
                return {
                    inFooter: node.parentElement.classList.contains('modal-footer'),
                    bodyScrolls: getComputedStyle(body).overflowY === 'auto',
                    viewport: window.innerHeight,
                    totalTop: box ? Math.round(box.top) : null,
                    totalBottom: box ? Math.round(box.bottom) : null,
                    totalHeight: box ? Math.round(box.height) : null,
                    hit: hit, occludedBy: occludedBy,
                    totalText: total ? total.innerText.trim() : null,
                    totalOpacity: total ? getComputedStyle(total).opacity : null,
                };
            }"""
        )
        assert geometry["inFooter"], geometry
        assert geometry["bodyScrolls"], geometry
        assert geometry["totalHeight"], geometry
        # THE THREE ASSERTIONS THIS ROW EXISTS FOR — the number is on screen, at
        # rest, with the form at its tallest, and nothing is sitting on top of
        # it. The occlusion check is the one that matters: a bounding box inside
        # the viewport is NOT the same as a figure a human can read.
        assert geometry["totalBottom"] <= geometry["viewport"], (
            "the total is below the fold — staff would click Book without ever "
            f"seeing the price: {geometry}"
        )
        assert geometry["hit"], (
            "something is covering the total — it is rendered but unreadable, "
            f"which is how this shipped in the first place: {geometry}"
        )
        assert geometry["totalOpacity"] == "1", geometry
        assert "₱" in geometry["totalText"], geometry

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / "b42_quick_book.png"))
        page.evaluate("() => cur_dialog.hide()")

    def test_two_courts_in_one_cart_are_one_payment_and_one_statement(
        self, page: Page
    ):
        """BACKLOG B46, THE HEADLINE. Claims ledger day +89.

        The user, 2026-09-05: *"THE CLIENT CAN NOW BOOK MULTIPLE DATES, MULTIPLE
        COURTS, MULTIPLE TIME AND THE CBT-COURT-BOARD STILL RELY ON FUCKING SLOT
        AS INTEGER."* A customer booking two courts online has had one payment,
        one countdown and ONE billing statement since B35/B36; the same customer
        phoning the desk got two of each.

        Driven entirely by GESTURE — three cells tapped, a bar tapped, a
        customer typed — because the point of the row is that a person can now
        express this at all.
        """
        login_as(page, E2EF_STAFF)
        cancel_active_bookings(page, E2EF_COURT, D_CART)
        cancel_active_bookings(page, E2EF_COURT_2, D_CART)
        load_board(page, E2EF_BRANCH, D_CART)

        # Two ADJACENT hours on court 1 and one hour on court 2. The server
        # merges the first pair into ONE booking, so three taps are two
        # bookings — and that merge is _normalise_cart's rule, not the board's.
        select_slots(page, E2EF_COURT, "10:00:00", 2)
        select_slots(page, E2EF_COURT_2, "14:00:00", 1)
        assert cart_count(page) == 3, "three cells should be pressed"

        bar = page.locator("[data-testid='cart-bar']")
        expect(bar).to_be_visible()
        assert "3" in bar.locator("[data-testid='cart-count']").inner_text()

        open_cart_dialog(page)
        # The dialog states the SERVER's view: two bookings, not three taps.
        lines = page.locator(".modal.show [data-testid='cart-line']")
        assert lines.count() == 2, (
            "the cart should read as TWO bookings — the adjacent pair is one run"
        )
        assert "10 AM – 12 NN" in lines.nth(0).inner_text(), lines.nth(0).inner_text()
        assert "2 PM – 3 PM" in lines.nth(1).inner_text(), lines.nth(1).inner_text()

        gestures.fill(page, "customer", CUSTOMER, scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
        wait_money_settled(page)

        total = page.locator(".modal.show [data-testid='quote-total']").inner_text()
        assert "₱" in total, total
        summary = page.locator(".modal.show [data-testid='cart-summary']").inner_text()
        assert "2" in summary, summary

        result = _submit_cart(page)["payload"]
        assert result["count"] == 2, result
        assert result["booking_group"], "two bookings in one cart must share a group"

        # ONE billing document for the pair — B36's rule, reached from the desk.
        names = [row["name"] for row in result["bookings"]]
        invoices = set()
        for name in names:
            booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
            assert booking["booking_status"] == "Confirmed", booking
            assert booking["booking_group"] == result["booking_group"]
            invoices.add(booking["billing_doc"])
        assert len(invoices) == 1, f"one cart, {len(invoices)} statements: {invoices}"
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{invoices.pop()}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]
        assert float(invoice["total_amount"]) == float(result["total_amount"])

        # And the board now shows all three cells booked, with nothing selected.
        for court, start in (
            (E2EF_COURT, "10:00:00"),
            (E2EF_COURT, "11:00:00"),
            (E2EF_COURT_2, "14:00:00"),
        ):
            page.wait_for_selector(
                f".cbt-slot[data-court='{court}'][data-start='{start}']"
                "[data-booking-status='Confirmed']",
                timeout=15000,
            )
        assert cart_count(page) == 0, "the cart survived its own checkout"
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / "b46_desk_cart.png"), full_page=True)

    def test_a_slot_taken_while_it_sat_in_the_cart_refuses_the_WHOLE_cart(
        self, page: Page
    ):
        """Claims ledger day +90. All-or-nothing, said out loud.

        A partial cart is worse than a refusal: the operator has already read a
        total to the person in front of them. The server names the slot that
        went — in the app's one time language (B45) — and books nothing.
        """
        login_as(page, E2EF_STAFF)
        cancel_active_bookings(page, E2EF_COURT, D_CART_RACE)
        cancel_active_bookings(page, E2EF_COURT_2, D_CART_RACE)
        load_board(page, E2EF_BRANCH, D_CART_RACE)

        select_slots(page, E2EF_COURT, "09:00:00", 1)
        select_slots(page, E2EF_COURT_2, "09:00:00", 1)
        assert cart_count(page) == 2

        # Somebody else takes one of them — a separate HTTP request, so a real
        # race rather than a simulated one.
        stolen = book_slot_via_api(page, E2EF_COURT_2, D_CART_RACE, "09:00:00")
        assert stolen

        open_cart_dialog(page)
        gestures.fill(page, "customer", CUSTOMER, scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
        wait_money_settled(page)

        error = _submit_cart(page, expect_ok=False)["name"]
        assert "no longer available" in error.lower(), error
        assert "9 AM" in error, f"the refusal must name the hour in words: {error}"

        # NOTHING was booked: the surviving pick is still free.
        free = _get_json(
            page,
            "/api/resource/CBT Court Booking?filters="
            f'[["court","=","{E2EF_COURT}"],["booking_date","=","{D_CART_RACE}"],'
            '["booking_status","in",["Reserved","Confirmed","Extended"]]]',
        )
        assert free == [], f"a refused cart booked something anyway: {free}"

    def test_quick_book_cash_confirms_and_invoices(self, page: Page):
        login_as(page, E2EF_STAFF)
        cancel_active_bookings(page, E2EF_COURT, D_CASH)
        load_board(page, E2EF_BRANCH, D_CASH)
        assert slot(page, E2EF_COURT, "10:00:00").get_attribute("data-status") == "available"

        name = quick_book(page, E2EF_COURT, "10:00:00", "Cash")
        assert name.startswith("BK-E2EF-"), name

        # The board reloads asynchronously after the book — wait for the
        # re-rendered slot, never read the pre-reload DOM.
        booked = page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        assert "cbt-slot-confirmed" in (booked.get_attribute("class") or "")

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        assert booking["booking_status"] == "Confirmed"
        assert booking["billing_doc"], "invoice not linked"
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{booking['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]

    def test_extend_confirmed_session(self, page: Page):
        login_as(page, E2EF_STAFF)
        cancel_active_bookings(page, E2EF_COURT, D_EXTEND)
        load_board(page, E2EF_BRANCH, D_EXTEND)
        original = quick_book(page, E2EF_COURT, "10:00:00", "Cash")

        # Wait for the post-book reload to flip the slot — clicking the stale
        # "available" DOM would open the quick-book dialog, not details.
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        slot(page, E2EF_COURT, "10:00:00").click()
        wait_dialog(page)  # details dialog
        page.evaluate("() => cur_dialog.get_primary_btn().click()")  # Extend Session
        page.wait_for_function(
            "() => window.cur_dialog && (cur_dialog.title || '').startsWith('Extend')",
            timeout=15000,
        )
        with page.expect_response(
            lambda r: "extend_booking" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, (
            f"extend_booking: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )
        extension = resp_info.value.json()["message"]

        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        original_slot = page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Extended']",
            timeout=15000,
        )
        assert "cbt-slot-extended" in (original_slot.get_attribute("class") or "")
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='11:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        ext = _get_json(page, f"/api/resource/CBT Court Booking/{extension}")
        assert ext["extended_from"] == original

    def test_block_court_and_the_blocked_hour_cannot_enter_the_cart(
        self, page: Page, playwright: Playwright
    ):
        """Backlog B46 re-aimed this row, and the change is an IMPROVEMENT worth
        stating rather than a coverage loss.

        It used to book 10:00 with `number_of_slots=2` so the run spanned into a
        blocked 11:00, and asserted the SERVER's refusal. A cart cannot express
        that: a blocked cell is not selectable, so the desk can no longer ASK
        for an invalid window at all. What is asserted here now is the UI truth
        — the blocked hour says why and stays out of the cart — while the
        server's own all-or-nothing refusal keeps its backend row
        (`tests/test_desk_cart.py::test_a_blocked_hour_refuses_the_WHOLE_cart_
        and_leaves_nothing_behind`), and the RACE version of the same refusal is
        driven end-to-end below.
        """
        login_as(page, E2EF_STAFF)
        api = platform_api(playwright)
        try:
            clear_blocks(api, E2EF_BRANCH, D_BLOCK)
        finally:
            api.dispose()
        cancel_active_bookings(page, E2EF_COURT, D_BLOCK)
        load_board(page, E2EF_BRANCH, D_BLOCK)

        page.locator(".page-head button:has-text('Block Slots')").click()
        wait_dialog(page)
        # Section-21 (Backlog B12): From/To are now Selects of the branch's real
        # grid times, and their options arrive from a server round trip. Set the
        # court and date FIRST, then wait for the grid — a set_value on an
        # unpopulated <select> is silently dropped.
        gestures.fill(page, "court", E2EF_COURT, scope=gestures.DIALOG)
        gestures.fill(page, "block_date", D_BLOCK, scope=gestures.DIALOG)
        wait_block_grid(page, "11:00:00")
        # From BEFORE To, awaited between: choosing a From re-filters To to the
        # ends after it. Filling them together would race that re-filter.
        gestures.fill(page, "start_time", "11:00:00", scope=gestures.DIALOG)
        wait_block_grid(page, "12:00:00", field="end_time")
        gestures.fill(page, "end_time", "12:00:00", scope=gestures.DIALOG)
        gestures.fill(page, "reason", "Maintenance", scope=gestures.DIALOG)
        with page.expect_response(
            lambda r: "create_block" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, (
            f"create_block: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )

        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='11:00:00']"
            "[data-status='blocked']",
            timeout=15000,
        )

        # A blocked cell TELLS the operator why, and does not become a pick.
        slot(page, E2EF_COURT, "11:00:00").click()
        wait_dialog(page)
        reason = page.locator(".modal.show .msgprint").first.inner_text()
        assert "blocked" in reason.lower(), reason
        assert "maintenance" in reason.lower(), reason
        page.evaluate(
            """() => { frappe.msg_dialog.hide(); $('.modal.show').modal('hide'); }"""
        )
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        assert cart_count(page) == 0, "a blocked hour went into the cart"
        assert page.locator("[data-testid='cart-bar']").is_hidden()

        # And the free hour beside it is still perfectly bookable — the block
        # took ONE cell, not the court.
        assert slot(page, E2EF_COURT, "10:00:00").get_attribute("data-status") == "available"
        select_slots(page, E2EF_COURT, "10:00:00", 1)
        assert cart_count(page) == 1
        page.locator("[data-action='cart-clear']").click()
        assert cart_count(page) == 0

    def test_whole_branch_block_reddens_every_court(self, page: Page, playwright: Playwright):
        login_as(page, AYALA_STAFF)
        api = platform_api(playwright)
        try:
            clear_blocks(api, BGC_BRANCH, D_BRANCH_BLOCK)
        finally:
            api.dispose()
        load_board(page, BGC_BRANCH, D_BRANCH_BLOCK)

        page.locator(".page-head button:has-text('Block Slots')").click()
        wait_dialog(page)
        # Section-21 (Backlog B12): grid-time Selects — see the court-block test
        # above for why the date settles before the times and From before To.
        # No court is set here on purpose: an empty court closes the WHOLE branch.
        gestures.fill(page, "block_date", D_BRANCH_BLOCK, scope=gestures.DIALOG)
        wait_block_grid(page, "13:00:00")
        gestures.fill(page, "start_time", "13:00:00", scope=gestures.DIALOG)
        wait_block_grid(page, "15:00:00", field="end_time")
        gestures.fill(page, "end_time", "15:00:00", scope=gestures.DIALOG)
        gestures.fill(page, "reason", "Holiday", scope=gestures.DIALOG)
        with page.expect_response(
            lambda r: "create_block" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, (
            f"create_block: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )

        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        page.wait_for_selector(
            f".cbt-slot[data-court='{BGC_COURTS[0]}'][data-start='13:00:00']"
            "[data-status='blocked']",
            timeout=15000,
        )
        for court in BGC_COURTS:
            for start in ("13:00:00", "14:00:00"):
                assert (
                    slot(page, court, start).get_attribute("data-status") == "blocked"
                ), f"{court} {start} not blocked"

    def test_fund_transfer_reserved_countdown_on_board_and_panel(self, page: Page):
        login_as(page, AYALA_STAFF)
        cancel_active_bookings(page, RESERVED_COURT, D_RESERVED)
        load_board(page, BGC_BRANCH, D_RESERVED)
        name = quick_book(page, RESERVED_COURT, "10:00:00", "Fund Transfer")
        assert name.startswith("BK-AYALA-"), name

        reserved_el = page.wait_for_selector(
            f".cbt-slot[data-court='{RESERVED_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Reserved']",
            timeout=15000,
        )
        assert "cbt-slot-reserved" in (reserved_el.get_attribute("class") or "")
        reserved = slot(page, RESERVED_COURT, "10:00:00")
        chip = reserved.locator(".cbt-count")
        assert chip.count() == 1, "Reserved slot must show a countdown chip"
        chip_text = chip.inner_text().strip()
        # 30-min AYALA base clock: a live mm:ss countdown, never already "due".
        assert chip_text and chip_text != "due", chip_text

        panel_item = page.locator(f".cbt-pending-item[data-booking='{name}']")
        panel_item.wait_for(state="visible", timeout=15000)
        panel_chip = panel_item.locator(".cbt-count").inner_text().strip()
        assert panel_chip and panel_chip != "due", panel_chip
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "board_pending_panel.png"), full_page=True
        )

    def test_membership_discount_autofills_in_quick_book(self, page: Page):
        """Section-11: selecting a member in the quick-book dialog fills the
        discount AND says why.

        The hint is the point. An untouched Percent field reads as 0, which is
        indistinguishable from a deliberate 0 — so the dialog sends exactly
        what staff can see, and a failed membership lookup has to be visible
        rather than silently charging list price.
        """
        login_as(page, AYALA_STAFF)
        cancel_active_bookings(page, MEMBER_COURT, D_MEMBER)
        load_board(page, BGC_BRANCH, D_MEMBER)

        select_slots(page, MEMBER_COURT, "09:00:00", 1)
        open_cart_dialog(page)
        gestures.fill(page, "customer", MIA, scope=gestures.DIALOG)
        hint = page.locator("[data-testid='member-hint']")
        hint.wait_for(state="visible", timeout=15000)
        assert "VIP" in hint.inner_text(), hint.inner_text()
        assert (
            float(page.evaluate("() => cur_dialog.get_value('discount_percent')")) == 20.0
        )
        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        wait_money_settled(page)

        booking = _submit_cart(page)["name"]

        # ₱450 BGC court-2 − 20% = ₱360, and the invoice must agree.
        detail = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert float(detail["discount_percent"]) == 20.0, detail
        assert float(detail["total_amount"]) == 360.0, detail
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{detail['billing_doc']}"
        )
        assert float(invoice["subtotal"]) == 450.0, invoice
        assert float(invoice["discount_amount"]) == 90.0, invoice
        assert float(invoice["total_amount"]) == 360.0, invoice

    def test_non_member_quick_book_stays_at_list_price(self, page: Page):
        """The other half of the same rule — the hint says so explicitly."""
        login_as(page, AYALA_STAFF)
        cancel_active_bookings(page, MEMBER_COURT, D_NONMEMBER)
        load_board(page, BGC_BRANCH, D_NONMEMBER)

        select_slots(page, MEMBER_COURT, "09:00:00", 1)
        open_cart_dialog(page)
        gestures.fill(page, "customer", "cust.pia@example.com", scope=gestures.DIALOG)
        hint = page.locator("[data-testid='member-hint']")
        hint.wait_for(state="visible", timeout=15000)
        assert "No membership" in hint.inner_text(), hint.inner_text()
        assert (
            float(page.evaluate("() => cur_dialog.get_value('discount_percent')")) == 0.0
        )
