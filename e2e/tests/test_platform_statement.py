"""
E2E — Issuing, paying and reading a platform statement (Backlog B21(a),
2026-08-27).

The backend rows (`tests/test_reports.py::TestPlatformStatements`) prove the
properties — snapshot at issue, per-tenant numbering, paid/cancelled, tenant
scope, the print. What only a browser can prove is that the people involved
can DO it from where they already are:

  * a Platform Admin opens the month close from the Hub, clicks "Issue
    statements", finds the statement from the Hub, marks it paid through the
    dialog (typing the reference), and prints it with the form's own Ctrl+P —
    and the print view already names the statement format (B23's lesson);
  * a tenant Company Admin reaches the same list from THEIR Hub, sees only
    their own statements, opens one, has no paying/cancelling button, can
    print it — and cannot read the month close or another tenant's statement.

Every gesture is a human's: Hub tiles, list rows, form buttons, a dialog
field, a keyboard shortcut. The API is used only to ARRANGE (the close — the
closing gesture is `test_month_close.py`'s), to LOOK, and to CLEAN UP.

Runs on LAST MONTH by the site calendar and deletes everything it made in
`finally`, so no ledger date is claimed and the backend module's August 2027
stays untouched. "Everything it made" is literal: the pre-clean and the
cleanup touch only rows closed/issued by THIS SUITE'S SEAT — the seeded
platform seat `cbt.admin@example.com` (CBT Platform Admin and nothing else,
Batch 17; the Administrator context bypasses DocPerms and proved nothing about
the role). A close or statement made by any other user for that month — a
human's walkthrough on a shared bench — stops the test with a message rather
than being deleted (ducky, 2026-08-27). The cleanup itself runs on the admin
API context: a statement is cancelled, never deleted, so the role holds no
delete on it BY DESIGN, and deletion here is test hygiene, not a platform act.
Each run consumes a PST number per tenant, as any real issue-and-cancel does;
numbers are never reused. QC Smash is the tenant under test because it is
billed by SUBSCRIPTION — it owes its flat fee in any month, bookings or not,
so a statement for it always exists.
"""
import json
from datetime import date

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures, reports
from helpers.auth import PLATFORM_ADMIN, login_as
from helpers.worker_routing import bench_json
from helpers.navigation import goto_workspace, wait_for_page_load

CLOSE_DOCTYPE = "CBT Platform Month Close"
STATEMENT_DOCTYPE = "CBT Platform Statement"
PRINT_FORMAT = "CBT Platform Statement"
ENGINE = "court_booking_tech.court_booking_tech.doctype"
CLOSE_METHOD = f"{ENGINE}.cbt_platform_month_close.cbt_platform_month_close.close_month"
ISSUE_METHOD = f"{ENGINE}.cbt_platform_statement.cbt_platform_statement.issue_statements"
PAY_METHOD = f"{ENGINE}.cbt_platform_statement.cbt_platform_statement.mark_paid"

TENANT_ADMIN = "admin.qcsm@example.com"
TENANT_COMPANY = "qc-smash"
TENANT_TITLE = "QC Smash"
PAYMENT_REF = "E2E-GCASH-REF-0042"


# --- site facts + API arrange/verify/cleanup ---------------------------------


def _last_month(page: Page) -> tuple:
    """Last month by the SITE's calendar (see test_month_close.py). Needs a
    loaded desk page."""
    today = date.fromisoformat(page.evaluate("() => frappe.datetime.now_date()"))
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return year, month


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _csrf(page: Page) -> str:
    token = page.evaluate("() => (window.frappe && frappe.csrf_token) || ''") or ""
    assert token, f"no CSRF token on {page.url!r} — load a desk page before writing"
    return token


def _post(page: Page, method: str, form: dict):
    return page.request.post(
        f"/api/method/{method}",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
        form=form,
    )


def _close_month(page: Page, year: int, month: int):
    resp = _post(page, CLOSE_METHOD, {"year": year, "month": month})
    assert resp.ok, f"close_month {year}-{month}: HTTP {resp.status} {resp.text()}"


def _close_rows(page: Page, period: str) -> list:
    resp = page.request.get(f"/api/resource/{CLOSE_DOCTYPE}/{period}")
    assert resp.ok, f"GET {CLOSE_DOCTYPE}/{period}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]["rows"]


def _statements(page: Page, period: str) -> list:
    resp = page.request.get(
        f"/api/resource/{STATEMENT_DOCTYPE}",
        params={
            "filters": json.dumps([["period", "=", period]]),
            "fields": json.dumps(["name", "company", "status", "amount_due", "issued_by"]),
            "limit_page_length": 0,
        },
    )
    assert resp.ok, f"list statements {period}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _statement(page: Page, name: str) -> dict:
    resp = page.request.get(f"/api/resource/{STATEMENT_DOCTYPE}/{name}")
    assert resp.ok, f"GET {STATEMENT_DOCTYPE}/{name}: HTTP {resp.status} {resp.text()}"
    return resp.json()["data"]


def _printview(page: Page, name: str) -> str:
    resp = page.request.get(
        "/printview",
        params={
            "doctype": STATEMENT_DOCTYPE,
            "name": name,
            "format": PRINT_FORMAT,
            "no_letterhead": "1",
        },
    )
    assert resp.ok, f"printview {name}: HTTP {resp.status}"
    return resp.text()


OWN_SEAT = PLATFORM_ADMIN  # the seat this suite runs as — its fingerprint on rows


def _cleanup(period: str):
    """Delete what THIS suite made for the month and nothing else.

    Backlog B43: through `bench execute`, not an HTTP context. The previous
    version said the quiet part out loud — *"the platform role holds no delete
    on them by design, Administrator does"* — which is exactly the class of
    fixture this row exists to remove: one only a superuser could ever run.

    The SAFETY is unchanged and now lives beside the deletion, in
    `court_booking_tech.testing.purge_platform_statements`: a row issued or
    closed by anyone but this suite's seat is somebody's real work on a shared
    bench and the helper refuses rather than deleting it. Unconditional, because
    a cleanup that quietly skips leaves a PAID statement on the site.
    """
    return bench_json(
        "court_booking_tech.testing.purge_platform_statements", [period, OWN_SEAT]
    )


# --- the human path ------------------------------------------------------------


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
        "(name) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === name"
        " && !cur_frm.doc.__islocal",
        arg=row_text,
        timeout=30000,
    )


def _indicator(page: Page):
    # The desk is an SPA: the Hub's and the list's `.page-head` stay in the
    # DOM beside the form's, so only the VISIBLE pill is the form's.
    return page.locator(".page-head .indicator-pill:visible")


def _headline(page: Page, text: str):
    """The form's dashboard headline, located by what a human reads — the
    wrapper classes differ between the headline and the rest of the dashboard
    in v16, and the words are the contract anyway."""
    return page.get_by_text(text, exact=False).first


def _dismiss(page: Page, modal):
    for _attempt in range(3):
        modal.locator(".modal-header .btn-modal-close").first.click()
        try:
            expect(modal).to_have_count(0, timeout=3000)
            return
        except AssertionError:
            continue
    expect(modal).to_have_count(0, timeout=5000)


def _action_labels(page: Page) -> list:
    """The visible form's action buttons, by label — with the container
    asserted FOUND first, so "no such button" can never be the selector
    missing the toolbar altogether (ducky, 2026-08-27: three `== 0`
    assertions with no positive control)."""
    containers = page.locator(":is(.custom-actions, .page-actions):visible")
    assert containers.count() >= 1, "no visible form-action container on the page"
    labels = [
        (t or "").strip()
        for t in containers.locator("button").all_inner_texts()
    ]
    return [label for label in labels if label]


@pytest.mark.e2e
class TestPlatformStatement:
    def test_platform_admin_issues_marks_paid_and_prints(
        self, page: Page, platform_seat: str
    ):
        login_as(page, platform_seat)
        goto_workspace(page, "CBT Hub")  # site calendar + CSRF live in a loaded desk
        year, month = _last_month(page)
        period = _period(year, month)
        try:
            _cleanup(period)  # a previous aborted run must not skew this
            _close_month(page, year, month)

            # Hub → Month Close → the period → Issue statements.
            _open_from_hub(page, CLOSE_DOCTYPE, period, "cbt-platform-month-close")
            expect(_headline(page, "No statements issued yet")).to_be_visible(timeout=20000)
            gestures.click_form_action(page, "Issue statements")
            gestures.confirm_yes(page)
            done = page.locator(".modal.show").filter(has_text="Statements issued")
            expect(done).to_be_visible(timeout=30000)
            _dismiss(page, done)

            owing = {r["company"] for r in _close_rows(page, period) if r["amount_due"] > 0}
            assert TENANT_COMPANY in owing, "QC Smash is subscription-billed and must owe"
            issued = _statements(page, period)
            assert {s["company"] for s in issued} == owing, (issued, owing)
            assert all(s["status"] == "Issued" for s in issued), issued
            mine = next(s for s in issued if s["company"] == TENANT_COMPANY)
            expect(
                _headline(page, f"{len(issued)} statement(s) issued for this month, 0 paid")
            ).to_be_visible(timeout=20000)

            # Hub → Statement → the QC Smash one → Mark paid (typing the reference).
            _open_from_hub(page, STATEMENT_DOCTYPE, mine["name"], "cbt-platform-statement")
            expect(_indicator(page)).to_contain_text("Issued", timeout=20000)
            site_today = page.evaluate("() => frappe.datetime.now_date()")
            # Backlog B29: the platform's OWN channels in the dialog — GCash is
            # chosen by hand, not left on the default.
            platform_gcash = next(
                r["name"]
                for r in page.request.get(
                    "/api/resource/CBT Payment Channel",
                    params={
                        "filters": json.dumps([["scope", "=", "Platform"], ["label", "=", "GCash"]]),
                        "fields": json.dumps(["name"]),
                    },
                ).json()["data"]
            )
            gestures.click_form_action(page, "Mark paid")
            gestures.fill_dialog(page, {"payment_channel": platform_gcash, "payment_reference": PAYMENT_REF})
            gestures.confirm_yes(page)
            expect(_indicator(page)).to_contain_text("Paid", timeout=30000)
            labels = _action_labels(page)
            assert "Cancel statement" in labels, labels  # the positive control
            assert "Mark paid" not in labels, labels  # a paid statement offers no Mark paid

            after = _statement(page, mine["name"])
            assert after["status"] == "Paid", after
            assert after["payment_reference"] == PAYMENT_REF, after
            assert after["paid_on"] == site_today, after
            assert after["paid_by"] == platform_seat, after
            assert after["amount_due"] == mine["amount_due"], "paying must not move the figure"
            # Backlog B29: the channel chosen in the dialog is what the
            # statement recorded (our side of the reconciliation)...
            assert after.get("payment_channel") == platform_gcash, after

            # Print — the form's own shortcut, and the selector must ALREADY
            # read the statement format (B23: a doctype with no default format
            # prints frappe's field-label layout).
            page.keyboard.press("Control+p")
            page.wait_for_url("**/desk/print/**", timeout=30000)
            wait_for_page_load(page)
            expect(
                page.locator(".frappe-control[data-fieldname='print_format'] input").first
            ).to_have_value(PRINT_FORMAT, timeout=30000)
            html = _printview(page, mine["name"])
            # ...and the print says so, beside the reference — and names the
            # SUBSCRIPTION shape's own line (section-26 coverage).
            month_label = date(year, month, 1).strftime("%B %Y")
            for needle in ("STATEMENT OF ACCOUNT", "PAID", PAYMENT_REF, mine["name"],
                           f"{after['amount_due']:,.2f}", "via GCash",
                           f"Platform subscription for {month_label}", "Flat monthly fee"):
                assert needle in html, needle

            # Both books, today, read off the rendered Tenant Ledger — the
            # Subscription tenant's payable is an EXPENSE recognised at issue,
            # then settled; our side is a receivable turned into GCash.
            due = float(after["amount_due"])
            platform = reports.voucher(
                reports.ledger_rows(page, TENANT_COMPANY, TENANT_TITLE, site_today, "Platform", "Accounts Receivable"),
                mine["name"],
            )
            assert platform == [
                ("Accounts Receivable", due, 0.0),
                ("Subscription Revenue", 0.0, due),
                ("Cash in E-Wallet - GCash", due, 0.0),
                ("Accounts Receivable", 0.0, due),
            ], platform
            tenant = reports.voucher(
                reports.ledger_rows(page, TENANT_COMPANY, TENANT_TITLE, site_today, "Tenant", "Platform Fees Expense"),
                mine["name"],
            )
            assert tenant == [
                ("Platform Fees Expense", due, 0.0),
                ("Due to Platform", 0.0, due),
                ("Due to Platform", due, 0.0),
                ("Cash/Bank (remitted to platform)", 0.0, due),
            ], tenant
            assert "UNPAID" not in html
        finally:
            _cleanup(period)

    def test_tenant_admin_reads_and_prints_own_statement_only(
        self, page: Page, browser, base_url, platform_seat: str
    ):
        login_as(page, platform_seat)  # the platform ARRANGES (close + issue) as itself
        goto_workspace(page, "CBT Hub")
        year, month = _last_month(page)
        period = _period(year, month)
        try:
            _cleanup(period)
            _close_month(page, year, month)
            resp = _post(page, ISSUE_METHOD, {"period": period})
            assert resp.ok, f"issue_statements: HTTP {resp.status} {resp.text()}"
            issued = _statements(page, period)
            mine = next(s for s in issued if s["company"] == TENANT_COMPANY)
            others = [s for s in issued if s["company"] != TENANT_COMPANY]

            tenant = browser.new_context(
                base_url=base_url,
                viewport={"width": 1400, "height": 960},
                ignore_https_errors=True,
            )
            tpage = tenant.new_page()
            try:
                login_as(tpage, TENANT_ADMIN)
                # Their Hub carries the tile; the list behind it is theirs only.
                _open_from_hub(tpage, STATEMENT_DOCTYPE, mine["name"], "cbt-platform-statement")
                visible = _statements(tpage, period)  # the same list query, as the tenant
                assert visible and all(s["company"] == TENANT_COMPANY for s in visible), visible
                expect(_indicator(tpage)).to_contain_text("Issued", timeout=20000)
                labels = _action_labels(tpage)  # asserts the toolbar is FOUND
                assert "Mark paid" not in labels, labels
                assert "Cancel statement" not in labels, labels

                html = _printview(tpage, mine["name"])
                assert "STATEMENT OF ACCOUNT" in html and "UNPAID" in html
                assert f"{mine['amount_due']:,.2f}" in html

                # What stays behind the platform wall.
                assert tpage.request.get(f"/api/resource/{CLOSE_DOCTYPE}/{period}").status == 403
                for other in others:
                    assert (
                        tpage.request.get(f"/api/resource/{STATEMENT_DOCTYPE}/{other['name']}").status
                        == 403
                    ), other
                    # `print` is the one extra ptype this seat holds — the
                    # print route must be scoped exactly like the read.
                    assert (
                        tpage.request.get(
                            "/printview",
                            params={
                                "doctype": STATEMENT_DOCTYPE,
                                "name": other["name"],
                                "format": PRINT_FORMAT,
                            },
                        ).status
                        == 403
                    ), other
                refused = _post(
                    tpage, PAY_METHOD, {"name": mine["name"], "paid_on": _period(year, month) + "-01"}
                )
                assert refused.status == 403, f"tenant mark_paid: HTTP {refused.status}"
                assert _statement(page, mine["name"])["status"] == "Issued"
            finally:
                tenant.close()
        finally:
            _cleanup(period)
