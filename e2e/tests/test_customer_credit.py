"""
E2E — store credit from a refund, end to end (Backlog B39).

The user's question that opened the row (2026-09-04): *"if ever the custom wants
to spend the 400 she can tell the staff to remove it as CASH?"* — they can, and
that is exactly the problem: it posts cash in a drawer nobody put it in. This
file walks the honest path a human actually takes:

  quick-book and take the cash  ->  cancel it as a REFUND with the box ticked
  ->  book again with "Use store credit"  ->  the booking goes green with no
  payment step, and the printed statement says what settled it.

DRIVEN AS A COMPANY ADMIN (`admin.ayala@example.com`), because the refund gate
is Company-Admin-only for every seat including the platform's — an Administrator
run would prove nothing about it.

Date ledger: this file claims **+86**.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import login_as, portal_login_as
from helpers.board import (
    BGC_BRANCH,
    CUSTOMER,
    _submit_cart,
    cancel_active_bookings,
    load_board,
    open_cart_dialog,
    quick_book,
    select_slots,
    slot,
    wait_dialog,
    wait_money_settled,
)
from helpers.worker_routing import base_url, bench_json

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

ALONA = "admin.ayala@example.com"  # Company Admin — the refund gate
BGC_COURT = "AYALA-bgc-court-1"
D_CREDIT = (date.today() + timedelta(days=86)).isoformat()


def _get(page: Page, url: str, params: dict | None = None):
    resp = page.request.get(url, params=params)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _credits(page: Page) -> list:
    return _get(
        page,
        "/api/resource/CBT Customer Credit",
        params={
            "filters": json.dumps([["customer", "=", CUSTOMER], ["company", "=", "ayala-courts"]]),
            "fields": json.dumps(["name", "amount", "balance", "status"]),
            "order_by": "creation desc",
            "limit_page_length": 0,
        },
    )


def _void_existing_credits():
    """Deterministic arrange. `take_credit` spends the OLDEST Active credit
    first, so a credit left behind by an earlier run would be the one this
    booking drains — and the assertions would read the wrong document.

    Backlog B43: through `bench execute`, not a REST PUT. `CBT Customer Credit`
    carries no write DocPerm for ANY role — the credit engine writes it with
    ignore_permissions — so the PUT this used to make only ever worked because
    the context was Administrator. That is the finding, not the obstacle.
    """
    return bench_json(
        "court_booking_tech.testing.void_customer_credits",
        ["ayala-courts", CUSTOMER],
    )


@pytest.mark.e2e
class TestCustomerCredit:
    def test_a_refund_becomes_store_credit_and_pays_for_the_next_booking(
        self, page: Page
    ):
        try:
            cancel_active_bookings(page, BGC_COURT, D_CREDIT)
            _void_existing_credits()
            login_as(page, ALONA)
            load_board(page, BGC_BRANCH, D_CREDIT)

            # 1. Cash at the desk — instantly Confirmed, invoice Paid & Verified.
            paid = quick_book(page, BGC_COURT, "10:00:00", "Cash")
            booking = _get(page, f"/api/resource/CBT Court Booking/{paid}")
            assert booking["booking_status"] == "Confirmed", booking
            price = float(booking["total_amount"])
            before = len(_credits(page))

            # 2. Cancel it as a REFUND, choosing store credit over cash.
            # Wait for the post-book RELOAD to flip the cell first: clicking the
            # stale "available" DOM would put the hour in the cart (B46) instead
            # of opening the booking, and the failure would land three lines
            # later on a button that was never rendered.
            page.wait_for_selector(
                f".cbt-slot[data-court='{BGC_COURT}'][data-start='10:00:00']"
                "[data-booking-status='Confirmed']",
                timeout=20000,
            )
            slot(page, BGC_COURT, "10:00:00").click()
            wait_dialog(page)
            page.locator(".modal.show [data-action='cancel-booking']").click()
            page.wait_for_function(
                "() => window.cur_dialog && cur_dialog.fields_dict"
                " && cur_dialog.fields_dict.issue_credit",
                timeout=20000,
            )
            from helpers import gestures

            gestures.fill(
                page, "reason", "Court closed for repairs", scope=gestures.DIALOG
            )
            gestures.check(page, "issue_credit", True, scope=gestures.DIALOG)
            SCREENSHOT_DIR.mkdir(exist_ok=True)
            page.screenshot(
                path=str(SCREENSHOT_DIR / "b39_refund_to_credit.png"), full_page=True
            )
            with page.expect_response(
                lambda r: "cancel_booking" in r.url, timeout=30000
            ) as info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert info.value.ok, info.value.text()

            rows = _credits(page)
            assert len(rows) == before + 1, rows
            credit = rows[0]
            credit_name = credit["name"]
            assert float(credit["amount"]) == price, credit
            assert float(credit["balance"]) == price, credit
            assert credit["status"] == "Active", credit

            # 2b. Backlog B44 — THE CUSTOMER CAN SEE IT. B39 shipped the credit
            # and told nobody: /my-bookings said nothing, so the only way to
            # answer "do I have anything left?" was a raw desk list view. Read
            # as the real customer in her own browser context, because that is
            # the only seat this claim is about.
            shopper = page.context.browser.new_context(
                base_url=base_url(), ignore_https_errors=True
            )
            try:
                cpage = shopper.new_page()
                portal_login_as(cpage, CUSTOMER)
                cpage.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
                note = cpage.locator(
                    "[data-testid='credit-balance'][data-company='ayala-courts']"
                )
                expect(note).to_be_visible(timeout=20000)
                text = note.inner_text()
                # The AMOUNT and WHOSE it is, both on screen — a balance with no
                # facility beside it is unusable on a cross-company portal.
                assert "Ayala Courts" in text, text
                assert f"{price:,.2f}" in text, (text, price)
                SCREENSHOT_DIR.mkdir(exist_ok=True)
                cpage.screenshot(
                    path=str(SCREENSHOT_DIR / "b44_my_bookings_credit.png"),
                    full_page=True,
                )
            finally:
                shopper.close()

            # 3. Book again and SPEND it — no payment step at all.
            load_board(page, BGC_BRANCH, D_CREDIT)
            # B46: the cell selects; the cart bar opens the dialog.
            select_slots(page, BGC_COURT, "12:00:00", 1)
            open_cart_dialog(page)
            gestures.fill(page, "customer", CUSTOMER, scope=gestures.DIALOG)
            gestures.fill(page, "payment_method", "Fund Transfer", scope=gestures.DIALOG)
            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)

            # The box only appears once the quote says this customer has a balance.
            # `.first`: frappe renders a disabled twin checkbox beside the real one.
            box = page.locator(
                ".modal.show .frappe-control[data-fieldname='apply_credit']"
                " input[type='checkbox']"
            ).first
            expect(box).to_be_visible(timeout=20000)
            expect(
                page.locator(".modal.show [data-testid='credit-available']")
            ).to_be_visible()
            gestures.check(page, "apply_credit", True, scope=gestures.DIALOG)
            due = page.locator(".modal.show [data-testid='credit-due']")
            expect(due).to_be_visible(timeout=15000)
            assert "0.00" in due.inner_text(), due.inner_text()
            page.screenshot(
                path=str(SCREENSHOT_DIR / "b39_spend_credit.png"), full_page=True
            )

            spent = _submit_cart(page)["name"]

            row = _get(page, f"/api/resource/CBT Court Booking/{spent}")
            assert float(row["credit_applied"]) == price, row
            assert row["booking_status"] == "Confirmed", (
                "credit that covers the whole price IS payment — this must not"
                f" sit waiting for a transfer receipt: {row}"
            )
            # The document the booking actually drew on, by name — never "the
            # newest", which is a different credit the moment one is left over.
            assert row["credit_document"] == credit_name, row
            spent_doc = _get(page, f"/api/resource/CBT Customer Credit/{credit_name}")
            assert float(spent_doc["balance"]) == 0.0, spent_doc
            assert spent_doc["status"] == "Spent", spent_doc

            # 4. The paper says what settled it.
            statement = page.request.get(
                "/printview",
                params={
                    "doctype": "CBT Booking Invoice",
                    "name": row["billing_doc"],
                    "format": "CBT Billing Statement",
                    "no_letterhead": "1",
                },
            )
            assert statement.ok, statement.status
            body = statement.text()
            assert "Paid by store credit" in body, body[:2000]
            assert "AMOUNT DUE" in body, body[:2000]
        finally:
            cancel_active_bookings(page, BGC_COURT, D_CREDIT)
