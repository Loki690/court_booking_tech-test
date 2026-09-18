"""
Section-5 pull-forward — payment-proof API surface over real HTTP.

User rule: every change ships with E2E. Section-9 files 05/07 keep the
portal-UI flows and the real expiry-timing scenarios; this file covers only
the section-5 surface end-to-end: multipart upload through the whitelisted
endpoint (CSRF + private File wiring + deadline stamp), reject-with-reason,
and the private-file guest gate. No sleeps — timing exactness lives in the
backend battery's monkeypatched clock.

Determinism / re-runnability: bookings are created via the REST resource API
(the desk-form pipeline is covered by test_booking_form.py) on E2EF slots and
dates no other E2E file uses (+32/+33/+34 days vs +30/+31); each test cancels
any active booking left on its slot by a previous non-reset run.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page

PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

E2EF_COURT = "E2EF-main-court-1"

UPLOAD_DATE = (date.today() + timedelta(days=32)).isoformat()
REJECT_DATE = (date.today() + timedelta(days=33)).isoformat()
GUEST_DATE = (date.today() + timedelta(days=34)).isoformat()


def _csrf(page: Page) -> str:
    # API-only tests never navigate on their own — frappe (and its csrf
    # token) only exists once a desk page has loaded in this tab.
    if "/app" not in page.url:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => window.frappe && frappe.csrf_token", timeout=15000)
    return page.evaluate("frappe.csrf_token")


def _get_booking(page: Page, name: str) -> dict:
    resp = page.request.get(f"/api/resource/CBT Court Booking/{name}")
    assert resp.ok, f"read {name}: HTTP {resp.status}"
    return resp.json()["data"]


def _cancel_existing(page: Page, court: str, booking_date: str, start_time: str):
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
            form={"name": row["name"]},
        )
        assert cancel.ok, f"cancel {row['name']}: HTTP {cancel.status}"


def _create_ft_booking(page: Page, booking_date: str, start_time: str) -> str:
    _cancel_existing(page, E2EF_COURT, booking_date, start_time)
    resp = page.request.post(
        "/api/resource/CBT Court Booking",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
        data={
            "court": E2EF_COURT,
            "customer": "cust.carla@example.com",
            "booking_date": booking_date,
            "start_time": start_time,
            "number_of_slots": 1,
            "payment_method": "Fund Transfer",
        },
    )
    assert resp.ok, f"create booking: HTTP {resp.status} {resp.text()}"
    booking = resp.json()["data"]
    assert booking["booking_status"] == "Reserved", booking
    return booking["name"]


def _upload_proof(page: Page, booking: str) -> dict:
    resp = page.request.post(
        "/api/method/court_booking_tech.api.proofs.upload_proof",
        headers={"X-Frappe-CSRF-Token": _csrf(page)},
        multipart={
            "booking": booking,
            "reference_no": "E2E-REF-42",
            "file": {
                "name": "proof_sample.jpg",
                "mimeType": "image/jpeg",
                "buffer": PROOF_JPG.read_bytes(),
            },
        },
    )
    assert resp.ok, f"upload_proof: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]


@pytest.mark.e2e
class TestProofFlow:

    def test_upload_stamps_deadline_and_survives_sweep(self, page: Page):
        """Multipart upload through the real endpoint: proof row (source
        Staff — the desk seat is not the customer), private file, deadline
        stamped; the sweep leaves the verification-clock hold alone."""
        name = _create_ft_booking(page, UPLOAD_DATE, "08:00:00")
        result = _upload_proof(page, name)

        # Deadline: non-null and in the future ONLY — E2EF office hours are
        # weekdays-only, so a weekend run walks to Monday (exact values are
        # the backend battery's job).
        assert result["verification_deadline_at"], result
        booking = _get_booking(page, name)
        assert booking["verification_deadline_at"], booking
        assert booking["verification_deadline_at"] > booking["creation"], booking

        proof = page.request.get(
            f"/api/resource/CBT Payment Proof/{result['proof']}"
        ).json()["data"]
        assert proof["status"] == "Pending", proof
        assert proof["source"] == "Staff", proof
        assert proof["file"].startswith("/private/files/"), proof

        sweep = page.request.post(
            "/api/method/court_booking_tech.tasks.run_expiry_sweep",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
        )
        assert sweep.ok, f"run_expiry_sweep: HTTP {sweep.status}"
        assert name not in sweep.json()["message"]["expired"]
        assert _get_booking(page, name)["booking_status"] == "Reserved"

    def test_reject_fake_expires_booking(self, page: Page):
        """Reject with the fatal reason: booking Expired now, proof Rejected
        with the reason recorded, rejection_count incremented."""
        name = _create_ft_booking(page, REJECT_DATE, "09:00:00")
        result = _upload_proof(page, name)

        resp = page.request.post(
            "/api/method/court_booking_tech.api.proofs.reject_proofs",
            headers={"X-Frappe-CSRF-Token": _csrf(page)},
            form={"booking": name, "reason": "Invalid / suspected fake"},
        )
        assert resp.ok, f"reject_proofs: HTTP {resp.status} {resp.text()}"
        assert resp.json()["message"]["outcome"] == "Expired"

        booking = _get_booking(page, name)
        assert booking["booking_status"] == "Expired", booking
        assert booking["rejection_count"] == 1, booking

        proof = page.request.get(
            f"/api/resource/CBT Payment Proof/{result['proof']}"
        ).json()["data"]
        assert proof["status"] == "Rejected", proof
        assert proof["rejection_reason"] == "Invalid / suspected fake", proof

    def test_private_proof_file_blocked_for_guests(self, page: Page, browser, base_url):
        """Section-5 gotcha: a proof image must NEVER be fetchable by URL as
        Guest — expect a redirect/denial, and never the JPEG bytes."""
        name = _create_ft_booking(page, GUEST_DATE, "10:00:00")
        result = _upload_proof(page, name)
        file_url = page.request.get(
            f"/api/resource/CBT Payment Proof/{result['proof']}"
        ).json()["data"]["file"]

        # Authenticated session CAN read it (sanity for the negative below).
        authed = page.request.get(file_url)
        assert authed.ok, f"authed fetch: HTTP {authed.status}"

        guest = browser.new_context(base_url=base_url)
        try:
            resp = guest.request.get(file_url, max_redirects=0)
            assert resp.status in (301, 302, 303, 307, 308, 401, 403), (
                f"guest fetch of {file_url}: HTTP {resp.status}"
            )
            assert not resp.body().startswith(b"\xff\xd8"), "guest got JPEG bytes"
        finally:
            guest.close()
