"""
Query-report helpers — open a report from the Hub, drive its filter bar by
gesture, and READ THE RENDERED TABLE the way a person does (section-26; lifted
verbatim from test_channel_ledger.py so test_platform_statement and the refund
lane read the same table the same way).

MEASURED (2026-08-27, three instrumented runs, section-25 note 10): frappe's
datatable renders only the rows in VIEW and RECYCLES row elements while
scrolling — a row's `data-row-index` updates before its cells repaint — so a
read taken the moment the index moves pairs a new index with stale content.
`table()` therefore reads only a SETTLED window at every scroll step and
accepts a row only when its own serial cell (the first column, index + 1)
agrees with its index; the newest read wins. Every filter change re-runs the
report and REPLACES the datatable; an empty result hides the old table
("Nothing to show") rather than emptying it; currency cells render `₱0.00`.
"""

import re

from playwright.sync_api import Page, expect

from helpers import gestures
from helpers.navigation import goto_workspace, wait_for_page_load

FILTER_BAR = ".page-form"
ROWS = ".datatable .dt-row[data-row-index]"


def money(text: str) -> list:
    return [float(v.replace(",", "")) for v in re.findall(r"\d[\d,]*\.\d{2}", text)]


def num(row: dict | None, column: str) -> float:
    """A cell as a number — `₱0.00` for a blank side, thousands separators."""
    if not row:
        return 0.0
    text = row.get(column, "") or ""
    found = money(text)
    if found:
        return found[0]
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return 0.0


def open_report_from_hub(page: Page, report: str):
    """The Hub tile is CLICKED; the report's own filter bar is then driven."""
    goto_workspace(page, "CBT Hub")
    tile = page.locator(".shortcut-widget-box", has_text=report)
    expect(tile).to_be_visible(timeout=15000)
    tile.click()
    page.wait_for_url("**/desk/query-report/**", timeout=30000)
    wait_for_page_load(page)
    # The desk is an SPA: the Hub's page (its own `.page-form` included) stays
    # in the DOM beside the report's, so a scoped filter locator can land on
    # the wrong bar. The tile has done its job (the URL says so); a reload of
    # the URL it landed on — a human's F5 — leaves ONE page in the DOM.
    page.goto(page.url, wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    expect(page.locator(f"{FILTER_BAR} .frappe-control[data-fieldname='company']")).to_be_visible(timeout=20000)


def set_company_and_day(page: Page, company: str, label: str, day: str, to_day: str | None = None):
    """Company + the day window, by gesture — except that a TENANT seat never
    types its company: the report's `onload` fills it from `get_my_company`
    over an async call, and a fill typed while that call is in flight has its
    option list wiped from under it (2026-08-27, the refund lane's second
    run). The seat is asked the same question; when it is bound to a company
    the helper WAITS for the autofill — what a person does: reads that the
    filter already says their company — and types only on a platform seat."""
    resp = page.request.get("/api/method/court_booking_tech.api.company_users.get_my_company")
    mine = resp.json().get("message") if resp.ok else None
    if mine:
        assert mine == company, f"this seat is bound to {mine!r}, not {company!r}"
        page.wait_for_function(
            "(company) => window.frappe && frappe.query_report"
            " && frappe.query_report.get_filter_value('company') === company",
            arg=company,
            timeout=30000,
        )
    else:
        gestures.fill(page, "company", company, scope=FILTER_BAR, label=label)
    gestures.fill(page, "from_date", day, scope=FILTER_BAR)
    gestures.fill(page, "to_date", to_day or day, scope=FILTER_BAR)


def wait_table_text(page: Page, needle: str):
    """The report re-runs itself after each filter change; wait for the row a
    human is looking for, not for silence.

    The datatable renders only the rows in VIEW, so a needle that lives below
    the fold never reaches a rendered cell — a statement's voucher (`PST-…`)
    sorts after every `INV-…` voucher, and on the rig tenant, whose every
    run's payments are dated today, that is ~60 rows down (2026-08-27, the
    Percentage row's tenant books). The wait therefore also accepts the
    report's own data model — what the table paints from — once a table is on
    the page; `table()` then scrolls the rows into view and reads them.
    """
    page.wait_for_function(
        """(needle) => {
            const cells = Array.from(document.querySelectorAll('.datatable .dt-cell__content'));
            if (cells.some((el) => (el.innerText || '').includes(needle))) return true;
            if (!document.querySelector('.datatable')) return false;
            const data = (window.frappe && frappe.query_report && frappe.query_report.data) || [];
            return data.some((row) => row && Object.values(row).some(
                (v) => String(v == null ? '' : v).includes(needle)));
        }""",
        arg=needle,
        timeout=30000,
    )


def wait_table_settled(page: Page, interval_ms: int = 800):
    """Every filter change re-runs the report and REPLACES the datatable; a
    scroll begun on the previous render is lost with it. Wait until the table
    has stopped changing — same rendered window twice, `interval_ms` apart —
    before reading it, exactly as a person waits for the spinner to stop."""
    fingerprint = """() => {
        const rows = Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]'));
        const table = document.querySelector('.datatable');
        return JSON.stringify([table ? table.dataset.dtId || rows.length : null,
            rows.map((r) => r.dataset.rowIndex + ':' + (r.innerText || '').length)]);
    }"""
    previous = None
    for _attempt in range(25):
        current = page.evaluate(fingerprint)
        if current == previous:
            return
        previous = current
        page.wait_for_timeout(interval_ms)
    raise AssertionError("the report's table kept changing — a re-render loop?")


def visible_rows(page: Page) -> dict:
    return page.evaluate(
        """() => {
            const heads = Array.from(document.querySelectorAll('.datatable .dt-header .dt-cell__content'))
                .map((el) => (el.innerText || '').trim());
            const out = {};
            Array.from(document.querySelectorAll('.datatable .dt-row[data-row-index]')).forEach((row) => {
                const cells = Array.from(row.querySelectorAll('.dt-cell__content')).map((el) => (el.innerText || '').trim());
                const record = {_sr: cells[0] || ''};
                heads.forEach((h, i) => { if (h) record[h] = cells[i] || ''; });
                out[parseInt(row.dataset.rowIndex, 10)] = record;
            });
            return out;
        }"""
    )


def table(page: Page) -> list:
    """The rendered datatable as rows of {header: cell text} — what a human
    reads. Scrolls the body to the bottom step by step (as a person would) and
    collects the rows as they appear; rows are keyed by index so nothing is
    double-counted, and accepted only with a matching serial (see module doc)."""
    wait_table_settled(page)
    seen = {}

    def _collect():
        for key, row in visible_rows(page).items():
            index = int(key)  # JS object keys arrive as strings
            if row.get("_sr") == str(index + 1):
                seen[index] = row

    _collect()
    for _step in range(80):
        moved = page.evaluate(
            """() => {
                const body = document.querySelector('.datatable .dt-scrollable');
                if (!body) return false;
                const before = body.scrollTop;
                body.scrollTop = before + Math.floor(body.clientHeight * 0.8);
                return body.scrollTop !== before;
            }"""
        )
        if not moved:
            break
        wait_table_settled(page, interval_ms=250)
        _collect()
    wait_table_settled(page, interval_ms=250)
    _collect()
    missing = [i for i in range(max(seen) + 1) if i not in seen] if seen else []
    assert not missing, f"rows never rendered with a matching serial: {missing[:10]}"
    page.evaluate("() => { const b = document.querySelector('.datatable .dt-scrollable'); if (b) b.scrollTop = 0; }")
    return [seen[index] for index in sorted(seen)]


def ledger_rows(page: Page, company: str, label: str, day: str, books: str, needle: str) -> list:
    """Tenant Ledger from the Hub, company + one day + Books by hand, read
    off the rendered table once `needle` (an account name) is on screen."""
    open_report_from_hub(page, "CBT Tenant Ledger")
    set_company_and_day(page, company, label, day)
    gestures.select(page, "books", books, scope=FILTER_BAR)
    wait_table_text(page, needle)
    return [r for r in table(page) if r.get("Document")]


def voucher(rows: list, document: str) -> list:
    """(Account, Debit, Credit) triples of one document's lines, in order."""
    return [(r["Account"], num(r, "Debit"), num(r, "Credit")) for r in rows if r.get("Document") == document]
