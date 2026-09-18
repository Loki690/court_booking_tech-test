"""
E2E — A refund is the Company Admin's decision (section-26), by hand.

The user's ruling (2026-08-27): *"refund/cancel after confirmed is such a money
related thing to do that we need the company's admin to do it, not just the
staff."* So, on one paid Cash booking, two seats:

1. **staff.ayala** books it from the board (quick-book, Cash → Confirmed) and
   opens its details: there is NO cancel button — the admin-only sentence sits
   where it would be, beside the buttons that are there (the positive control)
   — and the API refuses the seat with the same sentence (the missing button is
   not the guard).
2. **admin.ayala** opens the same booking: the button reads *Cancel & Refund…*,
   the prompt asks WHY, the reason is typed; then the billing document, the
   Tenant Ledger's reversal line (read off the rendered table) and the printed
   statement (the booking form's own *Billing Statement* button, a new tab)
   all carry the reason and the seat.

SEATS. `login_as` swaps the page's session; every context starts from
admin.json and `verify_session` heals a Guest, so nothing restores
Administrator here — the cleanup helper cancels through its OWN admin context
whatever seat the page is on.

Date ledger: THIS FILE CLAIMS **+82** on AYALA-bgc-court-1 (+80/+81 were the
last claimed, test_channel_ledger.py), and **+91** for the B53 policy row below
— desk-only, above the seeded 90-day customer horizon, which is legal because
staff bypass it. No sleeps.
"""
import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures, reports
from helpers.auth import login_as
from helpers.board import (
    BGC_BRANCH,
    BGC_COURTS,
    cancel_active_bookings,
    csrf,
    load_board,
    quick_book,
    slot,
    wait_dialog,
)
from helpers.navigation import goto_form
from helpers.worker_routing import bench_json

AYALA = "ayala-courts"
AYALA_TITLE = "Ayala Courts"
STAFF = "staff.ayala@example.com"
ADMIN = "admin.ayala@example.com"
ADMIN_FULL_NAME = "Alona AyalaAdmin"

COURT = BGC_COURTS[0]  # AYALA-bgc-court-1
D_REFUND = (date.today() + timedelta(days=82)).isoformat()
START = "10:00:00"

REASON = "E2E: customer's flight cancelled, cash returned at the desk"
ADMIN_ONLY = "Only a Company Admin can cancel a paid booking"
PROMPT_SENTENCE = "Cancelling it is a refund"
INVOICE_DOCTYPE = "CBT Booking Invoice"

# --- B53: refund policy per company ---------------------------------------
D_POLICY = (date.today() + timedelta(days=91)).isoformat()  # desk-only, past +90
START_POLICY = "14:00:00"
POLICY_REASON = "E2E: customer changed their mind"
SET_POLICY = "court_booking_tech.testing.set_refund_policy"
NO_CASH = "store credit"


def _get(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _open_details(page: Page):
    slot(page, COURT, START).click()
    wait_dialog(page)
    return page.locator(gestures.DIALOG)


def _close_dialog(page: Page):
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal.show", state="detached", timeout=15000)


@pytest.mark.e2e
class TestRefundGate:
    def test_staff_cannot_refund_but_the_admin_can_and_every_paper_reads_why(self, page: Page):
        cancel_active_bookings(page, COURT, D_REFUND)
        try:
            # --- the front desk books and pays it ------------------------------
            login_as(page, STAFF)
            load_board(page, BGC_BRANCH, D_REFUND)
            booking = quick_book(page, COURT, START, "Cash")
            row = _get(page, f"/api/resource/CBT Court Booking/{booking}")
            assert row["booking_status"] == "Confirmed", row
            invoice_name = row["billing_doc"]
            assert _get(page, f"/api/resource/{INVOICE_DOCTYPE}/{invoice_name}")["status"] == "Paid & Verified"

            # --- and cannot refund it: no button, the sentence instead ---------
            dialog = _open_details(page)
            expect(dialog.locator("[data-action='open-form']")).to_be_visible(timeout=15000)  # positive control
            expect(dialog.locator("[data-action='cancel-booking']")).to_have_count(0)
            expect(dialog.locator("[data-testid='cancel-refusal']")).to_contain_text(ADMIN_ONLY)
            _close_dialog(page)
            refused = page.request.post(
                "/api/method/court_booking_tech.api.bookings.cancel_booking",
                headers={"X-Frappe-CSRF-Token": csrf(page)},
                form={"name": booking, "reason": "staff trying anyway"},
            )
            assert refused.status == 403 and ADMIN_ONLY in refused.text(), (refused.status, refused.text())
            assert _get(page, f"/api/resource/CBT Court Booking/{booking}")["booking_status"] == "Confirmed"

            # --- the Company Admin refunds it, typing why ----------------------
            login_as(page, ADMIN)
            load_board(page, BGC_BRANCH, D_REFUND)
            dialog = _open_details(page)
            button = dialog.locator("[data-action='cancel-booking']")
            expect(button).to_have_text(re.compile("Cancel & Refund"), timeout=15000)
            button.click()
            prompt = page.locator(".modal.show").filter(has_text=PROMPT_SENTENCE)
            expect(prompt).to_be_visible(timeout=15000)
            gestures.fill(page, "reason", REASON, scope=f".modal.show:has-text('{PROMPT_SENTENCE}')")
            with page.expect_response(lambda r: "cancel_booking" in r.url, timeout=30000) as resp_info:
                prompt.locator(".btn-primary, .btn-modal-primary").first.click()
            assert resp_info.value.ok, resp_info.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)

            invoice = _get(page, f"/api/resource/{INVOICE_DOCTYPE}/{invoice_name}")
            assert invoice["status"] == "Cancelled", invoice
            assert invoice["refund_reason"] == REASON and invoice["refunded_by"] == ADMIN, invoice
            total = float(invoice["total_amount"])

            # --- the Tenant Ledger, from the Hub, read off the table -----------
            today = page.evaluate("() => frappe.datetime.now_date()")
            rows = reports.ledger_rows(page, AYALA, AYALA_TITLE, today, "Tenant", "Refund / reversal")
            reversal = next(
                r for r in rows
                if r.get("Document") == invoice_name and "Refund / reversal" in (r.get("Description") or "")
            )
            assert REASON in reversal["Description"], reversal
            lines = reports.voucher(rows, invoice_name)
            # Paid today (back-recorded) and refunded today: the payment, then
            # its exact reversal — the cash leaves the drawer.
            assert lines[0] == ("Cash on Hand", total, 0.0), lines
            assert ("Cash on Hand", 0.0, total) in lines[len(lines) // 2:], lines

            # --- the printed statement: the booking form's own button ----------
            # "Billing Statement" opens the print view in a new tab
            # (window.open) — the path the walkthrough gives the admin (§13
            # "Where to print it"); the popup is what the admin reads.
            goto_form(page, "CBT Court Booking", booking)
            with page.context.expect_page(timeout=30000) as popup_info:
                gestures.click_form_action(page, "Billing Statement")
            printed = popup_info.value
            printed.wait_for_load_state("load", timeout=30000)
            assert "printview" in printed.url and invoice_name in printed.url, printed.url
            html = printed.content()
            printed.close()
            for needle in ("CANCELLED", "refunded", ADMIN_FULL_NAME, REASON):
                assert needle in html, needle
            assert "PAID &amp; VERIFIED" not in html
        finally:
            cancel_active_bookings(page, COURT, D_REFUND)


@pytest.mark.e2e
class TestReschedulePolicy:
    """B53 — a venue that does not refund CASH says so, and pays credit instead.

    Driven as the Company Admin: the refund gate is admin-only for every seat and
    the CASH gate sits BEHIND it, so a lesser seat would never reach the thing
    under test.

    ⚠ The policy fields are permlevel 1 — no product seat can set them — so the
    arrange goes through `court_booking_tech.testing` via `bench_json`, and the
    `finally` puts the company back. `Refund` is the default that roughly thirty
    backend tests lean on; leaving AYALA flipped would turn them red far from here.
    """

    def test_a_reschedule_only_venue_refuses_cash_and_pays_store_credit_instead(self, page: Page):
        # Clear the slot BEFORE the policy goes on: the cleanup helper cancels
        # paid bookings with no cause, which is exactly what this policy refuses.
        cancel_active_bookings(page, COURT, D_POLICY)
        before = bench_json(SET_POLICY, [AYALA, "Reschedule only", "Company Admin only"])["before"]
        try:
            login_as(page, ADMIN)
            load_board(page, BGC_BRANCH, D_POLICY)
            booking = quick_book(page, COURT, START_POLICY, "Cash")
            invoice_name = _get(page, f"/api/resource/CBT Court Booking/{booking}")["billing_doc"]
            assert _get(page, f"/api/resource/{INVOICE_DOCTYPE}/{invoice_name}")["status"] == "Paid & Verified"

            # --- the SERVER refuses cash. Done from a CLEAN page on purpose:
            # frappe.prompt stacks a second modal over the details dialog, and a
            # request issued while both are open times out.
            refused = page.request.post(
                "/api/method/court_booking_tech.api.bookings.cancel_booking",
                headers={"X-Frappe-CSRF-Token": csrf(page)},
                form={"name": booking, "reason": POLICY_REASON, "cause": "Customer request"},
            )
            assert refused.status == 417, (refused.status, refused.text())
            assert NO_CASH in refused.text(), refused.text()
            assert _get(page, f"/api/resource/CBT Court Booking/{booking}")["booking_status"] == "Confirmed"

            # --- the dialog explains it, and store credit goes through by hand -
            slot(page, COURT, START_POLICY).click()
            wait_dialog(page)
            page.locator(gestures.DIALOG).locator("[data-action='cancel-booking']").click()
            prompt = page.locator(".modal.show").filter(has_text=PROMPT_SENTENCE)
            expect(prompt).to_be_visible(timeout=15000)
            scope = f".modal.show:has-text('{PROMPT_SENTENCE}')"
            expect(prompt.locator("[data-testid='cash-refund-note']")).to_contain_text(NO_CASH)
            # An admin may declare either cause, so the Select renders at all.
            expect(prompt.locator(".frappe-control[data-fieldname='cause'] select")).to_be_visible()
            gestures.fill(page, "cause", "Customer request", scope=scope)
            gestures.fill(page, "reason", POLICY_REASON, scope=scope)
            gestures.fill(page, "issue_credit", True, scope=scope)
            with page.expect_response(lambda r: "cancel_booking" in r.url, timeout=30000) as resp:
                prompt.locator(".btn-primary, .btn-modal-primary").first.click()
            assert resp.value.ok, resp.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)

            invoice = _get(page, f"/api/resource/{INVOICE_DOCTYPE}/{invoice_name}")
            assert invoice["status"] == "Cancelled", invoice
            assert invoice["refund_cause"] == "Customer request", invoice
            # The books must tell credit from cash, or a Reschedule only venue
            # looks like it paid out money it never paid.
            assert int(invoice["refund_as_credit"]) == 1, invoice
            resp = page.request.get(
                "/api/resource/CBT Customer Credit",
                params={
                    "filters": f'[["source_invoice","=","{invoice_name}"]]',
                    "limit_page_length": 0,
                },
            )
            assert resp.ok, resp.text()
            minted = resp.json()["data"]
            assert len(minted) == 1, minted
        finally:
            bench_json(
                SET_POLICY,
                [
                    AYALA,
                    before.get("refund_policy") or "Refund",
                    before.get("facility_fault_cancel_by") or "Company Admin only",
                ],
            )
            cancel_active_bookings(page, COURT, D_POLICY)
