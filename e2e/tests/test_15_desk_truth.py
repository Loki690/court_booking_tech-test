"""
E2E file 15 — Desk truth (section-18; Backlog B3 + B4 + B7).

Three lies the staff desk used to tell, fixed together because they share one
surface and one recipe:

- **B3** the booking FORM's Customer link could not find portal customers. It set
  only `filters`, so the search fell through to frappe's stock `user_query`,
  which excludes Website Users — and every CBT customer IS one. Staff got
  "Create a new User", the exact throwaway-account invitation B1 was closed to
  prevent.
- **B4** the board quick-book dialog's money mixed two authorities: a SERVER
  subtotal with a CLIENT-side discount multiplication.
- **B7** the board refused to quick-book the hour that is RUNNING unless the
  company had opted into no-show release, though `create_booking` has always
  accepted a staff back-record (S4 as-built 6). The desk surface refused what
  the API accepts, and pushed staff to the form — where B3 then bit them.

Runs as Administrator, admin-driven like files 06/11/12/13/14. The staff-actor
positives live in the BACKEND rows (tests/test_portal.py and
tests/test_isolation.py prove a real staff session's quote and every refusal a
guest/customer/cross-tenant caller gets — which a single-actor E2E cannot).

Date map (all relative to the run day). THE FULL E2E LEDGER, re-verified against
every one of the 21 test files this session rather than copied forward:

  +0 TODAY  file 14 (E2EF-main-court-1) and THIS FILE (AYALA-bgc-court-1)
  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   file 13
  +60/+61   files 08 AND 10                 +62       file 10
  +63/+64   file 11                         +65       file 14
  +66       reserved in prose for section-16, never used

  **THIS FILE CLAIMS +67 and +68**, plus TODAY on a court file 14 never touches.

    +67  AYALA-bgc-court-2 — the member money row
    +68  AYALA-bgc-court-2 — the walk-in override row
    TODAY  AYALA-bgc-court-1 — the two running-hour rows

  `test_form_customer_picker_finds_portal_customer` claims NO date: it opens a
  new booking form and never saves, so it consumes no court x date.

TWO PRE-EXISTING LEDGER DEFECTS were found while verifying the above. Neither is
fixed here (they are other files' docstrings, and one wants a real decision), but
they are written down so the next reader does not re-derive them — and they are
recorded as Backlog B16:

  * files 08 and 10 BOTH use +60/+61 and each claims the range exclusively. It
    survives only because file 08 asserts AYALA-bgc-court-3 available at **10:00**
    while file 10 books that court at **09:00** — a one-hour undeclared margin,
    not isolation.
  * every "+40..+47 file 06" recital (files 09/11/12/13/14) is wrong: +45 belongs
    to file 02, whose `_qcsm_open_date()` slides it to +46 on Sunday run-days —
    and +46 is file 06's `D_MEMBER`, documented as "used by no other file".
    Different courts, so it is latent.
  * file 05 also carries +200, a beyond-the-horizon REJECTION constant that
    persists nothing. It is not a ledger slot.

THE CLOCK IS PINNED FOR THE RUNNING-HOUR ROWS ONLY, at CLASS scope (section-17's
lever, Backlog B8). `TestDeskTruth`'s money rows are on future dates and want the
real clock; only `TestRunningHour` needs a pretend now. Class scope rather than
file 14's module scope is deliberate: it halves the `bench execute` round trips
and keeps the pin off the rows that do not need it.

Unlike file 14 this file sets **no knob**. AYALA ships `no_show_release_minutes`
= 0 and stays there, which is the whole point of B7: the staff relaxation is now
knob-INDEPENDENT, so knob 0 is the STRONGER statement. That also means
section-17's nested clock-then-knob teardown ordering has nothing to order here —
there is one piece of shared state, not two.

AYALA-bgc is open 06:00-22:00 on all seven days (only QCSM-timog carries a
closed day), so a 14:00 chip exists whatever weekday the run lands on.

Every test cleans its court x date FIRST **and AFTER** — including `No Show`
rows, which are not "active" and would otherwise survive the stock filter. The
running-hour rows share a court x date with each other, so first-and-after is
what keeps them independent in either order: row 5 asserts the portal chip reads
`past`, which a leftover booking would turn into `booked`.

No sleeps. Every assertion waits on a rendered node or a server response.
"""
import json
import re
from datetime import date, datetime, time, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers import gestures
from helpers.board import (
    ACTIVE_OR_RELEASED_STATUSES,
    BGC_BRANCH,
    CUSTOMER,
    _submit_cart,
    cancel_active_bookings,
    load_board,
    open_cart_dialog,
    quick_book_walkin,
    select_slots,
    slot,
    wait_dialog,
    wait_money_settled,
)
from helpers.desk_form import wait_for_new_form
from helpers.navigation import wait_for_page_load
from helpers.worker_routing import bench_execute

AYALA_COMPANY_SLUG = "ayala-courts"
BGC_SLUG = "bgc"

MEMBER_COURT = "AYALA-bgc-court-2"  # ₱450/hr FLAT, no rate rules
RUNNING_COURT = "AYALA-bgc-court-1"  # ₱400/hr FLAT

MIA = "cust.mia@example.com"  # AYALA VIP 20% (section-11 seeds)
# The DISCRIMINATING control for B3. A System User, so frappe's stock
# `user_query` WOULD have returned this one while missing every Website-User
# customer — which is exactly the bug. If the form's query were still the stock
# one, the assertion that this yields no customer row would fail.
STAFF_PROBE = "staff.ayala@example.com"

D_MEMBER = (date.today() + timedelta(days=67)).isoformat()
D_WALKIN = (date.today() + timedelta(days=68)).isoformat()
TODAY = date.today().isoformat()

# Pretend-now, on TODAY. Any minute strictly inside the hour gives the running
# chip its shape (slots.py marks `past` with a strict `<` on the slot's END);
# 14:05 matches file 14 so the two files cannot drift apart.
PRETEND_TIME = time(14, 5)
# Seconds. Covers minute-granular offset rounding plus the latency of the second
# bench_execute. NOT a fudge factor for a clock that half-landed.
CLOCK_DRIFT_TOLERANCE = 120

WALKIN_NAME = "Walk-in Wendell S18"
STAFF_DISCOUNT = 25  # a number no membership would produce


# ---------------------------------------------------------------------------
# the section-17 clock lever — copied from file 14 rather than re-derived
# ---------------------------------------------------------------------------


def _set_clock(minutes: int) -> datetime:
    """Pin (0 = release) this worker's site clock; return the server's now.

    The RETURN VALUE is the assertion surface. `bench_execute` runs in its own
    process, so nothing else here proves what the web workers will read.
    """
    result = bench_execute(
        "court_booking_tech.testing.set_test_clock_offset",
        json.dumps([minutes]),
        # check=False so the assert below is REACHABLE. bench_execute defaults to
        # check=True, whose CalledProcessError carries the command and exit
        # status but NOT stderr.
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = None
    for line in reversed(result.stdout.strip().splitlines()):
        if line.startswith("{"):
            payload = json.loads(line)
            break
    assert payload, f"no JSON from set_test_clock_offset: {result.stdout!r}"
    return datetime.strptime(payload["server_now"][:19], "%Y-%m-%d %H:%M:%S")


def _pin_clock_to_pretend_afternoon() -> datetime:
    """Put the site at PRETEND_TIME on TODAY, and PROVE it landed.

    The offset is derived from the SERVER's real now, never the host's. The only
    clock this suite verifies is the site's (`conftest.server_timezone`), so a
    host skewed by less than the tolerance would otherwise mis-land the clock
    while the assertion below still passed.
    """
    real_now = _set_clock(0)
    target = datetime.combine(date.fromisoformat(TODAY), PRETEND_TIME)
    minutes = round((target - real_now).total_seconds() / 60)
    assert abs(minutes) <= 24 * 60, (
        f"host and site disagree about the date by more than a day (server real "
        f"now {real_now}, this file's TODAY {TODAY}) — an environment "
        f"regression, not something to widen a bound for"
    )
    pretend_now = _set_clock(minutes)
    drift = abs((pretend_now - target).total_seconds())
    assert drift <= CLOCK_DRIFT_TOLERANCE, (
        f"the test clock did not land: server now reads {pretend_now}, expected "
        f"~{target} (drift {drift:.0f}s from an offset of {minutes}min). This "
        f"assertion is what makes the rows below independent of the wall clock — "
        f"do not loosen it to get green."
    )
    return pretend_now


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


def _clean(page: Page, court: str, booking_date: str):
    """Clear the court x date, RELEASED rows included."""
    cancel_active_bookings(
        page, court, booking_date, statuses=ACTIVE_OR_RELEASED_STATUSES
    )


def _money(text: str) -> float:
    """The peso figure out of a rendered money node.

    Comparing FORMATTED strings would couple these rows to format_currency's
    locale output; comparing numbers is what the assertions actually mean.
    """
    match = re.search(r"([\d,]+\.\d{2})", text)
    assert match, f"no money figure in {text!r}"
    return float(match.group(1).replace(",", ""))


def _displayed_total(page: Page) -> float:
    return _money(
        page.locator(".modal.show [data-testid='quote-total']").inner_text()
    )


def _server_quote(court: str, booking_date: str, start_time: str, customer: str) -> dict:
    """get_quote as the SERVER computes it, out-of-band via bench execute.

    A second, independent opinion on the money: it does not go through the page,
    so it cannot agree with the dialog by sharing its bug. `bench execute` runs as
    Administrator, which holds platform scope and therefore passes B4's staff gate.

    ALL FOUR ARGUMENTS ARE REQUIRED, and that is a constraint of the transport,
    not a style choice: `bench execute` builds its argument list with `eval(args)`
    (frappe/commands/utils.py), so a JSON `null` for an omitted middle parameter
    raises NameError — and the except branch then passes the ENTIRE JSON STRING as
    a single argument instead of failing. A helper that quietly did that would be
    worse than no helper. Pass every positional or use a different call.
    """
    result = bench_execute(
        "court_booking_tech.api.portal.get_quote",
        json.dumps([court, 1, booking_date, start_time, customer]),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # bench execute prints the return value as COMPACT single-line JSON
    # (`json.dumps(ret, default=json_handler)`), so this is a real parse — the
    # same scan file 14 uses for the clock lever.
    for line in reversed(result.stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON dict from get_quote: {result.stdout!r}")


def _running_start(page: Page, court: str) -> str:
    """The start_time of the chip that is happening RIGHT NOW, per the SERVER.

    Derived from the payload (`server_now`, `date`) rather than hardcoded to
    14:00, and parsed with `frappe.datetime.str_to_obj` on BOTH sides exactly as
    the board itself does — so the browser's own clock and timezone cancel out of
    the comparison, which is what lets this work while the site pretends.
    """
    start = page.evaluate(
        """(court) => {
            const board = frappe.pages['cbt-court-board'].court_board;
            const data = board.board;
            const at = (t) => frappe.datetime.str_to_obj(data.date + ' ' + t).getTime();
            const now = frappe.datetime.str_to_obj(data.server_now).getTime();
            const row = data.courts.find((c) => c.court === court);
            const hit = (row.slots || []).find(
                (s) => at(s.start_time) <= now && now < at(s.end_time)
            );
            return hit ? hit.start_time : null;
        }""",
        court,
    )
    assert start, (
        "no slot on this court contains the server's `now`. The clock is pinned "
        "to 14:05 and AYALA-bgc runs 06:00-22:00 every day, so if this is None "
        "the clock fixture did not land — and it asserts that it did, so start "
        "there rather than widening anything here."
    )
    return start


@pytest.fixture(scope="module", autouse=True)
def clock_released_after_this_file():
    """Last line of defence: this file must never hand a pretend clock onward.

    `TestRunningHour` clears the offset in its own class teardown; this runs after
    the whole module either way. It is not decoration — it also covers a future
    row added AFTER that class which would otherwise inherit a stale clock if the
    class fixture were ever edited to leak one. Redis is not rolled back by a test
    transaction or by snapshot_reset; the key's 1-hour TTL and
    `conftest.real_clock` are the outer layers, not this one's job.
    """
    yield
    _set_clock(0)


@pytest.mark.e2e
class TestDeskTruth:
    """B3 and B4 — the form's search, and the dialog's money. Real clock."""

    def test_form_customer_picker_finds_portal_customer(self, page: Page):
        """Backlog B3, proven through the PICKER rather than through set_value.

        `set_value` never consults `set_query`, so it would pass just as happily
        against the broken stock query — the search IS the bug, so the search is
        what this row drives: type a name, let the awesomplete dropdown resolve,
        click the option, and check the model took it.

        The `search_link` RESPONSE is the primary assertion. It is server truth,
        it proves the FORM sent our query name (a directly-called endpoint could
        not), and it is free of the "Create a new User" row frappe appends to the
        rendered list unless `only_select` is set — so counting DOM options would
        measure the wrong thing.

        Saves nothing, so it claims no ledger date.
        """
        page.goto(
            "/desk/cbt-court-booking/new", wait_until="domcontentloaded", timeout=60000
        )
        wait_for_page_load(page)
        # Backlog B11 prevention. The new-doc route can resolve TWICE under
        # 3-worker load, and a typed interaction cannot be safely re-run the way
        # a set_value chain can — so settle the router BEFORE touching the form.
        wait_for_new_form(page, "CBT Court Booking")
        page.wait_for_selector(
            ".frappe-control[data-fieldname='customer'] input", timeout=15000
        )

        field = page.locator(".frappe-control[data-fieldname='customer'] input")

        # --- the customer IS findable by name (the B3 fix) ------------------
        # focus first: the control's search callback returns early unless the
        # input still holds focus (link.js), so nothing may steal it mid-type.
        field.click()
        with page.expect_response(
            lambda r: "search_link" in r.url, timeout=30000
        ) as search:
            field.fill("Carla")
        assert search.value.ok, search.value.text()
        values = [row.get("value") for row in search.value.json()["message"]]
        assert CUSTOMER in values, (
            f"the desk form's Customer search did not return the seeded portal "
            f"customer — Backlog B3 is back. Returned: {values}"
        )

        # The rendered option, and the click a human would make. Awesomplete
        # renders each item as a role=option node whose own click handler calls
        # awesomplete.select -> parse_validate_and_set_in_model.
        option = page.locator(
            ".frappe-control[data-fieldname='customer'] .awesomplete "
            "[role='option']", has_text="Carla"
        ).first
        option.wait_for(state="visible", timeout=15000)
        option.click()
        page.wait_for_function(
            "(want) => window.cur_frm && cur_frm.doc && cur_frm.doc.customer === want",
            arg=CUSTOMER,
            timeout=15000,
        )

        # --- the control: a STAFF user must NOT be offered -----------------
        # This is what discriminates the new query from the old one. Stock
        # `user_query` filters `user_type != 'Website User'`, so it returned
        # staff and hid customers — precisely inverted from what the desk needs.
        field.click()
        with page.expect_response(
            lambda r: "search_link" in r.url, timeout=30000
        ) as staff_search:
            field.fill("AyalaStaff")
        assert staff_search.value.ok, staff_search.value.text()
        staff_values = [
            row.get("value") for row in staff_search.value.json()["message"]
        ]
        assert STAFF_PROBE not in staff_values, (
            f"the Customer search offered a STAFF user — the form is back on "
            f"frappe's stock user_query. Returned: {staff_values}"
        )

        # Leave the form cleanly: a wedged dirty form breaks later navigation in
        # the shared worker context (S3 pattern).
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)

    def test_dialog_total_is_server_truth_member(self, page: Page):
        """Backlog B4 for an account holder: the number staff READ ALOUD is the
        number the booking and its statement carry.

        MEMBER_COURT is ₱450/hr flat and MIA is an AYALA VIP at 20%, so the whole
        chain must say ₱360.00 — and it is asserted against an INDEPENDENT server
        quote as well as against the booking, so the dialog cannot agree with
        itself.
        """
        _clean(page, MEMBER_COURT, D_MEMBER)
        load_board(page, BGC_BRANCH, D_MEMBER)

        # B46: the desk selects on the board, then opens ONE dialog for the set.
        select_slots(page, MEMBER_COURT, "09:00:00", 1)
        open_cart_dialog(page)
        gestures.fill(page, "customer", MIA, scope=gestures.DIALOG)
        # The hint means the membership lookup answered; wait_money_settled means
        # the re-quote it triggered has rendered. Since B4 the first no longer
        # implies the second — the hint is written in the same .then that STARTS
        # the quote.
        page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
        expect(
            page.locator(".modal.show [data-testid='member-hint']")
        ).to_contain_text("VIP")
        wait_money_settled(page)

        on_screen = _displayed_total(page)
        expected = _server_quote(MEMBER_COURT, D_MEMBER, "09:00:00", customer=MIA)
        assert on_screen == float(expected["total_amount"]), (
            f"the dialog shows {on_screen} but the server prices this at "
            f"{expected['total_amount']} — B4's whole claim is that these cannot "
            f"disagree"
        )
        assert on_screen == 360.0, on_screen  # ₱450 − 20%

        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        wait_money_settled(page)
        name = _submit_cart(page)["name"]

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{booking['billing_doc']}"
        )
        assert float(booking["total_amount"]) == on_screen, booking
        assert float(invoice["total_amount"]) == on_screen, invoice
        assert float(booking["discount_percent"]) == 20.0, booking

        _clean(page, MEMBER_COURT, D_MEMBER)

    def test_dialog_total_walkin_discount_override(self, page: Page):
        """B4 for the other half of the desk: a walk-in with a discount STAFF
        typed, not one a membership produced.

        A walk-in has no account and therefore no membership, so a client-side
        multiplication would have looked right here for the wrong reason. 25% is
        a number no seeded tier grants, which is what makes the row discriminate.
        """
        _clean(page, MEMBER_COURT, D_WALKIN)
        load_board(page, BGC_BRANCH, D_WALKIN)

        select_slots(page, MEMBER_COURT, "09:00:00", 1)
        open_cart_dialog(page)
        # walk_in FIRST: it drives depends_on, and the name control does not
        # exist in the layout until the Check flips.
        gestures.check(page, "walk_in", True, scope=gestures.DIALOG)
        gestures.fill(page, "customer_name", WALKIN_NAME, scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        page.wait_for_selector(".modal.show [data-testid='member-hint']", timeout=15000)
        # THEN the staff discount, so it cannot be overwritten by the walk-in
        # branch's forced reset to 0.
        gestures.fill(page, "discount_percent", STAFF_DISCOUNT, scope=gestures.DIALOG)
        wait_money_settled(page)

        on_screen = _displayed_total(page)
        assert on_screen == 337.5, on_screen  # ₱450 − 25%

        name = _submit_cart(page)["name"]

        booking = _get_json(page, f"/api/resource/CBT Court Booking/{name}")
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{booking['billing_doc']}"
        )
        assert float(booking["total_amount"]) == on_screen, booking
        assert float(invoice["total_amount"]) == on_screen, invoice
        # Key ABSENCE, never `is None`: /api/resource serialises with
        # no_nulls=True, so a NULL field is dropped entirely (S13 as-built 15).
        assert "customer" not in booking, booking.get("customer")
        assert booking["customer_name"] == WALKIN_NAME, booking

        _clean(page, MEMBER_COURT, D_WALKIN)


@pytest.mark.e2e
class TestRunningHour:
    """B7 — the hour that is RUNNING, on a company at knob 0.

    Knob 0 is the STRONGER statement: section-16 shipped this relaxation gated on
    `no_show_release_minutes > 0`, and B7's claim is that it is knob-independent.
    AYALA never opts in, here or in the seeds.
    """

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def pretend_afternoon(cls):
        """Pin the clock for THIS class only, and release it whatever happens.

        `@classmethod` is required, not decoration: pytest deprecates a
        class-scoped fixture defined as an instance method (PytestRemovedIn10),
        because each test gets a fresh instance while the fixture runs once. This
        one sets no attributes either way, but a deprecation warning here would
        land in the same pytest warnings summary the suite greps for
        `[B6-RETRY]` markers — noise in the one channel that must stay readable.

        Class scope, not file 14's module scope: `TestDeskTruth`'s money rows are
        on future dates and neither need nor want a pretend now, and two fewer
        `bench execute` round trips is real time against this file's budget.

        There is no knob to nest a restore around — see the module docstring.
        """
        try:
            _pin_clock_to_pretend_afternoon()
            yield
        finally:
            _set_clock(0)

    def test_running_hour_bookable_knob_off(self, page: Page):
        """The ungate itself, plus the state it renders as (the USER DECISION:
        its own "in progress" look, not a plain green chip).

        The booking is asserted to be auto-checked-in, which is not incidental:
        it is what stops the slot being swept away again if the facility later
        opts into no-show release, and it is why reselling a released hour sticks.
        """
        _clean(page, RUNNING_COURT, TODAY)
        load_board(page, BGC_BRANCH, TODAY)

        start = _running_start(page, RUNNING_COURT)
        chip = slot(page, RUNNING_COURT, start)
        assert chip.get_attribute("data-status") == "available", (
            f"the running hour ({start}) is not bookable. Before section-18 this "
            f"read 'past' unless the company had opted into no-show release — "
            f"that gate is gone, so this is a real regression in slots.py."
        )
        # `data-status` deliberately stays "available" — the click handler and
        # every E2E selector key on it — so the new state is a CLASS.
        assert "cbt-slot-running" in (chip.get_attribute("class") or ""), (
            "the running hour must render as its own state, not as a plain free "
            "chip: booking it means the session has already started"
        )
        assert "in progress" in chip.inner_text().lower(), chip.inner_text()
        # The legend has to explain the new look where staff go to read colours.
        expect(page.locator(".cbt-legend")).to_contain_text("In progress")

        # Backlog B9, and this file is the only place in the suite that can assert
        # it: the "Updated" stamp must agree with the SERVER clock the countdowns
        # beside it use, not with the browser's.
        #
        # A BOUNDED DIFFERENCE, not a string match on HH:mm — the stamp is written
        # once when the load lands while `server_now_ms()` keeps advancing, so a
        # formatted comparison would go red whenever the two straddle a minute
        # boundary. That would be a test defect, not a product one.
        #
        # Compared against the payload's own clock rather than a literal, so the
        # row is not itself wall-clock dependent. It cannot DISCRIMINATE on a run
        # that genuinely starts between 14:00 and 14:10 real time (the offset is
        # then ~0 and the two clocks agree anyway) — but it can never falsely
        # fail, which is the property that matters.
        stamp = page.locator(".cbt-board-updated").inner_text()
        clock_match = re.search(r"(\d{2}):(\d{2}):(\d{2})", stamp)
        assert clock_match, f"no time in the Updated stamp: {stamp!r}"
        stamp_seconds = (
            int(clock_match.group(1)) * 3600
            + int(clock_match.group(2)) * 60
            + int(clock_match.group(3))
        )
        server_seconds = page.evaluate(
            """() => {
                const board = frappe.pages['cbt-court-board'].court_board;
                const at = new Date(board.server_now_ms());
                return at.getHours() * 3600 + at.getMinutes() * 60 + at.getSeconds();
            }"""
        )
        assert abs(server_seconds - stamp_seconds) <= 120, (
            f"the board stamped 'Updated {clock_match.group(0)}' but its own "
            f"server clock reads {server_seconds // 3600:02d}:"
            f"{server_seconds % 3600 // 60:02d} — the stamp is being drawn from "
            f"the BROWSER clock (Backlog B9) while every countdown beside it uses "
            f"the server's."
        )

        # A WALK-IN, because that is the case B7 exists for: a stranger turns up
        # mid-hour with cash and the desk has to be able to sell them the court
        # that is standing empty right now.
        booked = quick_book_walkin(page, RUNNING_COURT, start, "Cash", WALKIN_NAME)
        assert booked.startswith("BK-AYALA-"), booked
        page.wait_for_selector(
            f".cbt-slot[data-court='{RUNNING_COURT}'][data-start='{start}']"
            "[data-booking-status='Confirmed']",
            timeout=20000,
        )

        row = _get_json(page, f"/api/resource/CBT Court Booking/{booked}")
        assert row["booking_status"] == "Confirmed", row
        assert row["checked_in_at"], (
            "a back-record of a started slot must be born checked in — otherwise "
            "the very next sweep could take the court away from somebody standing "
            "on it (S16 as-built 8, re-pinned here at knob 0)"
        )

        _clean(page, RUNNING_COURT, TODAY)

    def test_running_hour_stays_past_on_portal(self, page: Page, customer_page: Page):
        """The other half of the same rule, and the reason it is not symmetric.

        The controller refuses a CUSTOMER booking of a started slot
        (`_reject_past_for_customers`), so the portal must not offer the hour the
        desk can still sell — a grid that offers what the server will refuse is a
        grid that lies. Pinned from the E2E side for the first time here.

        Cleans with the ADMIN page (the portal cannot cancel a confirmed booking)
        and asserts with the customer's, so a leftover booking from the row above
        cannot turn `past` into `booked` and pass this for the wrong reason.
        """
        _clean(page, RUNNING_COURT, TODAY)
        load_board(page, BGC_BRANCH, TODAY)
        start = _running_start(page, RUNNING_COURT)

        customer_page.goto(
            f"/book?c={AYALA_COMPANY_SLUG}&b={BGC_SLUG}&d={TODAY}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        customer_page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
        chip = customer_page.locator(
            f".cbt-slot[data-court='{RUNNING_COURT}'][data-start='{start}']"
        )
        chip.wait_for(state="visible", timeout=15000)
        assert chip.get_attribute("data-status") == "past", (
            f"the portal offered the RUNNING hour ({start}). The controller "
            f"refuses it, so this grid must not show it as bookable — "
            f"get_public_availability must never pass past_from_end."
        )
        assert chip.is_disabled(), "a past chip must not be clickable"

        # ...and a LATER hour on the same grid is still bookable, so the row
        # cannot pass merely because the whole grid failed to render.
        later = customer_page.locator(
            f".cbt-slot[data-court='{RUNNING_COURT}'][data-status='available']"
        )
        assert later.count() > 0, (
            "no bookable slot anywhere on today's portal grid — the assertion "
            "above would then be meaningless"
        )

        _clean(page, RUNNING_COURT, TODAY)
