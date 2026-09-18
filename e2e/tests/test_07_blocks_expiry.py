"""
Section-9 file 07 — blocks, expiry, and the rejection lifecycle, through the
portal, with REAL sweeps and NO wall-clock sleeps.

Timing is made deterministic by testing.age_booking_clocks (backdate a clock
into the past) + the allow_tests-gated run_expiry_sweep — a sleep here would be
a review-rejectable defect (budget + flake, section-9 gotcha). Runs on the
E2EF determinism fixture; Noel is the portal actor (portal_login_as). Staff
actions (block, reject, accept) go through the server as Administrator
(platform scope) — this file proves the PORTAL reflects state and the sweep
fires, not the board UI (that is file 06). Dates +52..+57 are unused elsewhere.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers import portal
from helpers.auth import portal_login_as
from helpers.worker_routing import bench_execute

PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

NOEL = "cust.noel@example.com"
E2EF_COURT = "E2EF-main-court-1"
E2EF_BRANCH = "E2EF-main"

D_COURT_BLOCK = (date.today() + timedelta(days=52)).isoformat()
D_BRANCH_BLOCK = (date.today() + timedelta(days=53)).isoformat()
D_UNPAID = (date.today() + timedelta(days=54)).isoformat()
D_DEADLINE = (date.today() + timedelta(days=55)).isoformat()
D_REGRACE = (date.today() + timedelta(days=56)).isoformat()
D_REJECT = (date.today() + timedelta(days=57)).isoformat()


def _reject(booking: str, reason: str):
    """Staff reject on the PLATFORM seat (platform scope passes the tenancy gate)."""
    bench_execute(
        "court_booking_tech.api.proofs.reject_proofs",
        json.dumps([booking, reason]),
    )


def _accept(booking: str):
    bench_execute("court_booking_tech.api.proofs.accept_proofs", json.dumps([booking]))


def _block(branch: str, block_date: str, start: str, end: str, court: str | None):
    args = [branch, block_date, start, end, "Maintenance"]
    if court:
        args.append(court)
    bench_execute("court_booking_tech.api.bookings.create_block", json.dumps(args))


def _age_base(booking: str, minutes: int = 5):
    """Push the base clock `minutes` into the past (leaves the deadline)."""
    bench_execute(
        "court_booking_tech.testing.age_booking_clocks",
        json.dumps([booking, minutes]),
    )


def _sweep(page: Page):
    """Run the expiry sweep (allow_tests-gated — any authed session on a test
    site, S4 as-built 13). Returns the {"expired": [...]} payload."""
    resp = page.request.post(
        "/api/method/court_booking_tech.tasks.run_expiry_sweep",
        headers={"X-Frappe-CSRF-Token": portal.csrf(page), "Content-Type": "application/json"},
        data="{}",
    )
    assert resp.ok, f"run_expiry_sweep: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]


def _status(page: Page, booking: str) -> str:
    """Read a booking's status through the OWNER-guarded portal API — a
    customer holds no DocPerm on CBT Court Booking, so /api/resource would
    403 (leak vector 4)."""
    detail = page.request.get(
        "/api/method/court_booking_tech.api.portal.get_my_booking_detail",
        params={"name": booking},
    )
    assert detail.ok, f"get_my_booking_detail: HTTP {detail.status} {detail.text()}"
    return detail.json()["message"]["booking_status"]


def _upload(page: Page, booking: str) -> dict:
    resp = page.request.post(
        "/api/method/court_booking_tech.api.proofs.upload_proof",
        headers={"X-Frappe-CSRF-Token": portal.csrf(page)},
        multipart={
            "booking": booking,
            "file": {
                "name": "proof_sample.jpg",
                "mimeType": "image/jpeg",
                "buffer": PROOF_JPG.read_bytes(),
            },
        },
    )
    assert resp.ok, f"upload_proof: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]


def _slot_status(page: Page, court_date: str, start: str) -> str:
    """Load the portal /book grid for E2EF and read one slot's rendered state."""
    page.goto(
        f"/book?c=e2e-fast&b=main&d={court_date}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    locator = page.locator(f".cbt-slot[data-court='{E2EF_COURT}'][data-start='{start}']")
    expect(locator).to_have_count(1, timeout=20000)
    return locator.get_attribute("data-status")


def _slot_face(page: Page, court_date: str, start: str) -> dict:
    """The same cell, read the way a CUSTOMER reads it (Backlog B33).

    `data-status` says "booked" for a paid booking and for an unpaid hold
    alike; the words on the cell are where the two stopped being the same
    thing, so this returns what is actually printed there.
    """
    page.goto(
        f"/book?c=e2e-fast&b=main&d={court_date}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    locator = page.locator(f".cbt-slot[data-court='{E2EF_COURT}'][data-start='{start}']")
    expect(locator).to_have_count(1, timeout=20000)
    return locator.evaluate(
        """el => ({
            status: el.dataset.status,
            rate: ((el.querySelector('.cbt-slot-rate') || {}).textContent || '').trim(),
            word: ((el.querySelector('.cbt-slot-state') || {}).textContent || '').trim(),
            hold: ((el.querySelector('.cbt-slot-hold') || {}).textContent || '').trim(),
        })"""
    )


@pytest.fixture(scope="class")
def noel_page(browser, base_url, bench_health):
    context = browser.new_context(
        base_url=base_url, ignore_https_errors=True,
        viewport={"width": 1400, "height": 960},
    )
    page = context.new_page()
    portal_login_as(page, NOEL)
    yield page
    context.close()


@pytest.mark.e2e
class TestBlocksExpiry:

    def test_court_block_shows_blocked_on_the_portal_grid(self, noel_page: Page):
        _block(E2EF_BRANCH, D_COURT_BLOCK, "08:00:00", "09:00:00", E2EF_COURT)
        assert _slot_status(noel_page, D_COURT_BLOCK, "08:00:00") == "blocked"
        # A neighbouring slot stays bookable — the block is court+window scoped.
        assert _slot_status(noel_page, D_COURT_BLOCK, "10:00:00") == "available"

    def test_whole_branch_block_closes_every_court(self, noel_page: Page):
        # Empty court = whole-branch closure (PLAN §8d).
        _block(E2EF_BRANCH, D_BRANCH_BLOCK, "08:00:00", "09:00:00", None)
        assert _slot_status(noel_page, D_BRANCH_BLOCK, "08:00:00") == "blocked"

    def test_unpaid_reservation_expires_and_frees_the_slot(self, noel_page: Page):
        result = portal.reserve(noel_page, E2EF_COURT, D_UNPAID, "08:00:00")
        booking = result["booking"]

        # Backlog B33: while the hold is LIVE the cell says so and counts down.
        # A hold is not a sale — reading "Booked" on it is what makes a
        # customer stop watching a slot that is about to come back. Asserted by
        # pattern, never by value: the clock is running while this reads it.
        held = _slot_face(noel_page, D_UNPAID, "08:00:00")
        assert held["status"] == "booked", held
        assert held["word"] == "Reserved", held
        assert re.match(r"^free in \d{1,2}:\d{2}$", held["hold"]), held
        # ...and it is still priced. The user's rule is "always".
        assert held["rate"].startswith("₱"), held

        _age_base(booking, 5)
        assert booking in _sweep(noel_page)["expired"]
        # /my-bookings shows Expired ...
        noel_page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
        card = noel_page.locator(f".cbt-card[data-booking='{booking}']")
        expect(card).to_have_attribute("data-status", "Expired", timeout=20000)
        # ... and the slot is bookable again on the grid.
        assert _slot_status(noel_page, D_UNPAID, "08:00:00") == "available"

    def test_proof_hold_survives_a_base_clock_lapse(self, noel_page: Page):
        result = portal.reserve(noel_page, E2EF_COURT, D_DEADLINE, "08:00:00")
        booking = result["booking"]
        _upload(noel_page, booking)  # sets the verification deadline (future)
        _age_base(booking, 5)  # base clock lapses, deadline does NOT
        assert booking not in _sweep(noel_page)["expired"]
        detail = noel_page.request.get(
            "/api/method/court_booking_tech.api.portal.get_my_booking_detail",
            params={"name": booking},
        ).json()["message"]
        assert detail["booking_status"] == "Reserved", detail
        assert detail["has_pending_proof"], detail

    def test_recoverable_rejection_grants_reupload_then_confirms(self, noel_page: Page):
        result = portal.reserve(noel_page, E2EF_COURT, D_REGRACE, "08:00:00")
        booking = result["booking"]
        _upload(noel_page, booking)
        _reject(booking, "Unreadable")  # rejection #1 -> regrace window

        portal.open_detail(noel_page, booking)
        expect(noel_page.locator("#cbt-reject-note")).to_be_visible(timeout=20000)
        expect(noel_page.locator("#cbt-reject-note")).to_contain_text("Unreadable")
        # Re-upload is offered (Reserved, regrace).
        expect(noel_page.locator("#cbt-upload-zone")).to_be_visible()

        _upload(noel_page, booking)  # the clearer proof
        _accept(booking)
        detail = noel_page.request.get(
            "/api/method/court_booking_tech.api.portal.get_my_booking_detail",
            params={"name": booking},
        ).json()["message"]
        assert detail["booking_status"] == "Confirmed", detail

        # Backlog B33: the paid half of the same distinction. A Confirmed
        # booking reads "Booked" and carries no countdown — nothing is going to
        # free this slot.
        sold = _slot_face(noel_page, D_REGRACE, "08:00:00")
        assert sold["status"] == "booked", sold
        assert sold["word"] == "Booked", sold
        assert sold["hold"] == "", sold
        assert sold["rate"].startswith("₱"), sold

    def test_fatal_and_second_rejections_both_expire(self, noel_page: Page):
        # Fatal reason expires immediately.
        fake = portal.reserve(noel_page, E2EF_COURT, D_REJECT, "08:00:00")["booking"]
        _upload(noel_page, fake)
        _reject(fake, "Invalid / suspected fake")
        assert _status(noel_page, fake) == "Expired"

        # A recoverable first rejection, then a SECOND of any kind, expires.
        twice = portal.reserve(noel_page, E2EF_COURT, D_REJECT, "10:00:00")["booking"]
        _upload(noel_page, twice)
        _reject(twice, "Unreadable")  # #1 -> regrace
        _upload(noel_page, twice)  # re-upload within grace
        _reject(twice, "Unreadable")  # #2 -> expire
        assert _status(noel_page, twice) == "Expired"
