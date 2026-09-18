"""
E2E file 19 — FaceLift smoke: the design system actually reaches the browser
(section-23, Backlog B14).

This file is deliberately THIN. The real regression net for a restyle is every
other file in the suite: 102 rows already select on this DOM, and the gate for
section-23 is three consecutive full-suite passes. What those rows cannot tell
you is whether the theme ARRIVED — a stylesheet that 404s, a token that never
resolves, or a workspace card that silently renders nothing all leave the DOM
they assert on completely intact.

So each row here proves one seam, and proves it end-to-end rather than by
existence:

  1. the token layer reaches the PORTAL (a <link> in the shared head) AND a
     component actually consumes it — a resolved custom property with nothing
     reading it is a stylesheet nobody applied;
  2. the CBT Hub's number-card row renders. Number Cards fail SILENTLY: a
     content block whose `number_card_name` does not match a `number_cards`
     child row's LABEL makes block.js return an empty wrapper, with no error
     in the console, in the response, or anywhere else;
  3. the token layer reaches the DESK (app_include_css) — asserted on the
     board's court-card head — plus the two states a facelift is most likely
     to have broken while nobody was looking: the full legend, and the
     designed empty state.

Runs on the PLATFORM seat, the suite's default since Backlog B43 — which also
makes this a real check that the theme reaches a seat that is not a superuser.

**THIS FILE CLAIMS NO LEDGER SLOT.** Every row is a read-only page load: it
books nothing, blocks nothing, and creates no document, so it consumes no
court x date and cleans nothing up. The ledger below is restated (not copied
forward — Backlog B16 is what copying forward produces) only so the next
section can see that +75 upward is still free:

  +0 TODAY  file 14 (E2EF-main-court-1) and file 15 (AYALA-bgc-court-1)
  +30/+31   test_booking_form.py            +32..+34  test_proof_flow.py
  +35/+36   test_billing_print.py           +37..+39  file 09
  +40..+44, +46, +47   file 06              +45       file 02
  +48/+49   file 12                         +50/+51   file 05
  +52..+57  file 07                         +58/+59   file 13
  +60/+61   files 08 AND 10 both            +62       file 10
  +63/+64   file 11                         +65       file 14
  +66       reserved in prose, never used
  +67/+68   file 15                         +69/+70   file 16
  +71/+72   file 17                         +73/+74   file 18
  +75 up    FREE

The one date this file computes is the NEXT SUNDAY, and it is not a claim: it
is the day `QCSM-timog` is seeded CLOSED, which is how the "closed on this day"
empty state is reached without inventing a court-less branch fixture. Reading a
board writes nothing, so a Sunday shared with any other file is not a conflict.

No sleeps. Every assertion waits on a rendered node or a computed style.
"""
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from helpers.board import BGC_BRANCH, load_board
from helpers.navigation import wait_for_page_load

# The court green, as declared in public/css/cbt_theme.css. Stated as BOTH the
# authored hex and the rgb() a browser reports, because getPropertyValue returns
# the author's text while getComputedStyle(...).backgroundColor is resolved.
COURT_GREEN_HEX = "#0e7c5b"
COURT_GREEN_RGB = "rgb(14, 124, 91)"
PRIMARY_SOFT_RGB = "rgb(230, 244, 239)"

# EVERY token cbt_theme.css declares on :root. Not decoration — this list is
# the only thing that catches a malformed token, and it caught a real one:
# a "*/" inside a prose comment closed the comment early, CSS error recovery
# ate the declaration that followed, and --cbt-reserved-bg silently vanished.
# A booked slot then rendered with NO background — visually indistinguishable
# from an available one — and nothing warned, because this file is served raw
# through the assets symlink and never goes through esbuild.
# Add a token here when you add one there.
THEME_TOKENS = [
    "--cbt-primary", "--cbt-primary-600", "--cbt-primary-soft", "--cbt-primary-line",
    "--cbt-accent", "--cbt-accent-soft", "--cbt-accent-ink", "--cbt-accent-contrast",
    "--cbt-surface", "--cbt-card", "--cbt-ink", "--cbt-ink-muted", "--cbt-line",
    "--cbt-reserved-bg", "--cbt-reserved-ink",
    "--cbt-confirmed-bg", "--cbt-confirmed-ink",
    "--cbt-extended-bg", "--cbt-extended-ink",
    "--cbt-blocked-bg", "--cbt-blocked-ink", "--cbt-blocked-line",
    "--cbt-noshow-bg", "--cbt-noshow-ink",
    "--cbt-past-bg", "--cbt-past-ink",
    "--cbt-completed-ink", "--cbt-dead-ink",
    "--cbt-moved-bg", "--cbt-moved-ink", "--cbt-moved-line",
    "--cbt-radius", "--cbt-radius-sm", "--cbt-radius-pill",
    "--cbt-shadow", "--cbt-shadow-lift", "--cbt-shadow-bar", "--cbt-focus-ring",
]

# Section-22's imagery lives here and the branch is CLOSED ON SUNDAYS — both
# facts are used below, for two different rows.
QCSM_BRANCH = "QCSM-timog"
BOOK_URL = "/book?c=e2e-fast&b=main"

# The Hub's section order, top to bottom, after the section-23 rebuild.
HUB_SECTIONS = [
    "At a glance",
    "Bookings",
    "Open Play",
    "Members",
    "Facilities",
    "Reports",
    "Tenants",
    "Platform",
]
HUB_CARDS = ["Active Bookings", "Pending Proofs", "Memberships"]


def _next_sunday(today: date) -> str:
    """The next day QCSM-timog is closed. Today counts if today IS Sunday."""
    # date.weekday(): Monday=0 .. Sunday=6
    return str(today + timedelta(days=(6 - today.weekday()) % 7))


@pytest.mark.e2e
class TestFaceliftSmoke:

    def test_portal_carries_the_court_green_token(self, page: Page):
        """The token layer reached the portal AND something consumes it.

        Two halves, and both are needed. `--cbt-primary` resolving proves
        cbt_theme.css loaded; it does NOT prove cbt_portal.css was rewritten to
        read it, which is the half that makes the page look different. The
        selected date chip is the check for that: exactly one exists on /book at
        all times (file 05 pins that invariant), and its background is the
        brand colour by definition of the design system.
        """
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results", timeout=30000)
        token = page.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--cbt-primary').trim()"
        )
        assert token.lower() == COURT_GREEN_HEX, (
            f"--cbt-primary resolved to {token!r} on /find-court — is "
            "cbt_theme.css linked from cbt_portal_head.html, and did "
            "`bench build` run?"
        )

        # EVERY token, not just the one above. A token that never parses is
        # invisible until a customer looks at the surface that needed it.
        missing = page.evaluate(
            """(names) => {
                const cs = getComputedStyle(document.documentElement);
                return names.filter((n) => !cs.getPropertyValue(n).trim());
            }""",
            THEME_TOKENS,
        )
        assert not missing, (
            f"cbt_theme.css declares tokens that do not resolve: {missing} — a "
            "malformed declaration (or a comment that closed early) swallowed "
            "them; everything reading them falls back to unset"
        )

        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=60000)
        chip = page.locator(".cbt-date[aria-pressed='true']")
        chip.wait_for(state="visible", timeout=30000)
        background = chip.evaluate("(el) => getComputedStyle(el).backgroundColor")
        assert background == COURT_GREEN_RGB, (
            f"the selected date chip is {background}, not the court green — "
            "cbt_portal.css is not reading --cbt-primary"
        )

        # The status palette actually reaching a chip — what the swallowed-token
        # bug broke: a booked slot rendered on the page ground, which is exactly
        # what an available one looks like.
        #
        # A PROBE element rather than a real chip on purpose. Whether this
        # branch has a booked slot right now is a data question, and gating the
        # assertion on it would make the row silently do nothing on most days.
        # The probe asks the only question that matters — what does the cascade
        # resolve for this class — and asks it every run.
        booked_bg = page.evaluate(
            """() => {
                const probe = document.createElement('button');
                probe.className = 'cbt-slot cbt-slot--booked';
                document.body.appendChild(probe);
                const bg = getComputedStyle(probe).backgroundColor;
                probe.remove();
                return bg;
            }"""
        )
        assert booked_bg == "rgb(255, 246, 224)", (
            f"a booked slot chip resolves to {booked_bg} — --cbt-reserved-bg is "
            "not reaching it, so a booked slot is indistinguishable from an "
            "available one"
        )

    def test_workspace_shows_the_number_card_row(self, page: Page):
        """The Hub opens with three numbers and the curated sections below.

        Asserts PRESENCE, never a value: each worker runs its own site with its
        own data, and a count assertion here would be a fixture test wearing a
        facelift test's clothes.
        """
        page.goto("/app/cbt-hub", wait_until="domcontentloaded", timeout=60000)
        wait_for_page_load(page)

        for label in HUB_CARDS:
            card = page.get_by_text(label, exact=True).first
            card.wait_for(state="visible", timeout=30000)

        headings = page.eval_on_selector_all(
            ".layout-main-section .h4",
            "els => els.map((e) => e.innerText.trim())",
        )
        assert headings == HUB_SECTIONS, (
            f"CBT Hub sections are {headings}, expected {HUB_SECTIONS} — "
            "content blocks and links Card Breaks must agree"
        )

    def test_board_legend_and_empty_state(self, page: Page):
        """The desk half: app_include_css arrived, and both board states hold.

        The legend is the board's colour key, so a restyle that drops a swatch
        makes every chip an unexplained colour. The empty state is the screen a
        staff member sees on a closed day — the one place a facelift is least
        likely to be looked at and most likely to have left a bare grey box.
        """
        load_board(page, BGC_BRANCH, str(date.today()))

        legend = page.locator(".cbt-legend .cbt-legend-item")
        expect(legend).to_have_count(7)
        expect(page.locator(".cbt-legend")).to_contain_text("In progress")

        # B33: the per-court CARD head became the matrix's COLUMN head. Same
        # role, same token — this is still the board's proof that the design
        # layer reached the desk.
        head_bg = page.locator("th.cbt-colhead").first.evaluate(
            "(el) => getComputedStyle(el).backgroundColor"
        )
        assert head_bg == PRIMARY_SOFT_RGB, (
            f"court column head is {head_bg}, not the tinted green — is "
            "app_include_css wired in hooks.py, and did `bench build` run?"
        )

        # QCSM-timog is seeded closed on Sundays, so this reaches the designed
        # empty state with no fixture of its own. Read-only: nothing is booked.
        load_board(page, QCSM_BRANCH, _next_sunday(date.today()))
        empty = page.locator(".cbt-board-empty")
        expect(empty).to_have_count(1)
        expect(empty.locator(".cbt-empty-title")).to_be_visible()
        empty_bg = empty.evaluate("(el) => getComputedStyle(el).backgroundColor")
        assert empty_bg == PRIMARY_SOFT_RGB, (
            f"the board's empty state is {empty_bg} — it should be the designed "
            "tinted panel, not a bare box"
        )
