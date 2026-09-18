"""
E2E — Continuous booking across separate courts (Backlog B49, 2026-09-09).

The user: a customer who wanted 1–3 PM on one court and found the second hour
taken books 1–2 on Court A and 2–3 on Court B — *"that is still counted as 1
booking, it was just unfortunate that there was someone who was already
reserved first."* With both courts flagged as continue-on courts, the two
bookings pay ONE booking fee, and every money surface prints the fee the
bookings would have carried and a **Continuous booking discount** for what the
session saved — the customer's checkout, the desk's cart dialog, the booking
page and the printed statement.

TENANT. `qc-smash` on QCSM-annex, arranged exactly as `test_booking_fee` does:
switched to Per Booking (₱15 then ₱30) by API on the platform seat, a second
court created (or re-activated) and BOTH courts flagged, everything restored in
`finally`. The seeded courts elsewhere stay unflagged (opt-in, user ruling).

Date ledger: THIS FILE CLAIMS **+72 (portal, Pia)** and **+71 (desk, QCSM staff)**
— both inside the 90-day customer horizon (a customer claim cannot sit past +90).
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers import board, gestures
from helpers.auth import PLATFORM_ADMIN, login_as
from helpers.board import (
    _submit_quick_book,
    cancel_active_bookings,
    csrf,
    load_board,
    wait_money_settled,
)

TENANT = "qc-smash"
BRANCH = "QCSM-annex"
QCSM_STAFF = "staff.qcsm@example.com"
D_PORTAL = (date.today() + timedelta(days=72)).isoformat()
D_DESK = (date.today() + timedelta(days=71)).isoformat()

PROBE_SLUG = "b49-probe"
PROBE_NAME = "B49 Probe Court"
PROBE_RATE = 300.0

FIRST_FEE = 15
NEXT_FEE = 30
TIERS = [
    {"from_count": 1, "to_count": 1, "fee": FIRST_FEE},
    {"from_count": 2, "to_count": 0, "fee": NEXT_FEE},
]
SUBSCRIPTION = {"billing_mode": "Subscription", "subscription_fee": 2999}
PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}
SHOTS = Path(__file__).resolve().parent.parent / "screenshots"


def _money(text: str) -> list:
    return [float(v.replace(",", "")) for v in re.findall(r"\d[\d,]*\.\d{2}", text)]


def _get_json(page: Page, url: str, params: dict | None = None):
    resp = page.request.get(url, params=params)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _put(page: Page, url: str, values: dict):
    resp = page.request.put(
        url,
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps(values),
    )
    assert resp.ok, f"PUT {url}: HTTP {resp.status} {resp.text()}"


def _main_court(page: Page) -> tuple:
    rows = _get_json(
        page,
        "/api/resource/CBT Court",
        params={
            "filters": json.dumps([["branch", "=", BRANCH], ["court_name", "=", "Main Hall"]]),
            "fields": json.dumps(["name", "hourly_rate"]),
        },
    )
    assert rows, f"no Main Hall on {BRANCH}"
    return rows[0]["name"], float(rows[0]["hourly_rate"])


def _probe_court(page: Page) -> str:
    """The second court, created once and re-activated after (B41's pattern —
    a cancelled booking still links to it, so it is never deleted)."""
    existing = _get_json(
        page,
        "/api/resource/CBT Court",
        params={
            "filters": json.dumps([["branch", "=", BRANCH], ["court_name", "=", PROBE_NAME]]),
            "limit_page_length": 0,
        },
    )
    if existing:
        name = existing[0]["name"]
        _put(page, f"/api/resource/CBT Court/{name}", {"is_active": 1})
        return name
    resp = page.request.post(
        "/api/resource/CBT Court",
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps(
            {
                "branch": BRANCH,
                "slug": PROBE_SLUG,
                "court_name": PROBE_NAME,
                "court_type": "Pickleball",
                "hourly_rate": PROBE_RATE,
                "is_active": 1,
            }
        ),
    )
    assert resp.ok, f"create probe court: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]["name"]


def _arrange(page: Page, day: str) -> tuple:
    """Per Booking tiers, two FLAGGED courts, a clean slot ledger on `day`."""
    main, main_rate = _main_court(page)
    probe = _probe_court(page)
    for court in (main, probe):
        cancel_active_bookings(page, court, day)
        _put(page, f"/api/resource/CBT Court/{court}", {"allow_continuation": 1})
    _put(
        page,
        f"/api/resource/CBT Company/{TENANT}",
        {"billing_mode": "Per Booking", "booking_fee_tiers": TIERS, "open_play_fee_per_participant": 5},
    )
    company = _get_json(page, f"/api/resource/CBT Company/{TENANT}")
    assert company["billing_mode"] == "Per Booking", company
    return main, main_rate, probe


def _restore(page: Page, day: str):
    main, _rate = _main_court(page)
    probe = _probe_court(page)
    for court in (main, probe):
        cancel_active_bookings(page, court, day)
        _put(page, f"/api/resource/CBT Court/{court}", {"allow_continuation": 0})
    _put(page, f"/api/resource/CBT Court/{probe}", {"is_active": 0})
    _put(
        page,
        f"/api/resource/CBT Company/{TENANT}",
        {**SUBSCRIPTION, "booking_fee_tiers": [], "open_play_fee_per_participant": 0},
    )


def _row_amount(scope, testid: str) -> list:
    row = scope.locator(f"[data-testid='{testid}']").locator("xpath=ancestor::tr[1]")
    return _money(row.inner_text())


def _printed(page: Page, invoice: str) -> str:
    printed = page.request.get(
        "/printview", params={"doctype": "CBT Booking Invoice", "name": invoice, **PRINT_PARAMS}
    )
    assert printed.ok, f"printview: HTTP {printed.status}"
    return printed.text()


@pytest.mark.e2e
class TestContinuousBooking:
    def test_the_customer_pays_one_fee_for_a_session_that_moves_court(
        self, page: Page, customer_page: Page
    ):
        """Pia taps 10 AM on Main Hall and 11 AM on the probe court: two
        bookings, one fee, the discount line on the checkout, on the
        continuation's own page and on the one printed statement."""
        try:
            main, main_rate, probe = _arrange(page, D_PORTAL)
            customer_page.goto(
                f"/book?c={TENANT}&b=annex&d={D_PORTAL}", wait_until="domcontentloaded", timeout=60000
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(f".cbt-slot[data-court='{main}'][data-start='10:00:00']").click()
            customer_page.locator(f".cbt-slot[data-court='{probe}'][data-start='11:00:00']").click()
            expect(customer_page.locator("#cbt-selection-label")).to_contain_text("2", timeout=20000)
            # The bar already totals with ONE fee.
            expect(customer_page.locator("#cbt-selection-total")).to_contain_text(
                f"{main_rate + PROBE_RATE + FIRST_FEE:,.2f}"
            )

            customer_page.locator("#cbt-review").click()
            lines = customer_page.locator("#cbt-quote-lines")
            expect(lines.locator("[data-testid='cart-item']")).to_have_count(2, timeout=15000)
            expect(lines.locator("[data-testid='continuous-discount']")).to_be_visible()
            assert _row_amount(lines, "booking-fee") == [float(2 * FIRST_FEE)], lines.inner_text()
            assert _row_amount(lines, "continuous-discount") == [float(FIRST_FEE)], lines.inner_text()
            summary = lines.locator("[data-testid='cart-summary']").locator("xpath=ancestor::tr[1]")
            assert "1 booking fee" in summary.inner_text(), summary.inner_text()
            assert f"{main_rate + PROBE_RATE + FIRST_FEE:,.2f}" in lines.inner_text()
            customer_page.screenshot(path=str(SHOTS / "continuous_checkout.png"))

            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            refs = re.findall(r"BK-QCSM-\d{4}-\d{5}", ref.inner_text())
            assert len(refs) == 2, ref.inner_text()
            rows = [_get_json(page, f"/api/resource/CBT Court Booking/{name}") for name in refs]
            head = next(r for r in rows if not r.get("fee_chained_to"))
            tail = next(r for r in rows if r.get("fee_chained_to"))
            assert tail["fee_chained_to"] == head["name"], rows
            assert float(head["platform_fee"]) == FIRST_FEE and int(head["platform_fee_seq"]) == 1, head
            assert float(tail["platform_fee"]) == 0 and float(tail["platform_fee_waived"]) == FIRST_FEE, tail
            assert head["billing_doc"] == tail["billing_doc"], rows

            # The continuation's own page: the fee it would have carried, and
            # the same amount back.
            customer_page.goto(f"/my-bookings/{tail['name']}", wait_until="domcontentloaded", timeout=60000)
            money = customer_page.locator("#cbt-money")
            expect(money.locator("[data-testid='continuous-discount']")).to_be_visible(timeout=15000)
            assert _row_amount(money, "booking-fee") == [float(FIRST_FEE)], money.inner_text()
            assert _row_amount(money, "continuous-discount") == [float(FIRST_FEE)], money.inner_text()
            assert f"{float(tail['total_amount']):,.2f}" in money.inner_text()

            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{head['billing_doc']}")
            assert float(invoice["platform_fee"]) == FIRST_FEE, invoice
            assert float(invoice["continuous_discount"]) == FIRST_FEE, invoice
            html = _printed(page, invoice["name"])
            assert "Less: Continuous booking discount" in html
            assert re.search(r"Booking fee \(platform\)</td>\s*<td[^>]*>[^<]*\b30\.00", html), (
                "the fee line must print the as-if figure"
            )
            assert f"{main_rate + PROBE_RATE + FIRST_FEE:,.2f}" in html
        finally:
            _restore(page, D_PORTAL)

    def test_the_desk_cart_prints_the_same_two_lines(self, page: Page):
        """The desk face of the same rule: staff.qcsm selects the two cells,
        the dialog prints the fee as-if and the discount, Cash books both, and
        the statement carries the lines."""
        try:
            main, main_rate, probe = _arrange(page, D_DESK)
            login_as(page, QCSM_STAFF)
            load_board(page, BRANCH, D_DESK)
            board.select_slots(page, main, "10:00:00", 1)
            board.select_slots(page, probe, "11:00:00", 1)
            board.open_cart_dialog(page)
            gestures.fill(page, "customer", board.CUSTOMER, scope=gestures.DIALOG)
            gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)

            dialog = page.locator(gestures.DIALOG)
            fee_line = dialog.locator("[data-testid='booking-fee']")
            expect(fee_line).to_be_visible(timeout=15000)
            assert _money(fee_line.inner_text()) == [float(2 * FIRST_FEE)], fee_line.inner_text()
            discount_line = dialog.locator("[data-testid='continuous-discount']")
            expect(discount_line).to_be_visible()
            assert _money(discount_line.inner_text()) == [float(FIRST_FEE)], discount_line.inner_text()
            total = dialog.locator("[data-testid='quote-total']")
            assert _money(total.inner_text()) == [main_rate + PROBE_RATE + FIRST_FEE], total.inner_text()
            summary = dialog.locator("[data-testid='cart-summary']")
            assert "1 booking fee" in summary.inner_text(), summary.inner_text()

            first = _submit_quick_book(page)
            row = _get_json(page, f"/api/resource/CBT Court Booking/{first}")
            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert float(invoice["platform_fee"]) == FIRST_FEE, invoice
            assert float(invoice["continuous_discount"]) == FIRST_FEE, invoice
            assert invoice["status"] == "Paid & Verified", invoice
            html = _printed(page, invoice["name"])
            assert "Less: Continuous booking discount" in html
            assert f"{main_rate + PROBE_RATE + FIRST_FEE:,.2f}" in html
        finally:
            login_as(page, PLATFORM_ADMIN)
            _restore(page, D_DESK)
