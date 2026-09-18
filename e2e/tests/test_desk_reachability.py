"""
E2E — Desk reachability (Backlog B25 + B26, 2026-08-27).

The user's words, 2026-08-20: *"when i look at the company view i can upload
banner and logo. then why the hell was gallery not on the same view? or even a
shortcut to it"*. Two rows, one per half of that sentence, and both driven the
way staff would drive them — a tile is CLICKED on the Hub, a Connection badge
is CLICKED on the company form. Neither row calls `set_route`; a shortcut that
only works when a test navigates for the user is not a shortcut.

The backend contract (`tests/test_workspace.py`) already proves the JSON and
the migrated rows. What only a browser can prove is that the thing a human
sees is clickable and lands on the right list with the right rows — so every
row here ends on a rendered list whose row count equals an out-of-band API
count. Row counts, not "the list is visible": a filter that silently did not
apply would still show a visible list.

Runs as Administrator (the `page` fixture). Claims NO ledger date: nothing
here books, blocks or writes.
"""
import json

import pytest
from playwright.sync_api import Page, expect

from helpers.navigation import goto_form, goto_workspace, wait_for_page_load

# Re-pointed from CBT Company Media when photographs moved onto the facility
# (section-29). The CONTRACT is unchanged — a Hub tile and a Connections badge
# are clicked, not routed to — only the doctype it is proven against.
TARGET_DOCTYPE = "CBT Branch"
TARGET_ROUTE = "cbt-branch"
TARGET_COMPANY = "qc-smash"


def _names(page: Page, doctype: str, filters: list) -> list:
    """An out-of-band opinion on WHICH rows the list should show."""
    resp = page.request.get(
        f"/api/resource/{doctype}",
        params={"filters": json.dumps(filters), "limit_page_length": 0},
    )
    assert resp.ok, f"GET {doctype}: HTTP {resp.status} {resp.text()}"
    return sorted(row["name"] for row in resp.json()["data"])


def _rendered_names(page: Page, want: int) -> list:
    """The rows a human could tick. `.list-row-container` is NOT the row count
    in v16 (measured: 7 containers for 6 rows — one is chrome), so this counts
    the per-row checkbox, which only data rows carry."""
    # The desk list renders 20 rows a page; past that, a count mismatch would
    # read as a reachability regression when it is the seed that grew.
    assert want <= 20, f"seed outgrew one list page ({want} rows) — page or filter"
    rows = page.locator("input.list-row-checkbox[data-name]")
    expect(rows).to_have_count(want, timeout=15000)
    return sorted(rows.evaluate_all("els => els.map((e) => e.dataset.name)"))


@pytest.mark.e2e
class TestDeskReachability:
    def test_hub_shortcut_opens_the_list(self, page: Page):
        """B25 half one: the CBT Hub carries a tile, and clicking it lands on
        that list — every seeded row, since the PLATFORM seat's
        `get_session_company()` is None, which means all tenants."""
        goto_workspace(page, "CBT Hub")
        tile = page.locator(".shortcut-widget-box", has_text=TARGET_DOCTYPE)
        expect(tile).to_be_visible(timeout=15000)
        tile.click()
        page.wait_for_url(f"**/desk/{TARGET_ROUTE}**", timeout=30000)
        wait_for_page_load(page)
        # Drop any remembered filter (section-29).
        page.evaluate(
            "() => window.cur_list && cur_list.filter_area && cur_list.filter_area.clear()"
        )
        wait_for_page_load(page)

        want = _names(page, TARGET_DOCTYPE, [])
        assert want, f"no {TARGET_DOCTYPE} is seeded — fixture regression"
        assert _rendered_names(page, len(want)) == want

    def test_company_form_connection_opens_its_rows(self, page: Page):
        """B25 half two (and B26's pattern): the company form shows a
        Connections badge, the badge carries the count a human reads, and
        clicking it opens the list FILTERED to this company."""
        goto_form(page, "CBT Company", TARGET_COMPANY)
        page.wait_for_function(
            "(name) => window.cur_frm && cur_frm.doc && cur_frm.doc.name === name",
            arg=TARGET_COMPANY,
            timeout=30000,
        )
        want = _names(page, TARGET_DOCTYPE, [["company", "=", TARGET_COMPANY]])
        assert want, f"{TARGET_COMPANY} has none seeded — fixture regression"

        badge = page.locator(f".document-link[data-doctype='{TARGET_DOCTYPE}']")
        expect(badge).to_be_visible(timeout=15000)
        # The count is fetched AFTER the form renders (get_open_count); a human
        # sees the number settle, and so must this row before it trusts it.
        expect(badge.locator(".count")).to_have_text(str(len(want)), timeout=15000)

        badge.locator("a.badge-link").click()
        # The list lands on /view/list FIRST and only then applies the
        # route_options the badge set, rewriting the URL with the filter — so
        # the filter is waited for, not read off the first URL. This wait is
        # the load-bearing half of the row.
        page.wait_for_url(
            f"**/desk/{TARGET_ROUTE}**company={TARGET_COMPANY}**", timeout=30000
        )
        wait_for_page_load(page)
        assert _rendered_names(page, len(want)) == want
