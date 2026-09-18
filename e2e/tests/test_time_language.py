"""
E2E — THE APP SPEAKS ONE TIME LANGUAGE (Backlog B45).

The user, 2026-09-05: *"TIME SLOT IS 06:00 - 07:00 DIDN'T THE CLIENT FACING DOES
6 AM - 7PM WHAT THE FUCK"*, and — a PRIOR ruling — *"DIDN'T I SAY THE FORMATTING
SHOULD BE REUSABLE BEFORE"*.

The convention (6 AM · 6:30 AM · 12 MN · 12 NN) was ruled on 2026-09-04 and
implemented in ONE PAGE instead of one function, so every desk surface built
after it silently opted out. By 2026-09-05 the same hour had SIX renderings and
the desk printed `06:00 – 07:00` at sixteen sites.

WHY THIS FILE EXISTS AND `test_06`'S OLD ASSERTION DID NOT DO ITS JOB.
`test_06` asserted the time cell's text `.count("–") == 1`. That passes on
`06:00 – 07:00` AND on `6 AM – 7 AM`: it is structurally incapable of failing on
a FORMAT, which is the only thing it was there to protect. An assertion that
passes on both the right and the wrong answer is not a test.

So these rows assert the rendered STRING, and they assert it ACROSS the seam:

  1. the JavaScript and the Python agree on every row of the ruled table —
     `cbt.fmt.timeShort` in a real browser vs `timeutil.label_short` on the
     server, plus literals hard-coded here so "both wrong" cannot pass;
  2. the DESK's matrix time column and the CUSTOMER's render the SAME STRINGS
     for the same branch and the same day — the assertion that would have caught
     B45 on the day it shipped;
  3. a midnight END reads `12 MN`, never `12:00 PM`. `www/cbt-my-bookings.html`
     shipped a fork of the label WITHOUT its `% 24`, so a booking ending at
     `24:00:00` (a 23:59 closing means MIDNIGHT — S4 as-built 9) printed NOON on
     the customer's own list.

⚠ EVERY row asserts its COLLECTION SIZE before comparing. Two empty selector
results are equal, and a table of zero rows compares clean — that is how a
contract test goes green while both sides are wrong.

LEDGER: THIS FILE CLAIMS NO DATE. It is read-only on both faces — it loads
grids, reads their headers and books nothing. AYALA-bgc (06:00–22:00, open all
seven days) and the e2e-fast rig (00:00–23:59, which is what makes a `24:00:00`
end exist at all) at today+7, comfortably inside every seeded booking horizon.

SEATS: the desk board as Stella (AYALA staff) and Elsa (E2E Fast staff) — a
company seat may act only on its own company (B43). The portal grid is
guest-safe and is read from the same page without re-seating.
"""
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page

from helpers.auth import login_as
from helpers.board import (
    AYALA_STAFF,
    BGC_BRANCH,
    E2EF_BRANCH,
    E2EF_STAFF,
    load_board,
)
from helpers.worker_routing import bench_json

# Read-only on both faces, so this is a VIEW date, not a ledger claim.
D_VIEW = (date.today() + timedelta(days=7)).isoformat()

BGC_BOOK_URL = f"/book?c=ayala-courts&b=bgc&d={D_VIEW}"
E2EF_BOOK_URL = f"/book?c=e2e-fast&b=main&d={D_VIEW}"

# AYALA-bgc opens 06:00 and closes 22:00 on all seven days, at the platform's
# 60-minute grid — so these two strings are true whatever weekday the run lands
# on, and they are the exact pair the user pointed at.
BGC_FIRST_ROW = "6 AM – 7 AM"
BGC_LAST_ROW = "9 PM – 10 PM"
# The e2e-fast rig runs 00:00-23:59, i.e. midnight to MIDNIGHT.
E2EF_FIRST_ROW = "12 MN – 1 AM"
E2EF_LAST_ROW = "11 PM – 12 MN"

TIME_CELLS = "table.cbt-matrix tbody th.cbt-timecell"


def _time_column(page: Page) -> list:
    """Every rendered time-column string, in row order, from whichever matrix is
    on screen. Both faces build the same table (a `thead` corner cell plus one
    `tbody` row header per grid slot), which is what makes the two lists
    comparable at all."""
    return [
        text.strip()
        for text in page.locator(TIME_CELLS).all_inner_texts()
        if text.strip()
    ]


def _open_portal_grid(page: Page, url: str) -> list:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=30000)
    return _time_column(page)


@pytest.mark.e2e
class TestTimeLanguage:
    def test_the_javascript_and_the_python_agree_on_every_hour(self, page: Page):
        """`cbt.fmt.timeShort` (public/js/cbt_time_format.js) and
        `timeutil.label_short` are two implementations of ONE ruling. This is
        the only thing standing between them and the drift that produced B45."""
        table = bench_json("court_booking_tech.testing.time_label_table", [])
        rows = table["rows"]
        # The size gate, first. A helper that returned [] would make every
        # comparison below vacuously true — which is exactly the shape of test
        # this row exists to replace.
        assert len(rows) >= 13, f"the ruled table shrank to {len(rows)} rows: {rows}"

        login_as(page, AYALA_STAFF)
        load_board(page, BGC_BRANCH, D_VIEW)
        # The module must be REACHABLE on the desk before anything is compared —
        # a missing include would otherwise surface as a wall of identical
        # "undefined" mismatches instead of "the file did not load".
        assert page.evaluate(
            "() => !!(window.cbt && cbt.fmt && typeof cbt.fmt.timeShort === 'function')"
        ), "cbt.fmt.timeShort is not on the desk — check hooks.app_include_js"

        rendered = page.evaluate(
            "(values) => values.map((v) => cbt.fmt.timeShort(v))",
            [row["value"] for row in rows],
        )
        assert len(rendered) == len(rows)
        for row, browser in zip(rows, rendered):
            assert row["server"] == row["expected"], (
                f"the SERVER broke the ruling for {row['value']}: "
                f"{row['server']!r} != {row['expected']!r}"
            )
            assert browser == row["expected"], (
                f"the BROWSER and the server disagree about {row['value']}: "
                f"browser {browser!r} vs ruled {row['expected']!r}"
            )

        # Hard-coded literals as well as the table: if BOTH sides were changed
        # together the comparison above would still pass, and the ruling is the
        # thing being protected — not the agreement.
        assert page.evaluate("() => cbt.fmt.timeShort('00:00:00')") == "12 MN"
        assert page.evaluate("() => cbt.fmt.timeShort('12:00:00')") == "12 NN"
        assert page.evaluate("() => cbt.fmt.timeShort('24:00:00')") == "12 MN"
        assert page.evaluate("() => cbt.fmt.timeShort('06:30:00')") == "6:30 AM"
        assert page.evaluate("() => cbt.fmt.timeShort('13:00:00')") == "1 PM"
        assert (
            page.evaluate("() => cbt.fmt.timeRange('18:00:00', '19:00:00')")
            == "6 PM – 7 PM"
        )

    def test_the_desk_and_the_portal_render_the_same_hour_the_same_way(
        self, page: Page
    ):
        """THE ROW B45 ASKED FOR. Staff read a time out loud to a customer who is
        looking at their own phone; until 2026-09-05 the two screens disagreed
        about what that time was called, and no assertion could see it."""
        login_as(page, AYALA_STAFF)
        load_board(page, BGC_BRANCH, D_VIEW)
        desk = _time_column(page)
        assert len(desk) >= 8, f"the desk matrix rendered {len(desk)} hours: {desk}"
        assert desk[0] == BGC_FIRST_ROW, desk[:3]
        assert desk[-1] == BGC_LAST_ROW, desk[-3:]

        portal = _open_portal_grid(page, BGC_BOOK_URL)
        assert len(portal) >= 8, f"the portal grid rendered {len(portal)} hours"
        assert desk == portal, (
            "the desk and the customer are looking at the same branch on the "
            f"same day and calling the hours different things:\n"
            f"  desk:   {desk[:4]}\n  portal: {portal[:4]}"
        )
        # And it is the RULED language, not merely a shared one.
        assert "AM" in desk[0] and ":" not in desk[0], desk[0]

    def test_a_midnight_end_reads_as_12_MN_and_never_as_noon(self, page: Page):
        """The `% 24` regression, on the only rig whose grid reaches midnight.

        `www/cbt-my-bookings.html` parsed the hour without `% 24`, so
        `24 % 12 || 12` gave 12 with an hour not less than 12 — "12:00 PM".
        Noon, for midnight, on the page the customer checks their own booking on.
        """
        login_as(page, E2EF_STAFF)
        load_board(page, E2EF_BRANCH, D_VIEW)
        desk = _time_column(page)
        assert len(desk) >= 24, f"the e2e-fast grid rendered {len(desk)} hours"
        assert desk[0] == E2EF_FIRST_ROW, desk[:2]
        assert desk[-1] == E2EF_LAST_ROW, desk[-2:]
        assert "12:00 PM" not in desk[-1], (
            "a midnight END is rendering as NOON — the `% 24` is gone again"
        )

        portal = _open_portal_grid(page, E2EF_BOOK_URL)
        assert len(portal) >= 24, f"the portal grid rendered {len(portal)} hours"
        assert desk == portal, (desk[-2:], portal[-2:])

    def test_the_customers_own_list_speaks_the_same_language(self, page: Page):
        """`/my-bookings` is where the buggy fork lived. Its cards must now read
        the ruled words — proved on the STRING, not on the function's existence.
        """
        from helpers.auth import portal_login_as

        portal_login_as(page, "cust.carla@example.com")
        page.goto("/my-bookings", wait_until="domcontentloaded", timeout=60000)
        # NOT wait_for_page_load: `body[data-ajax-state="complete"]` is a DESK
        # marker and a website page never sets it. The cards' own wait below is
        # the portal's gate, exactly as test_mobile_portal does it.
        cards = page.locator(
            "#cbt-open .cbt-card[data-status], #cbt-past .cbt-card[data-status]"
        )
        cards.first.wait_for(state="visible", timeout=30000)
        assert cards.count() >= 1, "Carla has no bookings — the seed changed"

        assert page.evaluate(
            "() => !!(window.cbt && cbt.fmt && typeof cbt.fmt.timeShort === 'function')"
        ), (
            "cbt.fmt is not on the portal — templates/includes/cbt_portal_head.html "
            "no longer loads cbt_time_format.js, and every portal page is throwing"
        )

        # The card's second sub-line is "<date> · <start> – <end>".
        spans = [
            text.strip()
            for text in page.locator(
                "#cbt-open .cbt-card .cbt-card-sub, #cbt-past .cbt-card .cbt-card-sub"
            ).all_inner_texts()
            if "–" in text
        ]
        assert len(spans) >= 1, "no card rendered a time span"
        for span in spans:
            when = span.split("·")[-1].strip()
            assert " – " in when, when
            for half in when.split(" – "):
                assert half.endswith(("AM", "PM", "MN", "NN")), (
                    f"{half!r} is not the ruled language (from {span!r})"
                )
                # "1:00 PM" is the OLD form this row retires: a bare hour never
                # carries ":00".
                assert not half.endswith(":00 AM") and not half.endswith(":00 PM"), (
                    f"{half!r} still prints the dead minutes"
                )
