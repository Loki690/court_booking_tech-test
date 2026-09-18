"""
E2E file 12 — Peak/off-peak rate rules (section-14, PLAN §8b).

The customer-facing half of time-based pricing: the night rate is visible ON
THE GRID before anything is committed, the checkout names both rates when a
session crosses the boundary, and the total the server charges is the total the
screen showed. Then the desk half: the quick-book dialog is priced by the
server, and the printed statement carries one line per rate.

Court `E2EF-main-court-2` — base ₱200, ₱350 from 18:00 every day (the seeded
fixture; see seeds `E2E_RATE_RULES`). It is a NEW court that nothing else
touches, because Pia's ₱800 at BGC and the §4 cast are asserted to the peso
across half a dozen green files.

ONLY THE NIGHT RULE IS ASSERTED HERE. The court also carries a Weekends
05:00–07:00 rule, deliberately scoped to hours this file never touches: E2E
dates are RELATIVE, so the weekday changes with the run day, and a weekend rule
that outranked the night rule would move these prices every Saturday. Weekend
classification is proven on absolute October 2027 dates in
tests/test_rate_rules.py instead.

Date map (all relative to the run day). Ledger CORRECTED 2026-08-18 (Backlog B16):
this file used to say "+40..+47 file 06", which is wrong — **+45 belongs to file 02**,
whose `_qcsm_open_date()` slides +45 -> +46 when the run day makes +45 a Sunday.
The re-verified ledger, copied from file 15:

  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +50/+51   file 05                         +52..+57  file 07
  +58/+59   file 13                         +60/+61   files 08 AND 10 (see B16)
  +62       file 10                         +63/+64   file 11
  +65       file 14                         +66       reserved in prose, never used
  +67/+68   file 15                         +69/+70   file 16
  +71/+72   file 17                         +73/+74   file 18
  **+75 upward is FREE.**

THIS FILE CLAIMS **+48 and +49**, which the section-13 ledger already reserved for it.

  +48  E2EF-main-court-2 — the portal path (Pia, /book)
  +49  E2EF-main-court-2 — the desk path (Administrator, board)

Both are inside the seeded E2EF `advance_booking_days = 90`, which this file
REQUIRES: it drives /book as a real customer, and the booking controller
refuses a customer booking past the horizon.

PIA HOLDS NO MEMBERSHIP — deliberately (the seed comment says making her one
"would quietly break a green suite"). Every peso below is undiscounted, and a
membership appearing on her account would break this file, not fix it.

No sleeps. Every assertion waits on a rendered node or a server response.
"""
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures, portal
from helpers import board
from helpers.board import (
    E2EF_BRANCH,
    cancel_active_bookings,
    load_board,
    quick_book,
    slot,
)

RATE_COURT = "E2EF-main-court-2"
BASE_RATE = 200
NIGHT_RATE = 350

D_PORTAL = (date.today() + timedelta(days=48)).isoformat()
D_DESK = (date.today() + timedelta(days=49)).isoformat()

BOOK_URL = f"/book?c=e2e-fast&b=main&d={D_PORTAL}"

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


def _open_grid(page: Page, url: str = BOOK_URL):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(
        f".cbt-slot[data-court='{RATE_COURT}']", timeout=20000
    )


def _chip(page: Page, start_time: str):
    return page.locator(
        f".cbt-slot[data-court='{RATE_COURT}'][data-start='{start_time}']"
    )


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


@pytest.mark.e2e
class TestRateRules:

    def test_book_grid_shows_the_night_rate_on_the_slot_itself(
        self, page: Page, customer_page: Page
    ):
        """The whole point of putting the price on the cell: a customer must
        not discover the night rate at checkout.

        Backlog B33 changed the RULE this row pins, on the user's own words —
        *"always show amount and status of that time slot"*. Section-14 stamped
        a price only where it differed from the court's headline rate, so the
        17:00 cell used to carry none; every cell carries one now. The signal
        that density rule protected did not go away, it moved: the cell that
        costs MORE than the headline is the one marked `--up`. So the assertion
        is no longer "priced or not" — it is "which one is flagged", which is
        the thing a customer actually needs to see.
        """
        # Cleanup runs as ADMIN, never as Pia: customers hold NO DocPerm on
        # CBT Court Booking (leak vector 4), so a customer-side list probe
        # correctly 403s. The portal only ever sees its whitelisted APIs.
        cancel_active_bookings(page, RATE_COURT, D_PORTAL)
        _open_grid(customer_page)

        day_chip = _chip(customer_page, "17:00:00")
        night_chip = _chip(customer_page, "18:00:00")
        expect(day_chip).to_be_visible()
        expect(night_chip).to_be_visible()

        # Both cells state their amount; only the dearer one is flagged.
        expect(day_chip.locator(".cbt-slot-rate")).to_have_text("₱200")
        expect(night_chip.locator(".cbt-slot-rate")).to_have_text("₱350")
        assert day_chip.locator(".cbt-slot-rate--up").count() == 0, (
            "the court's own headline rate must not be flagged as dearer — "
            "if every cell is accented, none of them is"
        )
        expect(night_chip.locator(".cbt-slot-rate--up")).to_have_count(1)

        # The column header is the court name only; sport on hover, price on each cell.
        court = _get_json(page, f"/api/resource/CBT Court/{RATE_COURT}")
        assert court.get("court_type"), court
        head = customer_page.locator(f"th.cbt-colhead[data-court='{RATE_COURT}']")
        expect(head).to_have_text(court["court_name"])
        expect(head).to_have_attribute("title", court["court_type"])
        expect(head.locator(".cbt-card-sub")).to_have_count(0)

    def test_checkout_breaks_down_a_session_that_crosses_the_boundary(
        self, page: Page, customer_page: Page
    ):
        """₱200 for the 17:00 hour + ₱350 for the 18:00 hour = ₱550, shown as
        two lines. A single blended "₱275/hr" would be a rate on no rule."""
        cancel_active_bookings(page, RATE_COURT, D_PORTAL)
        _open_grid(customer_page)

        _chip(customer_page, "17:00:00").click()
        _chip(customer_page, "18:00:00").click()
        # The checkout bar carries the server total for the selection.
        expect(customer_page.locator("#cbt-selection-total")).to_contain_text(
            "550", timeout=20000
        )

        customer_page.locator("#cbt-review").click()
        lines = customer_page.locator("#cbt-quote-lines [data-testid='rate-breakdown']")
        expect(lines).to_have_count(2, timeout=15000)
        assert "200" in lines.nth(0).inner_text(), lines.nth(0).inner_text()
        assert "350" in lines.nth(1).inner_text(), lines.nth(1).inner_text()
        assert "Night rate" in lines.nth(1).inner_text()
        expect(customer_page.locator("#cbt-quote-lines")).to_contain_text("550.00")

    def test_reserving_charges_exactly_what_the_checkout_showed(
        self, page: Page, customer_page: Page
    ):
        """quote == reserve, through the real UI. The booking's own segments
        are checked server-side: the price has to be STORED per slot, not
        recomputed later from a court whose rules may have moved on."""
        cancel_active_bookings(page, RATE_COURT, D_PORTAL)

        result = portal.reserve(customer_page, RATE_COURT, D_PORTAL, "17:00:00", slots=2)
        booking = result["booking"]
        assert booking.startswith("BK-E2EF-"), result
        assert result["total_amount"] == 550, result

        # Read as ADMIN — /api/resource is a DocPerm surface, and Pia has none.
        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert float(row["total_amount"]) == 550.0, row["total_amount"]
        segments = row["rate_segments"]
        assert len(segments) == 2, segments
        assert float(segments[0]["hourly_rate"]) == float(BASE_RATE), segments[0]
        assert float(segments[1]["hourly_rate"]) == float(NIGHT_RATE), segments[1]
        # /api/resource DROPS null fields (S13 as-built 15), so a segment with
        # no label has NO label key — never subscript it blind.
        assert segments[0].get("label") in (None, ""), segments[0]
        assert segments[1].get("label") == "Night rate", segments[1]
        # The blend is display-only, and it is NOT what was charged per hour.
        assert float(row["hourly_rate"]) == 275.0, row["hourly_rate"]

        # The customer's own detail page shows both rates, not the blend.
        portal.open_detail(customer_page, booking)
        breakdown = customer_page.locator("[data-testid='rate-breakdown']")
        expect(breakdown).to_have_count(2, timeout=20000)

    def test_desk_quickbook_is_server_priced_and_prints_one_line_per_rate(
        self, page: Page
    ):
        """The desk half. Staff must see the charge BEFORE they say it out
        loud (WYSIWYG money, S11), and the statement they hand over has to
        itemise it — "₱550" with no explanation is what starts the argument."""
        cancel_active_bookings(page, RATE_COURT, D_DESK)
        load_board(page, E2EF_BRANCH, D_DESK)
        assert (
            slot(page, RATE_COURT, "17:00:00").get_attribute("data-status")
            == "available"
        )

        # The column header is the court name only; sport on hover, price on each cell.
        court = _get_json(page, f"/api/resource/CBT Court/{RATE_COURT}")
        assert court.get("court_type"), court
        head = page.locator(f"th.cbt-colhead[data-court='{RATE_COURT}']")
        expect(head).to_have_text(court["court_name"])
        expect(head).to_have_attribute("title", court["court_type"])
        expect(head.locator(".cbt-colhead-meta")).to_have_count(0)

        # B46: two slots crosses 18:00, and the desk says so by SELECTING two
        # cells — the `number_of_slots` Int is gone. The dialog must still
        # re-quote rather than re-multiply.
        board.select_slots(page, RATE_COURT, "17:00:00", 2)
        board.open_cart_dialog(page)
        # ONE atomic wait that gates on cur_dialog itself, never on the DOM
        # alone: dialog.js nulls cur_dialog on hidden.bs.modal and rebinds it
        # only after the fade (S13 as-built 16), so a predicate that assumes it
        # exists throws instead of retrying.
        #
        # ⚠ Scoped to the dialog WRAPPER, not to fields_dict.estimate: B46 moved
        # the per-segment lines into the cart summary in the dialog BODY, where
        # a twelve-court tournament cannot push the total off the screen. The
        # testid and the count are unchanged.
        page.wait_for_function(
            """() => window.cur_dialog
                && cur_dialog.$wrapper
                && cur_dialog.$wrapper
                    .find("[data-testid='rate-breakdown']").length === 2""",
            timeout=15000,
        )
        estimate = page.locator(
            ".modal.show [data-testid='quote-total']"
        ).inner_text()
        assert "550" in estimate, estimate
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        # Book it for real and check the document the customer is handed.
        name = quick_book(page, RATE_COURT, "17:00:00", "Cash", number_of_slots=2)
        assert name.startswith("BK-E2EF-"), name

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        assert float(booking["total_amount"]) == 550.0, booking["total_amount"]
        assert len(booking["rate_segments"]) == 2, booking["rate_segments"]

        resp = page.request.get(
            "/printview",
            params={
                "doctype": "CBT Booking Invoice",
                "name": booking["billing_doc"],
                **PRINT_PARAMS,
            },
        )
        assert resp.ok, f"printview: HTTP {resp.status}"
        html = resp.text()
        assert "Night rate" in html, "the statement must name the rate that applied"
        assert "550" in html
        # Both rates appear as their own line — not one averaged line.
        assert html.count("Court rental") == 2, (
            "expected one statement line per rate segment"
        )
