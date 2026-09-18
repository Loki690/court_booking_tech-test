"""
E2E file 08 — Open Play (section-10; PLAN §10 row 08).

The whole session loop through the Open Play Board: dedicated courts blocked on
the Court Board, players added (through the dialog AND the API), the first four
seated per court, Game Done rotation, court ✕ + backfill, queue ✕ reindex, then
TV mode and Complete.

These tests are SEQUENTIAL BY DESIGN — one session, played in order, exactly as
a real session runs. The module fixture creates and opens it once; each test
picks up the board state the previous one left. A failure therefore cascades,
which is informative here (the narrative broke at step N) rather than
misleading.

Runs as Administrator, like file 06: the board is always pointed at
ayala-courts explicitly, because platform scope sees every tenant and letting
the bootstrap pick would race the fixture.

Dates: +60 (session day) and +61 (the morning-after check that Open Play blocks
never leak into the next day). **No other E2E file claims either — and as of
2026-08-18 that is finally TRUE** (Backlog B16). It was written as fact while
file 10 also booked AYALA-bgc-court-3 on both days; the suite survived only
because this file asserts that court AVAILABLE at 10:00 while file 10 booked it
at 09:00. That is a one-hour undeclared margin, not isolation, and this file's
`available` assertion was the row that would have broken the day file 10 moved
an hour. File 10's BAN_DATE/PORTAL_DATE now sit on +75/+76.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright

from helpers import gestures
from helpers.board import BGC_BRANCH, platform_api, load_board, slot, wait_dialog
from helpers.open_play import (
    SESSION_COURTS,
    SPARE_COURT,
    add_players,
    court_players,
    court_vacancies,
    create_open_session,
    purge_sessions,
    queue_customers,
    reload_board,
    show_session,
)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

D_OP = (date.today() + timedelta(days=60)).isoformat()
D_NEXT = (date.today() + timedelta(days=61)).isoformat()

COURT_1, COURT_2 = SESSION_COURTS

# The eight seeded open play players, then two portal customers — the queue is
# built in this order, which every rotation assertion below depends on.
OP_PLAYERS = [
    "op.ace@example.com",
    "op.bea@example.com",
    "op.caloy@example.com",
    "op.dina@example.com",
    "op.elmo@example.com",
    "op.fely@example.com",
    "op.gani@example.com",
    "op.hazel@example.com",
]
EXTRA_PLAYERS = ["cust.carla@example.com", "cust.pia@example.com"]


@pytest.fixture(scope="module")
def session_name(playwright: Playwright, platform_auth):
    api = platform_api(playwright)
    try:
        purge_sessions(api, D_OP)
        purge_sessions(api, D_NEXT)
        yield create_open_session(api, D_OP)
    finally:
        api.dispose()


def _doc(page: Page, doctype: str, name: str) -> dict:
    resp = page.request.get(f"/api/resource/{doctype}/{name}")
    assert resp.ok, f"GET {doctype}/{name}: HTTP {resp.status}"
    return resp.json()["data"]


@pytest.mark.e2e
class TestOpenPlay:
    def test_dedicated_courts_are_blocked_on_the_court_board(
        self, page: Page, session_name
    ):
        load_board(page, BGC_BRANCH, D_OP)
        for court in SESSION_COURTS:
            for start in ("09:00:00", "10:00:00", "11:00:00"):
                element = slot(page, court, start)
                assert element.get_attribute("data-status") == "blocked", (
                    f"{court} {start} should be blocked for open play"
                )
                assert element.get_attribute("data-reason") == "Open Play"
        # A court of the same branch that the session did NOT dedicate stays
        # bookable — open play takes only what it reserved.
        assert (
            slot(page, SPARE_COURT, "10:00:00").get_attribute("data-status")
            == "available"
        )

    def test_add_players_bills_every_participant(self, page: Page, session_name):
        show_session(page, session_name)

        # Eight through the API (budget: the dialog is proven below, and
        # repeating it ten times would burn the file's time budget)...
        add_players(page, OP_PLAYERS)
        reload_board(page, session_name)

        # ...and two through the real desk interaction, which is the point of
        # covering it at all: the dialog stays open between arrivals.
        page.locator(".cbt-op-bottom [data-action='add']").click()
        wait_dialog(page)
        for email in EXTRA_PLAYERS:
            gestures.fill(page, "customer", email, scope=gestures.DIALOG)
            with page.expect_response(
                lambda r: "add_players" in r.url, timeout=30000
            ) as resp_info:
                page.evaluate("() => cur_dialog.get_primary_btn().click()")
            assert resp_info.value.ok, (
                f"add_players: HTTP {resp_info.value.status} {resp_info.value.text()}"
            )
            # The dialog clears itself for the next arrival INSIDE the response
            # handler — setting the next name before that lands would have it
            # wiped, and the following click would submit an empty form.
            page.wait_for_function(
                "() => window.cur_dialog && !cur_dialog.get_value('customer')",
                timeout=15000,
            )
        page.evaluate("() => cur_dialog.get_secondary_btn().click()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        doc = _doc(page, "CBT Open Play Session", session_name)
        assert len(doc["participants"]) == 10, doc["current_participants"]
        assert float(doc["total_revenue"]) == 1500.0, doc["total_revenue"]

        for row in doc["participants"]:
            assert row["billing_doc"], f"{row['customer']} has no billing document"
        invoice = _doc(page, "CBT Booking Invoice", doc["participants"][0]["billing_doc"])
        assert invoice["status"] == "Paid & Verified", invoice["status"]
        assert invoice["participant_ref"] == doc["participants"][0]["name"]
        assert not invoice.get("booking")

    def test_start_seats_four_on_each_court(self, page: Page, session_name):
        show_session(page, session_name)
        with page.expect_response(
            lambda r: "start_session" in r.url, timeout=30000
        ) as resp_info:
            page.locator(".cbt-op-bottom [data-action='start']").click()
        assert resp_info.value.ok, (
            f"start_session: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )
        page.wait_for_function(
            """() => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                return b.state && b.state.assignments.length === 2;
            }""",
            timeout=30000,
        )

        assert court_players(page, COURT_1) == OP_PLAYERS[:4]
        assert court_players(page, COURT_2) == OP_PLAYERS[4:8]
        # The two late arrivals wait their turn.
        assert queue_customers(page) == EXTRA_PLAYERS

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "open_play_board.png"), full_page=True
        )

    def test_game_done_sends_players_to_the_back(self, page: Page, session_name):
        show_session(page, session_name)
        with page.expect_response(
            lambda r: "game_done" in r.url, timeout=30000
        ) as resp_info:
            page.locator(
                f".cbt-op-court-card[data-court='{COURT_1}'] [data-action='game-done']"
            ).click()
        assert resp_info.value.ok, (
            f"game_done: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )
        page.wait_for_function(
            """(expected) => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                if (!b.state) return false;
                const court = b.state.assignments.find((a) => a.court === expected.court);
                return court && court.players.length === 4
                    && court.players[0].customer === expected.first;
            }""",
            arg={"court": COURT_1, "first": EXTRA_PLAYERS[0]},
            timeout=30000,
        )

        # The four who just played go to the BACK in seat order; the two who
        # were waiting plus the first two returners take the court.
        assert court_players(page, COURT_1) == EXTRA_PLAYERS + OP_PLAYERS[:2]
        assert queue_customers(page) == OP_PLAYERS[2:4]

        doc = _doc(page, "CBT Open Play Session", session_name)
        played = {
            row["customer"]: row["games_played"]
            for row in doc["queue"]
            if row["customer"] in OP_PLAYERS[:4]
        }
        assert set(played.values()) == {1}, played

    def test_court_x_frees_a_slot_and_backfill_fills_it(
        self, page: Page, session_name
    ):
        show_session(page, session_name)
        benched = OP_PLAYERS[4]  # court 2 is untouched by the rotation above

        with page.expect_response(
            lambda r: "return_to_queue" in r.url, timeout=30000
        ) as resp_info:
            page.locator(
                f".cbt-op-court-card[data-court='{COURT_2}'] "
                f".cbt-op-player[data-player='{benched}'] [data-action='return']"
            ).click()
        assert resp_info.value.ok, resp_info.value.text()
        page.wait_for_function(
            """(court) => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                const c = b.state && b.state.assignments.find((a) => a.court === court);
                return c && c.vacancies === 1;
            }""",
            arg=COURT_2,
            timeout=30000,
        )
        assert court_vacancies(page, COURT_2) == 1
        assert benched not in court_players(page, COURT_2)
        # Sat out, still in the session, at the back of the line.
        assert queue_customers(page)[-1] == benched

        filler = queue_customers(page)[0]
        page.locator(
            f".cbt-op-court-card[data-court='{COURT_2}'] .cbt-op-vacancy"
        ).click()
        wait_dialog(page)
        gestures.fill(page, "player", filler, scope=gestures.DIALOG)
        with page.expect_response(
            lambda r: "backfill" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, resp_info.value.text()

        page.wait_for_function(
            """(court) => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                const c = b.state && b.state.assignments.find((a) => a.court === court);
                return c && c.vacancies === 0;
            }""",
            arg=COURT_2,
            timeout=30000,
        )
        assert filler in court_players(page, COURT_2)
        assert filler not in queue_customers(page)

    def test_queue_x_removes_the_player_and_reindexes(self, page: Page, session_name):
        show_session(page, session_name)
        before = queue_customers(page)
        assert before, "the queue should not be empty at this point"
        leaving = before[0]

        page.locator(
            f".cbt-op-queue-row[data-customer='{leaving}'] [data-action='remove']"
        ).click()
        page.wait_for_function(
            "() => window.cur_dialog && cur_dialog.$wrapper && cur_dialog.$wrapper.hasClass('show')",
            timeout=15000,
        )
        with page.expect_response(
            lambda r: "remove_from_session" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, resp_info.value.text()

        page.wait_for_function(
            """(leaving) => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                if (!b.state) return false;
                return !b.state.queue.some(
                    (r) => r.customer === leaving && r.status === 'Waiting'
                );
            }""",
            arg=leaving,
            timeout=30000,
        )
        assert leaving not in queue_customers(page)

        doc = _doc(page, "CBT Open Play Session", session_name)
        left = [r for r in doc["queue"] if r["customer"] == leaving]
        assert left and left[-1]["status"] == "Left", left
        waiting = sorted(
            [r for r in doc["queue"] if r["status"] == "Waiting"],
            key=lambda r: r["queue_position"],
        )
        # No gaps: positions stay 1..n after anyone leaves.
        assert [r["queue_position"] for r in waiting] == list(
            range(1, len(waiting) + 1)
        ), waiting

    def test_tv_mode_hides_staff_controls_then_complete_frees_the_next_day(
        self, page: Page, session_name
    ):
        show_session(page, session_name)

        page.locator(".cbt-op-bottom [data-action='tv']").click()
        page.wait_for_selector(".cbt-op-wrap.cbt-op-tv", timeout=15000)
        # TV mode is unattended: nothing on screen may invite a touch. The
        # controls stay in the DOM and are hidden by CSS, so every assertion
        # here is about VISIBILITY — a display:none button is unclickable, but
        # it still counts.
        assert page.locator(".cbt-op-bottom").is_visible() is False
        assert page.locator("[data-action='game-done']:visible").count() == 0
        assert page.locator(".cbt-op-drag:visible").count() == 0
        assert page.locator("[data-action='remove']:visible").count() == 0
        assert page.locator(".cbt-op-tv-exit").count() == 1
        # TV mode must OWN the screen. Asserted by OCCLUSION, not geometry: the
        # overlay once covered the viewport on paper while the desk sidebar
        # painted straight over it and clipped the first court. elementFromPoint
        # asks what a viewer actually sees at that spot.
        for x, y in ((30, 300), (30, 700), (700, 500)):
            owner = page.evaluate(
                """([x, y]) => {
                    const el = document.elementFromPoint(x, y);
                    return el ? !!el.closest('.cbt-op-wrap') : false;
                }""",
                [x, y],
            )
            assert owner, f"desk chrome is visible at ({x}, {y}) in TV mode"
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "open_play_tv_mode.png"), full_page=True
        )

        page.locator("[data-action='exit-tv']").click()
        page.wait_for_selector(".cbt-op-wrap:not(.cbt-op-tv)", timeout=15000)

        page.locator(".cbt-op-bottom [data-action='complete']").click()
        page.wait_for_function(
            "() => window.cur_dialog && cur_dialog.$wrapper && cur_dialog.$wrapper.hasClass('show')",
            timeout=15000,
        )
        page.evaluate("() => cur_dialog.get_primary_btn().click()")
        # Gated on the OUTCOME (the board reflects Completed), not on catching
        # the POST: a confirm-driven action fires its request from inside the
        # dialog's own handler, and matching that response is a transport
        # detail this test does not care about.
        page.wait_for_function(
            """() => {
                const b = frappe.pages['cbt-open-play-board'].open_play_board;
                return b.state && b.state.status === 'Completed';
            }""",
            timeout=30000,
        )
        doc = _doc(page, "CBT Open Play Session", session_name)
        assert doc["status"] == "Completed"
        assert not [r for r in doc["queue"] if r["status"] in ("Waiting", "Playing")]

        # The blocks are history for the day that was played — the courts are
        # free again the next morning.
        load_board(page, BGC_BRANCH, D_NEXT)
        for court in SESSION_COURTS:
            assert (
                slot(page, court, "10:00:00").get_attribute("data-status")
                == "available"
            ), f"{court} should be free the day after the session"
