"""
E2E file 11 — Walk-in desk flow (section-13, Backlog B1).

The stranger paying cash: staff book them with a free-text name and no account,
the slot shows who is on court, the billing statement names the actual person,
and the booking stays completely outside the portal (which is built on
`customer = session.user`) and outside the per-account ban machinery.

Runs as Administrator, admin-driven like file 06 (E2EF has no staff user; the
staff-actor positives live in the BACKEND rows — tests/test_verification.py
proves a real staff session uploading a walk-in's proof).

Date map (all relative to the run day). Ledger CORRECTED 2026-08-18 (Backlog B16):
this file used to say "+40..+47 file 06", which is wrong — **+45 belongs to file 02**,
whose `_qcsm_open_date()` slides +45 -> +46 when the run day makes +45 a Sunday.
The re-verified ledger, copied from file 15:

  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   file 13
  +60/+61   files 08 AND 10 (see B16)       +62       file 10
  +65       file 14                         +66       reserved in prose, never used
  +67/+68   file 15                         +69/+70   file 16
  +71/+72   file 17                         +73/+74   file 18
  **+75 upward is FREE.**

THIS FILE CLAIMS **+63 and +64**.

  +63  E2EF-main-court-1 — cash walk-ins (instant confirm, ₱100/hr)
  +64  AYALA-bgc-court-1 — the fund-transfer hold and the dialog assertions

DELIBERATE DEVIATION from the section draft, which put everything on E2EF:
E2EF's reservation expiry is ONE MINUTE (it exists to make expiry tests fast).
A fund-transfer walk-in that has to survive open-dialog -> file-upload ->
accept would be racing that clock, so the FT half moves to AYALA's 30-minute
base clock — the same reasoning file 06 records for its own D_RESERVED row.

No sleeps. Every slot assertion waits on the RE-RENDERED node (the board
reloads asynchronously after every mutation).
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page

from helpers.board import (
    BGC_BRANCH,
    E2EF_BRANCH,
    E2EF_COURT,
    cancel_active_bookings,
    load_board,
    quick_book,
    quick_book_walkin,
    slot,
    wait_dialog,
)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"
PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech" / "seeds" / "files" / "proof_sample.jpg"
)

BGC_COURT = "AYALA-bgc-court-1"  # ₱400/hr, AYALA 30-minute base clock

D_CASH = (date.today() + timedelta(days=63)).isoformat()
D_FT = (date.today() + timedelta(days=64)).isoformat()

WALKIN_NAME = "Walk-in Wanda E2E"
WALKIN_PHONE = "0917-000-2222"

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


def _printview_text(page: Page, invoice: str) -> str:
    resp = page.request.get(
        "/printview",
        params={"doctype": "CBT Booking Invoice", "name": invoice, **PRINT_PARAMS},
    )
    assert resp.ok, f"printview {invoice}: HTTP {resp.status}"
    return resp.text()


def _open_booked_slot(page: Page, court: str, start_time: str):
    """Click a booked slot and wait for ITS dialog to be visible.

    Closed frappe dialogs stay in the DOM (S12 walkthrough lesson), so every
    assertion below is scoped to `.modal.show` rather than the document.
    """
    slot(page, court, start_time).click()
    wait_dialog(page)


@pytest.mark.e2e
class TestWalkInDesk:

    def test_walkin_cash_quickbook_puts_the_typed_name_on_the_board(
        self, page: Page
    ):
        """The core of Backlog B1: no account, no email demanded, and the
        person's own name is what the desk (and later the receipt) shows."""
        cancel_active_bookings(page, E2EF_COURT, D_CASH)
        load_board(page, E2EF_BRANCH, D_CASH)
        assert (
            slot(page, E2EF_COURT, "10:00:00").get_attribute("data-status")
            == "available"
        )

        name = quick_book_walkin(
            page, E2EF_COURT, "10:00:00", "Cash", WALKIN_NAME, WALKIN_PHONE
        )
        assert name.startswith("BK-E2EF-"), name

        booked = page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        assert "cbt-slot-confirmed" in (booked.get_attribute("class") or "")
        assert booked.query_selector(".cbt-slot-who").inner_text() == WALKIN_NAME

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        # The whole point: a real booking with NO User row behind it.
        #
        # ASSERT KEY ABSENCE, never `booking["customer"] is None`. The REST
        # resource endpoint returns a Document, and `BaseDocument.__json__`
        # serialises with `as_dict(no_nulls=True)` — every NULL field is
        # DROPPED from the payload, so subscripting a null field raises
        # KeyError. (`frappe.client.get` keeps nulls; /api/resource does not.)
        assert "customer" not in booking, (
            f"walk-in booking must carry no account, got {booking['customer']!r}"
        )
        assert booking["customer_name"] == WALKIN_NAME
        assert booking["customer_phone"] == WALKIN_PHONE
        assert booking["booking_status"] == "Confirmed"

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "s13_walkin_board.png"), full_page=True
        )

    def test_walkin_statement_names_the_person_who_paid(self, page: Page):
        """The honest-receipt requirement — the reason option (b) was chosen
        over one shared "Walk-in Customer" account for the whole company."""
        cancel_active_bookings(page, E2EF_COURT, D_CASH)
        load_board(page, E2EF_BRANCH, D_CASH)
        name = quick_book_walkin(
            page, E2EF_COURT, "11:00:00", "Cash", WALKIN_NAME, WALKIN_PHONE
        )

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        assert booking["billing_doc"], "walk-in booking has no billing document"
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{booking['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]
        assert invoice["customer_name"] == WALKIN_NAME
        # Key absence, not a null read — see the note in the test above.
        assert "customer" not in invoice, (
            f"walk-in invoice must carry no account, got {invoice['customer']!r}"
        )

        html = _printview_text(page, invoice["name"])
        assert WALKIN_NAME in html
        assert "PAID &amp; VERIFIED" in html
        # A blank or a literal "None" on a printed receipt is the failure mode
        # this whole section exists to avoid.
        assert "Customer:</strong> None" not in html

    def test_walkin_fund_transfer_proof_is_uploaded_and_accepted(self, page: Page):
        """A walk-in may still pay by transfer, so the two-clock engine has to
        run with no account behind the booking — staff upload the proof they
        were sent and accept it from the same dialog."""
        cancel_active_bookings(page, BGC_COURT, D_FT)
        load_board(page, BGC_BRANCH, D_FT)

        name = quick_book_walkin(
            page, BGC_COURT, "10:00:00", "Fund Transfer", WALKIN_NAME, WALKIN_PHONE
        )
        assert name.startswith("BK-AYALA-"), name
        page.wait_for_selector(
            f".cbt-slot[data-court='{BGC_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Reserved']",
            timeout=15000,
        )

        _open_booked_slot(page, BGC_COURT, "10:00:00")
        identity = page.locator(".modal.show [data-testid='detail-customer']")
        assert WALKIN_NAME in identity.inner_text()
        assert "walk-in" in identity.inner_text()
        # PLAN §8j — staff see name AND phone; nothing else, and nowhere else.
        assert WALKIN_PHONE in page.locator(".modal.show").inner_text()

        # The staff upload is a real hidden file input; the handler hides the
        # dialog and REOPENS it, so wait for the proof card in the new one
        # (never for the old modal to detach — S12 walkthrough lesson).
        with page.expect_response(
            lambda r: "upload_proof" in r.url, timeout=30000
        ) as upload:
            page.set_input_files(".modal.show .cbt-staff-proof-file", str(PROOF_JPG))
        assert upload.value.ok, f"upload_proof: HTTP {upload.value.status}"
        # ONE atomic wait, and it must gate on `cur_dialog`, not on `.modal.show`.
        # dialog.js sets `cur_dialog = null` on hidden.bs.modal (the upload
        # handler hides the dialog before reopening it) and rebinds it only on
        # shown.bs.modal — which fires AFTER the fade, whereas Bootstrap adds
        # `.show` at the START of it. Waiting on `.modal.show .cbt-proof-card`
        # therefore returns mid-fade with cur_dialog still null, and the click
        # below dies on "Cannot read properties of null".
        page.wait_for_function(
            """() => window.cur_dialog && cur_dialog.$wrapper.hasClass('show')
                && cur_dialog.$wrapper.find('.cbt-proof-card').length > 0""",
            timeout=20000,
        )

        with page.expect_response(
            lambda r: "accept_proofs" in r.url, timeout=30000
        ) as accepted:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert accepted.value.ok, f"accept_proofs: HTTP {accepted.value.status}"

        page.wait_for_selector(
            f".cbt-slot[data-court='{BGC_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        assert "customer" not in booking, booking.get("customer")
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{booking['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]

    def test_walkin_is_unbannable_and_invisible_to_the_portal(
        self, page: Page, customer_page: Page
    ):
        """Two boundaries in one: a ban is per-ACCOUNT (section-11), and the
        portal is built on `customer = session.user`. The ACCOUNT booking below
        is the control — without it, "no Ban button" would also pass on a page
        that simply failed to render one."""
        cancel_active_bookings(page, BGC_COURT, D_FT)
        load_board(page, BGC_BRANCH, D_FT)

        walkin = quick_book_walkin(
            page, BGC_COURT, "12:00:00", "Cash", WALKIN_NAME, WALKIN_PHONE
        )
        page.wait_for_selector(
            f".cbt-slot[data-court='{BGC_COURT}'][data-start='12:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        account_booking = quick_book(page, BGC_COURT, "13:00:00", "Cash")
        page.wait_for_selector(
            f".cbt-slot[data-court='{BGC_COURT}'][data-start='13:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        # --- the walk-in: identified, but not bannable ----------------------
        _open_booked_slot(page, BGC_COURT, "12:00:00")
        assert "walk-in" in page.locator(
            ".modal.show [data-testid='detail-customer']"
        ).inner_text()
        assert (
            page.locator(".modal.show [data-action='ban-customer']").count() == 0
        ), "a walk-in has no account to ban"
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        # --- the control: an account booking DOES offer the ban -------------
        _open_booked_slot(page, BGC_COURT, "13:00:00")
        assert (
            page.locator(".modal.show [data-action='ban-customer']").count() == 1
        ), "an account booking must still offer Ban Customer"
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        # --- the portal cannot see the walk-in at all -----------------------
        resp = customer_page.request.get(
            "/api/method/court_booking_tech.api.portal.get_my_bookings"
        )
        assert resp.ok, f"get_my_bookings: HTTP {resp.status}"
        listed = resp.json()["message"]["bookings"]
        names = {row["booking"] for row in listed}
        assert walkin not in names, "a walk-in booking leaked into the portal"
        assert account_booking not in names, (
            "sanity: Carla's desk booking must not appear in Pia's list either"
        )
        body = resp.text()
        assert WALKIN_NAME not in body, "the walk-in's name leaked to the portal"
