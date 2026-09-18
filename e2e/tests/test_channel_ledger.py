"""
E2E — The channel story, end to end, by hand (Backlog B29, section-25).

The user's bar: *"playwright = human clickable, human fillup, human readable"*.
`test_payment_channels.py` proved the customer-pick / receipt-claim / staff-
confirm path; this file closes what that file left to the backend and the
walkthrough, on a VAT tenant billed Per Booking — the doc's own numbers
(₱100 court, ₱15 fee, 12% VAT):

1. the PLATFORM sets a tenant's channels up through the FORM — a new row typed
   in, seen on the customer's checkout, disabled, gone from the checkout, its
   Kind refused when changed;
2. the money: Per Booking + a tier typed into the company form; the customer's
   checkout reading ₱89.29 / ₱10.71 / fee ₱15 / ₱115 with the fee OUTSIDE the
   VAT lines; the receipt verified as GCash; a cash booking taken with "Paid
   via: Cash" and then CANCELLED from the board; **CBT Collections by Channel**
   and **CBT Tenant Ledger** opened from the Hub, filtered by hand, and READ
   OFF THE RENDERED TABLE — the payment, the refund, the four-line voucher and
   its reversal; **CBT Platform Revenue** showing the tenant's Per Booking row;
3. open play: **Paid via** in Add Players and **Received via** in Mark Paid;
4. the month: a paid Per Booking booking dated LAST MONTH (back-recorded by
   API — staff may, MR-10 relies on it — so the month is genuinely over and
   no clock lever is needed; `testing.set_test_clock_offset`'s own contract
   forbids pinning another date), then by hand: **Close this month** on
   Platform Revenue, **Issue statements** on the close, the tenant's
   statement opened from the Hub, **Mark paid** with the platform's GCash
   chosen, Ctrl+P and the print's *"Booking fees collected … N paid
   booking(s)"* and *via GCash*, and **Tenant Ledger** read for BOTH books —
   the platform's AR / Booking Fee Revenue / GCash rows with the tenant as
   party, and the tenant's Due to Platform / Cash/Bank rows.

TENANT. A dedicated VAT company, `chan-vat` (code CHVAT), created by API on
first run and kept — like `e2e-fast`, a persistent rig, because switching a
seeded tenant to Per Booking would move every peso-pinned assertion in the
suite. Its billing mode is reset to Subscription before each run and set to
Per Booking BY GESTURE inside the row. Its bookings are cancelled in `finally`.

Date ledger: THIS FILE CLAIMS **+80** (the tenant's one court) and **+81**
(AYALA-bgc-court-3, open play). +79 was the last claimed (test_payment_channels).
Row 4 books the rig court on the **15th of LAST MONTH** — a past date no file
claims (the ledger runs today+30 upward) and a court no other file touches.
Row 4 closes and re-opens LAST MONTH like test_month_close / test_platform_statement
do; `--dist loadfile` and site-per-worker keep the three from meeting.
The reports are read for TODAY — payment day, which is when the money moved.
No sleeps — every wait is on a rendered node, a dialog flag or a response.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright, expect

from helpers import gestures, portal, reports
from helpers.auth import PLATFORM_ADMIN, login_as
from helpers.board import (
    CUSTOMER,
    _submit_quick_book,
    platform_api,
    cancel_active_bookings,
    channel_options,
    csrf,
    load_board,
    open_cart_dialog,
    select_slots,
    slot,
    wait_channels_settled,
    wait_dialog,
    wait_money_settled,
)
from helpers.desk_form import wait_for_new_form
from helpers.worker_routing import bench_json
from helpers.navigation import goto_form, goto_workspace, wait_for_page_load
from helpers.open_play import create_open_session, purge_sessions, reload_board, show_session

PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

SCREENSHOTS = Path(__file__).resolve().parent.parent / "screenshots"

TENANT = "chan-vat"
TENANT_CODE = "CHVAT"
TENANT_TITLE = "Channel VAT Courts"
BRANCH = f"{TENANT_CODE}-main"
COURT_RATE = 100.0
FEE = 15.0

D_MONEY = (date.today() + timedelta(days=80)).isoformat()
D_OPEN_PLAY = (date.today() + timedelta(days=81)).isoformat()
OP_COURT = "AYALA-bgc-court-3"
OP_PLAYER = "op.ace@example.com"

FILTER_BAR = ".page-form"
ROWS = ".datatable .dt-row[data-row-index]"

CLOSE_DOCTYPE = "CBT Platform Month Close"
STATEMENT_DOCTYPE = "CBT Platform Statement"
STATEMENT_FORMAT = "CBT Platform Statement"
PAYMENT_REF = "E2E-CHVAT-GCASH-0450"
# Every row logs in as the seeded PLATFORM seat (CBT Platform Admin and nothing
# else — Batch 17): the platform's acts here — the channel form, the billing
# lever, Close / Issue / Mark paid — are the role's, not the Administrator
# context's, which bypasses DocPerms. Staff acts on the board pass the tenancy
# gate on platform scope. Month cleanup runs through bench execute (B43: no
# role holds delete on CBT Platform Statement, by design).
OWN_SEAT = PLATFORM_ADMIN
REFUND_REASON = "E2E: customer double-booked, cash handed back"


# The table reader and the money parsers moved to helpers/reports.py
# (section-26) so the statement and refund lanes read the same table the
# same way; the local names stay so every row reads as it did.
_money = reports.money
_num = reports.num
_table = reports.table
_wait_table_text = reports.wait_table_text
_wait_table_settled = reports.wait_table_settled
_visible_rows = reports.visible_rows


def _open_report_from_hub(page: Page, report: str):
    reports.open_report_from_hub(page, report)


def _set_company_and_day(page: Page, day: str):
    reports.set_company_and_day(page, TENANT, TENANT_TITLE, day)


def _ledger_rows(page: Page, day: str, books: str, needle: str) -> list:
    return reports.ledger_rows(page, TENANT, TENANT_TITLE, day, books, needle)


def _headers(page: Page) -> dict:
    """Signs the writes below; a desk page must be loaded (csrf() handles it)."""
    return {"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"}


def _get(page: Page, url: str, params: dict | None = None):
    resp = page.request.get(url, params=params)
    assert resp.ok, f"GET {url}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _post(page: Page, doctype: str, doc: dict) -> dict:
    resp = page.request.post(
        f"/api/resource/{doctype}", headers=_headers(page), data=json.dumps(doc)
    )
    assert resp.ok, f"POST {doctype}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _put(page: Page, doctype: str, name: str, values: dict):
    resp = page.request.put(
        f"/api/resource/{doctype}/{name}", headers=_headers(page), data=json.dumps(values)
    )
    assert resp.ok, f"PUT {doctype}/{name}: HTTP {resp.status} {resp.text()}"


def _exists(page: Page, doctype: str, name: str) -> bool:
    return page.request.get(f"/api/resource/{doctype}/{name}").status == 200


def _channels(page: Page, company: str, kind: str | None = None) -> dict:
    filters = [["company", "=", company]]
    if kind:
        filters.append(["kind", "=", kind])
    rows = _get(
        page,
        "/api/resource/CBT Payment Channel",
        params={
            "filters": json.dumps(filters),
            "fields": json.dumps(["name", "label", "enabled"]),
            "order_by": "sort_order asc",
        },
    )
    return {row["label"]: row for row in rows}


def _ensure_tenant(page: Page) -> str:
    """The persistent VAT rig: company + branch + one ₱100 court, Cash + GCash
    (created with the company) + a BDO bank; Subscription before every run so
    the row can switch it to Per Booking by hand. Returns the court name."""
    if not _exists(page, "CBT Company", TENANT):
        _post(
            page,
            "CBT Company",
            {
                "slug": TENANT,
                "company_code": TENANT_CODE,
                "company_name": TENANT_TITLE,
                "registered_name": "Channel VAT Courts Inc.",
                "tin": "111-222-333-000",
                "vat_registration": "VAT",
                "allow_self_branch_management": 0,
                "status": "Active",
                "advance_booking_days": 90,
                "payment_instructions": "Keep your reference number.",
                "office_hours": [
                    {"day": d, "is_open": 1, "opening_time": "00:00:00", "closing_time": "23:59:00"}
                    for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
                ],
            },
        )
    if not _exists(page, "CBT Branch", BRANCH):
        _post(
            page,
            "CBT Branch",
            {
                "company": TENANT,
                "slug": "main",
                "branch_name": "Main",
                "address_text": "1 Ledger St, Pasig",
                "is_active": 1,
            },
        )
    courts = _get(
        page,
        "/api/resource/CBT Court",
        params={"filters": json.dumps([["branch", "=", BRANCH]]), "fields": json.dumps(["name"])},
    )
    if not courts:
        _post(
            page,
            "CBT Court",
            {"branch": BRANCH, "court_name": "Court 1", "court_type": "Pickleball", "hourly_rate": COURT_RATE, "is_active": 1},
        )
        courts = _get(
            page,
            "/api/resource/CBT Court",
            params={"filters": json.dumps([["branch", "=", BRANCH]]), "fields": json.dumps(["name"])},
        )
    court = courts[0]["name"]
    channels = _channels(page, TENANT)
    assert {"Cash", "GCash"} <= set(channels), channels  # created with the company
    if "BDO" not in channels:
        _post(
            page,
            "CBT Payment Channel",
            {"scope": "Tenant", "company": TENANT, "label": "BDO", "kind": "Transfer", "account_number": "0012-9999-0001", "sort_order": 3},
        )
    for label, row in channels.items():
        if not row["enabled"]:
            _put(page, "CBT Payment Channel", row["name"], {"enabled": 1})
    _reset_billing(page)
    return court


def _reset_billing(page: Page):
    _put(page, "CBT Company", TENANT, {"billing_mode": "Subscription", "subscription_fee": 100, "booking_fee_tiers": []})


def _delete_channel_if_present(page: Page, label: str):
    row = _channels(page, TENANT).get(label)
    if not row:
        return
    resp = page.request.delete(f"/api/resource/CBT Payment Channel/{row['name']}", headers=_headers(page))
    assert resp.ok, f"delete {label}: HTTP {resp.status} {resp.text()}"


def _open_checkout(customer_page: Page, court: str, start: str):
    customer_page.goto(f"/book?c={TENANT}&b=main&d={D_MONEY}", wait_until="domcontentloaded", timeout=60000)
    customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
    customer_page.locator(f".cbt-slot[data-court='{court}'][data-start='{start}']").click()
    customer_page.locator("#cbt-review").click()
    expect(customer_page.locator("#cbt-quote-lines")).to_be_visible(timeout=15000)


def _card_labels(customer_page: Page) -> list:
    return customer_page.locator("#cbt-channels .cbt-channel-label").all_inner_texts()


def _site_today(page: Page) -> str:
    return page.evaluate("() => frappe.datetime.now_date()")


def _collections_today(page: Page, today: str) -> dict:
    """Collections by Channel for TODAY, opened from the Hub and read off the
    table — {channel label: row}. Read BEFORE and AFTER the money moves: the
    rig tenant keeps every earlier run's payments on the same day (a payment
    paid today stays a payment today even after its refund), so the assertions
    are deltas, exactly as a person reconciling two prints would read them."""
    _open_report_from_hub(page, "CBT Collections by Channel")
    _set_company_and_day(page, today)
    # The table may legitimately be EMPTY on a fresh rig — wait for the
    # report to finish running, not for a row. Empty renders as the words
    # "Nothing to show" (frappe hides the table rather than emptying it);
    # `.no-result` is not that element — the first fresh-snapshot gate run of
    # this file (2026-08-27) timed out on it.
    page.wait_for_function(
        "() => window.frappe && frappe.query_report && frappe.query_report.datatable !== undefined",
        timeout=30000,
    )
    expect(page.locator(".datatable").or_(page.get_by_text("Nothing to show")).first).to_be_visible(timeout=30000)
    # The report is ONE ROW PER CHANNEL PER DAY, and the tile's first render
    # runs the DEFAULT range — so a table that is already on the page can be
    # the un-filtered one while the typed day is still re-running. Read only
    # once the report's own data model says every row is TODAY's (or there is
    # none): on 2026-08-28 the first Batch-17 run keyed yesterday's GCash row
    # as "before" and today's as "after" and reported a delta of 0. NOT gated
    # on `qr.datatable`: an EMPTY result (the rig on a fresh snapshot) renders
    # "Nothing to show" and leaves it null — the full-suite gate the same day
    # timed out on exactly that.
    page.wait_for_function(
        """(day) => {
            const qr = window.frappe && frappe.query_report;
            if (!qr || typeof qr.get_filter_value !== 'function') return false;
            if (qr.get_filter_value('from_date') !== day || qr.get_filter_value('to_date') !== day) return false;
            return (qr.data || []).every((r) => !r || !r.day || String(r.day).slice(0, 10) === day);
        }""",
        arg=today,
        timeout=30000,
    )
    return {r.get("Channel Label"): r for r in _table(page) if r.get("Channel Label") in ("GCash", "Cash", "BDO")}


def _last_month(page: Page) -> tuple:
    """Last month by the SITE's calendar (the close refuses on the server clock)."""
    today = date.fromisoformat(_site_today(page))
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return year, month


def _statements(page: Page, period: str) -> list:
    return _get(
        page,
        f"/api/resource/{STATEMENT_DOCTYPE}",
        params={
            "filters": json.dumps([["period", "=", period]]),
            "fields": json.dumps(["name", "company", "status", "amount_due", "issued_by", "booking_count", "booking_fees"]),
            "limit_page_length": 0,
        },
    )


def _cleanup_month(period: str):
    """What THIS suite made for the month and nothing else.

    Backlog B43: through `bench execute`, not an HTTP context — the SECOND file
    to need it, and the one the first pass missed. Its previous docstring said
    the quiet part already: *"a statement is cancelled, never deleted, so the
    platform role holds no delete on it by design"*. It only ever worked because
    the context was Administrator, and it 403'd the moment that stopped.

    The safety is unchanged and now lives beside the deletion, in
    `court_booking_tech.testing.purge_platform_statements`: a row issued or
    closed by anyone but this suite's seat is somebody's real work on a shared
    bench and the helper refuses rather than deleting it.
    """
    return bench_json(
        "court_booking_tech.testing.purge_platform_statements", [period, OWN_SEAT]
    )


def _open_from_hub(page: Page, doctype: str, row_text: str, slug: str):
    """Hub tile → list → the row whose title is `row_text` → its form."""
    goto_workspace(page, "CBT Hub")
    tile = page.locator(".shortcut-widget-box", has_text=doctype)
    expect(tile).to_be_visible(timeout=15000)
    tile.click()
    page.wait_for_url(f"**/desk/{slug}**", timeout=30000)
    wait_for_page_load(page)
    row = page.locator(".frappe-list .list-row-container", has_text=row_text).first
    expect(row).to_be_visible(timeout=20000)
    row.locator("a").first.click()
    page.wait_for_function(
        "(name) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === name && !cur_frm.doc.__islocal",
        arg=row_text,
        timeout=30000,
    )


def _indicator(page: Page):
    return page.locator(".page-head .indicator-pill:visible")


def _dismiss(page: Page, modal):
    for _attempt in range(3):
        modal.locator(".modal-header .btn-modal-close").first.click()
        try:
            expect(modal).to_have_count(0, timeout=3000)
            return
        except AssertionError:
            continue
    expect(modal).to_have_count(0, timeout=5000)


@pytest.fixture(scope="module")
def op_session(playwright: Playwright, platform_auth):
    api = platform_api(playwright)
    try:
        purge_sessions(api, D_OPEN_PLAY)
        yield create_open_session(api, D_OPEN_PLAY, courts=[OP_COURT])
    finally:
        api.dispose()


@pytest.mark.e2e
class TestChannelLedger:

    def test_platform_sets_up_a_tenants_channels_through_the_form(
        self, page: Page, customer_page: Page, platform_seat: str
    ):
        login_as(page, platform_seat)
        court = _ensure_tenant(page)
        _delete_channel_if_present(page, "Maya")
        try:
            # The tile, then the list a human would scan.
            goto_workspace(page, "CBT Hub")
            tile = page.locator(".shortcut-widget-box", has_text="CBT Payment Channel")
            expect(tile).to_be_visible(timeout=15000)
            tile.click()
            page.wait_for_url("**/desk/cbt-payment-channel**", timeout=30000)
            wait_for_page_load(page)
            expect(page).to_have_title(re.compile("CBT Payment Channel"), timeout=15000)

            # A new row, typed in.
            page.goto("/desk/cbt-payment-channel/new", wait_until="domcontentloaded", timeout=60000)
            wait_for_page_load(page)
            wait_for_new_form(page, "CBT Payment Channel")
            gestures.fill(page, "company", TENANT, label=TENANT_TITLE)
            gestures.fill(page, "label", "Maya")
            gestures.fill(page, "kind", "Transfer")
            gestures.fill(page, "account_name", "Channel VAT Courts Inc.")
            gestures.fill(page, "account_number", "0918-000-4444")
            name = gestures.save_form(page)
            assert name.startswith(f"PCH-{TENANT_CODE}-MAYA-"), name
            row = _get(page, f"/api/resource/CBT Payment Channel/{name}")
            assert row["mode_of_payment"] == "Maya" and row["account_label"] == "Maya", row
            assert row["enabled"] == 1, row

            # The customer sees it at checkout, with its number.
            _open_checkout(customer_page, court, "10:00:00")
            assert _card_labels(customer_page) == ["GCash", "BDO", "Maya"], _card_labels(customer_page)
            expect(customer_page.locator(f"#cbt-channels .cbt-channel[data-channel='{name}']")).to_contain_text("0918-000-4444")

            # Disabled by hand → gone from the checkout, kept on record.
            goto_form(page, "CBT Payment Channel", name)
            gestures.check(page, "enabled", False)
            gestures.save_form(page)
            assert _get(page, f"/api/resource/CBT Payment Channel/{name}")["enabled"] == 0
            _open_checkout(customer_page, court, "10:00:00")
            assert _card_labels(customer_page) == ["GCash", "BDO"], _card_labels(customer_page)

            # Kind is fixed once created — the form says so and stores nothing.
            # Waited for by its SENTENCE, not a fixed settle (the B24 lesson):
            # the refusal is the server's answer and arrives when it arrives.
            goto_form(page, "CBT Payment Channel", name)
            gestures.fill(page, "kind", "Cash")
            page.keyboard.press("Control+s")
            refusal = page.locator(".modal.show").filter(has_text="fixed once a channel is created")
            expect(refusal).to_be_visible(timeout=20000)
            assert _get(page, f"/api/resource/CBT Payment Channel/{name}")["kind"] == "Transfer"
            refusal.locator(".modal-header .btn-modal-close").first.click()
            goto_workspace(page, "CBT Hub")  # leave the dirty form behind before the cleanup delete
        finally:
            _delete_channel_if_present(page, "Maya")

    def test_vat_per_booking_money_from_the_checkout_to_the_ledger(
        self, page: Page, customer_page: Page, platform_seat: str
    ):
        login_as(page, platform_seat)
        court = _ensure_tenant(page)
        cancel_active_bookings(page, court, D_MONEY)
        try:
            # --- Per Booking + one tier, typed into the company form --------
            goto_form(page, "CBT Company", TENANT)
            gestures.fill(page, "billing_mode", "Per Booking")
            idx = gestures.grid_add_row(page, "booking_fee_tiers")
            gestures.grid_fill(page, "booking_fee_tiers", idx, {"from_count": 1, "fee": FEE})
            gestures.save_form(page)
            company = _get(page, f"/api/resource/CBT Company/{TENANT}")
            assert company["billing_mode"] == "Per Booking", company
            assert [(r["from_count"], float(r["fee"])) for r in company["booking_fee_tiers"]] == [(1, FEE)], company
            channels = _channels(page, TENANT)
            gcash, cash = channels["GCash"]["name"], channels["Cash"]["name"]
            today = _site_today(page)
            before = _collections_today(page, today)

            # --- the customer's checkout: VAT on the court, fee outside it ----
            _open_checkout(customer_page, court, "10:00:00")
            lines = customer_page.locator("#cbt-quote-lines").inner_text()
            assert "89.29" in lines and "10.71" in lines and "115.00" in lines, lines
            fee_row = customer_page.locator("#cbt-quote-lines [data-testid='booking-fee']").locator("xpath=ancestor::tr[1]").inner_text()
            assert _money(fee_row) == [FEE], fee_row
            assert lines.index("VAT") < lines.index("Booking fee"), lines  # outside the VAT block
            expect(customer_page.locator("#cbt-channels input:checked")).to_have_value(gcash)
            customer_page.locator("#cbt-reserve").click()
            ref = customer_page.locator("#cbt-confirmed-ref")
            expect(ref).to_be_visible(timeout=20000)
            booking = re.search(rf"BK-{TENANT_CODE}-\d{{4}}-\d{{5}}", ref.inner_text()).group(0)
            portal.open_detail(customer_page, booking)
            # The booking page repeats the same order: VAT lines, then the fee.
            money_text = customer_page.locator("#cbt-money").inner_text()
            assert money_text.index("VAT") < money_text.index("Booking fee"), money_text
            customer_page.set_input_files("#cbt-file", str(PROOF_JPG))
            customer_page.click("#cbt-upload")
            expect(customer_page.locator("#cbt-deadline-note")).to_be_visible(timeout=20000)

            # --- the desk verifies it as GCash --------------------------------
            load_board(page, BRANCH, D_MONEY)
            slot(page, court, "10:00:00").click()
            wait_dialog(page)
            received = page.locator(f"{gestures.DIALOG} .frappe-control[data-fieldname='payment_channel'] select")
            expect(received).to_have_value(gcash)
            with page.expect_response(lambda r: "accept_proofs" in r.url, timeout=30000) as resp_info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert resp_info.value.ok, resp_info.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)
            row = _get(page, f"/api/resource/CBT Court Booking/{booking}")
            assert row["booking_status"] == "Confirmed" and row["payment_channel"] == gcash, row
            invoice = _get(page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}")
            assert (float(invoice["vatable_amount"]), float(invoice["vat_amount"]), float(invoice["platform_fee"]), float(invoice["total_amount"])) == (89.29, 10.71, FEE, 115.0), invoice

            # --- a cash booking at the desk, "Paid via: Cash", then cancelled --
            # B46: select on the board, then open ONE dialog for the set.
            select_slots(page, court, "11:00:00", 1)
            open_cart_dialog(page)
            gestures.fill(page, "customer", CUSTOMER, scope=gestures.DIALOG)
            gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
            wait_channels_settled(page)
            assert channel_options(page) == ["Cash"], channel_options(page)
            gestures.fill(page, "payment_channel", cash, scope=gestures.DIALOG)
            page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
            wait_money_settled(page)
            cash_booking = _submit_quick_book(page)
            cash_row = _get(page, f"/api/resource/CBT Court Booking/{cash_booking}")
            assert cash_row["booking_status"] == "Confirmed" and cash_row["payment_channel"] == cash, cash_row

            # Wait for the post-book reload to flip the cell: clicking a stale
            # "available" DOM would now put the hour in the CART (B46) instead
            # of opening the booking behind it.
            page.wait_for_selector(
                f".cbt-slot[data-court='{court}'][data-start='11:00:00']"
                "[data-booking-status='Confirmed']",
                timeout=20000,
            )
            slot(page, court, "11:00:00").click()
            wait_dialog(page)
            expect(page.locator(f"{gestures.DIALOG} [data-testid='detail-channel']")).to_contain_text("via Cash")
            # Section-26: a PAID booking's cancel is a refund — the board's
            # button says so, and the prompt asks WHY. Wait for THAT modal by
            # its sentence — a positional `.modal.show` last would be the
            # details dialog itself while the prompt is still mounting, and its
            # primary button is Extend Session.
            cancel_button = page.locator(f"{gestures.DIALOG} [data-action='cancel-booking']")
            expect(cancel_button).to_have_text(re.compile("Cancel & Refund"))
            cancel_button.click()
            prompt = page.locator(".modal.show").filter(has_text="Cancelling it is a refund")
            expect(prompt).to_be_visible(timeout=15000)
            gestures.fill(page, "reason", REFUND_REASON, scope=".modal.show:has-text('Cancelling it is a refund')")
            with page.expect_response(lambda r: "cancel_booking" in r.url, timeout=30000) as resp_info:
                prompt.locator(".btn-primary, .btn-modal-primary").first.click()
            assert resp_info.value.ok, resp_info.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)
            cash_invoice = _get(page, f"/api/resource/CBT Booking Invoice/{cash_row['billing_doc']}")
            assert cash_invoice["status"] == "Cancelled" and cash_invoice.get("cancelled_at"), cash_invoice
            assert cash_invoice["refund_reason"] == REFUND_REASON and cash_invoice["refunded_by"] == OWN_SEAT, cash_invoice

            # --- Collections by Channel, from the Hub, read off the table ----
            after = _collections_today(page, today)
            assert {"GCash", "Cash"} <= set(after), after
            g, c = after["GCash"], after["Cash"]
            g0, c0 = before.get("GCash"), before.get("Cash")
            assert g["Ledger Account"] == "Cash in E-Wallet - GCash" and c["Ledger Account"] == "Cash on Hand", after
            for column, delta in (("Payments", 1), ("Collected", 115.0), ("Court Revenue", COURT_RATE), ("Booking Fees Held", FEE), ("Output VAT", 10.71), ("Refunds", 0)):
                assert round(_num(g, column) - _num(g0, column), 2) == delta, (column, g0, g)
            for column, delta in (("Payments", 1), ("Collected", 115.0), ("Refunds", 1), ("Refunded", 115.0)):
                assert round(_num(c, column) - _num(c0, column), 2) == delta, (column, c0, c)

            # --- Tenant Ledger, from the Hub: the voucher and its reversal ----
            _open_report_from_hub(page, "CBT Tenant Ledger")
            _set_company_and_day(page, today)
            _wait_table_text(page, "Output VAT Payable")
            rows = _table(page)
            gcash_voucher = [r for r in rows if r.get("Document") == invoice["name"]]
            # A blank side renders as ₱0.00 — read the cells as numbers.
            assert [(r["Account"], _num(r, "Debit"), _num(r, "Credit")) for r in gcash_voucher] == [
                ("Cash in E-Wallet - GCash", 115.0, 0.0),
                ("Service Revenue", 0.0, 89.29),
                ("Output VAT Payable", 0.0, 10.71),
                ("Due to Platform", 0.0, FEE),
            ], gcash_voucher
            cash_lines = [r for r in rows if r.get("Document") == cash_invoice["name"]]
            assert len(cash_lines) == 8, cash_lines  # the payment and its reversal
            assert any(r["Account"] == "Cash on Hand" and _num(r, "Credit") == 115.0 for r in cash_lines), cash_lines
            # Section-26: the reversal line says WHY, in the words the admin typed.
            reversal = next(r for r in cash_lines if "Refund / reversal" in (r.get("Description") or ""))
            assert REFUND_REASON in reversal["Description"], reversal
            total = rows[-1]
            if total.get("Description") != "Total":
                # Instrumentation, not a retry: what the reader saw vs what is there.
                SCREENSHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SCREENSHOTS / "ledger_total_miss.png"), full_page=True)
                diag = page.evaluate(
                    """() => { const b = document.querySelector('.datatable .dt-scrollable');
                        const rows = Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]'));
                        return {scrollTop: b && b.scrollTop, scrollHeight: b && b.scrollHeight, clientHeight: b && b.clientHeight,
                                rendered: rows.length, first: rows[0] && rows[0].dataset.rowIndex, last: rows.slice(-1)[0] && rows.slice(-1)[0].dataset.rowIndex,
                                datatables: document.querySelectorAll('.datatable').length,
                                viewport: [window.innerWidth, window.innerHeight]}; }"""
                )
                tail = [(i, r.get("Document"), r.get("Description"), r.get("Account")) for i, r in enumerate(rows)][-6:]
                totals = [(i, r) for i, r in enumerate(rows) if r.get("Description") == "Total"]
                model = page.evaluate(
                    """() => { const d = (frappe.query_report && frappe.query_report.data) || [];
                        const t = d.findIndex((r) => r && r.is_total_row);
                        const b = document.querySelector('.datatable .dt-scrollable'); if (b) b.scrollTop = 100000;
                        return {data_len: d.length, total_at: t, after_total: d.slice(t + 1, t + 3).map((r) => r.voucher),
                                last_data: d.slice(-2).map((r) => [r.voucher, r.account, r.description])}; }"""
                )
                page.wait_for_timeout(800)
                dom_tail = page.evaluate(
                    """() => Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]')).slice(-4)
                        .map((r) => [r.dataset.rowIndex, (r.innerText || '').replace(/\\n/g, ' | ').slice(0, 90)])"""
                )
                raise AssertionError(
                    f"Total row not last: collected {len(rows)} rows; tail={tail}; totals={totals}; DOM {diag}; "
                    f"MODEL {model}; DOM-tail-after-scroll {dom_tail}"
                )
            assert _num(total, "Debit") == _num(total, "Credit"), total
            # The platform seat flips the same report to the PLATFORM's books —
            # this rig tenant has no statement yet, so the answer is the empty
            # state a person reads ("Nothing to show"); frappe hides the old
            # table rather than emptying it, so the old cells are not the tell.
            gestures.select(page, "books", "Platform", scope=FILTER_BAR)
            expect(page.get_by_text("Nothing to show").first).to_be_visible(timeout=30000)

            # --- Platform Revenue: the tenant's Per Booking row --------------
            d = date.fromisoformat(D_MONEY)
            page.goto("/desk/query-report/CBT Platform Revenue", wait_until="domcontentloaded", timeout=60000)
            wait_for_page_load(page)
            expect(page.locator(f"{FILTER_BAR} .frappe-control[data-fieldname='month'] select")).to_be_visible(timeout=20000)
            gestures.select(page, "month", str(d.month), scope=FILTER_BAR)
            gestures.type_value(page, "year", d.year, scope=FILTER_BAR)
            # Every tenant has a row in EVERY month, so the tenant's name is on
            # screen before the report re-runs — wait for OUR row to carry the
            # month's figure (the ₱15 fee), i.e. for the re-render itself.
            page.wait_for_function(
                """([title, fee]) => Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]'))
                    .some((row) => (row.innerText || '').includes(title) && (row.innerText || '').includes(fee))""",
                arg=[TENANT_TITLE, f"{FEE:,.2f}"],
                timeout=30000,
            )
            ours = next(r for r in _table(page) if r.get("Name") == TENANT_TITLE or r.get("Company") == TENANT)
            assert ours["Billing Mode"] == "Per Booking", ours
            assert ours["Fee Units"] == "1" and _money(ours["Booking Fees"]) == [FEE], ours
            assert _money(ours["Confirmed Revenue"]) == [COURT_RATE] and _money(ours["Amount Due"]) == [FEE], ours
        finally:
            cancel_active_bookings(page, court, D_MONEY)
            _reset_billing(page)

    def test_last_month_is_closed_billed_paid_and_both_books_read_by_hand(
        self, page: Page, playwright: Playwright, platform_seat: str
    ):
        """Steps 24–27 and 29–32 of the story, on the Per Booking shape."""
        api = platform_api(playwright)  # month cleanup only
        login_as(page, platform_seat)
        court = _ensure_tenant(page)
        goto_workspace(page, "CBT Hub")  # site calendar + CSRF live in a loaded desk
        year, month = _last_month(page)
        period = f"{year:04d}-{month:02d}"
        service_day = date(year, month, 15).isoformat()
        month_label = date(year, month, 1).strftime("%B %Y")
        platform_gcash = next(
            r["name"]
            for r in _get(
                page,
                "/api/resource/CBT Payment Channel",
                params={"filters": json.dumps([["scope", "=", "Platform"], ["label", "=", "GCash"]]), "fields": json.dumps(["name"])},
            )
        )
        try:
            _cleanup_month(period)  # a previous aborted run must not skew this
            cancel_active_bookings(page, court, service_day)
            # ARRANGE by API: Per Booking, and one paid cash booking back-recorded
            # on the 15th of last month (a staff seat may book a past slot).
            _put(page, "CBT Company", TENANT, {"billing_mode": "Per Booking", "booking_fee_tiers": [{"from_count": 1, "to_count": 0, "fee": FEE}]})
            booked = page.request.post(
                "/api/method/court_booking_tech.api.bookings.create_booking",
                headers=_headers(page),
                data=json.dumps({"court": court, "booking_date": service_day, "start_time": "10:00:00", "payment_method": "Cash", "customer": CUSTOMER, "number_of_slots": 1}),
            )
            assert booked.ok, f"create_booking: HTTP {booked.status} {booked.text()}"
            booking = _get(page, f"/api/resource/CBT Court Booking/{booked.json()['message']['name']}")
            assert booking["booking_status"] == "Confirmed" and float(booking["platform_fee"]) == FEE, booking

            # --- Platform Revenue → last month → Close this month ------------
            page.goto("/desk/query-report/CBT Platform Revenue", wait_until="domcontentloaded", timeout=60000)
            wait_for_page_load(page)
            expect(page.locator(f"{FILTER_BAR} .frappe-control[data-fieldname='month'] select")).to_be_visible(timeout=20000)
            gestures.select(page, "month", str(month), scope=FILTER_BAR)
            gestures.type_value(page, "year", year, scope=FILTER_BAR)
            page.wait_for_function(
                """([title, fee]) => Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]'))
                    .some((row) => (row.innerText || '').includes(title) && (row.innerText || '').includes(fee))""",
                arg=[TENANT_TITLE, f"{FEE:,.2f}"],
                timeout=30000,
            )
            ours = next(r for r in _table(page) if r.get("Name") == TENANT_TITLE)
            units = int(ours["Fee Units"])
            assert units >= 1 and ours["Billing Mode"] == "Per Booking", ours
            due = round(units * FEE, 2)
            assert _money(ours["Booking Fees"]) == [due] and _money(ours["Amount Due"]) == [due], ours
            expect(_indicator(page)).to_contain_text("Live", timeout=20000)
            gestures.click_form_action(page, "Close this month")
            gestures.confirm_yes(page)
            expect(_indicator(page)).to_contain_text("Closed on", timeout=30000)

            # --- Hub → the close → Issue statements ---------------------------
            _open_from_hub(page, CLOSE_DOCTYPE, period, "cbt-platform-month-close")
            gestures.click_form_action(page, "Issue statements")
            gestures.confirm_yes(page)
            done = page.locator(".modal.show").filter(has_text="Statements issued")
            expect(done).to_be_visible(timeout=30000)
            _dismiss(page, done)
            mine = next(s for s in _statements(page, period) if s["company"] == TENANT)
            assert float(mine["amount_due"]) == due and int(mine["booking_count"]) == units, mine

            # --- Hub → the statement → Mark paid via the platform's GCash → print
            _open_from_hub(page, STATEMENT_DOCTYPE, mine["name"], "cbt-platform-statement")
            expect(_indicator(page)).to_contain_text("Issued", timeout=20000)
            gestures.click_form_action(page, "Mark paid")
            gestures.fill_dialog(page, {"payment_channel": platform_gcash, "payment_reference": PAYMENT_REF})
            gestures.confirm_yes(page)
            expect(_indicator(page)).to_contain_text("Paid", timeout=30000)
            page.keyboard.press("Control+p")
            page.wait_for_url("**/desk/print/**", timeout=30000)
            wait_for_page_load(page)
            expect(page.locator(".frappe-control[data-fieldname='print_format'] input").first).to_have_value(STATEMENT_FORMAT, timeout=30000)
            printed = page.request.get(
                "/printview",
                params={"doctype": STATEMENT_DOCTYPE, "name": mine["name"], "format": STATEMENT_FORMAT, "no_letterhead": "1"},
            )
            assert printed.ok, f"printview: HTTP {printed.status}"
            html = printed.text()
            for needle in (
                f"Booking fees collected from customers for {month_label}",
                f"{units} paid booking(s)",
                f"{due:,.2f}",
                "PAID",
                "via GCash",
                PAYMENT_REF,
            ):
                assert needle in html, needle

            # --- Tenant Ledger, both books, read off the table ------------------
            today = _site_today(page)
            platform = [r for r in _ledger_rows(page, today, "Platform", "Accounts Receivable") if r.get("Document") == mine["name"]]
            assert [(r["Account"], _num(r, "Debit"), _num(r, "Credit")) for r in platform] == [
                ("Accounts Receivable", due, 0.0),
                ("Booking Fee Revenue", 0.0, due),
                ("Cash in E-Wallet - GCash", due, 0.0),
                ("Accounts Receivable", 0.0, due),
            ], platform
            assert platform[0]["Party (tenant)"] == TENANT_TITLE, platform[0]
            tenant = [r for r in _ledger_rows(page, today, "Tenant", "Due to Platform") if r.get("Document") == mine["name"]]
            # A Per Booking tenant's payable accrued per document — the
            # statement only SETTLES it: no expense row at issue.
            assert [(r["Account"], _num(r, "Debit"), _num(r, "Credit")) for r in tenant] == [
                ("Due to Platform", due, 0.0),
                ("Cash/Bank (remitted to platform)", 0.0, due),
            ], tenant
        finally:
            _cleanup_month(period)
            api.dispose()
            cancel_active_bookings(page, court, service_day)
            _reset_billing(page)

    def test_percentage_month_with_a_refund_closed_billed_paid_and_both_books_read_by_hand(
        self, page: Page, playwright: Playwright, platform_seat: str
    ):
        """The PERCENTAGE shape, by hand (section-26 coverage): 10% typed into
        the company form; two cash bookings last month, one REFUNDED from the
        board with a reason — it must drop out of confirmed revenue; Close →
        Issue → the statement's commission line → Mark paid → both books,
        the refund's reversal among them."""
        api = platform_api(playwright)  # month cleanup only
        login_as(page, platform_seat)
        court = _ensure_tenant(page)
        goto_workspace(page, "CBT Hub")
        year, month = _last_month(page)
        period = f"{year:04d}-{month:02d}"
        first_day = date(year, month, 1)
        last_day = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        day_kept, day_refunded = date(year, month, 16).isoformat(), date(year, month, 17).isoformat()
        month_label = first_day.strftime("%B %Y")
        commission = round(COURT_RATE * 0.10, 2)  # 10.00 on the ONE booking that stays paid
        platform_gcash = next(
            r["name"]
            for r in _get(
                page,
                "/api/resource/CBT Payment Channel",
                params={"filters": json.dumps([["scope", "=", "Platform"], ["label", "=", "GCash"]]), "fields": json.dumps(["name"])},
            )
        )
        try:
            _cleanup_month(period)  # a previous aborted run must not skew this
            # The WHOLE month, not just these two days: row 4's 15th survives an
            # aborted run and would land in confirmed revenue.
            cancel_active_bookings(page, court, first_day.isoformat(), to_date=last_day.isoformat())

            # --- Percentage 10%, typed into the company form, BEFORE booking ---
            # (a document snapshots its platform fee: under Percentage it is 0,
            # so the mode must be set first or the figures move).
            goto_form(page, "CBT Company", TENANT)
            gestures.fill(page, "billing_mode", "Percentage")
            gestures.fill(page, "commission_percent", 10)
            gestures.save_form(page)
            company = _get(page, f"/api/resource/CBT Company/{TENANT}")
            assert company["billing_mode"] == "Percentage" and float(company["commission_percent"]) == 10.0, company

            names = {}
            for day in (day_kept, day_refunded):
                booked = page.request.post(
                    "/api/method/court_booking_tech.api.bookings.create_booking",
                    headers=_headers(page),
                    data=json.dumps({"court": court, "booking_date": day, "start_time": "10:00:00", "payment_method": "Cash", "customer": CUSTOMER, "number_of_slots": 1}),
                )
                assert booked.ok, f"create_booking {day}: HTTP {booked.status} {booked.text()}"
                names[day] = booked.json()["message"]["name"]
            for name in names.values():
                row = _get(page, f"/api/resource/CBT Court Booking/{name}")
                assert row["booking_status"] == "Confirmed" and float(row["platform_fee"]) == 0.0, row

            # --- the 17th is refunded FROM THE BOARD, with a reason -------------
            load_board(page, BRANCH, day_refunded)
            slot(page, court, "10:00:00").click()
            wait_dialog(page)
            button = page.locator(f"{gestures.DIALOG} [data-action='cancel-booking']")
            expect(button).to_have_text(re.compile("Cancel & Refund"), timeout=15000)
            button.click()
            prompt = page.locator(".modal.show").filter(has_text="Cancelling it is a refund")
            expect(prompt).to_be_visible(timeout=15000)
            gestures.fill(page, "reason", REFUND_REASON, scope=".modal.show:has-text('Cancelling it is a refund')")
            with page.expect_response(lambda r: "cancel_booking" in r.url, timeout=30000) as resp_info:
                prompt.locator(".btn-primary, .btn-modal-primary").first.click()
            assert resp_info.value.ok, resp_info.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)
            refunded = _get(page, f"/api/resource/CBT Court Booking/{names[day_refunded]}")
            refunded_invoice = _get(page, f"/api/resource/CBT Booking Invoice/{refunded['billing_doc']}")
            assert refunded_invoice["status"] == "Cancelled" and refunded_invoice["refund_reason"] == REFUND_REASON, refunded_invoice

            # --- Platform Revenue → last month: 100.00 × 10% → Close ------------
            page.goto("/desk/query-report/CBT Platform Revenue", wait_until="domcontentloaded", timeout=60000)
            wait_for_page_load(page)
            expect(page.locator(f"{FILTER_BAR} .frappe-control[data-fieldname='month'] select")).to_be_visible(timeout=20000)
            gestures.select(page, "month", str(month), scope=FILTER_BAR)
            gestures.type_value(page, "year", year, scope=FILTER_BAR)
            page.wait_for_function(
                """([title, mode, due]) => Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]'))
                    .some((row) => { const t = row.innerText || ''; return t.includes(title) && t.includes(mode) && t.includes(due); })""",
                arg=[TENANT_TITLE, "Percentage", f"{commission:,.2f}"],
                timeout=30000,
            )
            ours = next(r for r in _table(page) if r.get("Name") == TENANT_TITLE)
            assert ours["Billing Mode"] == "Percentage", ours
            assert _money(ours["Confirmed Revenue"]) == [COURT_RATE], ours  # the refund is OUT
            assert _money(ours["Amount Due"]) == [commission], ours
            expect(_indicator(page)).to_contain_text("Live", timeout=20000)
            gestures.click_form_action(page, "Close this month")
            gestures.confirm_yes(page)
            expect(_indicator(page)).to_contain_text("Closed on", timeout=30000)

            # --- Hub → the close → Issue statements ---------------------------
            _open_from_hub(page, CLOSE_DOCTYPE, period, "cbt-platform-month-close")
            gestures.click_form_action(page, "Issue statements")
            gestures.confirm_yes(page)
            done = page.locator(".modal.show").filter(has_text="Statements issued")
            expect(done).to_be_visible(timeout=30000)
            _dismiss(page, done)
            mine = next(s for s in _statements(page, period) if s["company"] == TENANT)
            assert float(mine["amount_due"]) == commission, mine

            # --- Hub → the statement → Mark paid via GCash → the commission line
            _open_from_hub(page, STATEMENT_DOCTYPE, mine["name"], "cbt-platform-statement")
            expect(_indicator(page)).to_contain_text("Issued", timeout=20000)
            gestures.click_form_action(page, "Mark paid")
            gestures.fill_dialog(page, {"payment_channel": platform_gcash, "payment_reference": PAYMENT_REF})
            gestures.confirm_yes(page)
            expect(_indicator(page)).to_contain_text("Paid", timeout=30000)
            page.keyboard.press("Control+p")
            page.wait_for_url("**/desk/print/**", timeout=30000)
            wait_for_page_load(page)
            expect(page.locator(".frappe-control[data-fieldname='print_format'] input").first).to_have_value(STATEMENT_FORMAT, timeout=30000)
            printed = page.request.get(
                "/printview",
                params={"doctype": STATEMENT_DOCTYPE, "name": mine["name"], "format": STATEMENT_FORMAT, "no_letterhead": "1"},
            )
            assert printed.ok, f"printview: HTTP {printed.status}"
            html = printed.text()
            for needle in (
                f"Platform commission on confirmed revenue for {month_label}",
                f"{COURT_RATE:,.2f}",
                "× 10%",
                f"{commission:,.2f}",
                "PAID",
                "via GCash",
                PAYMENT_REF,
            ):
                assert needle in html, needle

            # --- both books, today, read off the table ---------------------------
            today = _site_today(page)
            platform = reports.voucher(_ledger_rows(page, today, "Platform", "Accounts Receivable"), mine["name"])
            assert platform == [
                ("Accounts Receivable", commission, 0.0),
                ("Commission Revenue", 0.0, commission),
                ("Cash in E-Wallet - GCash", commission, 0.0),
                ("Accounts Receivable", 0.0, commission),
            ], platform
            tenant_rows = _ledger_rows(page, today, "Tenant", "Platform Fees Expense")
            # A Percentage tenant's payable is recognised at ISSUE (an expense),
            # then settled — unlike Per Booking's per-document accrual (row 4).
            assert reports.voucher(tenant_rows, mine["name"]) == [
                ("Platform Fees Expense", commission, 0.0),
                ("Due to Platform", 0.0, commission),
                ("Due to Platform", commission, 0.0),
                ("Cash/Bank (remitted to platform)", 0.0, commission),
            ], tenant_rows
            # The refund: paid today (back-recorded), reversed today — the last
            # three lines of that document are the reversal, reason attached.
            refund_lines = reports.voucher(tenant_rows, refunded_invoice["name"])
            assert refund_lines[-3:] == [
                ("Cash on Hand", 0.0, COURT_RATE),
                ("Service Revenue", 89.29, 0.0),
                ("Output VAT Payable", 10.71, 0.0),
            ], refund_lines
            reversal = next(
                r for r in tenant_rows
                if r.get("Document") == refunded_invoice["name"] and "Refund / reversal" in (r.get("Description") or "")
            )
            assert REFUND_REASON in reversal["Description"], reversal
        finally:
            _cleanup_month(period)
            api.dispose()
            cancel_active_bookings(page, court, first_day.isoformat(), to_date=last_day.isoformat())
            _reset_billing(page)

    def test_open_play_paid_via_and_received_via_by_dialog(self, page: Page, op_session, platform_seat: str):
        login_as(page, platform_seat)
        ayala = _channels(page, "ayala-courts")
        gcash, bdo = ayala["GCash"]["name"], ayala["BDO"]["name"]
        show_session(page, op_session)

        page.locator(".cbt-op-bottom [data-action='add']").click()
        wait_dialog(page)
        gestures.fill(page, "customer", OP_PLAYER, scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Fund Transfer", scope=gestures.DIALOG)
        wait_channels_settled(page)
        assert channel_options(page) == ["GCash", "BDO"], channel_options(page)
        gestures.fill(page, "payment_channel", bdo, scope=gestures.DIALOG)
        page.wait_for_function(
            """() => { const el = document.querySelector(".modal.show [data-testid='op-member-hint']");
                return !!el && /Pays/.test(el.textContent); }""",
            timeout=15000,
        )
        with page.expect_response(lambda r: "add_players" in r.url, timeout=30000) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, resp_info.value.text()
        page.wait_for_function("() => window.cur_dialog && !cur_dialog.get_value('customer')", timeout=15000)
        page.evaluate("() => cur_dialog.get_secondary_btn().click()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        session = _get(page, f"/api/resource/CBT Open Play Session/{op_session}")
        participant = next(r for r in session["participants"] if r.get("customer") == OP_PLAYER)
        assert participant["payment_status"] == "Unpaid" and participant["payment_channel"] == bdo, participant

        reload_board(page, op_session)
        page.locator(f"[data-action='pay'][data-participant='{participant['name']}']").click()
        wait_dialog(page)
        wait_channels_settled(page)
        received = page.locator(f"{gestures.DIALOG} .frappe-control[data-fieldname='payment_channel'] select")
        expect(received).to_have_value(bdo)
        gestures.fill(page, "payment_channel", gcash, scope=gestures.DIALOG)
        with page.expect_response(lambda r: "confirm_participant_payment" in r.url, timeout=30000) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, resp_info.value.text()

        session = _get(page, f"/api/resource/CBT Open Play Session/{op_session}")
        participant = next(r for r in session["participants"] if r.get("customer") == OP_PLAYER)
        assert participant["payment_status"] == "Paid" and participant["payment_channel"] == gcash, participant
        invoice = _get(page, f"/api/resource/CBT Booking Invoice/{participant['billing_doc']}")
        assert invoice["status"] == "Paid & Verified" and invoice["payment_channel"] == gcash, invoice
