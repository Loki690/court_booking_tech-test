"""
E2E — ONE billing statement for a whole cart (Backlog B36).

The customer job: pay once, get ONE piece of paper. Driven the way a customer
drives it — tap slots on the real grid, check out, open /my-bookings, click a
card, click "View / print". Nothing here posts to an API to get where it is
going; if the route from a booking to its statement ever breaks, this file goes
red rather than quietly proving the server alone.

WHAT IT PINS. B36 turned booking -> invoice from 1:1 into N:1, so the assertions
that matter are the ones that would pass under EITHER design if written loosely:
the number of documents (one, not three), the number of VAT footers (one, not
three), and that a NON-anchor row reaches the same document as the anchor. The
three court-rental lines are asserted with their real printed text — the
matrix time convention and the Sep-03-2026 date form (user rulings 2026-09-04),
so a silent revert to "13:00" would fail here.

AYALA is Percentage-billed, so no booking fee lands on these totals: the fee's
own group behaviour is `test_booking_fee.py`. The absence is asserted LAST,
after the positives — an absence assertion on its own passes on a blank page.

Date ledger: THIS FILE CLAIMS **+79 and +80** (+78 was the last claimed, by
test_booking_fee). Both are inside AYALA's seeded advance_booking_days = 90,
which this file requires: it books as a real customer.

PIA HOLDS NO AYALA MEMBERSHIP by seed design, so every peso here is
undiscounted.

Console logs, page errors and failed /api responses are captured for the whole
customer journey and written beside the screenshot, so a failure is readable
without re-running (feedback: a screenshot + URL failure hook is blind).

No sleeps. Every assertion waits on a rendered node or a server response.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import Page, expect

from helpers.board import cancel_active_bookings

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

COMPANY = "ayala-courts"
BRANCH = "bgc"
COURT_1 = "AYALA-bgc-court-1"  # ₱400/hr
COURT_2 = "AYALA-bgc-court-2"  # ₱450/hr

D_ONE = date.today() + timedelta(days=79)
D_TWO = date.today() + timedelta(days=80)
S_ONE = D_ONE.isoformat()
S_TWO = D_TWO.isoformat()

# What billing.label_date prints — the month spelled out (user ruling).
L_ONE = D_ONE.strftime("%b-%d-%Y")
L_TWO = D_TWO.strftime("%b-%d-%Y")

CART_TOTAL = 400 + 450 + 400  # ₱1,250.00


class Recorder:
    """Console, pageerror and failed-/api capture for one journey."""

    def __init__(self, page: Page, name: str):
        self.page = page
        self.name = name
        self.console: list[str] = []
        self.errors: list[str] = []
        self.network: list[str] = []

    def __enter__(self):
        self.page.on(
            "console",
            lambda msg: self.console.append(f"[{msg.type}] {msg.text}"),
        )
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("response", self._response)
        return self

    def _response(self, response):
        if "/api" not in response.url or response.status < 400:
            return
        # Frappe throws land as 417 with the text inside _server_messages.
        try:
            body = response.text()[:1200]
        except Exception as exc:  # noqa: BLE001 - a closed body must not mask the failure
            body = f"<unreadable: {exc}>"
        self.network.append(f"HTTP {response.status} {response.url}\n{body}")

    def attach(self, page: Page):
        """Record a second page too (the statement opens in a popup)."""
        page.on("console", lambda msg: self.console.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        page.on("response", self._response)

    def __exit__(self, exc_type, exc, tb):
        ARTIFACT_DIR.mkdir(exist_ok=True)
        (ARTIFACT_DIR / f"{self.name}.log").write_text(
            json.dumps(
                {
                    "console": self.console,
                    "page_errors": self.errors,
                    "failed_api_calls": self.network,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # A page error or a failed API call is a defect even when the DOM
        # assertions happened to pass.
        assert not self.errors, f"uncaught page errors: {self.errors}"
        assert not self.network, f"failed API calls: {self.network}"
        return False


def _open_grid(page: Page, day: str):
    page.goto(
        f"/book?c={COMPANY}&b={BRANCH}&d={day}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)


def _cell(page: Page, court: str, start: str):
    return page.locator(f".cbt-slot[data-court='{court}'][data-start='{start}']")


def _open_statement(page: Page, booking: str, recorder: Recorder):
    """The human route: card -> detail -> View / print (a new tab)."""
    page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
    card = page.locator(f".cbt-card[data-booking='{booking}']")
    expect(card).to_be_visible(timeout=20000)
    card.click()
    page.wait_for_url(f"**/my-bookings/{booking}", timeout=20000)

    billing = page.locator("#cbt-billing-card")
    expect(billing).to_be_visible(timeout=20000)
    with page.expect_popup() as popup_info:
        page.locator("#cbt-print-link").click()
    statement = popup_info.value
    recorder.attach(statement)
    statement.wait_for_selector(".cbt-bs-content", timeout=20000)
    return statement


class TestGroupStatement:
    def test_a_cart_prints_ONE_statement_carrying_every_booking(
        self, page: Page, customer_page: Page
    ):
        """Three bookings across two dates and two courts, one checkout, one
        document — with one VAT footer over the summed court share."""
        for court in (COURT_1, COURT_2):
            cancel_active_bookings(page, court, S_ONE)
        cancel_active_bookings(page, COURT_1, S_TWO)

        with Recorder(customer_page, "b36_group_statement") as rec:
            _open_grid(customer_page, S_ONE)
            _cell(customer_page, COURT_1, "10:00:00").click()
            _cell(customer_page, COURT_2, "10:00:00").click()

            customer_page.locator(f".cbt-date[data-date='{S_TWO}']").click()
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            _cell(customer_page, COURT_1, "10:00:00").click()

            expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
                "3", timeout=20000
            )
            expect(customer_page.locator("#cbt-selection-total")).to_contain_text(
                "1,250"
            )

            customer_page.locator("#cbt-review").click()
            expect(
                customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
            ).to_have_count(3, timeout=15000)
            customer_page.locator("#cbt-reserve").click()

            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            bookings = sorted(
                set(
                    word.strip(" ,;")
                    for word in ref.inner_text().split()
                    if word.startswith("BK-AYALA-")
                )
            )
            assert len(bookings) == 3, ref.inner_text()

            # --- /my-bookings says the three share one payment ---------------
            customer_page.goto(
                "/my-bookings", wait_until="domcontentloaded", timeout=60000
            )
            heading = customer_page.locator("[data-testid='booking-group']").first
            expect(heading).to_be_visible(timeout=20000)
            expect(heading).to_contain_text("3")
            expect(heading).to_contain_text("one payment")
            group = heading.get_attribute("data-group")
            assert group and group.startswith("CART-AYALA-"), group

            # --- the statement, opened from the LAST row ---------------------
            # Deliberately not the first: the invoice's `booking` anchor is the
            # earliest row, so a design that only linked the anchor would 404
            # or show the wrong document here.
            statement = _open_statement(customer_page, bookings[-1], rec)
            body = statement.locator(".cbt-bs").inner_text()

            lines = statement.locator(".cbt-bs-items tr td:first-child")
            descriptions = [
                lines.nth(i).inner_text() for i in range(lines.count())
            ]
            assert len(descriptions) == 3, descriptions

            # Both dates, in the month-first form, on the ONE document.
            assert any(L_ONE in text for text in descriptions), descriptions
            assert any(L_TWO in text for text in descriptions), descriptions
            # Both courts.
            assert any("Court 1" in text for text in descriptions), descriptions
            assert any("Court 2" in text for text in descriptions), descriptions
            # The matrix time convention, not "10:00-11:00".
            for text in descriptions:
                assert "10 AM – 11 AM" in text, text

            assert body.count("VATable Sales") == 1, body
            assert body.count("VAT (12%)") == 1, body
            assert body.count("TOTAL") == 1, body
            assert "1,250.00" in body, body
            assert "1,116.07" in body, body  # 1250 / 1.12
            assert "133.93" in body, body

            assert group in body, "the statement must name the cart"
            assert "3 bookings" in body, body
            # The posting date is a SECOND call site and was left on the site's
            # DD-MM-YYYY default until a screenshot caught it.
            today = date.today().strftime("%b-%d-%Y")
            assert f"Date: {today}" in body, body

            invoice_name = statement.title()
            assert invoice_name.startswith("INV-AYALA-"), invoice_name

            ARTIFACT_DIR.mkdir(exist_ok=True)
            statement.screenshot(
                path=str(ARTIFACT_DIR / "b36_group_statement.png"), full_page=True
            )

            # Positives are in — NOW the absence is meaningful.
            assert "Booking fee" not in body, (
                "AYALA is Percentage-billed: no platform fee line belongs here"
            )
            statement.close()

            # --- the FIRST row reaches the SAME document ---------------------
            other = _open_statement(customer_page, bookings[0], rec)
            other.wait_for_selector(".cbt-bs-content", timeout=20000)
            assert other.title() == invoice_name, (
                f"{bookings[0]} printed {other.title()}, "
                f"{bookings[-1]} printed {invoice_name} — a cart is ONE document"
            )
            other.close()
