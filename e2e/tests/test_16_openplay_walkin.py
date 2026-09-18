"""
E2E file 16 — Open play walk-ins (section-20, Backlog B2).

The stranger with cash who wants to join tonight's session. Staff tick "Walk-in
(no account)", type a name, and that person is in the rotation like everybody
else — seated, rotated and billed under their own name, with no User row and no
email demanded of them.

THE POINT OF DOING THIS ON THE REAL BOARD is the KEY. Before this section every
rotation action addressed a player by their user id, so a walk-in would have
been `null` and the engine would have handed the first account-less player it
met to whichever button was pressed. `test_mixed_four_rotates` watches a walk-in
travel from the court to the back of the queue, identified by a key that is
provably NOT an email, while account players around them do the same.

Runs as Administrator, admin-driven like files 06 and 08 (E2EF has no staff
user; the staff-actor positives live in the backend rows —
tests/test_open_play_walkin.py).

The first two tests are SEQUENTIAL BY DESIGN, like file 08: `test_mixed_four_rotates`
plays the session the walk-in joined in `test_walkin_joins_via_the_toggle`, so a
failure there cascades — which is informative here (the narrative broke at step
N) rather than misleading. `--dist loadfile` keeps the whole class on one
worker. The last two tests use their own session and are independent.

Date map (all relative to the run day). The full E2E ledger, re-verified this
session by reading EVERY `timedelta(days=…)` in e2e/tests — not by copying an
earlier file's recital, which is how Backlog B16 happened:

  +30/+31 test_booking_form · +32..+34 test_proof_flow · +35/+36
  test_billing_print · +37..+39 file 09 · +40..+44 and +46/+47 file 06 ·
  **+45 file 02** (NOT file 06 — and it slides to +46 when the run day makes
  it a Sunday, landing on file 06's D_MEMBER: Backlog B16 (b)) · +48/+49
  file 12 · +50/+51 file 05 · +52..+57 file 07 · +58/+59 file 13 ·
  **+60/+61 files 08 AND 10 both** (Backlog B16 (a), a real double-claim that
  survives only on a one-hour margin) · +62 file 10 · +63/+64 file 11 ·
  +65 file 14 · +67/+68 file 15. **+66 is unclaimed.**

**THIS FILE CLAIMS +69 and +70**, both free on every court:

  +69  AYALA-bgc-court-1 — the join-and-rotate session (ONE court on purpose:
       rotation is only observable when there are more players than seats)
  +70  AYALA-bgc-court-2 — the billing-print and portal-invisibility session

Neither B16 defect is introduced or touched here.

No sleeps. Every board assertion waits on the RE-RENDERED node or on the board's
own payload — the page reloads asynchronously after every mutation.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, Playwright

from helpers import gestures
from helpers.board import platform_api, wait_dialog
from helpers.open_play import (
    add_players,
    add_walkin,
    court_player_names,
    court_players,
    create_open_session,
    purge_sessions,
    queue_customers,
    queue_player_keys,
    queue_player_names,
    reload_board,
    show_session,
)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

D_JOIN = (date.today() + timedelta(days=69)).isoformat()
D_BILL = (date.today() + timedelta(days=70)).isoformat()

JOIN_COURT = "AYALA-bgc-court-1"
BILL_COURT = "AYALA-bgc-court-2"

WALKIN_NAME = "Wanda Cruz"
WALKIN_PHONE = "0917-555-0169"
BILL_WALKIN = "Ruben Santos"

# Seeded open play players — the accounts the walk-in shares a queue with.
OP_PLAYERS = [
    "op.ace@example.com",
    "op.bea@example.com",
    "op.caloy@example.com",
    "op.dina@example.com",
    "op.elmo@example.com",
    "op.fely@example.com",
    "op.gani@example.com",
]

PRINT_PARAMS = {"format": "CBT Billing Statement", "no_letterhead": "1"}


@pytest.fixture(scope="module")
def join_session(playwright: Playwright, platform_auth):
    api = platform_api(playwright)
    try:
        purge_sessions(api, D_JOIN)
        yield create_open_session(api, D_JOIN, courts=[JOIN_COURT])
    finally:
        api.dispose()


@pytest.fixture(scope="module")
def bill_session(playwright: Playwright, platform_auth):
    api = platform_api(playwright)
    try:
        purge_sessions(api, D_BILL)
        yield create_open_session(api, D_BILL, courts=[BILL_COURT])
    finally:
        api.dispose()


def _doc(page: Page, doctype: str, name: str) -> dict:
    resp = page.request.get(f"/api/resource/{doctype}/{name}")
    assert resp.ok, f"GET {doctype}/{name}: HTTP {resp.status}"
    return resp.json()["data"]


def _walkin_participant(page: Page, session: str, name: str) -> dict:
    doc = _doc(page, "CBT Open Play Session", session)
    rows = [
        row
        for row in doc["participants"]
        # ASSERT KEY ABSENCE, never `row["customer"] is None`. /api/resource
        # serialises through `as_dict(no_nulls=True)`, which propagates into
        # child rows — every NULL field is DROPPED, so subscripting one raises
        # KeyError (S13 as-built 15).
        if "customer" not in row and row.get("customer_name") == name
    ]
    assert len(rows) == 1, f"expected exactly one walk-in {name!r}, got {rows}"
    return rows[0]


@pytest.mark.e2e
class TestOpenPlayWalkIn:
    def test_walkin_joins_via_the_toggle(self, page: Page, join_session):
        """The core of Backlog B2: no account, no email demanded, and the
        person's own name is what the desk (and the receipt) shows."""
        show_session(page, join_session)

        page.locator(".cbt-op-bottom [data-action='add']").click()
        wait_dialog(page)

        # walk_in FIRST: it drives depends_on, and the name/phone controls do
        # not exist in the layout until the Check flips.
        gestures.check(page, "walk_in", True, scope=gestures.DIALOG)
        gestures.fill(page, "customer_name", WALKIN_NAME, scope=gestures.DIALOG)
        gestures.fill(page, "customer_phone", WALKIN_PHONE, scope=gestures.DIALOG)
        gestures.fill(page, "payment_method", "Cash", scope=gestures.DIALOG)
        # The fee hint renders "Walk-in — no membership. Pays ₱150." into the
        # SAME node the account path uses, so this wait still means "the money
        # area has settled" (S13 as-built 13). Scoped to the VISIBLE modal:
        # closed frappe dialogs stay in the DOM.
        #
        # Gated on the hint's CONTENT, not on the dialog's `_hint_settled` flag.
        # sync_hint() runs once synchronously before dialog.show() and its
        # no-customer branch sets that flag true, so a wait on the flag alone is
        # already satisfied before this line runs and can never block — the
        # inverse of the failure helpers/board.wait_money_settled documents.
        page.wait_for_function(
            """() => {
                const el = document.querySelector(
                    ".modal.show [data-testid='op-member-hint']"
                );
                return !!el && /walk-in/i.test(el.textContent);
            }""",
            timeout=15000,
        )
        hint = page.locator(".modal.show [data-testid='op-member-hint']").inner_text()
        assert "no membership" in hint.lower(), hint

        with page.expect_response(
            lambda r: "add_players" in r.url, timeout=30000
        ) as resp_info:
            page.evaluate("() => cur_dialog.get_primary_btn().click()")
        assert resp_info.value.ok, (
            f"add_players: HTTP {resp_info.value.status} {resp_info.value.text()}"
        )
        # The dialog clears the identity for the next arrival INSIDE the
        # response handler, and KEEPS the walk-in toggle on — a desk taking
        # walk-ins is usually taking several.
        page.wait_for_function(
            """() => window.cur_dialog
                && !cur_dialog.get_value('customer_name')
                && cint(cur_dialog.get_value('walk_in')) === 1""",
            timeout=15000,
        )
        page.evaluate("() => cur_dialog.get_secondary_btn().click()")
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)

        reload_board(page, join_session)
        row = page.locator(f".cbt-op-queue-row[data-player-name='{WALKIN_NAME}']")
        assert row.count() == 1, "the typed name did not reach the queue"
        assert row.locator("[data-testid='op-walkin-chip']").count() == 1, (
            "a player with no account must be marked as a walk-in"
        )
        assert row.get_attribute("data-customer") is None, (
            "a walk-in row must carry no account attribute at all"
        )
        # Their key is NOT their name and NOT an email — it is their own row.
        key = row.get_attribute("data-player-key")
        assert key and "@" not in key and key != WALKIN_NAME, key
        assert queue_customers(page) == [], "no accounts are in this queue yet"
        assert queue_player_names(page) == [WALKIN_NAME]

        participant = _walkin_participant(page, join_session, WALKIN_NAME)
        assert participant["customer_phone"] == WALKIN_PHONE
        assert participant["payment_status"] == "Paid"
        assert float(participant["discount_percent"]) == 0.0

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "s20_openplay_walkin.png"), full_page=True
        )

    def test_mixed_four_rotates(self, page: Page, join_session):
        """THE KEYING PROOF on the real board: a walk-in and three account
        players share a court, and Game Done moves exactly the right people."""
        show_session(page, join_session)
        add_players(page, OP_PLAYERS)  # 7 accounts join the walk-in already there
        reload_board(page, join_session)

        walkin_key = page.locator(
            f".cbt-op-queue-row[data-player-name='{WALKIN_NAME}']"
        ).get_attribute("data-player-key")
        assert walkin_key and "@" not in walkin_key

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
                return b.state && b.state.assignments.length === 1;
            }""",
            timeout=30000,
        )

        # Seated in join order: the walk-in first, then the first three accounts.
        seated = court_players(page, JOIN_COURT)
        assert seated == [walkin_key] + OP_PLAYERS[:3], seated
        assert court_player_names(page, JOIN_COURT)[0] == WALKIN_NAME
        # The court card names the person, never the row hash.
        assert walkin_key not in court_player_names(page, JOIN_COURT)
        assert queue_player_keys(page) == OP_PLAYERS[3:7]

        with page.expect_response(
            lambda r: "game_done" in r.url, timeout=30000
        ) as resp_info:
            page.locator(
                f".cbt-op-court-card[data-court='{JOIN_COURT}'] [data-action='game-done']"
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
                    && court.players[0].key === expected.first;
            }""",
            arg={"court": JOIN_COURT, "first": OP_PLAYERS[3]},
            timeout=30000,
        )

        # The four who played go to the BACK in seat order — the walk-in among
        # them, addressed by key the whole way round.
        assert court_players(page, JOIN_COURT) == OP_PLAYERS[3:7]
        assert queue_player_keys(page) == [walkin_key] + OP_PLAYERS[:3]
        assert queue_player_names(page)[0] == WALKIN_NAME

        doc = _doc(page, "CBT Open Play Session", join_session)
        walkin_rows = [
            row
            for row in doc["queue"]
            if "customer" not in row and row["customer_name"] == WALKIN_NAME
        ]
        assert len(walkin_rows) == 1, walkin_rows
        assert walkin_rows[0]["name"] == walkin_key, (
            "a walk-in's key must be their own queue row name"
        )
        assert walkin_rows[0]["games_played"] == 1
        assert walkin_rows[0]["status"] == "Waiting"

    def test_walkin_perhead_billing_print(self, page: Page, bill_session):
        """The honest-receipt requirement, per head: the statement names the
        person who actually paid, not a shared "Walk-in" account."""
        show_session(page, bill_session)
        add_walkin(page, BILL_WALKIN, WALKIN_PHONE)
        reload_board(page, bill_session)

        participant = _walkin_participant(page, bill_session, BILL_WALKIN)
        assert participant["billing_doc"], "walk-in participant has no statement"

        invoice = _doc(page, "CBT Booking Invoice", participant["billing_doc"])
        assert invoice["status"] == "Paid & Verified", invoice["status"]
        assert invoice["customer_name"] == BILL_WALKIN
        assert "customer" not in invoice, (
            f"a walk-in statement must carry no account, got {invoice.get('customer')!r}"
        )
        assert invoice["participant_ref"] == participant["name"]
        assert not invoice.get("booking"), "an open play statement bills a participant"

        resp = page.request.get(
            "/printview",
            params={
                "doctype": "CBT Booking Invoice",
                "name": invoice["name"],
                **PRINT_PARAMS,
            },
        )
        assert resp.ok, f"printview {invoice['name']}: HTTP {resp.status}"
        html = resp.text()
        assert BILL_WALKIN in html
        assert "PAID &amp; VERIFIED" in html
        # A blank or a literal "None" on a printed receipt is the failure mode
        # this whole story exists to avoid.
        assert "Customer:</strong> None" not in html

    def test_walkin_invisible_to_customers(
        self, page: Page, customer_page: Page, bill_session
    ):
        """Open play has NO portal surface at all — `api/portal.py` ships no
        participant data of any kind — so a customer session cannot reach the
        walk-in's identity through anything, not even their own account's
        board. The phone number never leaves the desk (PLAN §8j)."""
        show_session(page, bill_session)
        participant = _walkin_participant(page, bill_session, BILL_WALKIN)
        assert participant["customer_phone"] == WALKIN_PHONE, "sanity: staff CAN see it"

        board = customer_page.request.get(
            "/api/method/court_booking_tech.api.open_play.get_open_play_board",
            params={"company": "ayala-courts", "session": bill_session},
        )
        assert not board.ok, (
            f"a customer must not read the open play board, got HTTP {board.status}"
        )
        assert BILL_WALKIN not in board.text()
        assert WALKIN_PHONE not in board.text()

        session_doc = customer_page.request.get(
            f"/api/resource/CBT Open Play Session/{bill_session}"
        )
        assert not session_doc.ok, (
            f"a customer must not read the session document, got HTTP {session_doc.status}"
        )
        assert BILL_WALKIN not in session_doc.text()

        # Their own portal history is untouched by any of it.
        mine = customer_page.request.get(
            "/api/method/court_booking_tech.api.portal.get_my_bookings"
        )
        assert mine.ok, f"get_my_bookings: HTTP {mine.status}"
        assert BILL_WALKIN not in mine.text(), "the walk-in's name leaked to the portal"
        assert WALKIN_PHONE not in mine.text()
