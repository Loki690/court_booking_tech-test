"""
E2E — Closing a platform month from the revenue report (Backlog B21(b),
2026-08-27).

The backend rows (`tests/test_reports.py::TestMonthClose`) prove the
property — a closed month's figures stop moving. What only a browser can prove
is that a Platform Admin can DO it from where they already are: pick a month
on CBT Platform Revenue, click "Close this month", confirm, and see the page
say so — and that the say-so survives a reload, that a second click is refused
in words, and that the close is reachable from the Hub afterwards.

Every gesture is a human's: the month filter is a <select> and the year an
<input> in the report's own filter bar; the button is the page's inner button;
the confirm is frappe's dialog. The API is used only to look at what was
written and to delete it afterwards.

Runs as the seeded PLATFORM seat (`cbt.admin@example.com`, the CBT Platform
Admin role and nothing else — Batch 17; the Administrator context bypasses
DocPerms and proves nothing about the role). Closes LAST MONTH, which is always
a month that has ended, and deletes the close in `finally` (the role holds
delete on the close) — so no ledger date is claimed and nothing is left behind
for the backend module that closes 2027-08.
"""
import re
from datetime import date

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures
from helpers.auth import login_as
from helpers.navigation import goto_workspace, wait_for_page_load

REPORT_ROUTE = "/desk/query-report/CBT Platform Revenue"
CLOSE_DOCTYPE = "CBT Platform Month Close"
FILTER_BAR = ".page-form"


def _last_month(page: Page) -> tuple:
    """Last month by the SITE's calendar, not the host's.

    The refusal rule runs on the server clock (Asia/Manila); a host that has
    already ticked over to the 1st while the server is still on the 31st would
    pick a month the server refuses — a wall-clock red, not a defect. The desk
    boot carries the site's time zone, and `frappe.datetime.now_date()` applies
    it. Needs a loaded desk page.
    """
    today = date.fromisoformat(page.evaluate("() => frappe.datetime.now_date()"))
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return year, month


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _csrf(page: Page) -> str:
    token = page.evaluate("() => (window.frappe && frappe.csrf_token) || ''") or ""
    assert token, f"no CSRF token on {page.url!r} — load a desk page before writing"
    return token


def _close_exists(page: Page, period: str) -> bool:
    resp = page.request.get(f"/api/resource/{CLOSE_DOCTYPE}/{period}")
    if resp.status == 404:
        return False
    assert resp.ok, f"GET {CLOSE_DOCTYPE}/{period}: HTTP {resp.status} {resp.text()}"
    return True


def _delete_close(page: Page, period: str):
    if not _close_exists(page, period):
        return
    resp = page.request.delete(
        f"/api/resource/{CLOSE_DOCTYPE}/{period}",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
    )
    assert resp.ok, f"cleanup DELETE {period}: HTTP {resp.status} {resp.text()}"


def _load_report(page: Page):
    page.goto(REPORT_ROUTE, wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    expect(
        page.locator(f"{FILTER_BAR} .frappe-control[data-fieldname='month'] select")
    ).to_be_visible(timeout=20000)


def _open_report(page: Page, year: int, month: int):
    """Load the report and set its filters BY GESTURE, then wait for rows."""
    _load_report(page)
    gestures.select(page, "month", str(month), scope=FILTER_BAR)
    gestures.type_value(page, "year", year, scope=FILTER_BAR)
    # The report refreshes itself after a filter change; wait for the rendered
    # rows, not for silence — every seeded tenant is a row, plus the total.
    expect(page.locator(".datatable .dt-row[data-row-index]").first).to_be_visible(
        timeout=30000
    )


def _indicator(page: Page):
    return page.locator(".page-head .indicator-pill")


@pytest.mark.e2e
class TestMonthClose:
    def test_platform_admin_closes_last_month_from_the_report(self, page: Page, platform_seat: str):
        login_as(page, platform_seat)
        # A desk page first: the site calendar and the CSRF token both live in
        # the loaded desk, and the pre-delete below signs its DELETE with it.
        _load_report(page)
        year, month = _last_month(page)
        period = _period(year, month)
        try:
            _delete_close(page, period)  # a previous aborted run must not skew this
            _open_report(page, year, month)
            expect(_indicator(page)).to_contain_text("Live", timeout=20000)

            gestures.click_form_action(page, "Close this month")
            gestures.confirm_yes(page)

            expect(_indicator(page)).to_contain_text("Closed on", timeout=30000)
            expect(_indicator(page)).to_contain_text(platform_seat)  # closed_by, raw
            assert _close_exists(page, period), "the indicator says closed but no record exists"
            resp = page.request.get(f"/api/resource/{CLOSE_DOCTYPE}/{period}")
            data = resp.json()["data"]
            companies = page.request.get(
                "/api/resource/CBT Company", params={"limit_page_length": 0}
            ).json()["data"]
            assert len(data["rows"]) == len(companies), (
                f"{len(data['rows'])} frozen rows for {len(companies)} companies"
            )
            assert data["closed_by"] == platform_seat, data["closed_by"]

            # The say-so is the SERVER's, so it survives a reload.
            _open_report(page, year, month)
            expect(_indicator(page)).to_contain_text("Closed on", timeout=20000)

            # A second close is refused in words a human can act on.
            gestures.click_form_action(page, "Close this month")
            gestures.confirm_yes(page)
            refusal = page.locator(".modal.show").filter(has_text="already closed")
            expect(refusal).to_be_visible(timeout=20000)
            for _attempt in range(3):
                refusal.locator(".modal-header .btn-modal-close").first.click()
                try:
                    expect(refusal).to_have_count(0, timeout=3000)
                    break
                except AssertionError:
                    continue
            expect(refusal).to_have_count(0, timeout=5000)
        finally:
            if "/desk" in page.url:
                _delete_close(page, period)

    def test_hub_tile_opens_the_close_list(self, page: Page, platform_seat: str):
        """Reachability, the B25 way: the tile is clicked, never routed to."""
        login_as(page, platform_seat)
        goto_workspace(page, "CBT Hub")
        tile = page.locator(".shortcut-widget-box", has_text=CLOSE_DOCTYPE)
        expect(tile).to_be_visible(timeout=15000)
        tile.click()
        page.wait_for_url("**/desk/cbt-platform-month-close**", timeout=30000)
        wait_for_page_load(page)
        # The desk is an SPA: the Hub's page (tile label included) stays in the
        # DOM beside the list's, so a `.page-head` locator is ambiguous either
        # way. The document title is set by the page that is actually showing.
        expect(page).to_have_title(re.compile(CLOSE_DOCTYPE), timeout=15000)
        expect(page.locator(".frappe-list").last).to_be_visible(timeout=15000)
