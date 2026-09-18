"""
E2E file 02 — Tenant isolation (section-7; PLAN §10 row 02).

As AYALA staff (Stella), Company B (QCSM) is invisible and untouchable across
every surface the desk offers: the board's branch scope, the board APIs, list
views, direct form URLs, and the Pending Payments panel. The panel test doubles
as the S5 private-file regression guard — a proof THUMBNAIL must actually
render bytes under a staff session.

Admin-side fixtures run BEFORE switching the page session to Stella (the page
context starts as Administrator); standalone cleanup uses the verified admin
APIRequestContext, never the test user's silently-filtered lists.
"""
import json
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, Playwright

from helpers.auth import login_as
from helpers.board import (
    BGC_BRANCH,
    CUSTOMER,
    platform_api,
    csrf,
    load_board,
)
from helpers.navigation import goto_form, goto_list

STELLA = "staff.ayala@example.com"
QCSM_BRANCH = "QCSM-timog"
QCSM_COURT = "QCSM-timog-court-1"

BOARD_DATE = (date.today() + timedelta(days=45)).isoformat()


def _qcsm_open_date() -> str:
    """QCSM-timog is seeded CLOSED on Sundays — slide the fixture date off a
    Sunday so the server-side insert pipeline accepts it on any run day."""
    day = date.today() + timedelta(days=45)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


QCSM_BOOK_DATE = _qcsm_open_date()


@pytest.mark.e2e
class TestTenantIsolation:
    def test_board_branch_scope_only_own_company(self, page: Page):
        login_as(page, STELLA)
        # Server-side scope: the branch list Stella's selector feeds from.
        resp = page.request.get(
            "/api/resource/CBT Branch",
            params={"filters": json.dumps([["is_active", "=", 1]]), "limit_page_length": 0},
        )
        assert resp.ok, f"branch list: HTTP {resp.status}"
        names = [row["name"] for row in resp.json()["data"]]
        assert names, "Stella sees no branches at all"
        assert all(n.startswith("AYALA-") for n in names), names
        # And the board renders her branch (B33: one COLUMN per court).
        load_board(page, BGC_BRANCH, BOARD_DATE)
        assert page.locator("th.cbt-colhead").count() == 3

    def test_board_api_cross_company_denied(self, page: Page):
        login_as(page, STELLA)
        resp = page.request.get(
            "/api/method/court_booking_tech.api.board.get_board_data",
            params={"branch": QCSM_BRANCH, "date": BOARD_DATE},
        )
        assert resp.status == 403, f"expected 403, got {resp.status}: {resp.text()}"

    def test_company_list_view_scoped(self, page: Page):
        login_as(page, STELLA)
        goto_list(page, "CBT Company")
        # The list re-fetches after its first render (rows clear while
        # "Refreshing..." shows) — a wait-then-read leaves a gap that this
        # exact test flaked in twice. The predicate RETURNS the snapshot, so
        # settledness check and data capture are one atomic JS evaluation.
        # Data rows are .list-row (header skeleton is .list-row-head).
        handle = page.wait_for_function(
            """() => {
                const el = document.querySelector('.frappe-list');
                if (!el) return false;
                const text = el.innerText;
                if (text.includes('Refreshing')) return false;
                const rows = el.querySelectorAll('.list-row-container .list-row');
                if (rows.length === 0) return false;
                if (!text.includes('Ayala Courts')) return false;
                return JSON.stringify({ rowCount: rows.length, text: text });
            }""",
            timeout=15000,
        )
        snap = json.loads(handle.json_value())
        assert snap["rowCount"] == 1, (
            f"expected 1 scoped row, got {snap['rowCount']}: {snap['text']}"
        )
        assert "QC Smash" not in snap["text"], snap["text"]

    def test_other_company_booking_form_not_permitted(self, page: Page, playwright: Playwright):
        api = platform_api(playwright)
        try:
            resp = api.get(
                "/api/resource/CBT Court Booking",
                params={
                    "filters": json.dumps([["company", "=", "qc-smash"]]),
                    "limit_page_length": 1,
                },
            )
            assert resp.ok and resp.json()["data"], "no seeded QCSM booking found"
            qcsm_booking = resp.json()["data"][0]["name"]
        finally:
            api.dispose()

        login_as(page, STELLA)
        goto_form(page, "CBT Court Booking", qcsm_booking)
        page.wait_for_function(
            """() => /insufficient permission|not permitted/i.test(document.body.innerText)""",
            timeout=15000,
        )
        # The document body must not have rendered.
        assert page.locator(".form-layout .frappe-control[data-fieldname='court']").count() == 0

    def test_pending_panel_scoped_and_proof_thumbnail_renders(self, page: Page):
        # Fixture while the context is still Administrator: a QCSM Reserved
        # hold that must NOT appear in Stella's panel. Idempotent re-runs:
        # cancel any active hold on that slot first.
        token = csrf(page)
        probe = page.request.get(
            "/api/resource/CBT Court Booking",
            params={
                "filters": json.dumps(
                    [
                        ["court", "=", QCSM_COURT],
                        ["booking_date", "=", QCSM_BOOK_DATE],
                        ["booking_status", "in", ["Reserved", "Confirmed", "Extended"]],
                    ]
                )
            },
        )
        assert probe.ok
        for row in probe.json()["data"]:
            cancelled = page.request.post(
                "/api/method/court_booking_tech.api.bookings.cancel_booking",
                headers={"X-Frappe-CSRF-Token": token},
                # Section-26: a paid booking's cancel is a refund and needs a reason.
                form={"name": row["name"], "reason": "E2E cleanup"},
            )
            assert cancelled.ok
        created = page.request.post(
            "/api/resource/CBT Court Booking",
            headers={"X-Frappe-CSRF-Token": token},
            data={
                "court": QCSM_COURT,
                "customer": CUSTOMER,
                "booking_date": QCSM_BOOK_DATE,
                "start_time": "10:00:00",
                "number_of_slots": 1,
                "payment_method": "Fund Transfer",
            },
        )
        assert created.ok, f"QCSM fixture booking: HTTP {created.status} {created.text()}"
        qcsm_name = created.json()["data"]["name"]
        assert qcsm_name.startswith("BK-QCSM-")

        login_as(page, STELLA)
        load_board(page, BGC_BRANCH, BOARD_DATE)

        # API purity: no company arg — the session resolves it; only AYALA rows.
        resp = page.request.get(
            "/api/method/court_booking_tech.api.board.get_pending_payments"
        )
        assert resp.ok, f"get_pending_payments: HTTP {resp.status}"
        payload = resp.json()["message"]
        assert payload["company"] == "ayala-courts"
        assert payload["items"], "expected the seeded AYALA proof holds in the panel"
        assert all(item["name"].startswith("BK-AYALA-") for item in payload["items"])
        assert all(qcsm_name != item["name"] for item in payload["items"])

        # Panel UI: no QCSM leak, and the seeded AYALA proof thumbnail renders
        # actual bytes under Stella's session (S5 private-file guard).
        panel_text = page.locator(".cbt-pending-panel").inner_text()
        assert "QCSM" not in panel_text and "QC Smash" not in panel_text, panel_text
        thumb = page.locator(".cbt-pending-thumb").first
        thumb.wait_for(state="visible", timeout=15000)
        width = thumb.evaluate(
            """el => (el.complete ? Promise.resolve(el.naturalWidth)
                : new Promise(res => { el.onload = () => res(el.naturalWidth);
                                       el.onerror = () => res(0); }))"""
        )
        assert width > 0, "proof thumbnail did not render (private-file fetch failed?)"
