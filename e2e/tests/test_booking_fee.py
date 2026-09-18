"""
E2E — Per-booking platform fee (Backlog B27, Batches 9–11, 2026-08-27).

The third billing mode is the first the CUSTOMER can see: a Per Booking
tenant's customers pay a peso fee on top of the court, shown as its own
"Booking fee" line at checkout, on the booking page and on the printed
statement, read from marginal monthly brackets fixed at the moment of sale.

Two rows, one per money path:

1. the CUSTOMER books on /book — the checkout modal carries the fee line and
   the total that includes it, the reserve button is clicked, the confirmation
   and /my-bookings/<name> repeat the line, and the server's booking and
   billing document carry the same figures;
2. the DESK quick-books the SECOND unit of the month — the dialog shows the
   next bracket's fee (₱30, not ₱15), toggling Free drops the line and Cash
   brings it back, the booking is committed, and the printed statement shows
   the fee as its own line inside one total.

TENANT. `qc-smash` is switched to Per Booking BY API for each row and restored
to Subscription ₱2,999 in `finally` — it is the one seeded company no other E2E
file books or boards (seeds comment, section-22), so the switch is invisible to
the rest of the suite even under 3 workers. Brackets are deliberately narrow
(1st booking ₱15, 2nd and up ₱30) so the second row crosses a boundary.

BRANCH. QCSM-annex (open every day; timog closes Sundays and would slide the
ledger date). The court is looked up by API at arrange time, never hard-coded.

Date ledger: THIS FILE CLAIMS **+78** and, since Backlog B41, **+81, +82, +83**.
The first two rows share the tenant, the court and the date, and row 2 asserts
unit #2 exactly — that is safe because the suite distributes with
`--dist loadfile` (one file, one worker, in order); do not split this file. The
tenant switch is made INSIDE each row's `try`, so a failed arrange still restores
it. No sleeps — every wait is on a rendered node or a server response.

Backlog B41 added the three acceptance shapes that had never been browser-proven
with PESOS; see the block above those rows for the three traps they carry.
"""
import json
import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers import board, gestures, portal
from helpers.board import (
    _submit_quick_book,
    cancel_active_bookings,
    csrf,
    load_board,
    slot,
    wait_dialog,
    wait_money_settled,
)

TENANT = "qc-smash"
BRANCH = "QCSM-annex"
D_FEE = (date.today() + timedelta(days=78)).isoformat()
# Backlog B41 — the three acceptance shapes this file did not yet prove with
# money on screen. Ledger: this file now claims +78 and +81..+83.
D_CONTINUOUS = (date.today() + timedelta(days=81)).isoformat()
D_TWO_DAYS_A = (date.today() + timedelta(days=82)).isoformat()
D_TWO_DAYS_B = (date.today() + timedelta(days=83)).isoformat()
D_TWO_COURTS = (date.today() + timedelta(days=81)).isoformat()

SECOND_COURT_SLUG = "b41-probe"
SECOND_COURT_NAME = "B41 Probe Court"

FIRST_FEE = 15
NEXT_FEE = 30
TIERS = [
    {"from_count": 1, "to_count": 1, "fee": FIRST_FEE},
    {"from_count": 2, "to_count": 0, "fee": NEXT_FEE},
]
SUBSCRIPTION = {"billing_mode": "Subscription", "subscription_fee": 2999}

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


def _get_json(page: Page, url: str, params: dict | None = None) -> dict:
    resp = page.request.get(url, params=params)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _put_company(page: Page, values: dict):
    """Arrange/restore the tenant's billing mode on the PLATFORM seat (the desk
    form's own REST write, permlevel-1 fields included).

    Backlog B43: this said "as Administrator" and never needed to —
    `CBT Platform Admin` holds read=1 write=1 AT PERMLEVEL 1 on CBT Company,
    which is the level `billing_mode`, `subscription_fee`, `commission_percent`
    and `booking_fee_tiers` sit at."""
    resp = page.request.put(
        f"/api/resource/CBT Company/{TENANT}",
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps(values),
    )
    assert resp.ok, f"PUT CBT Company/{TENANT}: HTTP {resp.status} {resp.text()}"


def _court(page: Page) -> str:
    rows = _get_json(
        page,
        "/api/resource/CBT Court",
        params={
            "filters": json.dumps([["branch", "=", BRANCH], ["is_active", "=", 1]]),
            "fields": json.dumps(["name", "hourly_rate"]),
            "order_by": "name asc",
        },
    )
    assert rows, f"no active court on {BRANCH}"
    return rows[0]["name"], float(rows[0]["hourly_rate"])


def _arrange(page: Page):
    """Per Booking with the narrow brackets, and a clean slot ledger. Called
    INSIDE the row's try so `_restore` runs even if this fails half-way."""
    court, _rate = _court(page)
    cancel_active_bookings(page, court, D_FEE)
    _put_company(
        page,
        {
            "billing_mode": "Per Booking",
            "booking_fee_tiers": TIERS,
            "open_play_fee_per_participant": 5,
        },
    )
    company = _get_json(page, f"/api/resource/CBT Company/{TENANT}")
    assert company["billing_mode"] == "Per Booking", company
    assert len(company["booking_fee_tiers"]) == 2, company


def _restore(page: Page, court: str):
    cancel_active_bookings(page, court, D_FEE)
    _put_company(
        page,
        {**SUBSCRIPTION, "booking_fee_tiers": [], "open_play_fee_per_participant": 0},
    )
    company = _get_json(page, f"/api/resource/CBT Company/{TENANT}")
    assert company["billing_mode"] == "Subscription", company
    assert not company.get("booking_fee_tiers"), company


def _set_court_active(page: Page, name: str, active: int):
    resp = page.request.put(
        f"/api/resource/CBT Court/{name}",
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps({"is_active": active}),
    )
    assert resp.ok, f"PUT {name} is_active={active}: HTTP {resp.status} {resp.text()}"


def _make_second_court(page: Page, rate: float) -> str:
    """B41: QCSM-annex ships ONE active court. A second is created here rather
    than seeded — seeding one would change the court count every other row on
    this branch can see. Idempotent: a previous run's court is REACTIVATED."""
    existing = _get_json(
        page,
        "/api/resource/CBT Court",
        params={
            "filters": json.dumps(
                [["branch", "=", BRANCH], ["court_name", "=", SECOND_COURT_NAME]]
            ),
            "limit_page_length": 0,
        },
    )
    if existing:
        name = existing[0]["name"]
        _set_court_active(page, name, 1)
        return name
    resp = page.request.post(
        "/api/resource/CBT Court",
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps(
            {
                "branch": BRANCH,
                "slug": SECOND_COURT_SLUG,
                "court_name": "B41 Probe Court",
                "court_type": "Pickleball",
                "hourly_rate": rate,
                "is_active": 1,
            }
        ),
    )
    assert resp.ok, f"create second court: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]["name"]


def _drop_court(page: Page, name: str):
    """DEACTIVATE, never delete: a cancelled booking still LINKS to its court,
    so a delete is refused (LinkExistsError, HTTP 417). Every other row filters
    on is_active=1, so an inactive court is invisible to them."""
    _set_court_active(page, name, 0)


def _money(text: str) -> list:
    return [float(v.replace(",", "")) for v in re.findall(r"\d[\d,]*\.\d{2}", text)]


@pytest.mark.e2e
class TestBookingFee:

    def test_customer_sees_and_pays_the_fee_on_the_portal(
        self, page: Page, customer_page: Page
    ):
        """The customer's whole path, by gesture: /book → slot → Review →
        the fee line and the total → Reserve → confirmation → the booking page.
        Then the server: the booking and its billing document carry ₱15 and
        ₱265, the tenant's list price untouched underneath."""
        court, rate = _court(page)
        try:
            _arrange(page)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_FEE}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(
                f".cbt-slot[data-court='{court}'][data-start='10:00:00']"
            ).click()
            # The selection bar already totals WITH the fee — the customer is
            # never shown a court-only number that then grows at checkout.
            bar_total = customer_page.locator("#cbt-selection-total")
            expect(bar_total).to_be_visible(timeout=15000)
            assert _money(bar_total.inner_text()) == [rate + FIRST_FEE], bar_total.inner_text()

            customer_page.locator("#cbt-review").click()
            fee_line = customer_page.locator("#cbt-quote-lines [data-testid='booking-fee']")
            expect(fee_line).to_be_visible(timeout=15000)
            # The fee's OWN row, not the whole block — "265.00" would contain
            # "65.00"-shaped substrings and a missing line could pass.
            fee_row = fee_line.locator("xpath=ancestor::tr[1]").inner_text()
            assert _money(fee_row) == [float(FIRST_FEE)], fee_row
            lines = customer_page.locator("#cbt-quote-lines").inner_text()
            assert f"{rate + FIRST_FEE:,.2f}" in lines, lines
            assert "NON-VAT" in lines, lines  # QCSM: no VAT block, as ruled

            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            match = re.search(r"BK-QCSM-\d{4}-\d{5}", ref.inner_text())
            assert match, ref.inner_text()
            booking = match.group(0)
            confirmed_lines = customer_page.locator("#cbt-confirmed-lines").inner_text()
            assert f"{rate + FIRST_FEE:,.2f}" in confirmed_lines, confirmed_lines

            # The booking page repeats the line, the same number.
            portal.open_detail(customer_page, booking)
            money = customer_page.locator("#cbt-money")
            detail_fee = money.locator("[data-testid='booking-fee']")
            expect(detail_fee).to_be_visible(timeout=15000)
            detail_row = detail_fee.locator("xpath=ancestor::tr[1]").inner_text()
            assert _money(detail_row) == [float(FIRST_FEE)], detail_row
            money_text = money.inner_text()
            assert f"{rate + FIRST_FEE:,.2f}" in money_text, money_text

            # The server's own record.
            row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
            assert float(row["platform_fee"]) == FIRST_FEE, row
            assert int(row["platform_fee_seq"]) == 1, row
            assert float(row["total_amount"]) == rate + FIRST_FEE, row
            assert float(row["hourly_rate"]) == rate, row
            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert float(invoice["platform_fee"]) == FIRST_FEE, invoice
            assert float(invoice["subtotal"]) == rate, invoice
            assert float(invoice["total_amount"]) == rate + FIRST_FEE, invoice
            assert invoice["status"] == "Unpaid", invoice
        finally:
            _restore(page, court)

    def test_desk_quick_book_shows_the_next_bracket_and_prints_it(self, page: Page):
        """Unit 2 of the month crosses into the ₱30 bracket. The dialog says
        so before Book is clicked; Free removes the line, Cash restores it;
        the committed booking and its printed statement match the dialog."""
        court, rate = _court(page)
        try:
            _arrange(page)
            # Unit 1, arranged by API so this row is about the SECOND bracket.
            first = page.request.post(
                "/api/method/court_booking_tech.api.bookings.create_booking",
                headers={"X-Frappe-CSRF-Token": csrf(page)},
                form={
                    "court": court,
                    "booking_date": D_FEE,
                    "start_time": "10:00:00",
                    "payment_method": "Cash",
                    "customer": board.CUSTOMER,
                    "number_of_slots": 1,
                },
            )
            assert first.ok, f"create_booking: HTTP {first.status} {first.text()}"
            first_row = _get_json(page, f"/api/resource/CBT Court Booking/{first.json()['message']['name']}")
            assert float(first_row["platform_fee"]) == FIRST_FEE, first_row

            load_board(page, BRANCH, D_FEE)
            # B46: the desk selects on the board and opens ONE dialog for the
            # set; the slot is no longer a shortcut into the dialog.
            board.select_slots(page, court, "11:00:00", 1)
            board.open_cart_dialog(page)
            gestures.fill(page, "customer", board.CUSTOMER, scope=gestures.DIALOG)
            gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)

            fee_line = page.locator(".modal.show [data-testid='booking-fee']")
            expect(fee_line).to_be_visible(timeout=15000)
            assert f"{NEXT_FEE:.2f}" in fee_line.inner_text(), fee_line.inner_text()
            total = page.locator(".modal.show [data-testid='quote-total']")
            assert _money(total.inner_text()) == [rate + NEXT_FEE], total.inner_text()

            # Free means no platform fee — the line goes, the total drops...
            gestures.fill(page, "payment_method", "Free", scope=gestures.DIALOG)
            wait_money_settled(page)
            expect(fee_line).to_have_count(0, timeout=15000)
            assert _money(total.inner_text()) == [rate], total.inner_text()
            # ...and Cash brings it back, the same ₱30.
            gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
            wait_money_settled(page)
            expect(fee_line).to_be_visible(timeout=15000)
            assert _money(total.inner_text()) == [rate + NEXT_FEE], total.inner_text()

            booking = _submit_quick_book(page)
            row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
            assert float(row["platform_fee"]) == NEXT_FEE, row
            assert int(row["platform_fee_seq"]) == 2, row
            assert float(row["total_amount"]) == rate + NEXT_FEE, row
            assert row["booking_status"] == "Confirmed", row

            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert float(invoice["platform_fee"]) == NEXT_FEE, invoice
            assert invoice["status"] == "Paid & Verified", invoice
            printed = page.request.get(
                "/printview",
                params={"doctype": "CBT Booking Invoice", "name": invoice["name"], **PRINT_PARAMS},
            )
            assert printed.ok, f"printview: HTTP {printed.status}"
            html = printed.text()
            assert re.search(
                r"Booking fee \(platform\)</td>\s*<td[^>]*>[^<]*\b" + f"{NEXT_FEE:.2f}", html
            ), "the fee must print as its own line, with its own amount"
            assert f"{rate + NEXT_FEE:,.2f}" in html
            assert "PAID &amp; VERIFIED" in html
        finally:
            _restore(page, court)

    def test_a_cart_prints_the_booking_fee_as_ONE_summed_line(
        self, page: Page, customer_page: Page
    ):
        """Backlog B36, the user's own words: *"booking fee should just be
        total amount"*. Two non-adjacent hours on ONE court are TWO bookings and
        therefore TWO fees — and they must reach the customer as a SINGLE line
        on a SINGLE document, not as one document each."""
        court, rate = _court(page)
        try:
            _arrange(page)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_FEE}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            for start in ("14:00:00", "17:00:00"):
                customer_page.locator(
                    f".cbt-slot[data-court='{court}'][data-start='{start}']"
                ).click()

            expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
                "2", timeout=20000
            )
            customer_page.locator("#cbt-review").click()
            expect(
                customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
            ).to_have_count(2, timeout=15000)
            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            bookings = sorted(set(re.findall(r"BK-QCSM-\d{4}-\d{5}", ref.inner_text())))
            assert len(bookings) == 2, ref.inner_text()

            rows = [
                _get_json(page, f"/api/resource/CBT Court Booking/{name}")
                for name in bookings
            ]
            fees = [float(row["platform_fee"]) for row in rows]
            # Which BRACKET each row lands in is B27's arithmetic and depends on
            # the month's running count (rows 1-2 pin it). What B36 claims is
            # that two bookings really do carry two fees, summed onto one line.
            assert all(fee in (FIRST_FEE, NEXT_FEE) for fee in fees), rows
            assert len(fees) == 2 and sum(fees) > 0, rows
            invoices = {row["billing_doc"] for row in rows}
            assert len(invoices) == 1, f"a cart is ONE document: {invoices}"

            invoice = _get_json(
                page, f"/api/resource/CBT Booking Invoice/{invoices.pop()}"
            )
            assert float(invoice["platform_fee"]) == sum(fees), invoice
            assert float(invoice["subtotal"]) == rate * 2, invoice
            assert float(invoice["total_amount"]) == rate * 2 + sum(fees), invoice

            # The paper the customer holds, reached the way they reach it.
            portal.open_detail(customer_page, bookings[0])
            expect(customer_page.locator("#cbt-billing-card")).to_be_visible(
                timeout=20000
            )
            with customer_page.expect_popup() as popup_info:
                customer_page.locator("#cbt-print-link").click()
            statement = popup_info.value
            statement.wait_for_selector(".cbt-bs-content", timeout=20000)
            body = statement.locator(".cbt-bs").inner_text()

            # Two rental lines, ONE fee line, one total.
            assert body.count("Court rental") == 2, body
            fee_rows = statement.locator(
                ".cbt-bs-totals tr.cbt-bs-fee-row"
            )
            expect(fee_rows).to_have_count(1)
            assert _money(fee_rows.inner_text()) == [float(sum(fees))], (
                fee_rows.inner_text()
            )
            assert body.count("TOTAL") == 1, body
            assert f"{rate * 2 + sum(fees):,.2f}" in body, body
            assert "NON-VAT" in body, body  # QCSM, as ruled
            statement.close()
        finally:
            _restore(page, court)

    # --- Backlog B41: the acceptance table, with PESOS on screen -----------
    #
    # B35's rule, in the user's words: "compute it Per day, then how many courts
    # they used, then if it is continuous counted as 1 booking fee (1pm to 5pm),
    # if it is broken (1pm-2pm and 4pm-5pm) this is counted as 2 booking fee
    # worth". The fourth shape (one court, one day, BROKEN) is the row above.
    #
    # ⚠ THE FEE AMOUNT IS STATEFUL — each run takes the next marginal bracket for
    # the tenant's service MONTH, and the rows above cancel in `finally`, so the
    # running count does not carry the way it looks like it should. Every row
    # below asserts RELATIONSHIPS (how many fee units, and the screen's summed
    # line == their sum), never absolute pesos.

    def _cart_fees(self, page: Page, refs: str) -> tuple:
        """The server's truth for a confirmed cart: (booking names, fees)."""
        bookings = sorted(set(re.findall(r"BK-QCSM-\d{4}-\d{5}", refs)))
        rows = [
            _get_json(page, f"/api/resource/CBT Court Booking/{name}")
            for name in bookings
        ]
        fees = [float(row["platform_fee"]) for row in rows]
        assert all(fee in (FIRST_FEE, NEXT_FEE) for fee in fees), rows
        return bookings, fees

    def test_one_court_one_day_CONTINUOUS_is_one_booking_and_one_fee(
        self, page: Page, customer_page: Page
    ):
        """1pm–5pm on one court is ONE session, so the customer is charged ONE
        booking fee — four taps, one fee, and the checkout says so in pesos."""
        court, rate = _court(page)
        try:
            _arrange(page)
            cancel_active_bookings(page, court, D_CONTINUOUS)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_CONTINUOUS}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            for start in ("13:00:00", "14:00:00", "15:00:00", "16:00:00"):
                customer_page.locator(
                    f".cbt-slot[data-court='{court}'][data-start='{start}']"
                ).click()

            customer_page.locator("#cbt-review").click()
            items = customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
            expect(items).to_have_count(1, timeout=15000)
            # ONE fee, so the "Bookings · N booking fee(s)" summary is not
            # rendered at all — that ABSENCE is the claim for this shape.
            expect(
                customer_page.locator("#cbt-quote-lines [data-testid='cart-summary']")
            ).to_have_count(0)

            fee_line = customer_page.locator("#cbt-quote-lines [data-testid='booking-fee']")
            expect(fee_line).to_be_visible(timeout=15000)
            shown = _money(fee_line.locator("xpath=ancestor::tr[1]").inner_text())
            assert len(shown) == 1 and shown[0] in (FIRST_FEE, NEXT_FEE), shown

            # Capture the SERVER's answer: a refused reserve leaves the page
            # looking identical to a client-side throw, and "confirmation never
            # appeared" is not a diagnosis.
            with customer_page.expect_response(
                lambda r: "reserve_cart" in r.url, timeout=30000
            ) as reserve_info:
                customer_page.locator("#cbt-reserve").click()
            assert reserve_info.value.ok, (
                f"reserve_cart: HTTP {reserve_info.value.status}"
                f" {reserve_info.value.text()}"
            )
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            bookings, fees = self._cart_fees(page, ref.inner_text())

            assert len(bookings) == 1, f"four contiguous taps made {bookings}"
            assert len(fees) == 1, fees
            assert shown[0] == fees[0], (shown, fees)
            row = _get_json(page, f"/api/resource/CBT Court Booking/{bookings[0]}")
            assert int(row["number_of_slots"]) == 4, row
            assert float(row["total_amount"]) == rate * 4 + fees[0], row
        finally:
            cancel_active_bookings(page, court, D_CONTINUOUS)
            _restore(page, court)

    def test_one_court_TWO_DAYS_is_two_bookings_and_two_fees(
        self, page: Page, customer_page: Page
    ):
        """The fee is computed PER DAY, so the same court on two days can never
        merge. Two fees — and B36 prints ONE summed line, so the COUNT has to be
        read off the checkout summary and the SUM off the fee line."""
        court, rate = _court(page)
        try:
            _arrange(page)
            for day in (D_TWO_DAYS_A, D_TWO_DAYS_B):
                cancel_active_bookings(page, court, day)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_TWO_DAYS_A}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(
                f".cbt-slot[data-court='{court}'][data-start='13:00:00']"
            ).click()
            # Through the date strip, the way a customer moves days.
            customer_page.locator(f".cbt-date[data-date='{D_TWO_DAYS_B}']").click()
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(
                f".cbt-slot[data-court='{court}'][data-start='13:00:00']"
            ).click()
            expect(customer_page.locator("#cbt-selection-label")).to_contain_text(
                "2", timeout=20000
            )

            customer_page.locator("#cbt-review").click()
            expect(
                customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
            ).to_have_count(2, timeout=15000)
            summary = customer_page.locator("#cbt-quote-lines [data-testid='cart-summary']")
            expect(summary).to_be_visible(timeout=15000)
            summary_row = summary.locator("xpath=ancestor::tr[1]").inner_text()
            assert "2 booking fee" in summary_row, summary_row

            fee_line = customer_page.locator("#cbt-quote-lines [data-testid='booking-fee']")
            expect(fee_line).to_be_visible()
            shown = _money(fee_line.locator("xpath=ancestor::tr[1]").inner_text())

            # Capture the SERVER's answer: a refused reserve leaves the page
            # looking identical to a client-side throw, and "confirmation never
            # appeared" is not a diagnosis.
            with customer_page.expect_response(
                lambda r: "reserve_cart" in r.url, timeout=30000
            ) as reserve_info:
                customer_page.locator("#cbt-reserve").click()
            assert reserve_info.value.ok, (
                f"reserve_cart: HTTP {reserve_info.value.status}"
                f" {reserve_info.value.text()}"
            )
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            bookings, fees = self._cart_fees(page, ref.inner_text())

            assert len(bookings) == 2, ref.inner_text()
            assert len(fees) == 2, fees
            assert shown == [sum(fees)], (shown, fees)
            dates = {
                _get_json(page, f"/api/resource/CBT Court Booking/{name}")["booking_date"]
                for name in bookings
            }
            assert dates == {D_TWO_DAYS_A, D_TWO_DAYS_B}, dates
        finally:
            for day in (D_TWO_DAYS_A, D_TWO_DAYS_B):
                cancel_active_bookings(page, court, day)
            _restore(page, court)

    def test_TWO_COURTS_one_day_both_continuous_is_two_fees(
        self, page: Page, customer_page: Page
    ):
        """Two courts at the same hour can never merge — different courts — so
        two continuous runs are two bookings and two fees.

        QCSM-annex ships with ONE active court, so the second is created here and
        removed in `finally`: a second SEEDED court would change the court count
        every other row on this branch can see.
        """
        court, rate = _court(page)
        second = None
        try:
            _arrange(page)
            second = _make_second_court(page, rate)
            for name in (court, second):
                cancel_active_bookings(page, name, D_TWO_COURTS)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_TWO_COURTS}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            for name in (court, second):
                for start in ("13:00:00", "14:00:00"):
                    customer_page.locator(
                        f".cbt-slot[data-court='{name}'][data-start='{start}']"
                    ).click()

            customer_page.locator("#cbt-review").click()
            expect(
                customer_page.locator("#cbt-quote-lines [data-testid='cart-item']")
            ).to_have_count(2, timeout=15000)
            summary = customer_page.locator("#cbt-quote-lines [data-testid='cart-summary']")
            expect(summary).to_be_visible(timeout=15000)
            assert "2 booking fee" in summary.locator(
                "xpath=ancestor::tr[1]"
            ).inner_text()

            fee_line = customer_page.locator("#cbt-quote-lines [data-testid='booking-fee']")
            expect(fee_line).to_be_visible()
            shown = _money(fee_line.locator("xpath=ancestor::tr[1]").inner_text())

            # Capture the SERVER's answer: a refused reserve leaves the page
            # looking identical to a client-side throw, and "confirmation never
            # appeared" is not a diagnosis.
            with customer_page.expect_response(
                lambda r: "reserve_cart" in r.url, timeout=30000
            ) as reserve_info:
                customer_page.locator("#cbt-reserve").click()
            assert reserve_info.value.ok, (
                f"reserve_cart: HTTP {reserve_info.value.status}"
                f" {reserve_info.value.text()}"
            )
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            bookings, fees = self._cart_fees(page, ref.inner_text())

            assert len(bookings) == 2, ref.inner_text()
            assert len(fees) == 2, fees
            assert shown == [sum(fees)], (shown, fees)
            courts_used = {
                _get_json(page, f"/api/resource/CBT Court Booking/{name}")["court"]
                for name in bookings
            }
            assert courts_used == {court, second}, courts_used
            # Four taps, TWO bookings — each court's pair merged into one run.
            for name in bookings:
                row = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
                assert int(row["number_of_slots"]) == 2, row
        finally:
            for name in (court, second):
                if name:
                    cancel_active_bookings(page, name, D_TWO_COURTS)
            if second:
                _drop_court(page, second)
            _restore(page, court)
