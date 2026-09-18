"""
Section-12 E2E file 09 — billing-statement print rendering: the VAT x status
matrix.

The section-6 pull-forward (test_billing_print.py) covers the NON-VAT E2EF
surface: invoice auto-create + linkage, derived status over REST, stamp and
watermark PRESENCE, cancel-same-doc, and a NON-VAT PDF smoke. THIS file owes
what that one deferred (S6 as-built 9): the VAT-company rendering matrix
asserted on the RENDERED page DOM, the VAT-line ABSENCE proof for NON-VAT,
or_number rendering, and the PDF sanity check of a VAT invoice.

Fixtures: AYALA (VAT 12%, "Ayala Courts Sports Corp.", TIN 010-203-040-000,
bgc court-2 @ P450/hr — 450 / 1.12 = 401.79 vatable + 48.21 VAT) and
QCSM-annex Main Hall (NON-VAT — annex is open ALL week; timog closes Sundays
and these dates are relative, so timog could land on one). Dates today+37/
+38/+39 are unclaimed by every other E2E file (+30..36, +40..47, +50..57,
+60..62) and far from the fixed 2027 seed dates. cust.carla holds no
membership, so no discount interferes with the pinned peso math.

Re-runnability: every test clears active bookings on its slot first; no
sleeps.
"""
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Page

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

AYALA_COURT = "AYALA-bgc-court-2"          # P450/hr, 1-hour slots
QCSM_COURT = "QCSM-annex-main-hall"        # P250/hr, branch open all week
CUSTOMER = "cust.carla@example.com"

AYALA_NAME = "Ayala Courts Sports Corp."
AYALA_TIN = "TIN: 010-203-040-000"
QCSM_NAME = "QC Smash Badminton Center Inc."
QCSM_TIN = "TIN: 050-607-080-000"
DISCLAIMER = "official invoice/receipt issued manually by"
BLANK_OR = "____________________"

VAT_DATE = (date.today() + timedelta(days=37)).isoformat()
NONVAT_DATE = (date.today() + timedelta(days=38)).isoformat()
OR_DATE = (date.today() + timedelta(days=39)).isoformat()

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


def _csrf(page: Page) -> str:
    if "/app" not in page.url and "/desk" not in page.url:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => window.frappe && frappe.csrf_token", timeout=15000)
    return page.evaluate("frappe.csrf_token")


def _cancel_existing(page: Page, court: str, booking_date: str, start_time: str):
    """Re-runnability without a reset: clear any active booking on the slot."""
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
    page: Page, court: str, booking_date: str, start_time: str, payment_method: str
) -> dict:
    _cancel_existing(page, court, booking_date, start_time)
    resp = page.request.post(
        "/api/resource/CBT Court Booking",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
        data={
            "court": court,
            "customer": CUSTOMER,
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


def _printview_url(invoice: str) -> str:
    return "/printview?" + urlencode(
        {"doctype": "CBT Booking Invoice", "name": invoice, **PRINT_PARAMS}
    )


def _printview_text(page: Page, invoice: str) -> str:
    resp = page.request.get(
        "/printview",
        params={"doctype": "CBT Booking Invoice", "name": invoice, **PRINT_PARAMS},
    )
    assert resp.ok, f"printview {invoice}: HTTP {resp.status}"
    return resp.text()


@pytest.mark.e2e
class TestBillingPrintMatrix:

    def test_paid_vat_full_matrix_dom_and_pdf(self, page: Page):
        """Cash booking at the VAT company, asserted on the RENDERED page:
        PAID & VERIFIED stamp (no watermark), the VAT totals block with the
        exact-sum figures, company identity + TIN, title, disclaimer,
        verified-by line — then the same invoice renders as a real PDF."""
        booking = _create_booking(page, AYALA_COURT, VAT_DATE, "08:00:00", "Cash")
        assert booking["booking_status"] == "Confirmed", booking

        invoice = _get_invoice(page, booking["billing_doc"])
        assert invoice["status"] == "Paid & Verified", invoice
        assert invoice["vat_mode"] == "VAT", invoice
        # Exact-sum VAT math (S6): vatable + vat == total to the centavo.
        assert float(invoice["total_amount"]) == 450.00, invoice
        assert float(invoice["vatable_amount"]) == 401.79, invoice
        assert float(invoice["vat_amount"]) == 48.21, invoice

        page.goto(_printview_url(invoice["name"]), wait_until="domcontentloaded")
        page.wait_for_selector(".cbt-bs-content", timeout=15000)

        stamp = page.locator(".cbt-bs-stamp")
        assert stamp.count() == 1, "PAID & VERIFIED stamp missing"
        assert stamp.inner_text().strip() == "PAID & VERIFIED"
        assert page.locator(".cbt-bs-watermark").count() == 0, (
            "a paid statement must carry no watermark"
        )

        body = page.locator(".cbt-bs").inner_text()
        for expected in (
            "BILLING STATEMENT",
            AYALA_NAME,
            AYALA_TIN,
            "VATable Sales",
            "401.79",
            "VAT (12%)",
            "48.21",
            "TOTAL",
            "450.00",
            "Verified by",
            DISCLAIMER,
        ):
            assert expected in body, f"missing on rendered page: {expected!r}"
        assert "UNPAID" not in body
        assert "NON-VAT" not in body, "VAT company must not carry the annotation"

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "09_paid_vat_statement.png"), full_page=True
        )

        pdf = page.request.get(
            "/api/method/frappe.utils.print_format.download_pdf",
            params={
                "doctype": "CBT Booking Invoice",
                "name": invoice["name"],
                **PRINT_PARAMS,
            },
        )
        assert pdf.ok, f"download_pdf: HTTP {pdf.status}"
        body_bytes = pdf.body()
        assert body_bytes.startswith(b"%PDF"), "not a PDF payload"
        assert len(body_bytes) > 5000, (
            f"suspiciously small PDF ({len(body_bytes)} bytes) — "
            "wkhtmltopdf likely rendered an error page"
        )

    def test_unpaid_vat_watermark_keeps_vat_lines(self, page: Page):
        """Fund-transfer hold at the VAT company: UNPAID watermark, NO paid
        stamp — and the VAT totals block still renders (the breakdown is
        status-independent; the statement is printable before payment)."""
        booking = _create_booking(
            page, AYALA_COURT, VAT_DATE, "10:00:00", "Fund Transfer"
        )
        assert booking["booking_status"] == "Reserved", booking

        invoice = _get_invoice(page, booking["billing_doc"])
        assert invoice["status"] == "Unpaid", invoice

        html = _printview_text(page, invoice["name"])
        assert "UNPAID" in html
        assert "PAID &amp; VERIFIED" not in html and "PAID & VERIFIED" not in html
        assert "VATable Sales" in html
        assert "401.79" in html and "48.21" in html
        assert AYALA_NAME in html
        assert AYALA_TIN in html

    def test_nonvat_annotation_and_no_vat_lines(self, page: Page):
        """NON-VAT company: the annotation renders and the VAT lines are
        ABSENT — the absence half the section-6 pull-forward never asserted."""
        booking = _create_booking(page, QCSM_COURT, NONVAT_DATE, "08:00:00", "Cash")
        assert booking["booking_status"] == "Confirmed", booking

        invoice = _get_invoice(page, booking["billing_doc"])
        assert invoice["vat_mode"] == "NON-VAT", invoice

        html = _printview_text(page, invoice["name"])
        assert "NON-VAT" in html
        assert "VATable Sales" not in html, "NON-VAT must not render a VATable line"
        assert "VAT (" not in html, "NON-VAT must not render a VAT-rate line"
        assert QCSM_NAME in html
        assert QCSM_TIN in html
        assert "250.00" in html

    def test_desk_print_page_defaults_to_the_billing_statement(self, page: Page):
        """Backlog B23 — the surface the user actually hit: the desk print page
        with NO format named, opened by its URL exactly as the user did
        (`/desk/print/CBT Booking Invoice/<name>`). Every other print test here
        names the format in PRINT_PARAMS, so the DEFAULT resolution path had
        zero coverage. Reproduced 2026-08-27 pre-fix with these same selectors:
        selector value "", generic "Standard" field-label layout in the preview.
        Slot 12:00 on VAT_DATE is unused by the other tests here (08:00, 10:00)."""
        booking = _create_booking(page, AYALA_COURT, VAT_DATE, "12:00:00", "Cash")
        invoice_name = booking["billing_doc"]

        page.goto(
            f"/desk/print/CBT Booking Invoice/{invoice_name}",
            wait_until="domcontentloaded",
        )
        selector = page.locator('.print-preview-sidebar input[data-fieldname="print_format"]')
        selector.wait_for(timeout=15000)
        # The selector is what a human reads — it must land on the statement
        # without anyone picking it.
        page.wait_for_function(
            "() => document.querySelector('.print-preview-sidebar input[data-fieldname=\"print_format\"]')?.value === 'CBT Billing Statement'",
            timeout=15000,
        )

        preview = page.frame_locator("iframe.print-format-container")
        preview.locator(".cbt-bs-content").wait_for(timeout=20000)
        # The WHOLE preview body, not just .cbt-bs — so the negative assertion
        # can fail on its own if the Standard layout were rendered beside it.
        body = preview.locator("body").inner_text()
        assert AYALA_NAME in body, "statement identity missing from the default preview"
        assert DISCLAIMER in body
        assert "Customer Name:" not in body, "Standard field-label layout leaked through"

    def test_or_number_renders_after_staff_records_it(self, page: Page):
        """The one staff-writable field: blank underline before, the recorded
        O.R. number after a REST update (validate allows or_number alone)."""
        booking = _create_booking(page, AYALA_COURT, OR_DATE, "08:00:00", "Cash")
        invoice_name = booking["billing_doc"]

        before = _printview_text(page, invoice_name)
        assert f"Official O.R. No.: {BLANK_OR}" in before
        assert "OR-9901" not in before

        update = page.request.put(
            f"/api/resource/CBT Booking Invoice/{invoice_name}",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
            data={"or_number": "OR-9901"},
        )
        assert update.ok, f"or_number update: HTTP {update.status} {update.text()}"

        after = _printview_text(page, invoice_name)
        assert "Official O.R. No.: OR-9901" in after
        assert BLANK_OR not in after
