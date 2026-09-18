"""
Court Board page helpers (section-7 E2E).

Board loads are DETERMINISTIC: internal state is set first (so the field
change-handlers don't fire a racing load), then load() runs exactly once,
gated on the get_pending_payments response (the last call in the chain).
Countdown/deadline assertions always come from server payloads — never the
host clock (TZ discipline, section-1).
"""
import json
import re
import time

from playwright.sync_api import Page

from helpers import gestures
from helpers.navigation import wait_for_page_load
from helpers.worker_routing import auth_state_file, base_url

BOARD_ROUTE = "/desk/cbt-court-board"

E2EF_BRANCH = "E2EF-main"
E2EF_COURT = "E2EF-main-court-1"
E2EF_COURT_2 = "E2EF-main-court-2"  # B46: a cart needs a SECOND court
BGC_BRANCH = "AYALA-bgc"
BGC_COURTS = ["AYALA-bgc-court-1", "AYALA-bgc-court-2", "AYALA-bgc-court-3"]
CUSTOMER = "cust.carla@example.com"

# The desk seats, per company. Backlog B43 seeded the e2e-fast pair — until
# 2026-09-05 that rig had no company user at all, which is why every board row
# on it ran as Administrator. A seat may only act on ITS OWN company, so a file
# that drives two branches logs in twice, exactly as two people would.
AYALA_STAFF = "staff.ayala@example.com"
AYALA_ADMIN = "admin.ayala@example.com"
E2EF_STAFF = "staff.e2ef@example.com"
E2EF_ADMIN = "admin.e2ef@example.com"


def csrf(page: Page) -> str:
    """frappe.csrf_token needs a desk page loaded (S5 harness lesson — an
    API-only test may still sit on about:blank)."""
    if "/app" not in page.url and "/desk" not in page.url:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => window.frappe && frappe.csrf_token", timeout=15000)
    return page.evaluate("frappe.csrf_token")


def platform_api(playwright):
    """Verified PLATFORM-seat APIRequestContext for fixtures and cleanup.

    Backlog B43: this was `admin_api` and rode an Administrator cookie jar. The
    reason it exists is unchanged — a fixture must never depend on a test user's
    silently-filtered list results — and the platform seat satisfies it without
    a superuser: `tenancy.get_session_company()` returns None for CBT Platform
    Admin, which means ALL companies, so nothing is filtered out from under it.
    """
    from helpers.auth import PLATFORM_ADMIN, PLATFORM_STATE

    api = playwright.request.new_context(
        base_url=base_url(), storage_state=str(auth_state_file(PLATFORM_STATE))
    )
    resp = api.get("/api/method/frappe.auth.get_logged_user", timeout=5000)
    assert resp.ok and resp.json().get("message") == PLATFORM_ADMIN, (
        f"{PLATFORM_STATE} session is not a live {PLATFORM_ADMIN} session"
    )
    return api


def api_csrf(api) -> str:
    """CSRF token for an APIRequestContext (cookie-authed writes are
    CSRF-checked; the token ships in the desk boot HTML)."""
    html = api.get("/app", timeout=15000).text()
    match = re.search(r'frappe\.csrf_token\s*=\s*"([^"]+)"', html)
    assert match, "csrf token not found in /app boot HTML"
    return match.group(1)


def goto_board(page: Page):
    page.goto(BOARD_ROUTE, wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    # Wait past bootstrap_branch's async get_list — overriding the branch
    # while bootstrap is still in flight would let its load land last and
    # render the wrong branch (ducky concern 1).
    page.wait_for_function(
        """() => {
            const wrapper = frappe.pages['cbt-court-board'];
            return wrapper && wrapper.court_board && wrapper.court_board._bootstrapped;
        }""",
        timeout=15000,
    )


def load_board(page: Page, branch: str, date: str):
    """Point the board at branch × date and wait for THAT payload to render.

    Gated on payload identity (board.branch/date + pending panel), never on
    "any get_pending_payments response" — the bootstrap load answers that URL
    too and would end the wait early."""
    goto_board(page)
    page.evaluate(
        """([branch, date]) => {
            const board = frappe.pages['cbt-court-board'].court_board;
            return board.show(branch, date);  // guarded sets + exactly one load
        }""",
        [branch, date],
    )
    page.wait_for_function(
        """([branch, date]) => {
            const board = frappe.pages['cbt-court-board'].court_board;
            return board.board && board.board.branch === branch
                && board.board.date === date
                && board.pending && board.pending.company === board.board.company;
        }""",
        arg=[branch, date],
        timeout=30000,
    )
    # B33: the board is a time x court MATRIX now, not a stack of cards.
    page.wait_for_selector(".cbt-matrix, .cbt-board-empty", timeout=15000)


def slot(page: Page, court: str, start_time: str):
    return page.locator(f".cbt-slot[data-court='{court}'][data-start='{start_time}']")


def wait_dialog(page: Page):
    """Wait until cur_dialog points at a VISIBLE modal.

    Both halves of this check are load-bearing (dialog.js):
    - `cur_dialog` binds on **shown**.bs.modal, i.e. AFTER the fade-in, while
      Bootstrap adds `.show` at the START of it. So a DOM wait on `.modal.show`
      can return while cur_dialog is still the PREVIOUS dialog — or null.
    - on **hidden**.bs.modal cur_dialog is popped off frappe.ui.open_dialogs and
      set to `null` when the stack empties (dialog.js:112-119). It can therefore
      be null OR stale, which is why `!!window.cur_dialog` alone is never enough.
    """
    page.wait_for_function(
        "() => window.cur_dialog && cur_dialog.$wrapper && cur_dialog.$wrapper.hasClass('show')",
        timeout=15000,
    )


def wait_block_grid(page: Page, want: str | None = None, field: str = "start_time"):
    """Wait until the Block Slots dialog's grid-time dropdowns are populated.

    Section-21 (Backlog B12) turned that dialog's From/To from Time widgets into
    Selects whose options come from a `slots.get_availability` round trip. A
    `set_value` fired before those options exist is silently dropped by the
    `<select>` — the dialog then submits nothing (or, worse, whatever
    `add_options` left selected) and the test fails somewhere far from the cause.

    Both conditions are load-bearing:

      * `_grid_settled` — the dialog's own flag, the `_quote_settled` idiom from
        cbt_reschedule.js: false while a fetch is in flight, true once the
        options have rendered. It is set on the dialog's FAILURE path too, so a
        dead endpoint fails an assertion here rather than burning the timeout.
      * `want` is actually IN `df.options` — which turns "that time is not on
        this branch's grid" into a clear failure instead of a silent set_value
        no-op. Pass `field="end_time"` after choosing a From: To is re-filtered
        to the ends after it, so its option list is narrower than the grid's.

    `window.cur_dialog &&` is not optional — dialog.js nulls cur_dialog on
    hidden.bs.modal, so a predicate that assumes it exists THROWS out of the wait
    instead of retrying (S13 as-built 16).
    """
    page.wait_for_function(
        """([want, field]) => {
            if (!window.cur_dialog || cur_dialog._grid_settled !== true) return false;
            if (!want) return true;
            const control = cur_dialog.fields_dict && cur_dialog.fields_dict[field];
            if (!control) return false;
            return (control.df.options || []).some(
                (o) => (o && o.value !== undefined ? o.value : o) === want
            );
        }""",
        arg=[want, field],
        timeout=30000,
    )


def block_option_values(page: Page, field: str = "start_time") -> list:
    """The Block dialog's offered times for `field`, minus the blank prompt.

    The blank `{value: ""}` first option is the dialog's own (it stops
    ControlSelect's `selectedIndex = 0` from silently electing an hour nobody
    picked), so it is not part of "the grid's times" and every assertion about
    them drops it here rather than restating that everywhere.
    """
    return page.evaluate(
        """(field) => (cur_dialog.fields_dict[field].df.options || [])
            .map((o) => (o && o.value !== undefined ? o.value : o))
            .filter((v) => v)""",
        field,
    )


def slot_starts(page: Page, court: str) -> list:
    """This court's rendered start times, in ROW order.

    Backlog B46: "book two consecutive hours" is now expressed by tapping two
    cells, so a helper has to know which cell comes next. Read from the DOM
    rather than computed from a slot duration — a branch with a buffer between
    slots does not step by its duration, and the grid is the only honest source.
    """
    return page.evaluate(
        """(court) => Array.from(
            document.querySelectorAll(".cbt-slot[data-court='" + court + "']")
        ).map((el) => el.dataset.start)""",
        court,
    )


def select_slots(page: Page, court: str, start_time: str, count: int = 1) -> list:
    """Tap `count` consecutive cells on `court`, starting at `start_time`.

    THE GESTURE, not a model write: this is what a person at the desk does, and
    it is what proves the cells are reachable and toggleable at all. Waits on
    `aria-pressed`, which is the cell's own record of being in the cart — the
    cart bar's count is derived from the same state and would be a weaker gate.
    """
    starts = slot_starts(page, court)
    assert start_time in starts, (
        f"{court} does not render a cell at {start_time}: {starts[:8]}…"
    )
    index = starts.index(start_time)
    wanted = starts[index : index + count]
    assert len(wanted) == count, (
        f"{court} has only {len(wanted)} cell(s) from {start_time}, needed {count}"
    )
    # Nothing may be lying over the board — a dialog closed a moment ago is
    # still fading, and its backdrop eats the tap.
    wait_page_uncovered(page)
    # ⚠ RELATIVE to what is already picked. A cart spans courts and dates, so a
    # second call adds to the first — waiting for an absolute `count` here would
    # hang forever on the second court, which is exactly how this helper failed
    # the first time it was used for a real cart.
    #
    # ⚠ AND IDEMPOTENT. The cell is a TOGGLE, so re-selecting an already-picked
    # hour would UNPICK it — which is what a row does when it opens the dialog,
    # measures it, closes it and then books the same slots for real.
    before = cart_count(page)
    added = 0
    for start in wanted:
        already = slot(page, court, start).get_attribute("aria-pressed") == "true"
        if already:
            continue
        slot(page, court, start).click()
        added += 1
    page.wait_for_function(
        """(n) => document.querySelectorAll(
            ".cbt-slot[aria-pressed='true']"
        ).length === n""",
        arg=before + added,
        timeout=15000,
    )
    return wanted


def open_cart_dialog(page: Page):
    """Tap Book on the cart bar and wait for the dialog to be built AND priced.

    The bar is the desk's twin of the portal's #cbt-checkoutbar (B46). Gating on
    `_quote_settled` as well as the modal is what makes the money assertions
    that follow read the NEWEST server answer rather than "Pricing…".
    """
    page.locator("[data-testid='cart-book']").click()
    wait_dialog(page)
    wait_money_settled(page)


def cart_count(page: Page) -> int:
    """How many cells the board currently holds in its cart."""
    return page.locator(".cbt-slot[aria-pressed='true']").count()


def quick_book(
    page: Page,
    court: str,
    start_time: str,
    payment_method: str,
    number_of_slots: int = 1,
    expect_ok: bool = True,
    customer: str = CUSTOMER,
):
    """Select the slot(s), fill the quick-book dialog, submit.

    Returns the FIRST new booking's name when expect_ok; otherwise asserts the
    server rejected the call and returns the error-modal text.

    ⚠ Backlog B46 changed the GESTURE and kept the signature. `number_of_slots`
    used to be typed into an Int field; it is now expressed the way a person
    expresses it — by tapping that many consecutive cells — and the SERVER
    merges the contiguous run back into one booking with that
    `number_of_slots`, so the document this produces is unchanged. Six files
    call this and none of them had to move.
    """
    select_slots(page, court, start_time, number_of_slots)
    open_cart_dialog(page)
    # Await the set_value promise chain — Link/Select syncing is async and the
    # primary click must not race it. Setting `customer` also fires the
    # section-11 membership lookup, which writes discount_percent; the wait
    # below lets that settle so the dialog submits the price staff can see.
    # Driven by GESTURE — typed into the Customer Link and its option clicked, the
    # Select chosen, the count typed. The membership lookup that setting `customer`
    # fires is still awaited by the two gates below, exactly as before.
    gestures.fill(page, "customer", customer, scope=gestures.DIALOG)
    gestures.fill(page, "payment_method", payment_method, scope=gestures.DIALOG)
    # The membership hint renders (or explicitly says "no membership") before
    # we submit — gating on it means the discount field is settled, without a
    # sleep. Scoped to the VISIBLE modal: closed frappe dialogs stay in the DOM
    # (S12 walkthrough lesson), so a bare selector would match a PREVIOUS
    # dialog's hint and return instantly — a wait that silently does nothing.
    page.wait_for_selector(
        ".modal.show [data-testid='member-hint']", timeout=15000
    )
    wait_money_settled(page)
    return _submit_quick_book(page, expect_ok)


def quick_book_walkin(
    page: Page,
    court: str,
    start_time: str,
    payment_method: str,
    customer_name: str,
    customer_phone: str | None = None,
    number_of_slots: int = 1,
    expect_ok: bool = True,
):
    """Section-13: book the slot as a WALK-IN — no account, free-text name.

    A separate entry point rather than a branch through quick_book: that helper
    unconditionally sets the `customer` Link, and threading a "skip the Link"
    flag through a function four green files depend on is how you destabilise
    them. Both share _submit_quick_book, which is the fiddly part.

    ⚠ B46: a WALK-IN may hold a multi-row cart. `booking_group` and the
    one-invoice rule need no account (billing._billable_rows groups on the stamp
    alone) and "three courts for the tournament, cash" is the commonest desk
    cart there is. The only account-bound piece is store credit, and
    credits.take_credit refuses it for a walk-in by construction.
    """
    select_slots(page, court, start_time, number_of_slots)
    open_cart_dialog(page)
    # walk_in FIRST: it drives depends_on, and the name/phone controls do not
    # exist in the layout until the Check flips.
    # CLICKING the Check is the whole point of this conversion. The previous model
    # write set the flag without ever proving the controls it reveals are reachable —
    # which is exactly how a form nobody can operate stays green. Filling
    # `customer_name` by gesture now fails loudly if the depends_on stops rendering it.
    gestures.check(page, "walk_in", True, scope=gestures.DIALOG)
    gestures.fill(page, "customer_name", customer_name, scope=gestures.DIALOG)
    if customer_phone:
        gestures.fill(page, "customer_phone", customer_phone, scope=gestures.DIALOG)
    gestures.fill(page, "payment_method", payment_method, scope=gestures.DIALOG)
    # Same gate as the account path — walk-in mode renders "Walk-in — no
    # membership." into the SAME node rather than leaving it empty, precisely
    # so this wait still means "the money area has settled".
    page.wait_for_selector(
        ".modal.show [data-testid='member-hint']", timeout=15000
    )
    wait_money_settled(page)
    return _submit_quick_book(page, expect_ok)


def wait_channels_settled(page: Page):
    """Wait until a dialog's "Paid via" / "Received via" Select holds the
    NEWEST channel list (Backlog B29).

    `_channels_settled` is the dialog's own flag, the `_quote_settled` idiom:
    false the moment a payment-method change starts the fetch, true once the
    options have rendered — set on the failure path too, so a dead endpoint
    fails here instead of burning the timeout. Gating on a
    list_company_channels RESPONSE would race the synchronous flag flip.
    """
    page.wait_for_function(
        "() => window.cur_dialog && cur_dialog._channels_settled === true",
        timeout=30000,
    )


def channel_options(page: Page) -> list:
    """The labels the OPEN dialog's channel Select offers, in order."""
    return page.evaluate(
        """() => (cur_dialog.fields_dict.payment_channel.df.options || [])
            .map((o) => (o && o.label !== undefined ? o.label : o))"""
    )


def wait_money_settled(page: Page):
    """Wait until the quick-book dialog's TOTAL is the newest server answer.

    Section-18 (Backlog B4). The member-hint gate above used to be the whole
    story, because the dialog applied the discount in JS and repainted
    SYNCHRONOUSLY on the field change. It no longer does: the total now comes
    from `api.portal.get_quote`, and the hint is rendered in the same `.then`
    that STARTS that round trip — so the hint appearing no longer means the
    money has settled. Without this line the comments above would be quietly
    false for six files, which is worse than a slow test.

    `_quote_settled` is the dialog's own flag (the cbt_reschedule.js idiom):
    false while any pricing round-trip is in flight, true once the newest one
    has rendered. Gating on a get_quote RESPONSE instead would hang whenever
    set_value lands on the value the field already holds and fires no change.

    `window.cur_dialog &&` is not optional — dialog.js nulls cur_dialog on
    hidden.bs.modal and rebinds on shown.bs.modal, so a predicate that assumes
    it exists THROWS out of the wait instead of retrying (S13 as-built 16).
    """
    page.wait_for_function(
        "() => window.cur_dialog && cur_dialog._quote_settled === true",
        timeout=30000,
    )


def wait_page_uncovered(page: Page, timeout: int = 15000):
    """Wait until nothing is lying on top of the page any more.

    ⚠ NOT "the modal has .show removed". Bootstrap strips `.show` from the modal
    at the START of the fade and removes the BACKDROP at the end of it, so a row
    that clicks a board cell straight after a book meets
    "<div class='modal-backdrop fade show'> intercepts pointer events" and burns
    its entire click timeout on a dialog that is already logically gone. That is
    a real failure (`test_extend_confirmed_session`, 2026-09-05), not a flake.

    ⚠ AND NOT "there are zero .modal-backdrop nodes" either — the first fix
    tried that and hung on a page carrying TWO of them, one of which never
    leaves. MEASURED on 2026-09-05: after a desk booking the live backdrop is
    gone within 500 ms and the board is clickable again, so what is asserted is
    the thing that actually matters — a hit test at the middle of the viewport,
    the same `elementFromPoint` technique B42 used on the total.
    """
    # Polled by hand rather than through wait_for_function, ONLY so a timeout
    # can NAME what is still covering the page: "Timeout 15000ms exceeded" on a
    # predicate is the least useful failure message in this suite.
    probe = """() => Array.from(
        document.querySelectorAll('.modal, .modal-backdrop')
    ).filter((node) => {
        const style = getComputedStyle(node);
        // A CLOSED frappe dialog stays in the DOM at display:none and covers
        // nothing — that is the documented trap, and it is what makes `display`
        // the honest test. Anything still DISPLAYED is over the page, whatever
        // its opacity: mid-fade a modal is display:block at opacity 0 and eats
        // every click underneath it.
        return style.display !== 'none' && style.visibility !== 'hidden';
    }).map((node) => node.className + ' [display=' + getComputedStyle(node).display
        + ' opacity=' + getComputedStyle(node).opacity + '] '
        + (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200))"""

    deadline = time.time() + timeout / 1000.0
    covering = page.evaluate(probe)
    while covering and time.time() < deadline:
        page.wait_for_timeout(100)
        covering = page.evaluate(probe)
    assert not covering, (
        "a modal or backdrop is still over the page after the dialog closed, so "
        f"the next click will land on it instead of the board: {covering}"
    )


def cart_result(page: Page, resp) -> dict:
    """The whole create_desk_cart payload — group, rows, totals (B46)."""
    return resp.json()["message"]


def _submit_quick_book(page: Page, expect_ok: bool = True):
    """Click Book, await the server, and settle the modals. Shared by both
    quick-book entry points.

    ⚠ B46: the endpoint is `create_desk_cart` now, and the payload is a CART —
    `{booking_group, bookings: [...], count, total_amount}`. This still returns
    the first booking's NAME so the six calling files read unchanged; a row that
    wants the group asks for it through `_submit_cart` below.
    """
    return _submit_cart(page, expect_ok)["name"]


def _submit_cart(page: Page, expect_ok: bool = True) -> dict:
    """The full result: {"name": <first booking>, "payload": <create_desk_cart>}
    on success, or {"name": <error text>, "payload": None} on a refusal."""
    with page.expect_response(
        lambda r: "create_desk_cart" in r.url, timeout=30000
    ) as resp_info:
        page.evaluate("() => cur_dialog.get_primary_btn().click()")
    resp = resp_info.value

    if expect_ok:
        assert resp.ok, f"create_desk_cart: HTTP {resp.status} {resp.text()}"
        # Successful book closes the dialog and reloads the board.
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        wait_page_uncovered(page)
        payload = resp.json()["message"]
        return {"name": payload["bookings"][0]["name"], "payload": payload}

    assert not resp.ok, f"create_desk_cart unexpectedly succeeded: {resp.text()}"
    # frappe.call surfaces the server message as a msgprint modal (on top of
    # the still-open quick-book dialog). Wait until it has FINISHED its fade —
    # Bootstrap silently ignores hide() during the show transition, leaving
    # the modal stuck with .show. (cur_dialog binds on shown.bs.modal, after
    # the fade. Assumes a single msgprint in flight — true here: fresh page
    # per test, one rejected call.)
    page.wait_for_function(
        """() => window.cur_dialog && frappe.msg_dialog === cur_dialog
            && cur_dialog.$wrapper.hasClass('show')""",
        timeout=15000,
    )
    text = page.locator(".modal.show .msgprint").first.inner_text()
    page.evaluate(
        """() => {
            frappe.msg_dialog.hide();
            $('.modal.show').modal('hide');
        }"""
    )
    page.wait_for_selector(".modal.show", state="detached", timeout=15000)
    wait_page_uncovered(page)
    # B46: the refusal is all-or-nothing (create_desk_cart is one transaction),
    # and the CART SURVIVES it on screen — the operator fixes one slot and books
    # again rather than re-picking the other eleven.
    return {"name": text, "payload": None}


def wait_reschedule_dialog(page: Page):
    """Wait until the section-15 reschedule dialog is built AND priced.

    ONE atomic wait_for_function gating on `cur_dialog` plus its CONTENT, never
    on `.modal.show` (S13 as-built 16): this dialog is opened FROM another
    dialog, and dialog.js nulls cur_dialog on hidden.bs.modal, rebinding it only
    on shown.bs.modal — which fires AFTER the fade, while Bootstrap adds `.show`
    at the START of it. A DOM-only wait returns mid-fade with cur_dialog null
    and the next evaluate dies on "Cannot read properties of null".

    `_quote_settled` is the dialog's own flag: false while any pricing
    round-trip is in flight, true once the newest one has rendered. Gating on a
    get_quote RESPONSE instead would hang whenever set_value lands on the value
    the field already holds and fires no change event.
    """
    page.wait_for_function(
        """() => window.cur_dialog
            && cur_dialog.$wrapper && cur_dialog.$wrapper.hasClass('show')
            && cur_dialog.fields_dict && cur_dialog.fields_dict.start_time
            && cur_dialog._quote_settled === true
            && cur_dialog.fields_dict.estimate.$wrapper
                .find("[data-testid='reschedule-total']").length > 0""",
        timeout=30000,
    )


def open_reschedule(page: Page, court: str, start_time: str):
    """Click a booked slot, then its Reschedule action, and wait for the
    reschedule dialog. Works from BOTH board dialogs (Reserved opens the
    verification dialog, Confirmed the details dialog — both carry the button).
    """
    slot(page, court, start_time).click()
    wait_dialog(page)
    page.locator(".modal.show [data-action='reschedule']").click()
    wait_reschedule_dialog(page)


def set_reschedule_target(
    page: Page,
    court: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    slots: int | None = None,
):
    """Point the OPEN reschedule dialog at a new court/date/time/length."""
    if court or date:
        if court:
            gestures.fill(page, "court", court, scope=gestures.DIALOG)
        if date:
            gestures.fill(page, "booking_date", date, scope=gestures.DIALOG)
        wait_reschedule_dialog(page)
    if start_time:
        # A court/date change refetches that day's grid, so the option has to
        # EXIST before it can be selected — asserting on it here turns "the
        # slot was not offered" into a clear failure instead of a silent
        # set_value no-op that submits the original time.
        page.wait_for_function(
            """(want) => window.cur_dialog && cur_dialog.fields_dict
                && cur_dialog.fields_dict.start_time
                && (cur_dialog.fields_dict.start_time.df.options || []).some(
                    (o) => (o && o.value !== undefined ? o.value : o) === want
                )""",
            arg=start_time,
            timeout=30000,
        )
    if start_time:
        gestures.fill(page, "start_time", start_time, scope=gestures.DIALOG)
    if slots:
        gestures.fill(page, "number_of_slots", slots, scope=gestures.DIALOG)
    wait_reschedule_dialog(page)


def reschedule_total(page: Page) -> str:
    return page.locator(".modal.show [data-testid='reschedule-total']").inner_text()


def submit_reschedule(page: Page, expect_ok: bool = True):
    """Click Reschedule and settle the modals.

    Returns the API result dict when expect_ok, else the error-modal text.
    """
    with page.expect_response(
        lambda r: "reschedule_booking" in r.url, timeout=30000
    ) as resp_info:
        page.evaluate("() => cur_dialog.get_primary_btn().click()")
    resp = resp_info.value

    if expect_ok:
        assert resp.ok, f"reschedule_booking: HTTP {resp.status} {resp.text()}"
        page.wait_for_selector(".modal.show", state="detached", timeout=15000)
        wait_page_uncovered(page)
        return resp.json()["message"]

    assert not resp.ok, f"reschedule_booking unexpectedly succeeded: {resp.text()}"
    # frappe.call surfaces the server message as a msgprint modal ON TOP of the
    # still-open reschedule dialog. Wait until it has FINISHED its fade —
    # Bootstrap silently ignores hide() during the show transition.
    page.wait_for_function(
        """() => window.cur_dialog && frappe.msg_dialog === cur_dialog
            && cur_dialog.$wrapper.hasClass('show')""",
        timeout=15000,
    )
    text = page.locator(".modal.show .msgprint").first.inner_text()
    page.evaluate(
        """() => {
            frappe.msg_dialog.hide();
            $('.modal.show').modal('hide');
        }"""
    )
    page.wait_for_selector(".modal.show", state="detached", timeout=15000)
    wait_page_uncovered(page)
    return text


def book_slot_via_api(page: Page, court: str, booking_date: str, start_time: str):
    """Create a Cash booking straight through the API from THIS page's session.

    Used to occupy a slot a dialog is already holding open — a separate HTTP
    request is a separate transaction, which is what makes it an honest race
    against the dialog's stale option list. CSRF must be passed explicitly:
    page.request does not add the header the way frappe.call does.
    """
    resp = page.request.post(
        "/api/method/court_booking_tech.api.bookings.create_booking",
        headers={"X-Frappe-CSRF-Token": csrf(page)},
        form={
            "court": court,
            "booking_date": booking_date,
            "start_time": start_time,
            "payment_method": "Cash",
            "customer": CUSTOMER,
            "number_of_slots": 1,
        },
    )
    assert resp.ok, f"create_booking: HTTP {resp.status} {resp.text()}"
    return resp.json()["message"]["name"]


ACTIVE_STATUSES = ["Reserved", "Confirmed", "Extended"]
# Section-16: a released no-show holds NO slot, so it is not "active" — but it
# is still a row this court/date will carry into the next run, and it still
# shows up under the board's No-shows chip. File 14 passes this list; every
# other caller keeps the default and is byte-identical to before.
ACTIVE_OR_RELEASED_STATUSES = ACTIVE_STATUSES + ["No Show"]


CLEANUP_REASON = "E2E cleanup"


def cancel_active_bookings(
    page: Page,
    court: str,
    booking_date: str,
    statuses: list | None = None,
    to_date: str | None = None,
):
    """Re-runnability without a reset: clear ALL active bookings on the
    court × date (any start time — extensions included); `to_date` widens it
    to a date range.

    Section-26: cancelling a PAID booking is a refund — Company Admin only,
    with a reason. Cleanup therefore runs through its own context built from
    platform.json, whatever seat `page` is on (a staff-seat test must not 403 in
    its own teardown), and always sends the reason.

    Backlog B43: that context was Administrator's until 2026-09-05. The platform
    seat clears the same gate for a real reason, not a bypass —
    `tenancy.is_company_admin()` returns True for platform scope
    (tenancy.py:91-99), which is what `billing.require_refund` checks.
    """
    date_filter = (
        ["booking_date", "between", [booking_date, to_date]]
        if to_date
        else ["booking_date", "=", booking_date]
    )
    filters = json.dumps(
        [
            ["court", "=", court],
            date_filter,
            ["booking_status", "in", statuses or ACTIVE_STATUSES],
        ]
    )
    from helpers.auth import PLATFORM_STATE

    admin = page.context.browser.new_context(
        base_url=base_url(),
        ignore_https_errors=True,
        storage_state=str(auth_state_file(PLATFORM_STATE)),
    )
    try:
        resp = admin.request.get(
            "/api/resource/CBT Court Booking",
            params={"filters": filters, "limit_page_length": 0},
        )
        assert resp.ok, f"slot probe: HTTP {resp.status} {resp.text()}"
        rows = resp.json()["data"]
        if not rows:
            return
        token = api_csrf(admin.request)
        for row in rows:
            cancel = admin.request.post(
                "/api/method/court_booking_tech.api.bookings.cancel_booking",
                headers={"X-Frappe-CSRF-Token": token},
                form={"name": row["name"], "reason": CLEANUP_REASON},
            )
            assert cancel.ok, f"cancel {row['name']}: HTTP {cancel.status} {cancel.text()}"
    finally:
        admin.close()


def goto_pending_panel(page: Page):
    """Load the board and wait for its company-wide Pending Payments panel to
    finish rendering (the panel is date-independent — every Reserved booking of
    the company across dates). Used by section-9 file 05 for the staff-verify
    step of a PORTAL-created hold."""
    goto_board(page)
    page.wait_for_function(
        """() => {
            const board = frappe.pages['cbt-court-board'].court_board;
            return board && board.pending && Array.isArray(board.pending.items);
        }""",
        timeout=30000,
    )


def pending_item(page: Page, booking: str):
    return page.locator(f".cbt-pending-item[data-booking='{booking}']")


def accept_from_pending_panel(page: Page, booking: str):
    """Click a pending item's Accept, confirm the frappe.confirm, and wait for
    the server flip. accept_booking -> frappe.confirm -> confirm_booking, which
    also accepts any pending proofs (S5 as-built 4)."""
    # The panel may need a moment to surface a just-created portal hold.
    page.wait_for_selector(
        f".cbt-pending-item[data-booking='{booking}']", timeout=20000
    )
    page.locator(
        f".cbt-pending-item[data-booking='{booking}'] [data-panel-action='accept']"
    ).click()
    # frappe.confirm renders a modal with a primary "Yes".
    page.wait_for_function(
        "() => window.cur_dialog && cur_dialog.$wrapper && cur_dialog.$wrapper.hasClass('show')",
        timeout=15000,
    )
    with page.expect_response(
        lambda r: "confirm_booking" in r.url or "accept_proofs" in r.url, timeout=30000
    ) as resp_info:
        page.evaluate("() => cur_dialog.get_primary_btn().click()")
    resp = resp_info.value
    assert resp.ok, f"confirm from panel: HTTP {resp.status} {resp.text()}"


def clear_blocks(api, branch: str, block_date: str):
    """Delete this branch × date's slot blocks via the caller's api context
    (deleting = unblocking, S4 as-built 11 — but cleanup stays fixture-side)."""
    token = api_csrf(api)
    filters = json.dumps([["branch", "=", branch], ["block_date", "=", block_date]])
    resp = api.get("/api/resource/CBT Slot Block", params={"filters": filters})
    assert resp.ok, f"block probe: HTTP {resp.status}"
    for row in resp.json()["data"]:
        deleted = api.delete(
            f"/api/resource/CBT Slot Block/{row['name']}",
            headers={"X-Frappe-CSRF-Token": token},
        )
        assert deleted.ok, f"delete block {row['name']}: HTTP {deleted.status}"
