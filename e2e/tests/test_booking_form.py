"""
Section-4 pull-forward — CBT Court Booking desk form + deterministic sweep.

User rule: every change ships with E2E. Section-7 file 06 keeps the BOARD
operations (walk-in + extend via the Court Board page); this file covers only
the section-4 surface: the booking FORM pipeline (client scripts + server
validate + per-company numbering + instant-confirm) and the allow_tests-gated
run_expiry_sweep trigger that later E2E files depend on.

Determinism / re-runnability:
- Site-per-worker isolates the three xdist workers; within a site, each test
  cancels any active booking left on its slot by a previous non-reset run.
- The sweep no-op test uses an AYALA court (30-min default expiry): a
  seconds-old hold can never expire mid-test. The e2e-fast 1-minute company is
  exercised for real expiry by file 07 (section-9).
- Form values are set through cur_frm.set_value (client triggers still run);
  saving goes through cur_frm.save() — server-side validate is the authority
  and assertions read the SERVER state via page.request.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from helpers import gestures
from helpers.auth import PLATFORM_ADMIN
from helpers.navigation import wait_for_page_load

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

E2EF_COURT = "E2EF-main-court-1"
AYALA_COURT = "AYALA-bgc-court-1"

CASH_DATE = (date.today() + timedelta(days=30)).isoformat()
FT_DATE = (date.today() + timedelta(days=31)).isoformat()


def _csrf(page: Page) -> str:
    # _cancel_existing runs FIRST in each test, so the tab may still be
    # about:blank (which file precedes this one on the worker is a loadfile
    # distribution accident) — frappe only exists once a desk page loaded.
    if "/app" not in page.url and "/desk" not in page.url:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => window.frappe && frappe.csrf_token", timeout=15000)
    return page.evaluate("frappe.csrf_token")


def _get_booking(page: Page, name: str) -> dict:
    resp = page.request.get(f"/api/resource/CBT Court Booking/{name}")
    assert resp.ok, f"read {name}: HTTP {resp.status}"
    return resp.json()["data"]


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
            # Section-26: a paid booking's cancel is a refund and needs a reason.
            form={"name": row["name"], "reason": "E2E cleanup"},
        )
        assert cancel.ok, f"cancel {row['name']}: HTTP {cancel.status}"


def _create_booking_via_form(
    page: Page, court: str, booking_date: str, start_time: str, payment_method: str
) -> str:
    page.goto(
        "/desk/cbt-court-booking/new", wait_until="domcontentloaded", timeout=60000
    )
    wait_for_page_load(page)
    page.wait_for_selector(
        ".frappe-control[data-fieldname='court']", timeout=15000
    )
    # Typed and clicked. The client triggers this form depends on (branch/company
    # auto-fill from the court, the live preview) fire from the control's own change
    # event, which is what a human produces — the set_value chain this replaced only
    # simulated them.
    gestures.fill_link(page, "court", court)
    gestures.fill_link(page, "customer", "cust.carla@example.com")
    gestures.fill(page, "booking_date", booking_date)
    gestures.fill(page, "start_time", start_time)
    gestures.fill(page, "number_of_slots", 1)
    gestures.fill(page, "payment_method", payment_method)
    # Both Links must have SURVIVED the chain — a set_value against a
    # not-yet-rendered control silently no-ops (S11 note 22; the same class
    # fired in test_03 during the section-12 round-1 reshuffle).
    page.wait_for_function(
        """([court]) => cur_frm.doc.court === court
            && cur_frm.doc.customer === 'cust.carla@example.com'""",
        arg=[court],
        timeout=15000,
    )
    # Let the async client triggers (branch/company mirror from the court
    # chain, slot-config fetch for the live preview) settle before saving.
    # The company mirror matters: frappe pre-fills the `company` Link from
    # the site default (an ERPNext Company on this bench) — the trigger must
    # overwrite it or the server rejects the chain.
    page.wait_for_timeout(700)
    # frm.save() can RESOLVE even when the server rejects the insert, so a
    # blind is_new wait times out with zero diagnostics. Capture the outcome
    # and any msgprint text and fail loudly instead.
    #
    # SECTION-23 SWEEP (third and last of the first-save sites). A SUCCESSFUL
    # first save renames the new-doc route, and that navigation can destroy the
    # execution context before the resolved value crosses back, at which point
    # `page.evaluate` raises "Execution context was destroyed". That is the save
    # WORKING — a rejected save never navigates. File 01 has handled it since
    # section-11; file 17 had not and went red on 1 of 3 identical passes.
    try:
        return gestures.save_form(page)
    except PlaywrightError as exc:
        modals = page.evaluate(
            """() => Array.from(document.querySelectorAll('.modal.show'))
                .map(m => (m.innerText || '').trim()).filter(Boolean).slice(0, 3)"""
        )
        raise AssertionError(
            f"booking form save failed: {str(exc)[:300]}; modals: {modals}"
        ) from None


@pytest.mark.e2e
class TestBookingForm:

    def test_cash_booking_instant_confirm_via_form(self, page: Page):
        """Cash walk-in through the desk form: per-company E2EF series,
        instant Confirmed, server-computed schedule + total."""
        _cancel_existing(page, E2EF_COURT, CASH_DATE, "10:00:00")
        name = _create_booking_via_form(
            page, E2EF_COURT, CASH_DATE, "10:00:00", "Cash"
        )
        assert name.startswith("BK-E2EF-"), name

        booking = _get_booking(page, name)
        assert booking["booking_status"] == "Confirmed", booking
        assert booking["confirmed_by"] == PLATFORM_ADMIN, booking
        assert booking["end_time"] == "11:00:00", booking
        assert booking["duration_hours"] == 1.0, booking
        assert booking["total_amount"] == 100.0, booking  # E2EF rate x 1h
        assert booking["customer_name"] == "Carla Courtside", booking

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "booking_form_confirmed.png"), full_page=True
        )

    def test_fund_transfer_reserved_and_sweep_endpoint(self, page: Page):
        """Fund Transfer through the form: Reserved + base-clock stamp; the
        deterministic sweep endpoint answers and leaves the live hold alone
        (real expiry E2E arrives with file 07 in section-9)."""
        _cancel_existing(page, AYALA_COURT, FT_DATE, "06:00:00")
        name = _create_booking_via_form(
            page, AYALA_COURT, FT_DATE, "06:00:00", "Fund Transfer"
        )
        assert name.startswith("BK-AYALA-"), name

        booking = _get_booking(page, name)
        assert booking["booking_status"] == "Reserved", booking
        assert booking["reservation_expires_at"], booking  # base clock stamped

        resp = page.request.post(
            "/api/method/court_booking_tech.tasks.run_expiry_sweep",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
        )
        assert resp.ok, f"run_expiry_sweep: HTTP {resp.status} {resp.text()}"
        result = resp.json()["message"]
        assert name not in result["expired"], result  # 30-min hold is live

        booking = _get_booking(page, name)
        assert booking["booking_status"] == "Reserved", booking

        # Leave the form cleanly (S3 pattern — a wedged dirty form breaks
        # later navigation in the shared worker context).
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
