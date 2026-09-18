"""
E2E file 14 — No-show release (section-16, PLAN §8s).

The desk story end to end: a paid slot nobody turned up for goes back on sale,
a walk-in buys the freed hour, and the facility keeps both payments — while a
release made in error is one click from being undone. Check-in is the thing
that prevents all of it, so it is proven first.

Runs on the PLATFORM seat, the suite's default since Backlog B43 (it was
Administrator until 2026-09-05). The staff-actor positives live in the BACKEND
rows — tests/test_no_show.py proves the verification stamps and the peso/hour
split between the revenue and occupancy reports, which a single-actor E2E
cannot. `checked_in_by` below asserts the seat, so a default that drifts back
has to change that line to do it.

Date map (all relative to the run day). Ledger CORRECTED 2026-08-18 (Backlog B16):
this file used to say "+40..+47 file 06", which is wrong — **+45 belongs to file 02**,
whose `_qcsm_open_date()` slides +45 -> +46 when the run day makes +45 a Sunday.
The re-verified ledger, copied from file 15:

  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   file 13
  +60/+61   files 08 AND 10 (see B16)       +62       file 10
  +63/+64   file 11                         +66       reserved in prose, never used
  +67/+68   file 15                         +69/+70   file 16
  +71/+72   file 17                         +73/+74   file 18
  **+75 upward is FREE.**

THIS FILE CLAIMS **+65**, which the section-13, -14 and -15 ledgers all reserved
for section-16.

  +65  E2EF-main-court-1 — the check-in fixture (a future booking, nothing
       backdated, no sweep)

**AND TODAY**, which is a surface NO ledger day protects — because it cannot be
otherwise: a release only happens to a booking that is IN PROGRESS, and only
today has one. Two consequences the whole file is built around:

- EVERY test cleans its court × date FIRST, including `No Show` rows (a
  released booking is not "active", so the stock helper filter would walk past
  it). That is what makes the file re-runnable without a reset.
- Every test mints, backdates, sweeps and cleans up its OWN booking. Nothing is
  chained: a failure in one row must not cascade into a false failure in the
  next, and the module fixture's inline-restore doctrine only holds if no test
  depends on another's leftovers.

**THIS FILE PINS THE SERVER CLOCK, so it runs at ANY wall-clock time —
including 23:05.** That is section-17's whole point (Backlog B8). Before it,
the three release rows needed a booking IN PROGRESS *and* a still-bookable board
chip, and E2E Fast's hourly grid ended at 22:00–23:00 (its branch closes 23:59,
which read literally until 2026-09-02 — it now means midnight and the grid runs to
24:00) — so this file failed for ~70 minutes a day and cost
section-16 two of its three budget passes. **A green suite with an undeclared
wall-clock precondition is unsound, not flaky**, and shrinking the window was
never the fix.

The module fixture's FIRST act is to pin pretend-now to `PRETEND_TIME` on TODAY
through `testing.set_test_clock_offset`, and to ASSERT the server agrees before
any row runs. One offset moves the sweep, `_availability`, the board payload and
the portal together, because `clock.now_dt()` is this app's single time read
(grep-enforced by `tests/test_slots.py`).

WHY A TIME ON *TODAY*, AND WHY 14:05:

- **Today**, because every date constant in this suite is computed HOST-side
  with `date.today()` and the ledger is a map of `today + N`. The offset is
  derived from THIS module's `TODAY` constant, so pretend-date == the date the
  assertions use, at every real wall-clock time from 00:00 to 23:59 — and even
  across a real midnight mid-run, because the offset is STATIC: pretend time
  just keeps advancing from 14:05 on TODAY. That is strictly better than the old
  behaviour, where a run crossing real midnight booked on a `TODAY` that had
  become yesterday, so the booking was past its end and the sweep's post-end
  tidy Completed it instead of releasing it.
- **14:05, not 14:00**, because of a two-chip band. A booking backdated
  `BACKDATE` minutes from pretend-now starts in the PREVIOUS hour while that
  hour has already ENDED — the shape `_running_chip` is written for — exactly
  when the pretend minute is in `(0, 10)`. `slots.py` marks a chip `past` with a
  STRICT `<`, so at 14:00:00 the previous chip is not yet past and
  `_running_chip` would hand back a chip that has just ended. Nothing would go
  red (staff may legitimately book an ended slot), but the resale row would
  quietly stop testing "resell the hour that is RUNNING", which is its entire
  point. 14:05 sits in the middle of the band with rounding slack either side.

THE CLOCK AND THE KNOB ARE BOTH SHARED SITE STATE. `e2e-fast` ships with
`no_show_release_minutes = 0` (the feature is default OFF). The fixture sets the
clock FIRST and the knob second; teardown restores them in the opposite order
and the two restores are NESTED, so a throw while releasing the clock cannot
leave the knob on. The offset is the more dangerous of the two to leak — the
knob only ever affects bookings happening right now, whereas a stale offset
makes the whole site lie about time — which is also why it carries a TTL and why
`conftest.real_clock` clears it once per session.

Only this file's own worker is ever affected: the offset is namespaced per site
in redis, `--dist loadfile` pins a file to one worker, and tests within a worker
run sequentially. Nothing schedules `bench schedule` either, so the per-minute
cron cannot fire under the pretend clock — the sweep runs only when a row asks
for it.

No sleeps. Every assertion waits on a rendered node or a server response.
"""
import json
from datetime import date, datetime, time, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers.board import (
    ACTIVE_OR_RELEASED_STATUSES,
    E2EF_BRANCH,
    E2EF_COURT,
    cancel_active_bookings,
    csrf,
    load_board,
    quick_book,
    quick_book_walkin,
    slot,
    wait_dialog,
)
from helpers.auth import PLATFORM_ADMIN
from helpers.worker_routing import bench_execute

E2EF_COMPANY = "e2e-fast"
GRACE = 2  # minutes of grace this file gives a late player
BACKDATE = 10  # how far into the past a fixture booking is moved

D_CHECKIN = (date.today() + timedelta(days=65)).isoformat()
TODAY = date.today().isoformat()

# Pretend-now, on TODAY. See the module docstring for why the minute matters:
# any value in (0, BACKDATE) gives _running_chip its designed two-chip shape.
PRETEND_TIME = time(14, 5)
# Seconds. Covers minute-granular offset rounding plus the latency of the second
# bench_execute. NOT a fudge factor for a clock that half-landed — if this trips,
# something is wrong with the lever or the environment.
CLOCK_DRIFT_TOLERANCE = 120

WALKIN_NAME = "Walk-in Wanda S16"


def _set_knob(minutes: int):
    # check=False so the assert below is REACHABLE. bench_execute defaults to
    # check=True, which makes subprocess.run raise CalledProcessError first —
    # and its message carries the command and exit status but NOT stderr, so
    # every "assert returncode == 0, result.stderr" in this suite is dead code
    # unless the caller opts out. Worth knowing: `bench execute` also wraps the
    # call in try/except and falls back to eval(method), so a _guard() throw
    # surfaces as a NameError about the module path rather than the real reason.
    result = bench_execute(
        "court_booking_tech.testing.set_no_show_release",
        json.dumps([E2EF_COMPANY, minutes]),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def _set_clock(minutes: int) -> datetime:
    """Pin (0 = release) this worker's site clock; return the server's now.

    The RETURN VALUE is the assertion surface. `bench_execute` runs in its own
    process, so nothing else here proves what the web workers will read.
    """
    result = bench_execute(
        "court_booking_tech.testing.set_test_clock_offset",
        json.dumps([minutes]),
        check=False,  # see _set_knob: check=True would hide stderr
    )
    assert result.returncode == 0, result.stderr
    # Explicit scan rather than next(genexpr): a StopIteration raised inside the
    # generator fixture below would surface as a bare RuntimeError (PEP 479),
    # losing the one piece of context worth having.
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
    while the assertion below still passed — the tolerance masking exactly the
    defect it exists to catch.
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
        f"assertion is what makes every row below independent of the wall clock "
        f"— do not loosen it to get green."
    )
    return pretend_now


def _backdate(booking: str, minutes: int = BACKDATE):
    """Move a booking so it started `minutes` ago and is still running.

    The lever also CLEARS the check-in stamps, which matters here: the board
    only offers a slot the staff can click, and if the run lands late enough
    that the chosen slot has already started, the booking is born checked in
    (a staff back-record) and would be correctly immune to release.
    """
    result = bench_execute(
        "court_booking_tech.testing.backdate_booking_start",
        json.dumps([booking, minutes]),
        check=False,  # see _set_knob: check=True would hide stderr
    )
    assert result.returncode == 0, (
        f"backdate_booking_start failed. Its midnight guard should be "
        f"unreachable under the pinned clock, so if this says 'cross midnight' "
        f"suspect the clock fixture rather than the helper: {result.stderr}"
    )
    return result


def _sweep(page: Page) -> dict:
    """Run the real sweep on demand (allow_tests-gated) — never wait on cron."""
    resp = page.request.post(
        "/api/method/court_booking_tech.tasks.run_expiry_sweep",
        headers={
            "X-Frappe-CSRF-Token": csrf(page),
            "Content-Type": "application/json",
        },
        data="{}",
    )
    assert resp.ok, f"run_expiry_sweep: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]


def _get_json(page: Page, url: str) -> dict:
    resp = page.request.get(url)
    assert resp.ok, f"GET {url}: HTTP {resp.status}"
    return resp.json()["data"]


def _clean(page: Page, booking_date: str):
    """Clear the court × date, RELEASED rows included."""
    cancel_active_bookings(
        page, E2EF_COURT, booking_date, statuses=ACTIVE_OR_RELEASED_STATUSES
    )


def _bookable_slot(page: Page) -> str:
    """A free slot on today's board — an UN-STARTED one for preference.

    The honest story is a booking made in advance that nobody turned up for, so
    a future slot is chosen when the day still has one. But E2E Fast's last slot
    starts at 22:00, and after that the only free slot left is the hour in
    progress — which a real desk books all the time (a back-record) and which
    works here for the same reason it is safe: `backdate_booking_start` CLEARS
    the check-in stamps, so the auto-check-in a back-record is born with cannot
    quietly make the fixture immune to the very sweep under test.

    Under the pinned clock (pretend 14:05) the future branch always wins, since
    E2E Fast's grid runs to 22:00. The fallback is kept anyway: it costs nothing,
    it is still correct, and it keeps this helper honest if a later section pins
    the clock somewhere else.

    Both timestamps come from the SERVER (`data.server_now`, `data.date`) and are
    parsed the same way, so the browser's own clock and timezone cancel out of
    the comparison — which is what lets this work while the site pretends.
    """
    start = page.evaluate(
        """(court) => {
            const board = frappe.pages['cbt-court-board'].court_board;
            const data = board.board;
            // frappe.datetime.str_to_obj, never `new Date(...)`: server_now
            // carries MICROseconds and the board itself parses it this way to
            // build its clock offset.
            const now = frappe.datetime.str_to_obj(data.server_now);
            const row = data.courts.find((c) => c.court === court);
            const free = row.slots.filter((s) => s.status === 'available');
            const future = free.find(
                (s) => frappe.datetime.str_to_obj(data.date + ' ' + s.start_time) > now
            );
            return (future || free[0] || {}).start_time || null;
        }""",
        E2EF_COURT,
    )
    assert start, (
        "no free slot left on E2EF today. The clock is pinned to the early "
        "afternoon, so the grid has both future hours and an hour in progress — "
        "if nothing is free, a previous row leaked bookings onto this court and "
        "date (every row cleans its own court x date FIRST, 'No Show' rows "
        "included)."
    )
    return start


def _slots_showing(page: Page, booking: str) -> list:
    """Which grid chips currently render THIS booking.

    Read from the board rather than computed from the clock: a backdated
    booking starts at an arbitrary minute and overlaps one or two hourly slots,
    and asserting on "the slot containing now" would race the top of the hour.
    Whatever the chips were before the sweep is exactly what must be free
    after it.
    """
    return page.eval_on_selector_all(
        f".cbt-slot[data-booking='{booking}']", "els => els.map((e) => e.dataset.start)"
    )


def _running_chip(page: Page, occupied: list) -> str:
    """Of the chips the booking held, the one that is BOOKABLE again.

    A booking backdated by BACKDATE minutes starts mid-hour, so on an hourly
    grid it straddles TWO chips — and the earlier one has already ended, which
    makes it `past` and unclickable however free it is. The hour containing
    `now` is the one the desk can actually resell, and it is the only chip this
    file has any business asserting on.

    `running[0]` is the earliest of the chips STILL AVAILABLE. In the shape this
    file actually produces that is the LATER of the two chips the booking held:
    pretend-now is 14:05-ish, the booking runs 13:55–14:55, and the 13:00 chip
    has ended, so only 14:00 is available and it is the hour containing now.

    The offset is STATIC, so pretend time advances only as fast as real time, and
    this file takes 80–92 seconds (measured across section-17's three budget
    passes) — every row therefore runs at pretend 14:05–14:07 and gets that one
    shape. (An earlier draft of this docstring
    claimed both possible shapes occur within one run. They do not, and the
    section-16 as-built 13a lesson is exactly about not making confident claims
    about your own test.) A file slow enough to push the pretend minute past
    BACKDATE would see the booking start inside the current hour instead, giving
    the running chip and the next one — still earliest-first, so `running[0]`
    stays correct; the boundary is continuous, not a cliff.

    `_slots_showing` reads the chips in document order, which is grid order (the
    board appends over `court.slots` ascending), and the dict and list
    comprehensions below preserve it.
    """
    statuses = {
        start: page.locator(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='{start}']"
        ).get_attribute("data-status")
        for start in occupied
    }
    running = [start for start, status in statuses.items() if status == "available"]
    assert running, (
        f"no freed chip came back on sale — chips {statuses}. If a chip reads "
        "'booked', THAT is a real failure — the release did not free the slot. "
        "TWO causes this message used to name are no longer possible. 'Every "
        "chip reads past' cannot happen: the clock is pinned to mid-afternoon "
        "(section-17) and the fixture asserts it landed. And 'the staff "
        "relaxation is not applying because of the knob' cannot happen either: "
        "section-18 (Backlog B7) dropped the knob gate, so `past_from_end` is "
        "unconditional for every staff view. A running hour reading 'past' now "
        "means the ENGINE is wrong, not that this file's fixture is."
    )
    return running[0]


def _released_fixture(page: Page) -> tuple:
    """Mint a booking, backdate it past the grace, sweep, and hand back the
    booking plus the chips it used to occupy. The shared spine of three tests —
    each of which owns its own booking, so none can cascade into another."""
    _clean(page, TODAY)
    load_board(page, E2EF_BRANCH, TODAY)

    booking = quick_book(page, E2EF_COURT, _bookable_slot(page), "Cash")
    assert booking.startswith("BK-E2EF-"), booking

    _backdate(booking)
    load_board(page, E2EF_BRANCH, TODAY)
    occupied = _slots_showing(page, booking)
    assert occupied, "the backdated booking should occupy the running slot"

    assert booking in _sweep(page)["no_show"], "the sweep did not release it"
    return booking, occupied


def _open_no_show_dialog(page: Page):
    page.locator("[data-testid='no-show-chip']").click()
    wait_dialog(page)


@pytest.fixture(scope="module", autouse=True)
def pretend_afternoon_with_release_on():
    """Pin the clock, then opt E2E Fast into no-show release, for this file only.

    CLOCK FIRST: the knob and every row below it are meaningless unless the site
    is already on the pretend clock. Teardown restores in the opposite order and
    the restores are NESTED, so a throw while releasing the clock cannot leave
    the knob on — both are shared site state and both must come back. Both
    writes are absolute rather than relative, so running them after a setup that
    never got that far is harmless.
    """
    try:
        _pin_clock_to_pretend_afternoon()
        _set_knob(GRACE)
        yield
    finally:
        try:
            _set_clock(0)
        finally:
            _set_knob(0)


@pytest.mark.e2e
class TestNoShow:

    def test_check_in_keeps_a_late_players_court(self, page: Page):
        """The click that prevents everything else in this file. One tap at the
        board and the slot is theirs for the rest of the hour."""
        _clean(page, D_CHECKIN)
        load_board(page, E2EF_BRANCH, D_CHECKIN)

        booking = quick_book(page, E2EF_COURT, "10:00:00", "Cash")
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            "[data-booking-status='Confirmed']",
            timeout=15000,
        )

        slot(page, E2EF_COURT, "10:00:00").click()
        wait_dialog(page)
        with page.expect_response(
            lambda r: "bookings.check_in" in r.url, timeout=30000
        ) as resp_info:
            page.locator(".modal.show [data-action='check-in']").click()
        assert resp_info.value.ok, resp_info.value.text()

        # The board reloads itself, and the chip has to SAY they are here —
        # that mark is the only thing on the grid that distinguishes a court
        # in use from one about to be given away.
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='10:00:00']"
            " [data-testid='checked-in']",
            timeout=20000,
        )

        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert row["checked_in_at"], row
        assert row["checked_in_by"] == PLATFORM_ADMIN, row

        # Reopening shows who greeted them, and stops offering the action.
        slot(page, E2EF_COURT, "10:00:00").click()
        wait_dialog(page)
        expect(page.locator(".modal.show [data-testid='detail-checked-in']")).to_be_visible()
        assert page.locator(".modal.show [data-action='check-in']").count() == 0, (
            "Check in must disappear once it has happened — a button that "
            "silently no-ops is how staff stop trusting the one they need"
        )
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        _clean(page, D_CHECKIN)

    def test_the_sweep_releases_an_unattended_slot_back_onto_the_board(
        self, page: Page
    ):
        """The feature itself. The chips the booking held come back bookable,
        the money is untouched, and the release is reachable from the board —
        a released booking holds no slot, so without the No-shows chip it would
        vanish from the desk's world entirely."""
        booking, occupied = _released_fixture(page)

        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert row["booking_status"] == "No Show", row
        # The money never moves: that is the whole doctrine of this section.
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]

        load_board(page, E2EF_BRANCH, TODAY)
        assert _slots_showing(page, booking) == [], (
            "a released booking must hold no slot on the board"
        )
        # ...and the hour that is running is bookable again, which is the
        # resale this whole feature exists for.
        _running_chip(page, occupied)

        _open_no_show_dialog(page)
        expect(
            page.locator(f".modal.show .cbt-noshow-row[data-booking='{booking}']")
        ).to_be_visible()
        page.evaluate("() => cur_dialog.hide()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        _clean(page, TODAY)

    def test_undo_puts_a_released_booking_back_on_the_board(self, page: Page):
        """Mistakes happen at a busy desk — somebody was on court and nobody
        pressed the button. Undo is one click from the same chip that reported
        the release, and it must restore the booking, not a copy of it."""
        booking, occupied = _released_fixture(page)
        load_board(page, E2EF_BRANCH, TODAY)
        running = _running_chip(page, occupied)

        _open_no_show_dialog(page)
        with page.expect_response(
            lambda r: "undo_no_show" in r.url, timeout=30000
        ) as resp_info:
            page.locator(
                f".modal.show .cbt-noshow-row[data-booking='{booking}'] "
                "[data-action='undo']"
            ).click()
        assert resp_info.value.ok, resp_info.value.text()

        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='{running}']"
            f"[data-booking='{booking}']",
            timeout=20000,
        )
        row = _get_json(page, f"/api/resource/CBT Court Booking/{booking}")
        assert row["booking_status"] == "Confirmed", row
        # Restored AND marked present — otherwise the very next sweep takes it
        # away again and the button looks broken.
        assert row["checked_in_at"], row
        invoice = _get_json(
            page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}"
        )
        assert invoice["status"] == "Paid & Verified", invoice["status"]

        # It really is off the release path now.
        assert _sweep(page)["no_show"] == []

        _clean(page, TODAY)

    def test_a_walk_in_buys_the_freed_hour_and_both_payments_stand(
        self, page: Page
    ):
        """The revenue story, end to end. The forfeited payment stays paid, the
        stranger with cash gets a court that was standing empty, and the
        facility holds two valid statements for the same hour — which is the
        argument for building this at all."""
        no_show, occupied = _released_fixture(page)
        load_board(page, E2EF_BRANCH, TODAY)
        running = _running_chip(page, occupied)

        resold = quick_book_walkin(page, E2EF_COURT, running, "Cash", WALKIN_NAME)
        assert resold.startswith("BK-E2EF-"), resold
        page.wait_for_selector(
            f".cbt-slot[data-court='{E2EF_COURT}'][data-start='{running}']"
            f"[data-booking='{resold}']",
            timeout=20000,
        )

        released_row = _get_json(page, f"/api/resource/CBT Court Booking/{no_show}")
        resold_row = _get_json(page, f"/api/resource/CBT Court Booking/{resold}")
        assert released_row["booking_status"] == "No Show", released_row
        assert resold_row["booking_status"] == "Confirmed", resold_row
        # The replacement is a back-record — the player is standing at the desk
        # — so it is born checked in and the next sweep leaves it alone.
        assert resold_row["checked_in_at"], resold_row
        assert _sweep(page)["no_show"] == []

        for row in (released_row, resold_row):
            invoice = _get_json(
                page, f"/api/resource/CBT Booking Invoice/{row['billing_doc']}"
            )
            assert invoice["status"] == "Paid & Verified", (
                f"{row['name']}: {invoice['status']}"
            )

        _clean(page, TODAY)
