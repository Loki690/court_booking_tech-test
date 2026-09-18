"""
E2E — Payment channels (Backlog B29, Batch 13, 2026-08-27).

WHERE the money landed, driven the way the people involved actually do it:

1. the CUSTOMER books on /book, sees the facility's transfer channels as
   cards at checkout (GCash pre-selected), ticks BDO, reserves, reads BDO's
   account number on the confirmation and on the booking page, then uploads
   a receipt that says GCash; the DESK opens the verification dialog, finds
   "Received via" pre-set to what the receipt claims (GCash), accepts — and
   the booking, its billing document and the by-channel report all say GCash
   (the ruling: customer picks, staff correct against the upload);
2. the DESK quick-books: with Cash the "Paid via" Select offers only the cash
   drawer, switching to Fund Transfer offers GCash and BDO, BDO is picked, and
   the committed hold carries it.

3. (2026-09-03) the QR TILE: at checkout a tap on GCash's QR opens the lightbox
   AND picks GCash (the tile sits inside the card's <label>); on the booking page
   of a GCash hold the tile opens the same lightbox, Escape closes it.
   (2026-09-09, Backlog B50) both taps land on the "Tap or click to enlarge"
   WORDS, not the image: the wrap around tile + hint is the control now, bound
   directly, because an iPhone tap on the old image-only, document-delegated
   target opened nothing.

TENANT. AYALA / BGC — the one seeded tenant with THREE channels (Cash, GCash,
a BDO bank), which is what makes "pick the other one" a real choice. Court-1
for the customer rows (10:00 row 1, 14:00 row 3), court-2 for the desk row,
all on the same date. Seeded GCash carries a QR; seeded BDO has none.

Date ledger: THIS FILE CLAIMS **+79** (+78 was the last claimed, by
test_booking_fee). Slots are cleared before AND after each row through the
admin session (a portal customer cannot cancel a hold with a proof on it).
No sleeps — every wait is on a rendered node, a dialog flag or a response.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers import board, gestures, portal
from helpers.board import (
    _submit_quick_book,
    cancel_active_bookings,
    channel_options,
    load_board,
    slot,
    wait_channels_settled,
    wait_dialog,
    wait_money_settled,
)

PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

TENANT = "ayala-courts"
BRANCH = "AYALA-bgc"
COURT_1 = "AYALA-bgc-court-1"
COURT_2 = "AYALA-bgc-court-2"
D_CH = (date.today() + timedelta(days=79)).isoformat()
BDO_ACCOUNT = "0012-3456-7890"  # seeds: EXTRA_PAYMENT_CHANNELS


def _get_json(page: Page, url: str, params: dict | None = None):
    resp = page.request.get(url, params=params)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _transfer_channels(page: Page) -> dict:
    """label -> name for AYALA's ENABLED transfer channels, from the admin session."""
    rows = _get_json(
        page,
        "/api/resource/CBT Payment Channel",
        params={
            "filters": json.dumps(
                [["company", "=", TENANT], ["kind", "=", "Transfer"], ["enabled", "=", 1]]
            ),
            "fields": json.dumps(["name", "label"]),
            "order_by": "sort_order asc",
        },
    )
    return {row["label"]: row["name"] for row in rows}


@pytest.mark.e2e
class TestPaymentChannels:

    def test_customer_picks_a_bank_and_staff_confirm_what_the_receipt_says(
        self, page: Page, customer_page: Page
    ):
        cancel_active_bookings(page, COURT_1, D_CH)
        try:
            channels = _transfer_channels(page)
            assert {"GCash", "BDO"} <= set(channels), channels
            gcash, bdo = channels["GCash"], channels["BDO"]

            # --- checkout: the cards, GCash first, BDO ticked ----------------
            customer_page.goto(
                f"/book?c={TENANT}&b=bgc&d={D_CH}", wait_until="domcontentloaded", timeout=60000
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(
                f".cbt-slot[data-court='{COURT_1}'][data-start='10:00:00']"
            ).click()
            customer_page.locator("#cbt-review").click()
            radios = customer_page.locator("#cbt-channels input[name='cbt-channel']")
            expect(radios).to_have_count(2, timeout=15000)
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(gcash)
            bdo_card = customer_page.locator(f"#cbt-channels .cbt-channel[data-channel='{bdo}']")
            expect(bdo_card).to_contain_text(BDO_ACCOUNT)
            bdo_card.click()
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(bdo)

            # --- the QR tile: a lightbox, and a tap on it PICKS that channel --
            # Placed after BDO is ticked so the radio flip is an assertion that
            # can fail (GCash is the pre-selected default at :107).
            gcash_card = customer_page.locator(f"#cbt-channels .cbt-channel[data-channel='{gcash}']")
            tile = gcash_card.locator("img.cbt-channel-qr")
            expect(tile).to_be_visible()
            # B50: tap the WORDS under the tile — the target a thumb actually
            # lands on, which sat outside the control until 2026-09-09.
            gcash_card.locator(".cbt-channel-qr-hint").click()
            lightbox = customer_page.locator("#cbt-qr-modal")
            expect(lightbox).to_be_visible()
            expect(lightbox.locator("#cbt-qr-title")).to_have_text("GCash")
            # The card's OWN account line, not a seed constant: a platform admin
            # edits these on a live bench and the lightbox must follow the card.
            expect(lightbox.locator("#cbt-qr-meta")).to_have_text(
                gcash_card.locator(".cbt-channel-meta").inner_text()
            )
            assert lightbox.locator("#cbt-qr-image").get_attribute("src") == tile.get_attribute("src")
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(gcash)
            lightbox.locator("#cbt-qr-close").click()
            expect(lightbox).to_be_hidden()
            bdo_card.click()  # back to the channel this row pays through
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(bdo)
            # 2026-09-04 (user ruling, B31): a channel's OWN note is the instruction
            # and the company text yields to it at checkout — AYALA's GCash note is
            # seeded, so the company text is EMPTY here even with BDO ticked. The
            # confirmation and the booking page below pin the FALLBACK: BDO carries
            # no note, so there the company text must come back.
            expect(gcash_card.locator(".cbt-channel-note")).to_contain_text("reference number")
            expect(customer_page.locator("#cbt-instructions")).to_have_text("")

            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            match = re.search(r"BK-AYALA-\d{4}-\d{5}", ref.inner_text())
            assert match, ref.inner_text()
            booking = match.group(0)
            # Backlog B37: the confirmation repeats the venue's Directions link,
            # pointed at AYALA-bgc's ACTUAL seeded pin.
            where_href = customer_page.locator("#cbt-confirmed-where a").get_attribute("href") or ""
            assert "destination=14.5507,121.0494" in where_href, where_href
            confirmed = customer_page.locator("#cbt-confirmed-channel")
            expect(confirmed).to_contain_text("BDO")
            expect(confirmed).to_contain_text(BDO_ACCOUNT)
            # BDO carries no note → the company-level text is the fallback here.
            expect(confirmed.locator(".cbt-channel-note")).to_have_count(0)
            expect(customer_page.locator("#cbt-confirmed-instructions")).to_contain_text("GCash")

            # --- the booking page: BDO to pay, the receipt says GCash --------
            portal.open_detail(customer_page, booking)
            pay = customer_page.locator("#cbt-pay-channel")
            expect(pay).to_contain_text("BDO", timeout=15000)
            expect(pay).to_contain_text(BDO_ACCOUNT)
            expect(pay.locator(".cbt-channel-note")).to_have_count(0)
            expect(customer_page.locator("#cbt-instructions")).to_contain_text("GCash")
            select = customer_page.locator("#cbt-proof-channel")
            expect(select).to_be_visible()
            expect(select).to_have_value(bdo)
            select.select_option(gcash)
            customer_page.set_input_files("#cbt-file", str(PROOF_JPG))
            customer_page.click("#cbt-upload")
            expect(customer_page.locator("#cbt-deadline-note")).to_be_visible(timeout=20000)
            expect(customer_page.locator("#cbt-proofs .cbt-proof").first).to_contain_text("via GCash")

            proofs = _get_json(
                page,
                "/api/resource/CBT Payment Proof",
                params={
                    "filters": json.dumps([["booking", "=", booking]]),
                    "fields": json.dumps(["name", "status", "payment_channel"]),
                },
            )
            assert [p["payment_channel"] for p in proofs] == [gcash], proofs
            row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
            assert row["payment_channel"] == bdo, row  # unchanged until staff decide

            # --- the desk: "Received via" follows the receipt, staff accept --
            load_board(page, BRANCH, D_CH)
            slot(page, COURT_1, "10:00:00").click()
            wait_dialog(page)
            dialog = page.locator(gestures.DIALOG)
            expect(dialog.locator("[data-testid='detail-channel']")).to_contain_text("via BDO")
            expect(dialog.locator("[data-testid='proof-channel']")).to_contain_text("via GCash")
            received = dialog.locator(".frappe-control[data-fieldname='payment_channel'] select")
            expect(received).to_have_value(gcash)
            assert channel_options(page) == ["GCash", "BDO"], channel_options(page)
            with page.expect_response(
                lambda r: "accept_proofs" in r.url, timeout=30000
            ) as resp_info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert resp_info.value.ok, f"accept_proofs: HTTP {resp_info.value.status} {resp_info.value.text()}"
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)

            row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
            assert row["booking_status"] == "Confirmed", row
            assert row["payment_channel"] == gcash, row
            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert invoice["status"] == "Paid & Verified", invoice
            assert invoice["payment_channel"] == gcash, invoice

            # --- the split the user asked for: today's GCash row carries it --
            today = page.evaluate("() => frappe.datetime.now_date()")
            report = page.request.get(
                "/api/method/frappe.desk.query_report.run",
                params={
                    "report_name": "CBT Collections by Channel",
                    "filters": json.dumps({"company": TENANT, "from_date": today, "to_date": today}),
                },
            )
            assert report.ok, f"report run: HTTP {report.status} {report.text()}"
            rows = [r for r in report.json()["message"]["result"] if isinstance(r, dict)]
            gcash_rows = [r for r in rows if r.get("payment_channel") == gcash]
            assert gcash_rows and gcash_rows[0]["paid_count"] >= 1, rows
            assert gcash_rows[0]["collected"] >= float(row["total_amount"]), gcash_rows
        finally:
            cancel_active_bookings(page, COURT_1, D_CH)

    def test_desk_quick_book_offers_the_channels_of_the_method(self, page: Page):
        cancel_active_bookings(page, COURT_2, D_CH)
        try:
            channels = _transfer_channels(page)
            bdo = channels["BDO"]

            load_board(page, BRANCH, D_CH)
            # B46: select on the board, then open ONE dialog for the set.
            board.select_slots(page, COURT_2, "10:00:00", 1)
            board.open_cart_dialog(page)
            gestures.fill(page, "customer", board.CUSTOMER, scope=gestures.DIALOG)
            # The dialog opens on Cash, so the FIRST assertion is the initial
            # list; the coupling "method → list" is proved by the two changes
            # that follow, in BOTH directions (a fill to the value the field
            # already holds fires no change — ducky finding 8).
            wait_channels_settled(page)
            assert channel_options(page) == ["Cash"], channel_options(page)

            gestures.fill(page, "payment_method", "Fund Transfer", scope=gestures.DIALOG)
            wait_channels_settled(page)
            assert channel_options(page) == ["GCash", "BDO"], channel_options(page)
            gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
            wait_channels_settled(page)
            assert channel_options(page) == ["Cash"], channel_options(page)
            gestures.fill(page, "payment_method", "Fund Transfer", scope=gestures.DIALOG)
            wait_channels_settled(page)
            assert channel_options(page) == ["GCash", "BDO"], channel_options(page)
            gestures.fill(page, "payment_channel", bdo, scope=gestures.DIALOG)

            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)
            booking = _submit_quick_book(page)
            row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
            assert row["booking_status"] == "Reserved", row
            assert row["payment_channel"] == bdo, row
            invoice = _get_json(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert invoice["payment_channel"] == bdo, invoice

            # Free means no channel — the picker empties and the server stores none.
            board.select_slots(page, COURT_2, "12:00:00", 1)
            board.open_cart_dialog(page)
            gestures.fill(page, "customer", board.CUSTOMER, scope=gestures.DIALOG)
            gestures.fill(page, "payment_method", "Free", scope=gestures.DIALOG)
            wait_channels_settled(page)
            assert channel_options(page) == [], channel_options(page)
            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)
            free = _submit_quick_book(page)
            free_row = _get_json(page, f"/api/resource/CBT Court Booking/{free}")
            assert free_row["booking_status"] == "Confirmed", free_row
            assert "payment_channel" not in free_row or not free_row["payment_channel"], free_row
        finally:
            cancel_active_bookings(page, COURT_2, D_CH)

    def test_the_booking_page_qr_opens_the_same_lightbox(self, page: Page, customer_page: Page):
        """The page a customer actually scans from. A GCash hold (the default
        channel — the one with a QR) reaches /my-bookings/<name>; the tile there
        and on the confirmation are the same control as the checkout's."""
        cancel_active_bookings(page, COURT_1, D_CH)
        try:
            gcash = _transfer_channels(page)["GCash"]
            customer_page.goto(
                f"/book?c={TENANT}&b=bgc&d={D_CH}", wait_until="domcontentloaded", timeout=60000
            )
            customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            customer_page.locator(
                f".cbt-slot[data-court='{COURT_1}'][data-start='14:00:00']"
            ).click()
            customer_page.locator("#cbt-review").click()
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(
                gcash, timeout=15000
            )
            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            match = re.search(r"BK-AYALA-\d{4}-\d{5}", ref.inner_text())
            assert match, ref.inner_text()
            booking = match.group(0)
            expect(customer_page.locator("#cbt-confirmed-channel .cbt-channel-qr")).to_be_visible()

            portal.open_detail(customer_page, booking)
            tile = customer_page.locator("#cbt-pay-channel img.cbt-channel-qr")
            expect(tile).to_be_visible(timeout=15000)
            # B50: the hint words are part of the control here too (no <label>
            # around this card — the handler must not need one).
            customer_page.locator("#cbt-pay-channel .cbt-channel-qr-hint").click()
            lightbox = customer_page.locator("#cbt-qr-modal")
            expect(lightbox).to_be_visible()
            expect(lightbox.locator("#cbt-qr-title")).to_have_text("GCash")
            expect(lightbox.locator("#cbt-qr-meta")).to_have_text(
                customer_page.locator("#cbt-pay-channel .cbt-channel-meta").inner_text()
            )
            assert lightbox.locator("#cbt-qr-image").get_attribute("src") == tile.get_attribute("src")
            customer_page.keyboard.press("Escape")
            expect(lightbox).to_be_hidden()
        finally:
            cancel_active_bookings(page, COURT_1, D_CH)
