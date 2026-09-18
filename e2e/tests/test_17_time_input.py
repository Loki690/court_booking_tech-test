"""
E2E file 17 — Human time input (section-21, Backlog B12).

Every editable Time field in this app used to render frappe's Air-Datepicker in
timepicker-only mode: with the default HH:mm:ss system format that is THREE
range sliders — hour, minute AND second. The user's words were "the UI is like 3
sliders which is insane". Two layers replace it, and this file pins both:

- **the floor** — `frappe.ui.form.ControlTime` is now a ControlData subclass
  rendering a native `<input type="time">` (typed 2-digit segments, the
  browser's own picker, and an AM/PM segment on an en-US desk). Site-wide, via
  the app's first `app_include_js` hook.
- **the grid surface** — the Court Board's Block Slots dialog no longer asks for
  a clock value at all. From/To are Selects of the branch's REAL slot times,
  with To filtered to the ends after the chosen From, so the window this dialog
  can express is always one `CBT Slot Block._validate_window` accepts.

Runs as Administrator, admin-driven like files 06/11/12/13/14/16.

**THE B9 ROW THIS FILE WAS PLANNED WITH DOES NOT EXIST, deliberately.**
Section-21's plan carried `test_board_updated_stamp_uses_server_clock` as a
"ships from here REGARDLESS" item. It is SPENT: section-18 shipped B9's code
(both boards stamp `moment(this.server_now_ms())` —
`cbt_court_board.js::load`, `cbt_open_play_board.js::load`) AND its E2E pin
(file 15 row 4, as a bounded difference against the payload's own server clock).
Writing it here would duplicate file 15, and the plan's sketched assertion shape
(`assert "Updated 14:0" prefix`) is wall-clock-fragile in exactly the two ways
file 15 avoids. Verified by grep at build time; no row, and no clock lever in
this file at all.

Date map (all relative to the run day). THE FULL E2E LEDGER, re-verified this
session by reading EVERY `timedelta(days=…)` in e2e/tests rather than copying a
recital forward — which is how Backlog B16 happened:

  +30/+31 test_booking_form · +32..+34 test_proof_flow · +35/+36
  test_billing_print · +37..+39 file 09 · +40..+44 and +46/+47 file 06 ·
  **+45 file 02** (NOT file 06 — and it slides to +46 when the run day makes it
  a Sunday, landing on file 06's D_MEMBER: Backlog B16 (b)) · +48/+49 file 12 ·
  +50/+51 file 05 · +52..+57 file 07 · **+60/+61 files 08 AND 10 both**
  (Backlog B16 (a), a real double-claim surviving on a one-hour margin) ·
  +58/+59 file 13 · +62 file 10 · +63/+64 file 11 · +65 file 14 (plus TODAY) ·
  +67/+68 file 15 (plus TODAY) · +69/+70 file 16. **+66 is unclaimed.**

**THIS FILE CLAIMS +71 and +72**, on AYALA-makati — a branch NO other file's
block tests touch (file 06 blocks E2EF-main and AYALA-bgc; file 07 blocks
E2EF-main only), open 06:00–22:00 every day of the week at the platform-default
60-minute grid, so the grid is the same whatever weekday the run lands on:

  +71  AYALA-makati — the dialog's grid dropdowns, and a block made through them
  +72  AYALA-makati — the CBT Slot Block desk form's native time inputs

**AND +77 (Backlog B28, 2026-08-27)**, AYALA-makati again — a block made over a
Confirmed booking, the warn-not-refuse row. +73..+76 were claimed after the
ledger above was written (file 10's re-cast carries +75/+76 and records the
re-verified ledger as claimed through +74); a `qmd search` for `days=77` and
`days=78` across e2e/tests found no claimant — ranked, not exhaustive, so the
claim is also recorded here where the next ledger sweep will read it.

`test_business_hours_grid_native_time_input` claims NO date and NEVER SAVES —
see its docstring. Neither B16 defect is introduced or touched here.

`helpers.board.clear_blocks` is branch × date scoped (not court × date), which
is safe precisely because +71/+72 are this file's alone and `--dist loadfile`
keeps the whole file on one worker, hence one site.

No sleeps. Every assertion waits on a rendered node, a server response, or the
dialog's own settle flag.
"""
import json
import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from helpers.board import (
    platform_api,
    block_option_values,
    book_slot_via_api,
    cancel_active_bookings,
    clear_blocks,
    load_board,
    wait_block_grid,
    wait_dialog,
)
from helpers import gestures
from helpers.desk_form import wait_for_new_form
from helpers.navigation import wait_for_page_load
from helpers.worker_routing import bench_execute

MAKATI_BRANCH = "AYALA-makati"
# CBT Branch is a title-link doctype (show_title_field_in_link:1, title_field:
# branch_name), so the option a human clicks shows the TITLE while the server searches
# the NAME. Both strings are needed to drive the control by hand.
MAKATI_BRANCH_TITLE = "Makati Arena"
MAKATI_COURT = "AYALA-makati-court-a"

D_GRID = (date.today() + timedelta(days=71)).isoformat()
D_FORM = (date.today() + timedelta(days=72)).isoformat()

# On the grid (AYALA-makati runs 06:00–22:00, 60-minute slots).
BLOCK_START = "09:00:00"
BLOCK_END = "10:00:00"

# Backlog B28: a TWO-slot window over a one-slot Confirmed booking at 09:00, so
# the board can show both halves of the promise at once — 09:00 still booked,
# 10:00 blocked.
D_WARN = (date.today() + timedelta(days=77)).isoformat()
WARN_BLOCK_END = "11:00:00"
WARN_FREE_SLOT = "10:00:00"
WARN_TITLE = "Confirmed bookings in the blocked window"

# Deliberately NOT on the grid, and deliberately a single-digit hour. The desk
# FORM carries no grid constraint (only the board dialog does), so this proves
# the native input takes an arbitrary typed minute — and 07:30 is exactly the
# value that exercises frappe's leading-zero trap: `format_timedelta` renders
# hours with `:01` padding (frappe/utils/data.py:2679), so the server ships this
# back as "7:30:00", which is NOT a valid `<input type="time">` value.
FORM_START = "07:30"
FORM_END = "09:00"

# Court Hours row 0 on every seeded branch (CBT Branch._populate_default_hours
# appends in week order).
HOURS_DAY = "Monday"
HOURS_SEEDED_OPENING = "06:00:00"
HOURS_TYPED = "07:30"


def _hms(value: str) -> tuple:
    """A time as (h, m, s) ints.

    NEVER compare Time values as strings across this boundary. The server hands
    them out through `format_timedelta`, whose format string pads the hour to
    ONE digit — so "07:30:00" and "7:30:00" are the same instant spelled two
    ways, and a string compare fails on exactly half the clock.
    """
    parts = [int(part) for part in str(value).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _server_slot_grid(branch: str, day: str) -> list:
    """The branch's slot grid straight from the engine, out-of-band.

    An INDEPENDENT opinion on what the dialog should be offering: it does not go
    through the page, and it calls `get_slot_grid` rather than the
    `get_availability` the dialog uses, so the two cannot agree by sharing a bug.

    Both positionals are supplied — `bench execute` builds its argument list with
    `eval(args)`, so a JSON `null` for an omitted parameter raises NameError and
    the except branch then passes the WHOLE JSON string as one argument (S18).
    The return value is a LIST, so the scan looks for `[`, not the `{` the
    dict-returning helpers in files 14/15 look for.
    """
    result = bench_execute(
        "court_booking_tech.slots.get_slot_grid",
        json.dumps([branch, day]),
        # check=False so the assert below is REACHABLE: bench_execute defaults to
        # check=True, and CalledProcessError carries the exit status but not
        # stderr (S17 trap).
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for line in reversed(result.stdout.strip().splitlines()):
        if line.startswith("["):
            return json.loads(line)
    raise AssertionError(f"no JSON list from get_slot_grid: {result.stdout!r}")


def _open_block_dialog(page: Page, branch: str, day: str):
    """Load the board and open Block Slots with its grid dropdowns populated."""
    load_board(page, branch, day)
    page.locator(".page-head button:has-text('Block Slots')").click()
    wait_dialog(page)
    wait_block_grid(page)


def _select(page: Page, field: str, value: str):
    """Choose an option through the REAL widget.

    `select_option` drives the `<select>` the way a human does and lets the
    browser fire input+change; `cur_dialog.set_value` would bypass the control
    entirely and pass just as happily against a dialog whose dropdowns were never
    populated — which is the thing under test.
    """
    page.select_option(f".modal.show select[data-fieldname='{field}']", value)
    page.wait_for_function(
        """([field, want]) => window.cur_dialog
            && cur_dialog.get_value(field) === want""",
        arg=[field, value],
        timeout=15000,
    )


@pytest.mark.e2e
class TestTimeInput:
    def test_block_dialog_offers_grid_times(self, page: Page):
        """The Block dialog offers the branch's REAL slot times, not a slider.

        Three separate claims, because each could break alone:
          * the option VALUES are exactly the grid's starts (and ends), compared
            against an independent server read;
          * they are still canonical "HH:MM:SS", so `create_block`'s arguments are
            byte-identical to what the Time widget used to send;
          * the LABELS are human — a staff member reads "6 PM", not "18:00:00".

        ⚠ Backlog B45 changed the label, deliberately: `cbt.time_label` used to
        render moment's `h:mm A` ("6:00 AM"), a SEVENTH rendering of the same
        hour. It now delegates to the app's one time language, so this dialog,
        the board's matrix, the customer's grid and the printed statement all
        say "6 AM" — and "6:30 AM" when the grid really is on the half hour.

        Read-only: opens a dialog and closes it, mutating nothing.
        """
        _open_block_dialog(page, MAKATI_BRANCH, D_GRID)

        grid = _server_slot_grid(MAKATI_BRANCH, D_GRID)
        assert grid, f"{MAKATI_BRANCH} has no slots on {D_GRID} — fixture regression"
        want_starts = [_hms(row["start_time"]) for row in grid]
        want_ends = [_hms(row["end_time"]) for row in grid]

        starts = block_option_values(page, "start_time")
        ends = block_option_values(page, "end_time")
        assert [_hms(v) for v in starts] == want_starts, (
            f"From offers {starts}, but the engine's grid starts at {want_starts}"
        )
        assert [_hms(v) for v in ends] == want_ends, (
            f"To offers {ends}, but the engine's grid ends at {want_ends}"
        )

        # Values stay canonical: create_block's contract did not change.
        for value in starts + ends:
            assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", value), value

        # Labels are 12-hour and human.
        labels = page.evaluate(
            """() => (cur_dialog.fields_dict.start_time.df.options || [])
                .filter((o) => o.value)
                .map((o) => o.label)"""
        )
        assert labels, labels
        # B45: "6 AM" on the hour, "6:30 AM" off it, and the midnight/noon pair
        # spelled MN/NN — the ruled convention, matched exactly rather than by a
        # shape a 24-hour string could also satisfy.
        for label in labels:
            assert re.fullmatch(r"(\d{1,2}(:\d{2})? (AM|PM)|12 (MN|NN))", label), label
        assert "6 AM" in labels, labels  # AYALA-makati opens at 06:00
        assert "6:00 AM" not in labels, (
            "the dialog is back on the old h:mm A label — B45 collapsed the "
            f"app's six time renderings into one: {labels}"
        )

        # To is re-filtered to the ends AFTER the chosen From, which is what makes
        # _validate_window's "End must be after Start" throw unreachable here.
        _select(page, "start_time", BLOCK_START)
        wait_block_grid(page, BLOCK_END, field="end_time")
        remaining = block_option_values(page, "end_time")
        assert remaining, remaining
        for value in remaining:
            assert _hms(value) > _hms(BLOCK_START), (
                f"To still offers {value}, which is not after From {BLOCK_START}"
            )

    def test_block_via_dropdowns_blocks_the_chip(
        self, page: Page, playwright: Playwright
    ):
        """Driving the two dropdowns really blocks the slot.

        The picker IS the point, so this row chooses options through the widget
        rather than calling set_value — that is the difference between testing
        the dialog and testing the API behind it.
        """
        api = platform_api(playwright)
        try:
            clear_blocks(api, MAKATI_BRANCH, D_GRID)
        finally:
            api.dispose()

        _open_block_dialog(page, MAKATI_BRANCH, D_GRID)
        # The court is a Link, not part of what this section changed.
        gestures.fill(page, "court", MAKATI_COURT, scope=gestures.DIALOG)
        _select(page, "start_time", BLOCK_START)
        wait_block_grid(page, BLOCK_END, field="end_time")
        _select(page, "end_time", BLOCK_END)

        with page.expect_response(
            lambda r: "create_block" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, (
            f"create_block: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )

        page.wait_for_selector(
            f".cbt-slot[data-court='{MAKATI_COURT}'][data-start='{BLOCK_START}']"
            "[data-status='blocked']",
            timeout=15000,
        )

        api = platform_api(playwright)
        try:
            clear_blocks(api, MAKATI_BRANCH, D_GRID)
        finally:
            api.dispose()

    def test_slot_block_form_native_time_input(
        self, page: Page, playwright: Playwright
    ):
        """The CBT Slot Block desk form takes a TYPED time and stores it exactly.

        The form has no grid constraint, so this is where the native control is
        exercised for its own sake: assert the widget is really a native time
        input with no datepicker behind it, type a value that is deliberately off
        the grid and on a single-digit hour, save, and read it back from the
        server.
        """
        api = platform_api(playwright)
        try:
            clear_blocks(api, MAKATI_BRANCH, D_FORM)
        finally:
            api.dispose()

        page.goto(
            "/desk/cbt-slot-block/new", wait_until="domcontentloaded", timeout=60000
        )
        wait_for_page_load(page)
        # BACKLOG B11, MEASURED HERE. `body[data-ajax-state="complete"]` goes up
        # while `cur_frm` is still null, and the router THEN rewrites
        # /cbt-slot-block/new -> /cbt-slot-block/new-cbt-slot-block-<hash> about
        # half a second later — the "new-doc route resolves twice" B11 describes.
        # A `wait_for_new_form` alone can settle on the first resolution and be
        # stale by the time the next call lands, which is how this row first
        # failed: `cur_frm` was null inside the fill.
        #
        # So gate the way files 01/03 do, strongest check LAST: a rendered
        # control proves the form painted, `cur_frm.doc` proves the form object
        # exists, and only then does wait_for_new_form assert the router has
        # stopped moving.
        page.wait_for_selector(
            ".frappe-control[data-fieldname='branch'] input", timeout=20000
        )
        page.wait_for_function(
            "() => window.cur_frm && cur_frm.doc", timeout=30000
        )
        wait_for_new_form(page, "CBT Slot Block")
        # Driven by GESTURE: type into the Link and click its option, type the date in
        # the site's own format, choose the Select. The previous chained
        # `cur_frm.set_value` wrote the model directly, which is exactly what cannot
        # fail on a field a human can neither see nor reach.
        gestures.fill_link(page, "branch", MAKATI_BRANCH, label=MAKATI_BRANCH_TITLE)
        gestures.type_date(page, "block_date", D_FORM)
        gestures.select(page, "reason", "Maintenance")
        # The company mirror this section had to ship is ASYNC (a frappe.db
        # get_value behind the branch trigger), so filling the branch and saving
        # immediately would race it. Gate on the mirrored value, and assert it —
        # `company` is read_only here, and read-only is a render concern rather
        # than a transmit one, so the only honest proof that it reaches the
        # server is the save below succeeding.
        page.wait_for_function(
            "() => window.cur_frm && cur_frm.doc && cur_frm.doc.company === 'ayala-courts'",
            timeout=15000,
        )

        # --- it is the browser's control, and nothing else ------------------
        widget = page.evaluate(
            """() => {
                const control = cur_frm.fields_dict.start_time;
                return {
                    type: control.$input.attr('type'),
                    step: control.$input.attr('step'),
                    // Both halves matter: the stock ControlTime kept its
                    // Air-Datepicker on the control AND on the input's jQuery
                    // data. If either survived, the slider is still there.
                    control_picker: control.datepicker === undefined,
                    input_picker: control.$input.data('datepicker') === undefined,
                };
            }"""
        )
        assert widget["type"] == "time", widget
        assert widget["step"] == "60", widget
        assert widget["control_picker"], "a datepicker is still attached to the control"
        assert widget["input_picker"], "a datepicker is still attached to the input"

        # --- type through the widget ----------------------------------------
        page.locator(".frappe-control[data-fieldname='start_time'] input").fill(
            FORM_START
        )
        page.locator(".frappe-control[data-fieldname='end_time'] input").fill(FORM_END)
        page.wait_for_function(
            """([start, end]) => window.cur_frm && cur_frm.doc
                && cur_frm.doc.start_time === start && cur_frm.doc.end_time === end""",
            arg=[f"{FORM_START}:00", f"{FORM_END}:00"],
            timeout=15000,
        )

        # frm.save() can RESOLVE even when the server rejects (S4 as-built 10),
        # and a first save renames the route, tearing down the JS context the
        # promise lives in — so report the outcome and verify from the server.
        #
        # SECTION-23: the teardown the comment above describes is not merely a
        # risk to the promise — it can destroy the execution context BEFORE the
        # resolved value crosses back to Playwright, and then `evaluate` itself
        # raises "Execution context was destroyed". That is the FIRST SAVE
        # WORKING, not failing, and file 01 has treated it that way since
        # section-11 (test_01_platform_onboarding.py:108-120); this row simply
        # did not inherit the guard, and went red on 1 of 3 identical passes.
        # Caught while banking section-23's three-pass gate.
        #
        # Swallowing it costs NOTHING, because the outcome is diagnostic only:
        # the assertions below read the row back from the SERVER, which is what
        # the original comment already declared to be the source of truth. A
        # save that genuinely did not happen still fails, loudly, two lines down.
        # Ctrl+S — the keystroke a human presses. `gestures.save_form` owns the
        # execution-context guard the comment above describes, so it is no longer
        # duplicated per file. A save that genuinely did not happen still fails
        # loudly at the server read below.
        try:
            outcome = {"name": gestures.save_form(page)}
        except PlaywrightError as exc:
            outcome = {"save_did_not_commit": str(exc)[:200]}

        rows = page.request.get(
            "/api/resource/CBT Slot Block",
            params={
                "filters": json.dumps(
                    [["branch", "=", MAKATI_BRANCH], ["block_date", "=", D_FORM]]
                ),
                "fields": json.dumps(["name", "start_time", "end_time"]),
            },
        )
        assert rows.ok, f"block probe: HTTP {rows.status}"
        data = rows.json()["data"]
        assert len(data) == 1, (
            f"expected exactly one block on {MAKATI_BRANCH} x {D_FORM}, got "
            f"{data}. Save outcome was {outcome}"
        )
        assert _hms(data[0]["start_time"]) == _hms(FORM_START), data
        assert _hms(data[0]["end_time"]) == _hms(FORM_END), data

        api = platform_api(playwright)
        try:
            clear_blocks(api, MAKATI_BRANCH, D_FORM)
        finally:
            api.dispose()

    def test_business_hours_grid_native_time_input(self, page: Page):
        """A Court Hours row edits through the grid cell, in ONE click.

        Two things this row and nothing else covers:

          * the CHILD-GRID path. Grid cells build their control with
            `only_input: true`, a different branch of `refresh_input` from the
            form path above.
          * the one-click fix. `grid_row.js` focuses `input[type="Text"]:first`
            when a cell is clicked, and per the HTML spec that attribute selector
            is case-insensitive — so it matched the old text input and does NOT
            match `type="time"`. Without the delegated handler shipped in
            `cbt_time_control.js`, this section would have made every Court Hours
            and Rate Rule edit cost a second click. Asserting FOCUS is what pins
            that fix.

        IT NEVER SAVES, and that is deliberate rather than a shortcut. Court Hours
        drive `slots.get_slot_grid` for every court on the branch, so persisting a
        change here — or failing halfway through restoring one — would silently
        reshape the grid for the ~20 other E2E files that derive chips from it.
        Persistence of a Time field is already covered by the sibling row above;
        what is unique here is the WIDGET, and the widget's contract is the round
        trip DOM "07:30" <-> model "07:30:00", which is asserted in both
        directions. The last assertion proves nothing reached the server.

        Claims no ledger date: it consumes no court x date.
        """
        page.goto(
            f"/desk/cbt-branch/{MAKATI_BRANCH}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        wait_for_page_load(page)
        page.wait_for_function(
            """(branch) => window.cur_frm && cur_frm.doc
                && cur_frm.doc.name === branch
                && (cur_frm.doc.business_hours || []).length > 0""",
            arg=MAKATI_BRANCH,
            timeout=30000,
        )
        # Fail loudly if the seed's row order ever changes, rather than quietly
        # testing a different day.
        first_row = page.evaluate(
            "() => cur_frm.doc.business_hours[0]"
        )
        assert first_row["day"] == HOURS_DAY, first_row
        assert _hms(first_row["opening_time"]) == _hms(HOURS_SEEDED_OPENING), first_row

        cell = page.locator(
            ".frappe-control[data-fieldname='business_hours'] "
            ".grid-body .rows .grid-row"
        ).first.locator(".grid-static-col[data-fieldname='opening_time']")
        cell.click()

        editor = cell.locator("input[data-fieldname='opening_time']")
        editor.wait_for(state="visible", timeout=15000)
        assert editor.get_attribute("type") == "time", editor.get_attribute("type")
        # ONE click: the cell click opened the row AND focused the input.
        page.wait_for_function(
            """() => {
                const el = document.activeElement;
                return !!el && el.tagName === 'INPUT' && el.type === 'time'
                    && el.dataset.fieldname === 'opening_time';
            }""",
            timeout=15000,
        )

        editor.fill(HOURS_TYPED)
        # DOM -> model: parse() canonicalises the native control's "HH:mm".
        page.wait_for_function(
            "(want) => cur_frm.doc.business_hours[0].opening_time === want",
            arg=f"{HOURS_TYPED}:00",
            timeout=15000,
        )
        # model -> DOM: refresh() re-renders from the model through
        # format_for_input, which is where a non-normalising implementation would
        # blank the field instead of showing the value back.
        redisplayed = page.evaluate(
            """() => {
                const grid = cur_frm.fields_dict.business_hours.grid;
                const control = grid.grid_rows[0].on_grid_fields_dict.opening_time;
                control.refresh();
                return control.$input.val();
            }"""
        )
        assert redisplayed == HOURS_TYPED, redisplayed

        # Nothing was saved — the server still holds the seeded hours.
        stored = page.request.get(f"/api/resource/CBT Branch/{MAKATI_BRANCH}")
        assert stored.ok, f"GET branch: HTTP {stored.status}"
        row = stored.json()["data"]["business_hours"][0]
        assert _hms(row["opening_time"]) == _hms(HOURS_SEEDED_OPENING), (
            f"this row must never persist — {MAKATI_BRANCH} {HOURS_DAY} now opens "
            f"at {row['opening_time']}, which would reshape the slot grid for "
            f"every file that books this branch"
        )

        # Leave no dirty form behind: an unsaved form can trip frappe's
        # unsaved-changes guard on the next navigation in this context.
        page.evaluate("() => cur_frm.reload_doc()")
        page.wait_for_function("() => !cur_frm.is_dirty()", timeout=15000)

    def test_blocking_over_a_confirmed_booking_warns_and_still_blocks(
        self, page: Page, playwright: Playwright
    ):
        """Backlog B28 — the operator promise, pinned where it is made.

        The walkthrough promises that a Confirmed booking inside a blocked window
        is *"a warning, not a refusal — nothing is cancelled for you"*. Source:
        `CBTSlotBlock._warn_confirmed_overlaps` is a `msgprint`, not a throw.
        Nothing asserted it, so any later "tightening" of slot-block validation
        into a throw would make blocking a holiday impossible on every branch
        with a confirmed booking — and the suite would stay green.

        Both halves are load-bearing, in this order:
          * the WARNING is shown to the staff member, under the promised title,
            naming the booking — and is closed the way a human closes it, with
            the header's X;
          * the block was NEVERTHELESS created and the booking NEVERTHELESS kept.
            On the board that reads as: the 09:00 chip still says booked (a
            booking outranks a block in `slots._availability`, deliberately —
            the guest is still coming), and the 10:00 chip says blocked. The
            server is read for both, because a chip is a rendering.

        Arrange/cleanup are API calls (a Cash booking instant-confirms); the
        act is the dialog, driven through its real controls like the row above.
        """
        api = platform_api(playwright)
        try:
            clear_blocks(api, MAKATI_BRANCH, D_WARN)
        finally:
            api.dispose()
        cancel_active_bookings(page, MAKATI_COURT, D_WARN)

        booking = book_slot_via_api(page, MAKATI_COURT, D_WARN, BLOCK_START)
        try:
            status = page.request.get(f"/api/resource/CBT Court Booking/{booking}")
            assert status.ok, f"GET booking: HTTP {status.status}"
            assert status.json()["data"]["booking_status"] == "Confirmed", (
                "the fixture booking is not Confirmed — the warning only fires "
                "for Confirmed/Extended, so this row would prove nothing"
            )

            load_board(page, MAKATI_BRANCH, D_WARN)
            booked_chip = (
                f".cbt-slot[data-court='{MAKATI_COURT}'][data-start='{BLOCK_START}']"
                "[data-status='booked']"
            )
            page.wait_for_selector(booked_chip, timeout=15000)
            page.locator(".page-head button:has-text('Block Slots')").click()
            wait_dialog(page)
            wait_block_grid(page)
            gestures.fill(page, "court", MAKATI_COURT, scope=gestures.DIALOG)
            _select(page, "start_time", BLOCK_START)
            wait_block_grid(page, WARN_BLOCK_END, field="end_time")
            _select(page, "end_time", WARN_BLOCK_END)

            with page.expect_response(
                lambda r: "create_block" in r.url, timeout=30000
            ) as resp_info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert resp_info.value.ok, (
                f"create_block REFUSED: HTTP {resp_info.value.status} "
                f"{resp_info.value.text()} — the promise is warn, not refuse"
            )

            # --- the warning ---------------------------------------------
            # Scoped to the VISIBLE modal carrying the promised title: the block
            # dialog is a `.modal` too and is hiding at the same moment.
            warning = page.locator(".modal.show").filter(has_text=WARN_TITLE)
            expect(warning).to_be_visible(timeout=15000)
            expect(warning.locator(".modal-title")).to_contain_text(WARN_TITLE)
            expect(warning.locator(".msgprint")).to_contain_text(booking)
            # Closed with the header's X. Bootstrap ignores a hide() that lands
            # during the show transition, so a click that arrives too early is
            # simply repeated — which is what a human does too.
            for _attempt in range(3):
                if warning.count() == 0:
                    break  # a late fade-out from the previous click landed
                try:
                    warning.locator(".modal-header .btn-modal-close").click()
                    warning.wait_for(state="hidden", timeout=3000)
                    break
                except PlaywrightTimeoutError:
                    continue
            else:
                raise AssertionError("the warning modal did not close on X")
            page.wait_for_selector(".modal.show", state="detached", timeout=15000)

            # --- ...and nothing was cancelled for them ---------------------
            # ORDER MATTERS. The blocked chip can only exist after the board
            # re-rendered, so it is waited for FIRST; the booked chip is then
            # read from that same post-block render. The other way round, the
            # booked-chip wait is satisfied by the PRE-block DOM and proves
            # nothing about precedence (ducky, 2026-08-27).
            page.wait_for_selector(
                f".cbt-slot[data-court='{MAKATI_COURT}'][data-start='{WARN_FREE_SLOT}']"
                "[data-status='blocked']",
                timeout=15000,
            )
            expect(page.locator(booked_chip)).to_have_count(1)
            blocks = page.request.get(
                "/api/resource/CBT Slot Block",
                params={
                    "filters": json.dumps(
                        [["branch", "=", MAKATI_BRANCH], ["block_date", "=", D_WARN]]
                    ),
                    "fields": json.dumps(["name", "court", "start_time", "end_time"]),
                },
            )
            assert blocks.ok, f"block probe: HTTP {blocks.status}"
            rows = blocks.json()["data"]
            assert len(rows) == 1 and rows[0]["court"] == MAKATI_COURT, rows
            assert _hms(rows[0]["start_time"]) == _hms(BLOCK_START), rows
            assert _hms(rows[0]["end_time"]) == _hms(WARN_BLOCK_END), rows
            after = page.request.get(f"/api/resource/CBT Court Booking/{booking}")
            assert after.ok, f"GET booking: HTTP {after.status}"
            assert after.json()["data"]["booking_status"] == "Confirmed", (
                "the block CANCELLED the booking — the promise says it must not"
            )
        finally:
            api = platform_api(playwright)
            try:
                clear_blocks(api, MAKATI_BRANCH, D_WARN)
            finally:
                api.dispose()
            cancel_active_bookings(page, MAKATI_COURT, D_WARN)
