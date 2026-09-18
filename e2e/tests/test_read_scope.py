"""
E2E — read-list scope: the User list and the customer picker (Backlog B38).

The user's report, verbatim (2026-09-04): *"A normal CBT company's admin or
staff user when visiting UserList it can see every account outside of company
(admins included) … Also when staff or admin books a person. The dropdown list
(if not walkin) list every user even non customer"*.

DRIVEN AS A REAL COMPANY SEAT, never Administrator: `require_company_access` is
a NO-OP under platform scope (tenancy.py), so an Administrator run would go
green even if every staff seat were refused — and the reverse, here, is what is
being proved.

⚠ A permission change is invisible to a browser holding a cached boot. The
suite's own reset re-mints `e2e/auth_state`, and the runner clears the cache;
if you drive this file by hand, `bench clear-cache` first.

Dates: no booking is created — the board is loaded only to open the quick-book
dialog, on the E2E ledger's +87 so nothing collides.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import login_as
from helpers.board import (
    BGC_BRANCH,
    load_board,
    open_cart_dialog,
    select_slots,
    slot,
    wait_dialog,
)
from helpers.navigation import goto_list

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

STELLA = "staff.ayala@example.com"  # AYALA staff
ALONA = "admin.ayala@example.com"  # AYALA admin — same company
SAMUEL = "staff.qcsm@example.com"  # the OTHER tenant's staff
PLATFORM = "cbt.admin@example.com"

AYALA_CUSTOMER = "cust.carla@example.com"  # books at AYALA in the seeds
QCSM_ONLY_CUSTOMER = "cust.milo@example.com"  # QC Smash's member, not AYALA's

BOARD_DATE = (date.today() + timedelta(days=87)).isoformat()
BGC_COURT = "AYALA-bgc-court-1"


def _picker_options(page: Page, typed: str) -> list:
    """Type into the OPEN quick-book dialog's Customer link and read what the
    awesomplete actually offers — the list a human sees, not a server payload.

    ⚠ Options are `<div role="option">` INSIDE the `<ul role="listbox">`, never
    `<li>` (helpers/gestures.py's measured DOM facts). frappe appends its own
    "Create a new …" / "Advanced Search" pseudo-entries, which are dropped here.
    """
    root = ".modal.show .frappe-control[data-fieldname='customer']"
    box = page.locator(f"{root} input").first
    box.click()
    box.fill("")
    # Gate on the SEARCH ROUND TRIP, never on options appearing: an EMPTY list
    # is the expected answer for half this file, so waiting for a row would hang
    # on exactly the case being proved.
    with page.expect_response(lambda r: "search_link" in r.url, timeout=20000):
        box.press_sequentially(typed, delay=60)
    # Awesomplete debounces ~500ms after the response lands.
    page.wait_for_timeout(1200)
    texts = page.locator(f"{root} [role='option']").all_inner_texts()
    return [
        text
        for text in texts
        if "Create a new" not in text and "Advanced Search" not in text
    ]


@pytest.mark.e2e
class TestReadScope:
    def test_the_user_list_shows_only_this_companys_own_seats(self, page: Page):
        """Ruling 3, 2026-09-05: own-company seats ONLY, and customers never."""
        login_as(page, STELLA)
        goto_list(page, "User")
        page.wait_for_selector(".list-row-container, .no-result", timeout=30000)
        page.wait_for_function(
            "() => cur_list && cur_list.data && cur_list.data.length > 0",
            timeout=30000,
        )
        body = page.locator(".result").inner_text()

        # POSITIVE first: an empty list would satisfy every absence below.
        assert STELLA in body, f"the seat cannot even see itself:\n{body}"
        assert ALONA in body, f"own company's admin seat is missing:\n{body}"
        # Then the leaks the user reported.
        assert SAMUEL not in body, f"another tenant's staff seat is listed:\n{body}"
        assert PLATFORM not in body, f"the platform seat is listed:\n{body}"
        assert AYALA_CUSTOMER not in body, f"a customer is in the User list:\n{body}"

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / "b38_user_list.png"), full_page=True)

    def test_the_booking_picker_offers_only_customers_who_dealt_with_us(
        self, page: Page
    ):
        """Rulings 1 and 2: a customer who has dealt with THIS company is
        offered; one who has only dealt with the other tenant is invisible, and
        typing their exact address does not reveal them."""
        login_as(page, STELLA)
        load_board(page, BGC_BRANCH, BOARD_DATE)
        # B46: the cell selects; the cart bar opens the dialog the picker is in.
        select_slots(page, BGC_COURT, "10:00:00", 1)
        open_cart_dialog(page)

        ours = "\n".join(_picker_options(page, AYALA_CUSTOMER))
        assert AYALA_CUSTOMER in ours, (
            f"our own customer is unpickable — the desk cannot book them: {ours!r}"
        )

        theirs = "\n".join(_picker_options(page, QCSM_ONLY_CUSTOMER))
        assert QCSM_ONLY_CUSTOMER not in theirs, (
            f"another tenant's customer base is enumerable from our desk: {theirs!r}"
        )
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "b38_customer_picker.png"), full_page=True
        )
