"""
Section-11 file 10 — deep links, suspension, and the company customer ban.

Three enforcement levers proven through the real UI:

- **Deep link** (`/book?c=&b=`) — the URL companies print on their own sites
  (PLAN §5): branded landing when healthy, and a FRIENDLY card when it no
  longer resolves. A 404 would be aimed at the client's customers.
- **Suspension** — the platform's billing lever (PLAN §1): branches vanish from
  the marketplace and every booking path refuses. Driven via the new
  Suspend/Unsuspend button on the CBT Company form, not a raw field write.
- **Customer ban** (section-11) — the company's own block-list, raised from the
  board's booking dialog. Narrow on purpose: online booking at THAT company
  only.

SAFETY: this file mutates SHARED state (AYALA's status) that files 02/05/06/08
depend on. The module fixture restores Active and lifts every Mia ban in
teardown, and each test that suspends also restores inline — a leaked
suspension would fail later files with a confusing "not accepting bookings".
"""
import json
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers import board, portal
from helpers import gestures
from helpers.auth import login_as, portal_login_as
from helpers.worker_routing import auth_state_file, base_url

AYALA = "ayala-courts"
QCSM = "qc-smash"
MIA = "cust.mia@example.com"  # AYALA VIP 20% (section-11 seeds)

BGC_COURT_3 = "AYALA-bgc-court-3"
QCSM_COURT_1 = "QCSM-timog-court-1"

# Dates owned by this file alone — and now ACTUALLY alone (Backlog B16, fixed
# 2026-08-18). BAN_DATE/PORTAL_DATE used to be +60/+61, which file 08 ALSO claims
# while its own docstring said no other file did. The suite only survived that on a
# one-hour undeclared margin — file 08 asserts AYALA-bgc-court-3 available at 10:00
# and this file books that same court at 09:00 — so file 08's `available` assertion
# was one scheduling change away from going red for a reason nobody would find.
# Moved to +75/+76: the re-verified ledger is claimed through +74 (NOT +69/+70, which
# the backlog row suggested — file 16 took those in section-20).
BAN_DATE = (date.today() + timedelta(days=75)).isoformat()
PORTAL_DATE = (date.today() + timedelta(days=76)).isoformat()
# +62 stays. It is the only date here that touches QCSM-timog, which closes SUNDAYS —
# leave it where a run has proven it, and do not give it a sliding twin without
# declaring the slid date too (that is exactly B16's other half).
QCSM_DATE = (date.today() + timedelta(days=62)).isoformat()


def _set_status(api, status: str):
    token = board.api_csrf(api)
    resp = api.put(
        f"/api/resource/CBT Company/{AYALA}",
        headers={"X-Frappe-CSRF-Token": token, "Content-Type": "application/json"},
        data=json.dumps({"status": status}),
    )
    assert resp.ok, f"set {AYALA} -> {status}: HTTP {resp.status} {resp.text()}"


def _lift_all_mia_bans(api):
    token = board.api_csrf(api)
    filters = json.dumps([["customer", "=", MIA], ["status", "=", "Active"]])
    resp = api.get("/api/resource/CBT Customer Ban", params={"filters": filters})
    if not resp.ok:
        return
    for row in resp.json()["data"]:
        api.post(
            "/api/method/court_booking_tech.api.bans.lift_ban",
            headers={"X-Frappe-CSRF-Token": token, "Content-Type": "application/json"},
            data=json.dumps({"name": row["name"]}),
        )


@pytest.fixture(scope="module", autouse=True)
def restore_shared_state(playwright):
    """AYALA must leave this file exactly as it arrived — Active and ban-free.

    Teardown runs even when a test fails mid-suspension; without it the next
    file on this worker's site books against a suspended company and fails for
    a reason that has nothing to do with its own subject.
    """
    api = board.platform_api(playwright)
    try:
        yield
    finally:
        _set_status(api, "Active")
        _lift_all_mia_bans(api)
        api.dispose()


@pytest.mark.e2e
class TestDeepLinkAndSuspension:

    def test_deep_link_lands_on_a_branded_page(self, page: Page):
        """The healthy case: the printed URL resolves to that company's grid."""
        page.goto(
            f"/book?c={AYALA}&b=bgc", wait_until="domcontentloaded", timeout=60000
        )
        expect(page.locator("#cbt-company-name")).to_have_text("Ayala Courts")
        expect(page.locator("#cbt-branch-name")).to_contain_text("BGC")
        expect(page.locator("[data-testid='facility-unavailable']")).to_have_count(0)

    def test_suspend_hides_the_company_and_unsuspend_restores_it(
        self, page: Page, playwright, platform_seat: str
    ):
        """The whole lever in one flow, driven by the form BUTTON: suspend →
        gone from the marketplace, deep link friendly, portal reserve refused;
        unsuspend → bookable again. Pressed by the seeded PLATFORM seat — the
        button and the permlevel-1 `status` write are the role's, not the
        Administrator context's.

        One test rather than four because the suspended window is shared state:
        the shorter it is open, the smaller the blast radius if this file dies.
        """
        api = board.platform_api(playwright)
        try:
            login_as(page, platform_seat)
            # --- suspend via the platform button ---------------------------
            page.goto(
                f"/app/cbt-company/{AYALA}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_function(
                "() => window.cur_frm && cur_frm.doc && cur_frm.doc.name",
                timeout=30000,
            )
            # Platform > Suspend, then Yes in the confirm — the docstring above says
            # this flow is "driven by the form BUTTON", and now it actually is. The
            # previous version set `status` in the model and stubbed frappe.confirm
            # away, so neither the menu item nor the confirm dialog was ever proven
            # reachable; `status` is permlevel-1 and read-only to a tenant admin, so
            # the button IS the only human path to suspension.
            gestures.click_form_action(page, "Suspend", group="Platform")
            gestures.confirm_yes(page)
            page.wait_for_function(
                "() => cur_frm.doc.status === 'Suspended' && !cur_frm.is_dirty()",
                timeout=30000,
            )

            # --- the marketplace no longer lists its branches ----------------
            page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#cbt-results", timeout=20000)
            page.wait_for_function(
                "() => document.querySelectorAll('#cbt-results .cbt-card[data-slug]').length > 0"
                " || document.querySelector('#cbt-results .cbt-empty')",
                timeout=20000,
            )
            slugs = page.eval_on_selector_all(
                "#cbt-results .cbt-card[data-slug]", "els => els.map(e => e.dataset.slug)"
            )
            assert "bgc" not in slugs, slugs
            assert "makati" not in slugs, slugs

            # --- the printed deep link degrades KINDLY, not as a 404 ---------
            page.goto(
                f"/book?c={AYALA}&b=bgc",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            card = page.locator("[data-testid='facility-unavailable']")
            expect(card).to_be_visible()
            expect(card).to_contain_text("isn't taking online bookings")
            # Scoped to the CARD: the portal header also links to /find-court,
            # so an unscoped locator matches two elements and fails strict mode.
            expect(card.locator("a[href='/find-court']")).to_be_visible()

            # --- and the reserve endpoint itself refuses ---------------------
            resp = page.request.post(
                "/api/method/court_booking_tech.api.portal.reserve_booking",
                headers={"X-Frappe-CSRF-Token": board.csrf(page)},
                form={
                    "court": BGC_COURT_3,
                    "booking_date": PORTAL_DATE,
                    "start_time": "09:00:00",
                    "number_of_slots": 1,
                },
            )
            assert not resp.ok, "a suspended company must refuse new bookings"
            assert "not accepting bookings" in resp.text(), resp.text()

            # --- unsuspend ---------------------------------------------------
            _set_status(api, "Active")
            page.goto(
                f"/book?c={AYALA}&b=bgc",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            expect(page.locator("#cbt-company-name")).to_have_text("Ayala Courts")
            page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
        finally:
            _set_status(api, "Active")
            api.dispose()

    def test_unknown_company_shows_the_same_friendly_card(self, page: Page):
        """An unknown slug and a suspended one are deliberately
        indistinguishable — otherwise the page is an oracle for which
        facilities exist."""
        page.goto(
            "/book?c=no-such-company", wait_until="domcontentloaded", timeout=60000
        )
        expect(page.locator("[data-testid='facility-unavailable']")).to_be_visible()


@pytest.mark.e2e
class TestCustomerBan:

    def test_ban_from_the_board_blocks_only_this_company_then_lifts(
        self, page: Page, browser, base_url, playwright
    ):
        """Raise a ban through the board's real dialog, prove it is narrow, and
        lift it — the full company block-list loop."""
        api = board.platform_api(playwright)
        try:
            _lift_all_mia_bans(api)
            board.cancel_active_bookings(page, BGC_COURT_3, BAN_DATE)

            # --- a booking for Mia so the board has a dialog to open ---------
            board.load_board(page, board.BGC_BRANCH, BAN_DATE)
            booking = board.quick_book(
                page, BGC_COURT_3, "09:00:00", "Cash", customer=MIA
            )
            assert booking.startswith("BK-AYALA-"), booking

            # The VIP discount reached the real booking (section-11 wiring).
            detail = page.request.get(
                f"/api/resource/CBT Court Booking/{booking}"
            ).json()["data"]
            assert float(detail["discount_percent"]) == 20.0, detail
            assert float(detail["total_amount"]) == 400.0, detail  # ₱500 − 20%

            # --- ban Mia through the board dialog ----------------------------
            board.slot(page, BGC_COURT_3, "09:00:00").click()
            board.wait_dialog(page)
            page.locator("[data-action='ban-customer']").click()
            board.wait_dialog(page)
            gestures.fill(
                page,
                "reason",
                "E2E: repeated no-show reservations",
                scope=gestures.DIALOG,
            )
            with page.expect_response(
                lambda r: "bans.ban_customer" in r.url, timeout=30000
            ) as resp_info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert resp_info.value.ok, resp_info.value.text()

            # --- Mia can no longer book AYALA online -------------------------
            customer_ctx = browser.new_context(
                base_url=base_url, ignore_https_errors=True,
                viewport={"width": 1400, "height": 960},
            )
            try:
                mia = customer_ctx.new_page()
                portal_login_as(mia, MIA)
                blocked = portal.post(
                    mia,
                    "court_booking_tech.api.portal.reserve_booking",
                    {
                        "court": BGC_COURT_3,
                        "booking_date": PORTAL_DATE,
                        "start_time": "10:00:00",
                        "number_of_slots": 1,
                    },
                )
                assert not blocked.ok, "a banned customer must not book online"
                assert "cannot book at this facility online" in blocked.text(), (
                    blocked.text()
                )

                # ...but a ban is ONE company's decision: QCSM is unaffected.
                elsewhere = portal.post(
                    mia,
                    "court_booking_tech.api.portal.reserve_booking",
                    {
                        "court": QCSM_COURT_1,
                        "booking_date": QCSM_DATE,
                        "start_time": "10:00:00",
                        "number_of_slots": 1,
                    },
                )
                assert elsewhere.ok, f"QCSM must stay bookable: {elsewhere.text()}"
                other_booking = elsewhere.json()["message"]["booking"]
                assert other_booking.startswith("BK-QCSM-"), other_booking

                # --- lift the ban -> AYALA works again -----------------------
                _lift_all_mia_bans(api)
                restored = portal.post(
                    mia,
                    "court_booking_tech.api.portal.reserve_booking",
                    {
                        "court": BGC_COURT_3,
                        "booking_date": PORTAL_DATE,
                        "start_time": "10:00:00",
                        "number_of_slots": 1,
                    },
                )
                assert restored.ok, f"lifting the ban must restore booking: {restored.text()}"
            finally:
                customer_ctx.close()
        finally:
            _lift_all_mia_bans(api)
            api.dispose()
