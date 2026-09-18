"""
Portal page helpers (section-9 E2E, files 05/07).

The portal is served entirely by whitelisted APIs on top of a customer session
(no DocPerm — leak vector 4), so these helpers drive the real UI and lean on
the same discipline as the board helpers: gate on server responses, never the
host clock, and expire clocks via testing.age_booking_clocks (never a sleep —
a wall-clock sleep for expiry is a review-rejectable defect, section-9 gotcha).
"""
import json

from playwright.sync_api import Page

from helpers.worker_routing import base_url


def csrf(page: Page) -> str:
    """A portal page injects frappe.csrf_token inline (base_template_page
    add_csrf_token) — so any CBT portal route already carries it. Fall back to
    /find-court if the tab is still on about:blank."""
    if not page.evaluate("() => typeof frappe !== 'undefined' && !!frappe.csrf_token"):
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "() => typeof frappe !== 'undefined' && !!frappe.csrf_token", timeout=15000
        )
    return page.evaluate("() => frappe.csrf_token")


def post(page: Page, method: str, form: dict):
    """Authenticated JSON POST from the current page context (its cookies)."""
    resp = page.request.post(
        f"/api/method/{method}",
        headers={"X-Frappe-CSRF-Token": csrf(page), "Content-Type": "application/json"},
        data=json.dumps(form),
    )
    return resp


def reserve(page: Page, court: str, booking_date: str, start_time: str, slots: int = 1) -> dict:
    """Reserve through the customer endpoint (Fund Transfer, server-priced)."""
    resp = post(
        page,
        "court_booking_tech.api.portal.reserve_booking",
        {
            "court": court,
            "booking_date": booking_date,
            "start_time": start_time,
            "number_of_slots": slots,
        },
    )
    assert resp.ok, f"reserve_booking: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]


def cancel_own_active(page: Page, court: str, booking_date: str):
    """Cancel any active hold the session user left on this court × date.

    ⚠ REQUIRES A STAFF/ADMIN PAGE, despite the name and despite calling the
    CUSTOMER cancel endpoint. The probe below is `/api/resource/...`, a DocPerm
    surface — and customers hold NO DocPerm on CBT Court Booking (leak vector
    4), so passing a customer page makes the assert fail on a 403 that is the
    system working correctly. For slot cleanup before a portal test, use
    `helpers.board.cancel_active_bookings` with the admin `page` fixture, which
    is what files 05 and 11 do.

    (Written in section-9, never called until section-14 tried to — the 403 is
    recorded here so the next caller does not rediscover it.)
    """
    filters = json.dumps(
        [
            ["court", "=", court],
            ["booking_date", "=", booking_date],
            ["booking_status", "in", ["Reserved", "Confirmed", "Extended"]],
        ]
    )
    resp = page.request.get(
        "/api/resource/CBT Court Booking", params={"filters": filters}
    )
    assert resp.ok, f"slot probe: HTTP {resp.status}"
    for row in resp.json()["data"]:
        # Portal cancel only clears UNPAID reserved holds; anything with a
        # proof is left for the admin cleanup in the test's fixture teardown.
        post(
            page,
            "court_booking_tech.api.portal.cancel_my_booking",
            {"name": row["name"]},
        )


def open_detail(page: Page, booking: str):
    """Navigate to /my-bookings/<name> and wait for the status header to bind."""
    page.goto(f"/my-bookings/{booking}", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function(
        "() => { const el = document.getElementById('cbt-court');"
        " return el && el.textContent.trim().length > 0; }",
        timeout=20000,
    )
