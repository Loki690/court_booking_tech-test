"""
Section-9 file 05 — portal booking → proof → verify → PAID billing.

The customer-facing money path, end to end through the real UI:
- the marketplace home rendered nearest-first for a pinned customer (the
  page-level ordering assertion promised in section-8);
- the deep-linked, branded /book grid;
- reserve two consecutive slots → confirmation + live countdown;
- upload a proof → the exact verification deadline + office-hours notice;
- staff (Stella) accepts the PORTAL-created hold from the board pending panel;
- the customer's booking flips Confirmed and its billing statement shows
  PAID & VERIFIED, with the VAT-inclusive total matching the server quote;
- /my-bookings lists it in cross-company card style.

Runs on AYALA/BGC (30-min base clock — E2EF's 1-minute expiry would race a
multi-step UI test). Pia is the seeded customer_page fixture; her BGC-adjacent
pin is the load-bearing ordering origin (seeds force-restore it). Dates +50/+51
are used by no other E2E file. No wall-clock sleeps.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers import board, portal
from helpers.auth import PLATFORM_STATE, login_as, portal_login_as
from helpers.worker_routing import auth_state_file

PROOF_JPG = (
    Path(__file__).resolve().parents[2]
    / "court_booking_tech"
    / "seeds"
    / "files"
    / "proof_sample.jpg"
)

STELLA = "staff.ayala@example.com"  # AYALA staff
MIA = "cust.mia@example.com"  # AYALA VIP 20% (section-11 seeds)
BGC_COURT = "AYALA-bgc-court-1"

BOOK_DATE = (date.today() + timedelta(days=50)).isoformat()
MEMBER_DATE = (date.today() + timedelta(days=51)).isoformat()


def _admin_cancel(page: Page, court: str, booking_date: str):
    """Best-effort: release any active hold left on the slot by an earlier
    non-reset run (the money-path test confirms bookings, which the portal
    cannot self-cancel — this keeps the file re-runnable)."""
    filters = json.dumps(
        [
            ["court", "=", court],
            ["booking_date", "=", booking_date],
            ["booking_status", "in", ["Reserved", "Confirmed", "Extended"]],
        ]
    )
    resp = page.request.get("/api/resource/CBT Court Booking", params={"filters": filters})
    if not resp.ok:
        return
    for row in resp.json()["data"]:
        page.request.post(
            "/api/method/court_booking_tech.api.bookings.cancel_booking",
            headers={"X-Frappe-CSRF-Token": board.csrf(page), "Content-Type": "application/json"},
            data=json.dumps({"name": row["name"]}),
        )


@pytest.mark.e2e
class TestPortalBooking:

    def test_home_is_nearest_first_for_a_pinned_customer(self, customer_page: Page):
        """The page-level nearest-first assertion (promised in section-8): from
        Pia's saved BGC pin the cards render bgc, makati, timog, then annex
        (pin-less) last."""
        customer_page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        # Cards carry data-slug in DOM order — the E2E ordering hook.
        customer_page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=20000)
        slugs = customer_page.eval_on_selector_all(
            "#cbt-results .cbt-card[data-slug]",
            "els => els.map(e => e.dataset.slug)",
        )
        # Pinned branches lead, ordered by distance from Pia's BGC pin; every
        # pin-less branch (annex, and the E2EF fixture's "main") sorts after
        # them. (The geo-API test asserts the exact 4-branch order over the
        # SEEDED_SLUGS filter; the PAGE also lists the pin-less E2EF branch, so
        # here we assert the relative ordering that survives it.)
        assert slugs.index("bgc") < slugs.index("makati") < slugs.index("timog"), slugs
        assert slugs.index("timog") < slugs.index("annex"), slugs

    def test_deep_link_lands_on_a_branded_grid(self, customer_page: Page):
        """/book?c=&b= is the URL companies print on their own sites (PLAN §5):
        branded header + a real availability grid."""
        customer_page.goto(
            f"/book?c=ayala-courts&b=bgc&d={BOOK_DATE}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        expect(customer_page.locator("#cbt-company-name")).to_have_text("Ayala Courts")
        expect(customer_page.locator("#cbt-branch-name")).to_contain_text("BGC")
        # The grid actually rendered bookable slots.
        customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)

    def test_date_strip_labels_match_their_dates(self, page: Page):
        """Section-12 UAT regression: the /book date strip parsed server-today
        as browser-LOCAL midnight and read chip dates back via toISOString(),
        landing every dataset.date one day behind its rendered label in any
        UTC+ timezone — a Manila customer tapping "Fri 31" booked Thu 30.
        Pins the label<->date contract (int-compare: iso says "05", the label
        says "5" — never build a Date object here, that IS the bug class) and
        anchors chip[0] to the SERVER's today.

        NOTE: the server_now anchor can only go red if the run crosses
        midnight between the API probe and the page render — a rare,
        self-explaining failure; root-cause it, never blind-rerun.
        """
        resp = page.request.get(
            "/api/method/court_booking_tech.slots.get_public_availability",
            params={"branch": "AYALA-bgc", "date": BOOK_DATE},
        )
        assert resp.ok, f"availability probe: HTTP {resp.status}"
        server_today = str(resp.json()["message"]["server_now"])[:10]

        page.goto(
            "/book?c=ayala-courts&b=bgc", wait_until="domcontentloaded", timeout=60000
        )
        page.wait_for_selector(".cbt-date[data-date]", timeout=20000)
        chips = page.eval_on_selector_all(
            ".cbt-date[data-date]",
            """els => els.map(e => ({
                date: e.dataset.date,
                day: (e.querySelector('strong') || {}).textContent,
                pressed: e.getAttribute('aria-pressed'),
            }))""",
        )
        assert len(chips) == 7, chips
        for chip in chips:
            assert int(chip["day"]) == int(chip["date"].split("-")[2]), chips
        assert [c["pressed"] for c in chips].count("true") == 1, chips
        assert chips[0]["date"] == server_today, (chips[0], server_today)

    def test_date_chip_click_moves_the_highlight(self, page: Page):
        """The selected-date highlight must follow the tapped chip.

        Regression: buildDates() created each chip as `var button` inside the
        loop, so all 7 click handlers closed over ONE function-scoped binding —
        the last chip built. Every tap painted chip 7 while state.date (read off
        `this`) was correct, so the availability request looked right and only
        the UI lied. Reported from /book?c=ayala-courts&b=makati as "the black
        background always stays on 4".

        Deliberately a GUEST page (that is how it was found) and deliberately
        clicks TWICE — one click can accidentally pass if the tapped chip
        happens to be the last one.
        """
        page.goto(
            "/book?c=ayala-courts&b=bgc", wait_until="domcontentloaded", timeout=60000
        )
        chips = page.locator(".cbt-date[data-date]")
        expect(chips).to_have_count(7, timeout=20000)

        for index in (3, 1):  # neither is the last chip — the old bug's blind spot
            chip = chips.nth(index)
            wanted = chip.get_attribute("data-date")
            chip.click()
            expect(chip).to_have_attribute("aria-pressed", "true", timeout=10000)
            pressed = page.eval_on_selector_all(
                ".cbt-date[data-date]",
                """els => els.filter(e => e.getAttribute('aria-pressed') === 'true')
                             .map(e => e.dataset.date)""",
            )
            assert pressed == [wanted], (index, wanted, pressed)

    def test_date_strip_pages_past_the_first_week(self, page: Page):
        """Advance booking: the strip is a WINDOW over the company's horizon,
        not a 7-day wall. Reported as "there is no future date you can book?" —
        the server always accepted those dates, only the strip stopped at 7.

        Asserts the contract the booking controller enforces: page forward and
        a date beyond the first week is selectable and loads real slots; the
        date field is bounded by today .. today + advance_booking_days.
        """
        page.goto(
            "/book?c=ayala-courts&b=bgc", wait_until="domcontentloaded", timeout=60000
        )
        chips = page.locator(".cbt-date[data-date]")
        expect(chips).to_have_count(7, timeout=20000)
        first_page = chips.first.get_attribute("data-date")

        # The jump field is clamped to the horizon the server will honour.
        jump = page.locator("#cbt-date-jump")
        horizon = (
            date.fromisoformat(jump.get_attribute("max"))
            - date.fromisoformat(jump.get_attribute("min"))
        ).days
        assert horizon >= 14, f"horizon too small to book weeks ahead: {horizon}"

        page.click("#cbt-date-next")
        expect(chips.first).not_to_have_attribute("data-date", first_page, timeout=10000)
        second_page = chips.first.get_attribute("data-date")
        assert (
            date.fromisoformat(second_page) - date.fromisoformat(first_page)
        ).days == 7, (first_page, second_page)

        # The first date past the old 7-day wall is genuinely bookable, not
        # just rendered: it paints AND its availability grid loads.
        chips.first.click()
        expect(chips.first).to_have_attribute("aria-pressed", "true", timeout=10000)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)

    def test_reserve_beyond_the_horizon_is_refused(self, customer_page: Page):
        """The strip stops at the horizon, but the strip is client code: a
        printed ?d= link, a saved URL or a hand-rolled POST must still lose.
        Uses today+200 — far past any seeded company's window and outside the
        E2E date ledger (today+30 .. today+62)."""
        too_far = (date.today() + timedelta(days=200)).isoformat()
        resp = portal.post(
            customer_page,
            "court_booking_tech.api.portal.reserve_booking",
            {
                "court": BGC_COURT,
                "booking_date": too_far,
                "start_time": "10:00:00",
                "number_of_slots": 1,
            },
        )
        assert not resp.ok, f"a booking {too_far} must not be accepted"
        assert "days ahead" in resp.text(), resp.text()

    def test_reserve_upload_verify_billing(
        self, customer_page: Page, browser, base_url
    ):
        """The whole money path in one self-contained flow."""
        # Clean the slot as admin (confirmed bookings survive portal cancel).
        admin = browser.new_context(
            base_url=base_url,
            storage_state=str(auth_state_file(PLATFORM_STATE)),
            ignore_https_errors=True,
        )
        try:
            _admin_cancel(admin.new_page(), BGC_COURT, BOOK_DATE)
        finally:
            admin.close()

        # --- reserve two consecutive slots via the portal API (server-priced) --
        result = portal.reserve(customer_page, BGC_COURT, BOOK_DATE, "08:00:00", slots=2)
        booking = result["booking"]
        assert booking.startswith("BK-AYALA-"), result
        assert result["total_amount"] == 800  # 2 × ₱400 BGC court-1

        # --- confirmation screen shows a live countdown (server-clock driven) --
        portal.open_detail(customer_page, booking)
        expect(customer_page.locator("#cbt-status")).to_have_text("Reserved")
        expect(customer_page.locator("#cbt-clock-box")).to_be_visible()
        # 2026-09-04 (B31 ruling): the booking page shows the chosen channel's
        # own note and NOT the company text on top of it (AYALA GCash carries a
        # seeded note; the default channel of a Fund Transfer hold is GCash).
        expect(customer_page.locator("#cbt-pay-channel .cbt-channel-note")).to_contain_text(
            "reference number"
        )
        expect(customer_page.locator("#cbt-instructions")).to_have_text("")

        # --- upload a proof -> exact deadline + office-hours notice ------------
        customer_page.set_input_files("#cbt-file", str(PROOF_JPG))
        customer_page.click("#cbt-upload")
        deadline_note = customer_page.locator("#cbt-deadline-note")
        expect(deadline_note).to_be_visible(timeout=20000)
        expect(deadline_note).to_contain_text("verified by")
        # AYALA verifies Mon–Fri — the office-hours sentence must render.
        expect(deadline_note).to_contain_text("Mon")

        # --- staff (Stella) accepts the PORTAL hold from the board panel ------
        staff = browser.new_context(
            base_url=base_url, ignore_https_errors=True,
            viewport={"width": 1400, "height": 960},
        )
        try:
            staff_page = staff.new_page()
            login_as(staff_page, STELLA)
            board.goto_pending_panel(staff_page)
            board.accept_from_pending_panel(staff_page, booking)
        finally:
            staff.close()

        # --- the customer sees Confirmed + a PAID & VERIFIED statement --------
        portal.open_detail(customer_page, booking)
        expect(customer_page.locator("#cbt-status")).to_have_text("Confirmed", timeout=20000)

        # The guarded /billing-statement page renders the SHIPPED print format.
        customer_page.goto(
            f"/billing-statement?booking={booking}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        statement = customer_page.locator(".cbt-statement")
        expect(statement).to_contain_text("PAID", timeout=20000)
        expect(statement).to_contain_text("VERIFIED")

        # VAT-inclusive total matches the server quote (₱800 = 2 × ₱400).
        quote = customer_page.request.get(
            "/api/method/court_booking_tech.api.portal.get_quote",
            params={"court": BGC_COURT, "number_of_slots": 2},
        ).json()["message"]
        assert quote["total_amount"] == 800
        assert quote["vat_mode"] == "VAT"
        expect(statement).to_contain_text("800")

        # --- /my-bookings lists it cross-company style ------------------------
        customer_page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
        card = customer_page.locator(f".cbt-card[data-booking='{booking}']")
        expect(card).to_be_visible(timeout=20000)
        expect(card).to_contain_text("Ayala Courts")
        expect(card).to_contain_text("BGC")

    def test_member_sees_the_discount_at_checkout_and_on_the_booking(
        self, browser, base_url
    ):
        """Section-11: Mia is an AYALA VIP (20%). The checkout must NAME the
        saving — a total quietly lower than rate × hours reads as a pricing
        bug — and the booking it creates must charge exactly that.

        Mia, not Pia: Pia's undiscounted ₱800 is pinned by the money-path test
        above, which is precisely why the membership cast avoids her.
        """
        context = browser.new_context(
            base_url=base_url,
            ignore_https_errors=True,
            viewport={"width": 1400, "height": 960},
        )
        try:
            mia = context.new_page()
            portal_login_as(mia, MIA)

            # Free the slot BEFORE the grid renders, so a leftover hold from an
            # earlier non-reset run cannot make it unclickable. Cleanup runs as
            # ADMIN, never as Mia: customers hold no DocPerm on CBT Court
            # Booking (leak vector 4), so a customer-side list probe correctly
            # 403s — the portal only ever sees its own whitelisted APIs.
            admin = browser.new_context(
                base_url=base_url,
                storage_state=str(auth_state_file(PLATFORM_STATE)),
                ignore_https_errors=True,
            )
            try:
                _admin_cancel(admin.new_page(), BGC_COURT, MEMBER_DATE)
            finally:
                admin.close()

            # The server quote is the single source of money (the page renders
            # it, it never derives it).
            quote = mia.request.get(
                "/api/method/court_booking_tech.api.portal.get_quote",
                params={"court": BGC_COURT, "number_of_slots": 1},
            ).json()["message"]
            assert quote["subtotal"] == 400
            assert quote["discount_percent"] == 20
            assert quote["total_amount"] == 320  # ₱400 − 20%

            # The checkout modal shows the saving as its own line.
            mia.goto(
                f"/book?c=ayala-courts&b=bgc&d={MEMBER_DATE}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            mia.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
            mia.locator(
                f".cbt-slot[data-court='{BGC_COURT}'][data-start='16:00:00']"
            ).click()
            mia.locator("#cbt-review").click()
            # B35: the discount is per BOOKING now, and an id cannot repeat
            # across a cart's items — so the hook is a testid.
            discount_line = mia.locator("[data-testid='member-discount']")
            expect(discount_line).to_be_visible(timeout=15000)
            expect(discount_line).to_contain_text("20")
            expect(mia.locator("#cbt-quote-lines")).to_contain_text("320")
            # Section-12 UAT regression: the "How to pay" box renders at
            # checkout (it had NEVER rendered before the seeds carried any).
            # 2026-09-04 (user ruling, B31): a channel that carries its OWN note
            # is the instruction, and the company-level text yields to it —
            # AYALA's GCash note is seeded, so the company text must be EMPTY
            # here. The fallback (a channel without a note → company text
            # shown) is pinned on the e2e-fast rig in test_mobile_portal.py.
            expect(mia.locator("#cbt-instructions-box")).to_be_visible()
            expect(mia.locator("#cbt-channels .cbt-channel-label").first).to_contain_text("GCash")
            expect(mia.locator("#cbt-channels .cbt-channel-meta").first).to_contain_text("0917-000-1111")
            expect(mia.locator("#cbt-channels .cbt-channel-note").first).to_contain_text("reference number")
            expect(mia.locator("#cbt-instructions")).to_have_text("")

            # And the booking actually created charges the member price.
            result = portal.reserve(mia, BGC_COURT, MEMBER_DATE, "16:00:00", slots=1)
            assert result["total_amount"] == 320, result

            # /my-profile lists the perk — active at AYALA, expired at QCSM.
            mia.goto("/my-profile", wait_until="domcontentloaded", timeout=60000)
            memberships = mia.locator("[data-testid='memberships']")
            expect(memberships).to_be_visible(timeout=20000)
            expect(memberships).to_contain_text("Ayala Courts")
            expect(
                mia.locator("[data-testid='membership-row'][data-state='Active']")
            ).to_have_count(1)
            expect(
                mia.locator("[data-testid='membership-row'][data-state='Expired']")
            ).to_have_count(1)
        finally:
            context.close()
