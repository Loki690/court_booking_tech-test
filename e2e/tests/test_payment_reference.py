"""
E2E — the payment reference staff type at verification (Backlog B40).

The user's ruling, verbatim (2026-09-04): *"Reference# is optional during
confirm, WARN if there is exact record but do not stop them from recording
okay."* So this file proves three things a human can see: staff can TYPE a
reference the customer never gave, a second booking carrying the SAME reference
raises a visible warning, and that warning does not stop the confirmation.

DRIVEN AS AYALA STAFF, never Administrator: `require_company_access` is a no-op
under platform scope, so an Administrator run would pass even if every real
staff seat were refused (ducky STOP, 2026-09-05).

Console, `pageerror` and non-2xx /api bodies are captured to
`e2e/screenshots/b40_reference.log` and asserted empty — a screenshot-and-URL
failure hook is blind to a 417 whose message is in `_server_messages`.

Date ledger: this file claims **+84**.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import login_as
from helpers.board import (
    BGC_BRANCH,
    cancel_active_bookings,
    csrf,
    load_board,
    quick_book,
    slot,
    wait_dialog,
)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"
PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

STELLA = "staff.ayala@example.com"
BGC_COURT = "AYALA-bgc-court-1"
D_REF = (date.today() + timedelta(days=84)).isoformat()
REFERENCE = "GC-B40-88112"


# Harness noise, not application errors: the multisite lane runs three sites
# behind ONE socketio server, so the two clones log "Invalid namespace" on every
# page. They are still WRITTEN to the log — the record stays complete — they just
# do not fail the row. Anything else does.
#
# ⚠ THE SAME EVENT ARRIVES IN THREE MESSAGE SHAPES, and the first version of this
# tuple only caught two. socket.io's polling transport also fails, and Chrome
# reports THAT as a bare "Failed to load resource: … 400", which carries no URL
# and matched neither entry. The row was therefore clean only when `--dist
# loadfile` happened to put it on the PRIMARY site; it went red on a clone
# (2026-09-05). Widening the tuple costs this row nothing it relied on: every
# non-2xx **/api/** response is recorded separately in `Recorder.failed` and
# asserted on its own line, so a real application 400 still fails here — and
# `_response` now logs non-/api/ failures too, so the next person can see which
# URL it was instead of inferring it.
INFRA_CONSOLE_NOISE = (
    "socket.io",
    "WebSocket connection to",
    "Failed to load resource",
)


class Recorder:
    """Console + pageerror + non-2xx /api BODIES. A frappe throw lands as 417
    with its text inside `_server_messages`, which no screenshot shows."""

    def __init__(self, page: Page, name: str):
        self.errors, self.failed = [], []
        self.lines = []
        self.path = SCREENSHOT_DIR / f"{name}.log"
        page.on("console", self._console)
        page.on("pageerror", self._pageerror)
        page.on("response", self._response)

    def _console(self, message):
        self.lines.append(f"[console:{message.type}] {message.text}")
        if message.type == "error" and not any(
            noise in message.text for noise in INFRA_CONSOLE_NOISE
        ):
            self.errors.append(message.text)

    def _pageerror(self, error):
        self.lines.append(f"[pageerror] {error}")
        self.errors.append(str(error))

    def _response(self, response):
        if response.status < 400:
            return
        # Everything non-2xx is LOGGED, so the record names the URL rather than
        # leaving a bare "Failed to load resource" to be guessed at. Only /api/
        # failures are ASSERTED — the rest is harness plumbing (socket.io's
        # polling transport on a clone site), see INFRA_CONSOLE_NOISE.
        try:
            body = response.text()[:600]
        except Exception:
            body = "<unreadable>"
        entry = f"[{response.status}] {response.url}\n{body}"
        self.lines.append(entry)
        if "/api/" in response.url:
            self.failed.append(entry)

    def dump(self):
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        self.path.write_text("\n".join(self.lines), encoding="utf-8")

    def assert_clean(self):
        self.dump()
        assert not self.errors, f"console/page errors:\n" + "\n".join(self.errors)
        assert not self.failed, f"failed /api calls:\n" + "\n".join(self.failed)


def _reference_of(page: Page, booking: str) -> str | None:
    rows = page.request.get(
        "/api/resource/CBT Payment Proof",
        params={
            "filters": json.dumps([["booking", "=", booking]]),
            "fields": json.dumps(["reference_no", "reference_set_by"]),
        },
    )
    assert rows.ok, f"proof read: HTTP {rows.status} {rows.text()}"
    data = rows.json()["data"]
    return data[0] if data else None


def _upload_with_reference(page: Page, reference: str):
    """The desk's own 'sent it via Messenger' path — type the reference, then
    pick the file. Before B40 this posted no reference at all."""
    page.locator(".modal.show [data-fieldname='reference_no'] input").fill(reference)
    page.locator(".modal.show [data-action='staff-upload']").click()
    page.locator(".modal.show .cbt-staff-proof-file").set_input_files(str(PROOF_JPG))
    # The dialog closes, the board reloads and the dialog is reopened by the
    # page itself — wait for the proof card to exist in the NEW dialog.
    page.wait_for_selector(".modal.show .cbt-proof-card", timeout=30000)


@pytest.mark.e2e
class TestPaymentReference:
    def test_staff_type_a_reference_and_are_warned_when_it_repeats(self, page: Page):
        recorder = Recorder(page, "b40_reference")
        try:
            cancel_active_bookings(page, BGC_COURT, D_REF)
            login_as(page, STELLA)
            load_board(page, BGC_BRANCH, D_REF)

            first = quick_book(page, BGC_COURT, "10:00:00", "Fund Transfer")
            second = quick_book(page, BGC_COURT, "11:00:00", "Fund Transfer")

            # --- booking one: staff type a reference the customer never gave --
            slot(page, BGC_COURT, "10:00:00").click()
            wait_dialog(page)
            _upload_with_reference(page, REFERENCE)
            stored = _reference_of(page, first)
            assert stored and stored["reference_no"] == REFERENCE, stored
            assert stored["reference_set_by"] in (None, ""), (
                "an uploader's own reference must not be stamped as a staff correction"
            )

            # --- booking two: the SAME receipt, and the warning that follows ---
            # A fresh board load rather than closing the modal: the upload path
            # REOPENS the dialog itself, so `cur_dialog` is a moving target and
            # hiding the stale one leaves the new one on screen.
            load_board(page, BGC_BRANCH, D_REF)
            slot(page, BGC_COURT, "11:00:00").click()
            wait_dialog(page)
            _upload_with_reference(page, REFERENCE)

            # Re-typing the same reference fires the duplicate check on blur.
            field = page.locator(".modal.show [data-fieldname='reference_no'] input")
            field.fill(REFERENCE)
            field.blur()
            warning = page.locator(
                ".modal.show [data-testid='reference-duplicate-warning']"
            )
            expect(warning).to_be_visible(timeout=20000)
            assert first in warning.inner_text(), (
                f"the warning does not name the booking the receipt is already on:"
                f" {warning.inner_text()}"
            )
            SCREENSHOT_DIR.mkdir(exist_ok=True)
            page.screenshot(
                path=str(SCREENSHOT_DIR / "b40_duplicate_warning.png"), full_page=True
            )

            # --- the ruling: WARN, never block -------------------------------
            wait_dialog(page)
            with page.expect_response(
                lambda r: "accept_proofs" in r.url, timeout=30000
            ) as info:
                page.locator(".modal.show .btn-modal-primary").first.click()
            assert info.value.ok, info.value.text()
            page.wait_for_selector(".modal.show", state="detached", timeout=20000)

            row = page.request.get(f"/api/resource/CBT Court Booking/{second}")
            assert row.ok, row.text()
            assert row.json()["data"]["booking_status"] == "Confirmed", (
                "the duplicate BLOCKED the confirmation — the ruling says warn only"
            )
            # And the reference really persisted on the accepted proof.
            kept = _reference_of(page, second)
            assert kept and kept["reference_no"] == REFERENCE, kept
        finally:
            recorder.assert_clean()
            cancel_active_bookings(page, BGC_COURT, D_REF)
