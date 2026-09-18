"""
E2E file 13 — True reschedule, staff-only (section-15, PLAN §8c)
              + the customer is TOLD (section-19, Backlog B5).

The desk half of moving a booking: ONE action instead of cancel-then-rebook,
the old slot frees and the new one fills, the payment that was already verified
stays verified on the document the customer is handed, and the price the dialog
shows before submitting is the price the server charges at the NEW slot.

Section-19 adds the half that was missing: the customer's side of the same move.
Reschedule is staff-only *because it is a conversation*, but the customer still
ends up looking at their own history — where, until B5, they saw a Cancelled
booking they never cancelled beside a new one they never made, and no
explanation. The last two rows drive that: the portal story end to end, and the
mail actually reaching the queue for an account while staying silent for a
walk-in who has no inbox.

Runs as Administrator, admin-driven like files 06/11/12 (E2EF has no staff
user; the staff-actor positives live in the BACKEND rows —
tests/test_reschedule.py proves the verification stamps are the ORIGINAL
staff member's, which is the thing a single-actor E2E cannot show). The portal
row is the one exception: it asserts as the seeded customer through the
`customer_page` fixture, because the whole point is what the CUSTOMER sees.

Date map (all relative to the run day). THE FULL E2E LEDGER, taken from file
15's re-verified copy rather than from this file's older recital — which was one
of the five wrong ones Backlog B16 records (it said "+40..+47 file 06"; +45 is
file 02's, and file 02 slides it to +46 on Sunday run-days, landing on file 06's
D_MEMBER). Only THIS file's copy is corrected here; B16 stays open for the other
four and for the files 08/10 collision.

  +0 TODAY  file 14 (E2EF-main-court-1) and file 15 (AYALA-bgc-court-1)
  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   THIS FILE
  +60/+61   files 08 AND 10                 +62       file 10
  +63/+64   file 11                         +65       file 14
  +66       reserved in prose for section-16, never used
  +67/+68   file 15

THIS FILE CLAIMS **+58 and +59**, which the section-13/14 ledgers reserved for
it. Section-19's rows add no ledger day — they take free HOURS on the days this
file already owns:

  +58  E2EF-main-court-1 — 08:00 the portal story's SOURCE; 10:00 the cash
                           move's SOURCE; the mail row's account move
                           11:00 -> 12:00 and its walk-in move 13:00 -> 16:00;
                           09:00 -> 17:00 the discounted flat-rate move
                           (Backlog B15's ride-along row, 2026-08-27)
       E2EF-main-court-2 — 17:00 -> 18:00 the pricing assertion (section-14's
                           ruled court)
  +59  E2EF-main-court-1 — 08:00 the portal story's TARGET, 10:00 the cash
                           move's TARGET, 14:00/15:00 the occupied-target race

  Every hour a row TOUCHES is listed, targets included — an hour map that lists
  only sources is the same defect B16 records for the ledger recitals.

E2EF is deliberate for the cash rows: its 1-minute reservation expiry is
irrelevant to a Cash booking (born Confirmed, no base clock), and nothing here
holds an unpaid Reserved booking open across a dialog — the trap file 11
recorded for its fund-transfer half. That is also why the section-19 walk-in is
booked **Cash**: a Fund Transfer walk-in would put that 1-minute clock back in
play across two dialogs.

No sleeps. Every assertion waits on a rendered node or a server response.
"""
import json
import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers.board import (
    CUSTOMER,
    E2EF_BRANCH,
    E2EF_COURT,
    book_slot_via_api,
    cancel_active_bookings,
    csrf,
    load_board,
    open_reschedule,
    quick_book,
    quick_book_walkin,
    reschedule_total,
    set_reschedule_target,
    slot,
    submit_reschedule,
)
from helpers.worker_routing import bench_execute

RATE_COURT = "E2EF-main-court-2"  # section-14: base ₱200, ₱350 from 18:00
BASE_RATE = 200
NIGHT_RATE = 350

# Section-19. The `customer_page` fixture's user (conftest.CUSTOMER_EMAIL) — the
# portal rows have to assert as the person who OWNS the booking, so the booking
# has to be made for her rather than for helpers.board's default customer.
PIA = "cust.pia@example.com"
WALKIN_NAME = "Walk-in Wanda S19"

D_FROM = (date.today() + timedelta(days=58)).isoformat()
D_TO = (date.today() + timedelta(days=59)).isoformat()

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}

# Backlog B15's row. A number no seeded membership grants (file 15's idiom), so
# a total that matches can only have come from the discount the booking CARRIES.
STAFF_DISCOUNT = 25
# ...and a rate the court does NOT charge. The row's whole claim is the branch
# get_quote could not express before B15 — a flat rate INHERITED on a move —
# so the source booking must carry a rate that re-pricing off the court would
# never produce. Added to the court's base at arrange time, never hard-coded.
RATE_BUMP = 77


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


def _pesos(text: str) -> float:
    """The ONE amount in a rendered money line ("New total: ₱ 150.00")."""
    amounts = re.findall(r"\d[\d,]*\.\d{2}", text)
    assert len(amounts) == 1, f"expected exactly one amount in {text!r}"
    return float(amounts[0].replace(",", ""))


def _plain(message: str) -> str:
    """Undo quoted-printable SOFT line breaks before searching a queued body.

    Email Queue stores the full MIME. QP soft-wraps long lines with a trailing
    "=" + newline, which can split a phrase in half — so a plain substring
    search on the raw message passes or fails depending on where the phrase
    happens to land in the line. Assert on the BODY rather than the Subject
    header: our subject carries an en-dash (`_when` joins times with "–"), so
    the header is RFC2047-encoded and not searchable as text at all.
    """
    return re.sub(r"=\r?\n", "", message or "")


def _queued_mail(booking: str) -> dict:
    """What the mail stub actually queued for a booking (section-19).

    The queue IS the assertion surface — dev/E2E sites carry the seeded mail stub
    plus `mute_emails`, so rows are created and delivery is impossible (S8
    as-built 2). Row fields only, never a rendered body.

    check=False so the assert below is REACHABLE: bench_execute defaults to
    check=True and the CalledProcessError it raises carries the exit status but
    NOT stderr. The helper returns a DICT even for zero rows, because
    `bench execute` prints its return value only `if ret` — a list-returning
    helper would print nothing for a walk-in and "no mail" would be
    indistinguishable from a crashed call.
    """
    result = bench_execute(
        "court_booking_tech.testing.get_booking_mail", json.dumps([booking]), check=False
    )
    assert result.returncode == 0, result.stderr
    payload = None
    for line in reversed(result.stdout.strip().splitlines()):
        if line.startswith("{"):
            payload = json.loads(line)
            break
    assert payload, f"no JSON from get_booking_mail: {result.stdout!r}"
    return payload


@pytest.mark.e2e
class TestReschedule:

    def test_moving_a_paid_booking_frees_the_old_slot_and_keeps_it_paid(
        self, page: Page
    ):
        """The core of §8c. One action, and the money survives it: the
        replacement's statement is PAID & VERIFIED without anyone re-taking
        the cash."""
        cancel_active_bookings(page, E2EF_COURT, D_FROM)
        cancel_active_bookings(page, E2EF_COURT, D_TO)
        load_board(page, E2EF_BRANCH, D_FROM)

        original = quick_book(page, E2EF_COURT, "10:00:00", "Cash")
        assert original.startswith("BK-E2EF-"), original
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        open_reschedule(page, E2EF_COURT, "10:00:00")
        set_reschedule_target(page, date=D_TO, start_time="10:00:00")
        result = submit_reschedule(page)
        moved = result["name"]
        assert moved != original, result
        assert result["booking_status"] == "Confirmed", result

        # The board is still on the SOURCE day and reloads itself after the
        # move — the slot they vacated has to come back as bookable.
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-status='available']",
            timeout=20000,
        )

        # ...and the target day now shows the session.
        load_board(page, E2EF_BRANCH, D_TO)
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        new_row = _get_json(page, f"/api/resource/CBT Court Booking/{moved}")
        old_row = _get_json(page, f"/api/resource/CBT Court Booking/{original}")
        assert new_row["rescheduled_from"] == original, new_row
        assert new_row["booking_date"] == D_TO, new_row
        assert old_row["booking_status"] == "Cancelled", old_row
        assert old_row["rescheduled_to"] == moved, old_row

        # The document the customer is handed. Payment truth is the whole
        # point: nobody re-took the cash, so it must still read verified.
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{new_row['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]
        resp = page.request.get(
            "/printview",
            params={
                "doctype": "CBT Booking Invoice",
                "name": invoice["name"],
                **PRINT_PARAMS,
            },
        )
        assert resp.ok, f"printview: HTTP {resp.status}"
        assert "PAID &amp; VERIFIED" in resp.text()

        # The detail dialog explains the move rather than showing a bare row.
        slot(page, E2EF_COURT, "10:00:00").click()
        page.wait_for_function(
            """() => window.cur_dialog && cur_dialog.$wrapper.hasClass('show')
                && cur_dialog.$wrapper.find("[data-testid='rescheduled-from']").length""",
            timeout=15000,
        )
        assert (
            original
            in page.locator(".modal.show [data-testid='rescheduled-from']").inner_text()
        )

    def test_the_dialog_shows_the_new_slots_price_before_committing(
        self, page: Page
    ):
        """WYSIWYG money (S11/S14 doctrine) applied to a MOVE: a booking taken
        at the day rate and moved into the night window costs more, and staff
        must see that before they say it out loud — not after the customer has
        been told the old number."""
        cancel_active_bookings(page, RATE_COURT, D_FROM)
        load_board(page, E2EF_BRANCH, D_FROM)

        booking = quick_book(page, RATE_COURT, "17:00:00", "Cash")
        assert booking.startswith("BK-E2EF-"), booking
        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert float(row["total_amount"]) == float(BASE_RATE), row["total_amount"]

        page.wait_for_selector(
            f".cbt-slot[data-court='{RATE_COURT}'][data-start='17:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        open_reschedule(page, RATE_COURT, "17:00:00")

        # Opens on the CURRENT slot, so it opens on the day rate.
        assert str(BASE_RATE) in reschedule_total(page), reschedule_total(page)

        set_reschedule_target(page, start_time="18:00:00")
        moved_total = reschedule_total(page)
        assert str(NIGHT_RATE) in moved_total, moved_total
        assert str(BASE_RATE) not in moved_total, (
            "the dialog must stop showing the price of the slot being left"
        )

        # Nothing was committed — the booking is still where and what it was.
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        unchanged = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert unchanged["booking_status"] == "Confirmed"
        assert float(unchanged["total_amount"]) == float(BASE_RATE)
        assert "rescheduled_to" not in unchanged, (
            "/api/resource drops NULLs (S13 as-built 15) — the key must be absent"
        )

    def test_a_discounted_flat_booking_moves_at_the_total_the_dialog_shows(
        self, page: Page
    ):
        """Backlog B15 + B18's ride-along row (2026-08-27). The row above proves
        the dialog RE-QUOTES, but on a zero-discount booking and without ever
        committing — so the one seam B15 names was untested: a booking that
        carries a discount AND inherits a flat rate, moved, and the number staff
        read out compared with the statement the customer is handed.

        ARRANGED BY API, deliberately: the source booking carries a staff
        OVERRIDE rate (the court's base + RATE_BUMP) and a 25% discount. The
        quick-book dialog has no rate control to type, and a booking at the
        court's own base rate would make this row pass with B15's param
        REVERTED — re-pricing off a rule-less court lands on the same number
        (the ducky's finding on the first draft). With the override, the number
        the dialog shows can only be right if it asked the server for THAT rate.
        The dialog itself is driven by gesture as in every other row here.
        Worst case before this row: staff read a number to a customer that the
        invoice then contradicted.
        """
        cancel_active_bookings(page, E2EF_COURT, D_FROM)

        base = float(_get_json(page, f"/api/resource/CBT Court/{E2EF_COURT}")["hourly_rate"])
        override = base + RATE_BUMP
        resp = page.request.post(
            "/api/method/court_booking_tech.api.bookings.create_booking",
            headers={"X-Frappe-CSRF-Token": csrf(page)},
            form={
                "court": E2EF_COURT,
                "booking_date": D_FROM,
                "start_time": "09:00:00",
                "payment_method": "Cash",
                "customer": CUSTOMER,
                "number_of_slots": 1,
                "hourly_rate": override,
                "discount_percent": STAFF_DISCOUNT,
            },
        )
        assert resp.ok, f"create_booking: HTTP {resp.status} {resp.text()}"
        booking = resp.json()["message"]["name"]

        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert float(row["hourly_rate"]) == override, row
        assert float(row["discount_percent"]) == float(STAFF_DISCOUNT), row
        hours = float(row["duration_hours"])
        charged = float(row["total_amount"])
        assert charged == round(override * hours * 0.75, 2), row
        # The discriminator: what re-pricing off the COURT would say instead.
        off_the_court = round(base * hours * 0.75, 2)
        assert charged != off_the_court, (charged, off_the_court)

        load_board(page, E2EF_BRANCH, D_FROM)
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='09:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        open_reschedule(page, E2EF_COURT, "09:00:00")
        set_reschedule_target(page, start_time="17:00:00")

        shown = reschedule_total(page)
        assert "Estimated" not in shown, shown  # B15: the server's number now
        assert "New total" in shown, shown
        on_screen = _pesos(shown)
        assert on_screen == charged, (shown, charged)
        assert on_screen != off_the_court, (
            "the dialog quoted the court's rate, not the rate this booking carries"
        )
        estimate = page.locator(".modal.show [data-fieldname='estimate']").inner_text()
        assert "rate carried over" in estimate, estimate
        assert f"Less {STAFF_DISCOUNT}%" in estimate, estimate

        moved = submit_reschedule(page)["name"]
        assert moved != booking

        new_row = _get_json(page, f"/api/resource/CBT Court Booking/{moved}")
        assert new_row["start_time"] == "17:00:00", new_row
        assert float(new_row["total_amount"]) == on_screen, (new_row, shown)
        assert float(new_row["hourly_rate"]) == override, new_row
        assert float(new_row["discount_percent"]) == float(STAFF_DISCOUNT), new_row
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{new_row['billing_doc']}"
        )
        assert float(invoice["total_amount"]) == on_screen, (invoice, shown)
        assert float(invoice["discount_amount"]) == round(override * hours - on_screen, 2), (
            invoice
        )

    def test_an_occupied_target_fails_cleanly_and_costs_the_original_nothing(
        self, page: Page
    ):
        """The atomicity pin, through the UI. The dialog offers 11:00 because
        it WAS free when it opened; by submit time somebody else has taken it.
        Staff must get a clean refusal and still have their booking."""
        cancel_active_bookings(page, E2EF_COURT, D_TO)
        load_board(page, E2EF_BRANCH, D_TO)

        original = quick_book(page, E2EF_COURT, "14:00:00", "Cash")
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='14:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        # Open the dialog while 15:00 is still free, and select it.
        open_reschedule(page, E2EF_COURT, "14:00:00")
        set_reschedule_target(page, start_time="15:00:00")

        # ...then let somebody else take it. A separate HTTP request is a
        # separate transaction, so this is a real race, not a simulated one.
        blocker = book_slot_via_api(page, E2EF_COURT, D_TO, "15:00:00")

        error = submit_reschedule(page, expect_ok=False)
        assert "Slot already taken" in error, error

        # The original survives completely intact — status, schedule and the
        # absence of any half-written move link.
        row = _get_json(page, f"/api/resource/CBT Court Booking/{original}")
        assert row["booking_status"] == "Confirmed", row
        assert row["start_time"] == "14:00:00", row
        assert row["booking_date"] == D_TO, row
        assert "rescheduled_to" not in row, (
            f"a failed move must not link the original, got {row.get('rescheduled_to')!r}"
        )
        # ...and the booking that won the slot is untouched too.
        winner = _get_json(page, f"/api/resource/CBT Court Booking/{blocker}")
        assert winner["booking_status"] == "Confirmed", winner

    # --- section-19 (Backlog B5): the customer's side of the same move -------

    def test_portal_shows_moved_story(self, page: Page, customer_page: Page):
        """B5's portal half, both directions in ONE test so they can never
        silently diverge: the cancelled card points forward to its replacement,
        and following that link lands on a page that points back.

        Before this, the customer's own history was a Cancelled booking they
        never cancelled next to a new one they never made — which reads as a
        mistake by the facility, on the one screen they check.
        """
        cancel_active_bookings(page, E2EF_COURT, D_FROM)
        cancel_active_bookings(page, E2EF_COURT, D_TO)
        load_board(page, E2EF_BRANCH, D_FROM)

        # Booked FOR the customer whose portal we are about to read.
        original = quick_book(page, E2EF_COURT, "08:00:00", "Cash", customer=PIA)
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='08:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        open_reschedule(page, E2EF_COURT, "08:00:00")
        set_reschedule_target(page, date=D_TO, start_time="08:00:00")
        moved = submit_reschedule(page)["name"]
        assert moved != original

        # --- as the CUSTOMER ------------------------------------------------
        customer_page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
        card = customer_page.locator(f".cbt-card[data-booking='{original}']")
        expect(card).to_be_visible(timeout=20000)

        # The real status is untouched (test_07 asserts on this attribute) —
        # only the BADGE softens, because the customer did not cancel anything.
        expect(card).to_have_attribute("data-status", "Cancelled")
        expect(card.locator(".cbt-badge")).to_have_text("Moved")

        strip = customer_page.locator(
            f".cbt-moved-pair:has(.cbt-card[data-booking='{original}'])"
            " [data-testid='moved-to']"
        )
        expect(strip).to_be_visible(timeout=20000)
        expect(strip).to_have_attribute("data-moved-to", moved)
        # It names WHERE it went, not just that it went somewhere.
        expect(strip).to_contain_text(D_TO)

        # Following the link is the point of the link.
        strip.click()
        customer_page.wait_for_url(f"**/my-bookings/{moved}", timeout=30000)
        note = customer_page.locator("[data-testid='moved-from-note']")
        expect(note).to_be_visible(timeout=20000)
        expect(note).to_contain_text(D_FROM)

    def test_the_move_mails_an_account_and_stays_silent_for_a_walk_in(
        self, page: Page
    ):
        """The mail, on the queue that is the only honest surface for it — and
        its negative control in the same row.

        A walk-in has no inbox by construction, and B5's guard lives in the
        notify function so the skip is SILENT rather than a logged error. Both
        halves go through the same desk dialog, so this cannot pass by the two
        paths differing anywhere except in whether there is somebody to tell.
        """
        cancel_active_bookings(page, E2EF_COURT, D_FROM)
        load_board(page, E2EF_BRANCH, D_FROM)

        account = quick_book(page, E2EF_COURT, "11:00:00", "Cash", customer=PIA)
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='11:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        open_reschedule(page, E2EF_COURT, "11:00:00")
        set_reschedule_target(page, start_time="12:00:00")
        moved = submit_reschedule(page)["name"]
        assert moved != account

        mail = _queued_mail(moved)
        # A CASH move used to queue nothing AT ALL, and the Fund-Transfer carry
        # queued the "confirmed" template — so "exactly one row" is already a
        # statement only the new code satisfies.
        assert mail["count"] == 1, mail
        row = mail["rows"][0]
        assert PIA in row["recipients"], row
        assert "has moved your booking" in _plain(row["message"]), row["message"][:600]

        # --- the same desk action for a stranger paying cash ----------------
        # Re-load rather than clicking into the board mid-refresh: submitting the
        # move triggers its own reload, and the next slot click must not race it.
        load_board(page, E2EF_BRANCH, D_FROM)
        walk_in = quick_book_walkin(
            page, E2EF_COURT, "13:00:00", "Cash", WALKIN_NAME
        )
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='13:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )
        open_reschedule(page, E2EF_COURT, "13:00:00")
        set_reschedule_target(page, start_time="16:00:00")
        walk_in_moved = submit_reschedule(page)["name"]

        assert walk_in_moved != walk_in
        walk_in_mail = _queued_mail(walk_in_moved)
        assert walk_in_mail["count"] == 0, walk_in_mail
