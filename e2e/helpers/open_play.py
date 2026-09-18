"""
Open Play Board helpers (section-10 E2E).

Same discipline as helpers/board.py: the board is pointed at a session through
its PUBLIC `show(session, company)` entry (never by racing the bootstrap), and
every wait is gated on payload IDENTITY — "the board is now rendering THIS
session" — rather than on "some response arrived".

The default seat sees every company — Backlog B43 made that the PLATFORM seat
rather than Administrator, and `tenancy.get_session_company()` returns None for
it, which means ALL companies — so `show()` is always given the company
explicitly: letting the bootstrap pick would make the fixture depend on
whichever tenant sorted first.
"""
import json

from playwright.sync_api import Page

from helpers.board import api_csrf, csrf
from helpers.navigation import wait_for_page_load

BOARD_ROUTE = "/desk/cbt-open-play-board"

COMPANY = "ayala-courts"
COMPANY_TITLE = "Ayala Courts"  # what the Link box displays (title_field)
BGC_BRANCH = "AYALA-bgc"
SESSION_COURTS = ["AYALA-bgc-court-1", "AYALA-bgc-court-2"]
SPARE_COURT = "AYALA-bgc-court-3"  # in the branch, NOT in the session

SESSION_TITLE = "E2E Open Play"
ENTRY_FEE = 150


def goto_open_play(page: Page):
    page.goto(BOARD_ROUTE, wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    # Wait past bootstrap_company's async get_list — overriding the company
    # while it is still in flight would let its load land last.
    page.wait_for_function(
        """() => {
            const wrapper = frappe.pages['cbt-open-play-board'];
            return wrapper && wrapper.open_play_board
                && wrapper.open_play_board._bootstrapped;
        }""",
        timeout=15000,
    )


def board(page: Page, expression: str):
    return page.evaluate(
        f"() => {{ const b = frappe.pages['cbt-open-play-board'].open_play_board; return {expression}; }}"
    )


def show_session(page: Page, session: str):
    """Point the board at one session and wait for THAT session to render."""
    goto_open_play(page)
    page.evaluate(
        """([session, company]) => {
            const b = frappe.pages['cbt-open-play-board'].open_play_board;
            return b.show(session, company);
        }""",
        [session, COMPANY],
    )
    page.wait_for_function(
        """(session) => {
            const b = frappe.pages['cbt-open-play-board'].open_play_board;
            return b.state && b.state.name === session;
        }""",
        arg=session,
        timeout=30000,
    )
    page.wait_for_selector(".cbt-op-court-card", timeout=15000)
    # Invariant enforced on EVERY load: the company selector must NAME the
    # tenant whose session is on screen. Asserted on the visible input text,
    # not on get_value(): CBT Company sets show_title_field_in_link, so the box
    # displays "Ayala Courts" while the value is the slug — and it is the
    # displayed text a human reads and mistrusts.
    page.wait_for_function(
        """(expected) => {
            const b = frappe.pages['cbt-open-play-board'].open_play_board;
            const shown = b.company_field.$input && b.company_field.$input.val();
            return shown === expected;
        }""",
        arg=COMPANY_TITLE,
        timeout=15000,
    )


def reload_board(page: Page, session: str):
    """Force a fresh payload and wait for it to render (used after a mutation
    that the page itself already reloaded — this makes the wait explicit)."""
    page.evaluate(
        """([session, company]) => {
            const b = frappe.pages['cbt-open-play-board'].open_play_board;
            return b.show(session, company);
        }""",
        [session, COMPANY],
    )
    page.wait_for_function(
        """(session) => {
            const b = frappe.pages['cbt-open-play-board'].open_play_board;
            return b.state && b.state.name === session;
        }""",
        arg=session,
        timeout=30000,
    )


def queue_customers(page: Page) -> list[str]:
    """Waiting queue in board order, ACCOUNTS ONLY — read as ONE atomic snapshot.

    The panel re-renders on every load; a wait-then-read would sample it
    mid-render (the Court Board's list-view lesson).

    Section-20: a walk-in row carries no `data-customer` attribute at all (it
    has no account), so it is absent from this list by construction. Use
    `queue_player_keys` when you mean "everybody in the queue".
    """
    return page.evaluate(
        """() => Array.from(
            document.querySelectorAll('.cbt-op-queue-row[data-customer]')
        ).map((el) => el.getAttribute('data-customer'))"""
    )


def queue_player_keys(page: Page) -> list[str]:
    """Every waiting row's PLAYER KEY, in board order (section-20).

    For an account the key IS the user id; for a walk-in it is their queue-row
    name. This is what every rotation action sends back to the server.
    """
    return page.evaluate(
        """() => Array.from(
            document.querySelectorAll('.cbt-op-queue-row[data-player-key]')
        ).map((el) => el.getAttribute('data-player-key'))"""
    )


def queue_player_names(page: Page) -> list[str]:
    """Every waiting row's FULL name (the visible chip is shortened for TV)."""
    return page.evaluate(
        """() => Array.from(
            document.querySelectorAll('.cbt-op-queue-row[data-player-name]')
        ).map((el) => el.getAttribute('data-player-name'))"""
    )


def court_players(page: Page, court: str) -> list[str]:
    """The PLAYER KEYS seated on `court`, in seat order.

    Section-20 renamed what this reads without changing what it returns for an
    account: `data-player` used to be the user id and is now the player key,
    and an account's key IS their user id. A walk-in's key is a row name.
    """
    return page.evaluate(
        """(court) => Array.from(
            document.querySelectorAll(
                `.cbt-op-court-card[data-court="${court}"] .cbt-op-player[data-player]`
            )
        ).map((el) => el.getAttribute('data-player'))""",
        court,
    )


def court_player_names(page: Page, court: str) -> list[str]:
    return page.evaluate(
        """(court) => Array.from(
            document.querySelectorAll(
                `.cbt-op-court-card[data-court="${court}"] .cbt-op-player[data-player-name]`
            )
        ).map((el) => el.getAttribute('data-player-name'))""",
        court,
    )


def court_vacancies(page: Page, court: str) -> int:
    return page.evaluate(
        """(court) => document.querySelectorAll(
            `.cbt-op-court-card[data-court="${court}"] .cbt-op-vacancy`
        ).length""",
        court,
    )


def op_call(page: Page, method: str, args: dict) -> dict:
    """POST to an open play endpoint as the page's session."""
    resp = page.request.post(
        f"/api/method/court_booking_tech.api.open_play.{method}",
        headers={"X-Frappe-CSRF-Token": csrf(page)},
        form=args,
    )
    assert resp.ok, f"{method}: HTTP {resp.status} {resp.text()}"
    return resp.json().get("message")


def add_players(page: Page, customers: list[str], payment_method: str = "Cash"):
    return op_call(
        page,
        "add_players",
        {
            "session": page.evaluate(
                "() => frappe.pages['cbt-open-play-board'].open_play_board.state.name"
            ),
            "players": json.dumps(
                [
                    {"customer": customer, "payment_method": payment_method}
                    for customer in customers
                ]
            ),
        },
    )


def add_walkin(page: Page, name: str, phone: str | None = None, payment_method: str = "Cash"):
    """Section-20: add a walk-in over the API (the DIALOG path is driven
    directly by file 16's first test — this is for building queue depth)."""
    entry = {"customer_name": name, "payment_method": payment_method}
    if phone:
        entry["customer_phone"] = phone
    return op_call(
        page,
        "add_players",
        {
            "session": page.evaluate(
                "() => frappe.pages['cbt-open-play-board'].open_play_board.state.name"
            ),
            "players": json.dumps([entry]),
        },
    )


def purge_sessions(api, session_date: str):
    """Delete any leftover session for this date (re-runnability without a
    reset). Its slot blocks go first — they are what would otherwise keep the
    courts blocked for the next run."""
    token = api_csrf(api)
    filters = json.dumps(
        [["branch", "=", BGC_BRANCH], ["session_date", "=", session_date]]
    )
    resp = api.get("/api/resource/CBT Open Play Session", params={"filters": filters})
    assert resp.ok, f"session probe: HTTP {resp.status}"
    for row in resp.json()["data"]:
        block_filters = json.dumps([["open_play_session", "=", row["name"]]])
        blocks = api.get(
            "/api/resource/CBT Slot Block", params={"filters": block_filters}
        )
        assert blocks.ok, f"block probe: HTTP {blocks.status}"
        for block in blocks.json()["data"]:
            api.delete(
                f"/api/resource/CBT Slot Block/{block['name']}",
                headers={"X-Frappe-CSRF-Token": token},
            )
        deleted = api.delete(
            f"/api/resource/CBT Open Play Session/{row['name']}",
            headers={"X-Frappe-CSRF-Token": token},
        )
        assert deleted.ok, f"delete session {row['name']}: HTTP {deleted.status}"


def create_open_session(api, session_date: str, courts: list[str] | None = None) -> str:
    """Create + open a session over the admin context, returning its name.

    `courts` defaults to SESSION_COURTS (what file 08 has always used). File 16
    passes a SINGLE court on purpose: rotation is only observable when there are
    more players than seats.
    """
    courts = courts or SESSION_COURTS
    token = api_csrf(api)
    resp = api.post(
        "/api/resource/CBT Open Play Session",
        headers={"X-Frappe-CSRF-Token": token, "Content-Type": "application/json"},
        data={
            "branch": BGC_BRANCH,
            "title": SESSION_TITLE,
            "session_date": session_date,
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "court_type": "Pickleball",
            "rotation_mode": "Timed",
            "rotation_minutes": 15,
            "entry_fee": ENTRY_FEE,
            "courts": [{"court": court} for court in courts],
        },
    )
    assert resp.ok, f"create session: HTTP {resp.status} {resp.text()}"
    name = resp.json()["data"]["name"]

    opened = api.post(
        "/api/method/court_booking_tech.api.open_play.open_session",
        headers={"X-Frappe-CSRF-Token": token},
        form={"session": name},
    )
    assert opened.ok, f"open_session: HTTP {opened.status} {opened.text()}"
    return name
