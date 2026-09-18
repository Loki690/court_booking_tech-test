"""
E2E — the CART: several courts and several dates, ONE checkout (Backlog B35).

The customer job the row exists for, driven the way a customer drives it: tap
slots, read what it costs, pay once. Everything here goes through the real
grid and the real modal — `helpers/portal.reserve` posts to the API and would
prove nothing about the page that has to assemble a basket.

WHAT THIS FILE IS ACTUALLY PINNING. The client tracks SELECTED SLOTS and the
server decides what the bookings are: `_normalise_cart` unions the selection per
court+date and splits it into maximal contiguous runs, one run = one booking =
one booking fee. So the interesting assertions are the ones where the count of
taps and the count of BOOKINGS differ — four taps on one court are one booking,
two taps with a gap are two. A client that merged for itself would pass the
first and could quietly disagree with the server on the second.

Date map (relative to the run day). File 12 carries the re-verified ledger and
records **+75 upward as FREE**; file 15's copy stops at +68 and is the shorter
one. THIS FILE CLAIMS **+75 and +76**:

  +75  AYALA-bgc-court-1 and -court-2 — the multi-court rows
  +76  AYALA-bgc-court-1 — the second date of the multi-date row

Both are inside the seeded AYALA `advance_booking_days = 90`, which this file
REQUIRES: it books as a real customer, and the controller refuses a customer
booking past the horizon.

PIA HOLDS NO AYALA MEMBERSHIP by seed design, so every peso here is
undiscounted — a membership appearing on her account would break this file
rather than fix it. AYALA is Percentage-billed, so no booking fee lands on
these totals either; the fee's own path is `test_booking_fee.py`.

No sleeps. Every assertion waits on a rendered node or a server response.
"""
from datetime import date, timedelta

from playwright.sync_api import Page, expect

from helpers.board import cancel_active_bookings

COMPANY = "ayala-courts"
BRANCH = "bgc"
COURT_1 = "AYALA-bgc-court-1"  # ₱400
COURT_2 = "AYALA-bgc-court-2"  # ₱450

D_ONE = (date.today() + timedelta(days=75)).isoformat()
D_TWO = (date.today() + timedelta(days=76)).isoformat()


def _open_grid(page: Page, day: str):
    page.goto(
        f"/book?c={COMPANY}&b={BRANCH}&d={day}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)


def _cell(page: Page, court: str, start: str):
    return page.locator(f".cbt-slot[data-court='{court}'][data-start='{start}']")


class TestCartCheckout:
    def test_two_courts_on_one_date_is_two_bookings_and_one_payment(
        self, page: Page, customer_page: Page
    ):
        """The row's own headline. Two courts at the same hour can never merge
        — different courts — so two taps really are two bookings, and the bar
        must say so from the SERVER's count rather than from how many cells are
        lit."""
        for court in (COURT_1, COURT_2):
            cancel_active_bookings(page, court, D_ONE)

        _open_grid(customer_page, D_ONE)
        _cell(customer_page, COURT_1, "10:00:00").click()
        _cell(customer_page, COURT_2, "10:00:00").click()

        label = customer_page.locator("#cbt-selection-label")
        expect(label).to_contain_text("2", timeout=20000)
        # MONEY ONLY in the total element — ₱400 + ₱450, undiscounted.
        expect(customer_page.locator("#cbt-selection-total")).to_contain_text("850")

        customer_page.locator("#cbt-review").click()
        items = customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
        expect(items).to_have_count(2, timeout=15000)
        expect(customer_page.locator("#cbt-quote-lines")).to_contain_text("850.00")

        customer_page.locator("#cbt-reserve").click()
        ref = customer_page.locator("#cbt-confirmed-ref")
        expect(ref).to_be_visible(timeout=20000)
        # Two references, one confirmation, one amount due — one transfer.
        assert ref.inner_text().count("BK-AYALA-") == 2, ref.inner_text()
        expect(customer_page.locator("#cbt-confirmed-lines")).to_contain_text("850.00")
        # One pay-by clock for the whole cart.
        expect(customer_page.locator("#cbt-confirmed-countdown")).not_to_have_text(
            "—", timeout=20000
        )

    def test_the_cart_survives_a_date_change(self, page: Page, customer_page: Page):
        """"Several DATES in one checkout" is the half that used to be
        impossible: selectDate called clearSelection(), so changing the day
        emptied the basket. One tap on each of two days must add up to two
        bookings — and the first day's pick has to still be pressed when you
        page back to it, or the cart is only pretending to hold it."""
        cancel_active_bookings(page, COURT_1, D_ONE)
        cancel_active_bookings(page, COURT_1, D_TWO)

        _open_grid(customer_page, D_ONE)
        _cell(customer_page, COURT_1, "14:00:00").click()
        expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
            "1", timeout=20000
        )

        # Move to the next day THROUGH the date strip, the way a customer does.
        customer_page.locator(f".cbt-date[data-date='{D_TWO}']").click()
        customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
        _cell(customer_page, COURT_1, "14:00:00").click()
        expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
            "2", timeout=20000
        )

        # Back to day one: the pick is still there and still pressed.
        customer_page.locator(f".cbt-date[data-date='{D_ONE}']").click()
        customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
        expect(_cell(customer_page, COURT_1, "14:00:00")).to_have_attribute(
            "aria-pressed", "true", timeout=20000
        )
        expect(customer_page.locator("#cbt-selection-label")).to_contain_text("2")

    def test_a_broken_selection_on_one_court_is_two_bookings(
        self, page: Page, customer_page: Page
    ):
        """The user's own fee scenario, and the case the OLD client could not
        express at all: its consecutive-run logic restarted the selection on a
        non-adjacent tap. 4-5pm and 7-8pm on ONE court never merge, so they are
        two bookings — which is what makes them two booking fees."""
        cancel_active_bookings(page, COURT_2, D_ONE)
        _open_grid(customer_page, D_ONE)

        _cell(customer_page, COURT_2, "16:00:00").click()
        _cell(customer_page, COURT_2, "19:00:00").click()
        expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
            "2", timeout=20000
        )

        customer_page.locator("#cbt-review").click()
        expect(
            customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
        ).to_have_count(2, timeout=15000)

    def test_four_adjacent_taps_are_ONE_booking(
        self, page: Page, customer_page: Page
    ):
        """The other side of the same rule, and the reason the client does not
        merge for itself: four taps in a row are four ENTRIES on the wire and
        ONE booking on the screen, because the server unions them into one
        contiguous run. A client that counted its own taps would say four."""
        cancel_active_bookings(page, COURT_1, D_ONE)
        _open_grid(customer_page, D_ONE)

        for hour in ("08:00:00", "09:00:00", "10:00:00", "11:00:00"):
            _cell(customer_page, COURT_1, hour).click()

        expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
            "1", timeout=20000
        )
        # 4 hours x ₱400 on BGC court-1, undiscounted.
        expect(customer_page.locator("#cbt-selection-total")).to_contain_text("1,600")

        customer_page.locator("#cbt-review").click()
        expect(
            customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
        ).to_have_count(1, timeout=15000)

    def test_clear_empties_the_cart(self, page: Page, customer_page: Page):
        """A cart that survives date changes needs a way out that is not
        un-tapping every slot one at a time."""
        cancel_active_bookings(page, COURT_2, D_ONE)
        _open_grid(customer_page, D_ONE)

        _cell(customer_page, COURT_2, "13:00:00").click()
        bar = customer_page.locator("#cbt-checkoutbar")
        expect(bar).to_be_visible(timeout=20000)

        customer_page.locator("[data-testid='clear-cart']").click()
        expect(bar).to_be_hidden(timeout=15000)
        expect(_cell(customer_page, COURT_2, "13:00:00")).to_have_attribute(
            "aria-pressed", "false"
        )
