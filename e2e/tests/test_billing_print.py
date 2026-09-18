"""
Section-6 pull-forward — billing document + print surface over real HTTP.

User rule: every change ships with E2E. File 09 (section-12) keeps the full
VAT×status print-rendering matrix and PDF pixel checks; this file covers only
the section-6 surface end-to-end: invoice auto-created and linked at booking
creation, the derived Paid/Unpaid/Cancelled status over the REST API, the
print view rendering the company identity + status indicator, and a PDF
download smoke (wkhtmltopdf renders the format without error).

Determinism / re-runnability: bookings are created via the REST resource API
on E2EF slots and dates no other E2E file uses (+35/+36 days vs +30..34);
each test cancels any active booking left on its slot by a previous
non-reset run. No sleeps.
"""
import json
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page

E2EF_COURT = "E2EF-main-court-1"
REGISTERED_NAME = "E2E Fast Courts Testing Corp."

PRINT_DATE = (date.today() + timedelta(days=35)).isoformat()
LIFECYCLE_DATE = (date.today() + timedelta(days=36)).isoformat()

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


def _csrf(page: Page) -> str:
    if "/app" not in page.url:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => window.frappe && frappe.csrf_token", timeout=15000)
    return page.evaluate("frappe.csrf_token")


def _cancel_existing(page: Page, court: str, booking_date: str, start_time: str):
    filters = json.dumps(
        [
            ["court", "=", court],
            ["booking_date", "=", booking_date],
            ["start_time", "=", start_time],
            ["booking_status", "in", ["Reserved", "Confirmed", "Extended"]],
        ]
    )
    resp = page.request.get(
        "/api/resource/CBT Court Booking", params={"filters": filters}
    )
    assert resp.ok, f"slot probe: HTTP {resp.status}"
    for row in resp.json()["data"]:
        cancel = page.request.post(
            "/api/method/court_booking_tech.api.bookings.cancel_booking",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
            form={"name": row["name"]},
        )
        assert cancel.ok, f"cancel {row['name']}: HTTP {cancel.status}"


def _create_booking(
    page: Page, booking_date: str, start_time: str, payment_method: str
) -> dict:
    _cancel_existing(page, E2EF_COURT, booking_date, start_time)
    resp = page.request.post(
        "/api/resource/CBT Court Booking",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
        data={
            "court": E2EF_COURT,
            "customer": "cust.carla@example.com",
            "booking_date": booking_date,
            "start_time": start_time,
            "number_of_slots": 1,
            "payment_method": payment_method,
        },
    )
    assert resp.ok, f"create booking: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _get_invoice(page: Page, name: str) -> dict:
    resp = page.request.get(f"/api/resource/CBT Booking Invoice/{name}")
    assert resp.ok, f"read invoice {name}: HTTP {resp.status}"
    return resp.json()["data"]


def _printview(page: Page, invoice: str) -> str:
    resp = page.request.get(
        "/printview",
        params={"doctype": "CBT Booking Invoice", "name": invoice, **PRINT_PARAMS},
    )
    assert resp.ok, f"printview {invoice}: HTTP {resp.status}"
    return resp.text()


@pytest.mark.e2e
class TestBillingPrint:

    def test_cash_booking_gets_paid_invoice_print_and_pdf(self, page: Page):
        """Cash walk-in: invoice auto-created + linked, derived straight to
        Paid & Verified; the print view carries the company identity, the
        NON-VAT annotation (E2EF is NON-VAT) and the PAID stamp; the PDF
        endpoint renders it (wkhtmltopdf smoke — pixels wait for file 09)."""
        booking = _create_booking(page, PRINT_DATE, "08:00:00", "Cash")
        assert booking["booking_status"] == "Confirmed", booking
        assert booking["billing_doc"], "no invoice linked at creation"

        invoice = _get_invoice(page, booking["billing_doc"])
        assert invoice["status"] == "Paid & Verified", invoice
        assert invoice["booking"] == booking["name"], invoice
        assert invoice["vat_mode"] == "NON-VAT", invoice

        html = _printview(page, booking["billing_doc"])
        assert "PAID &amp; VERIFIED" in html or "PAID & VERIFIED" in html
        assert REGISTERED_NAME in html
        assert "NON-VAT" in html
        assert "BILLING STATEMENT" in html
        assert "UNPAID" not in html

        pdf = page.request.get(
            "/api/method/frappe.utils.print_format.download_pdf",
            params={
                "doctype": "CBT Booking Invoice",
                "name": booking["billing_doc"],
                **PRINT_PARAMS,
            },
        )
        assert pdf.ok, f"download_pdf: HTTP {pdf.status}"
        assert pdf.body().startswith(b"%PDF"), "not a PDF payload"

    def test_ft_booking_unpaid_watermark_then_cancelled(self, page: Page):
        """Fund-transfer hold: invoice Unpaid with the UNPAID watermark on
        print — printable BEFORE payment (the key requirement) — and the
        cancel path flips the SAME document to Cancelled (number retained)."""
        booking = _create_booking(page, LIFECYCLE_DATE, "09:00:00", "Fund Transfer")
        assert booking["booking_status"] == "Reserved", booking
        assert booking["billing_doc"], "no invoice linked at creation"

        invoice = _get_invoice(page, booking["billing_doc"])
        assert invoice["status"] == "Unpaid", invoice

        html = _printview(page, booking["billing_doc"])
        assert "UNPAID" in html
        assert REGISTERED_NAME in html

        cancel = page.request.post(
            "/api/method/court_booking_tech.api.bookings.cancel_booking",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
            form={"name": booking["name"]},
        )
        assert cancel.ok, f"cancel: HTTP {cancel.status} {cancel.text()}"

        after = _get_invoice(page, booking["billing_doc"])
        assert after["status"] == "Cancelled", after
        assert after["name"] == invoice["name"], "cancel must not reissue"
