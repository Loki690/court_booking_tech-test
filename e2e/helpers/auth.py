"""
Multi-user login helper for Playwright E2E tests.

Supports switching between users mid-test. Caches auth state per user in
auth_state/<worker>/<email>.json — but NEVER trusts a cache on file age alone:
sessions die on bench restart / snapshot_reset (DB rollback resets tabSessions
while the cookie file stays fresh), and a dead session silently turns the test
into Guest (/desk 301-redirects to /login, cur_frm never binds). Every restore
is verified server-side via frappe.auth.get_logged_user.

Backlog B43 (user ruling 2026-09-05, *"not ever use Admin for court booking
tech"*): Administrator is refused here, at the door. The default context is the
PLATFORM seat and every other seat is opted into by name.
"""
import json
import time

from playwright.sync_api import Page

from helpers.worker_routing import auth_state_dir, base_url

DEFAULT_PASSWORD = "P@ssw0rd@123"
# The seeded PLATFORM seat (seed_test_data.PLATFORM_ADMIN_EMAIL): the CBT
# Platform Admin role and nothing else — no System Manager. Rows that assert
# platform behaviour log in as THIS seat, never ride the Administrator context
# (Administrator bypasses DocPerms, so it can prove nothing about the role).
# The `platform_seat` fixture in conftest guarantees its password on the
# worker's site first.
PLATFORM_ADMIN = "cbt.admin@example.com"
# Backlog B43: the storage state EVERY test context starts from.
PLATFORM_STATE = "platform.json"
LIVENESS_TIMEOUT = 5000  # ms — a hung probe must never stall a test 30s

# Which seat each live Page was last logged in as, so conftest's per-test guard
# can heal a dead session back to THIS page's user instead of silently
# re-seating the test. Keyed on the Page object (identity-hashed); entries are
# dropped in that fixture's teardown, so nothing accumulates across a session.
_SEATS: dict = {}


def current_seat(page: Page) -> str | None:
    return _SEATS.get(page)


def forget_seat(page: Page):
    _SEATS.pop(page, None)


def _reject_administrator(email: str, caller: str):
    """Backlog B43. An Administrator arrange can create state no real seat
    could, and the test then passes against a world that cannot exist in
    production. Refused at the call, where the offending file is named, rather
    than one test later by conftest's guard."""
    if email.strip().lower() == "administrator":
        raise AssertionError(
            f"{caller}: court_booking_tech's E2E suite never runs as "
            "Administrator (Backlog B43). Use PLATFORM_ADMIN, a company seat "
            "(staff.ayala@example.com, admin.ayala@example.com, "
            "staff.e2ef@example.com, ...) or a bench-execute helper in "
            "court_booking_tech.testing for an arrange no seat can perform."
        )


def get_logged_user(page: Page) -> str:
    """Ask the server who the current context's cookies belong to.

    Returns the user id ("Administrator", "cbt.admin@example.com", ...) or
    "Guest" on any failure (dead session, non-200, timeout).
    """
    try:
        resp = page.request.get(
            f"{base_url()}/api/method/frappe.auth.get_logged_user",
            timeout=LIVENESS_TIMEOUT,
        )
        if resp.ok:
            return resp.json().get("message", "Guest")
    except Exception:
        pass
    return "Guest"


def login_as(page: Page, email: str, password: str = DEFAULT_PASSWORD):
    """Login as a specific user, replacing the current session.

    Restores cached cookies only if the server confirms they still belong to
    `email`; otherwise falls through to a fresh form login (also verified
    before being cached).
    """
    _reject_administrator(email, "login_as")
    _SEATS[page] = email
    safe_name = email.replace("@", "_at_").replace(".", "_")
    auth_file = auth_state_dir() / f"{safe_name}.json"

    # Kill the previous user's live SPA FIRST: its in-flight XHRs carry the
    # old sid, and frappe responses re-issue session cookies — a late response
    # landing after the jar swap silently reverts the context to the previous
    # user (the verify below passes, then the overwrite happens). about:blank
    # stops the request stream before we touch the cookie jar.
    page.goto("about:blank")

    # Clear previous user's session cookies
    page.context.clear_cookies()

    # Cached path — restore cookies, then VERIFY the session is alive and ours
    if auth_file.exists():
        state = json.loads(auth_file.read_text())
        for cookie in state.get("cookies", []):
            page.context.add_cookies([cookie])
        if get_logged_user(page).lower() == email.lower():
            page.goto(f"{base_url()}/desk", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(
                'body[data-ajax-state="complete"]', timeout=30000
            )
            # Tripwire: any residual session reversion must fail HERE, at the
            # cause, not as a mysterious missing-button timeout downstream.
            actual = get_logged_user(page)
            if actual.lower() == "guest":
                actual = get_logged_user(page)  # one retry: probe timeout != reversion
            if actual.lower() != email.lower():
                raise RuntimeError(
                    f"login_as({email}): session reverted to {actual!r} after "
                    f"/desk load (url={page.url})"
                )
            return
        # Dead or foreign session — drop the cache and the cookies it loaded
        auth_file.unlink(missing_ok=True)
        page.context.clear_cookies()

    # Fresh login via browser form. Cache-busted URL: Chromium may have cached
    # a permanent 301 (/login -> /app) from an earlier authed visit in this
    # browser session, which would skip the form entirely.
    def goto_login():
        page.goto(
            f"{base_url()}/login?t={int(time.time() * 1000)}",
            wait_until="domcontentloaded",
            timeout=60000,
        )

    goto_login()
    try:
        page.wait_for_selector("#login_email", timeout=10000)
    except Exception:
        # Redirected away from the form. NEVER logout the SHARED session here —
        # since B43 that is the PLATFORM seat's (platform.json is the storage
        # state every test context starts from, and cancel_active_bookings
        # builds a third context from the same file), so a server-side logout
        # would kill that sid suite-wide.
        current = get_logged_user(page)
        if current.lower() not in (PLATFORM_ADMIN.lower(), "guest"):
            page.request.get(
                f"{base_url()}/api/method/logout", timeout=LIVENESS_TIMEOUT
            )
        page.context.clear_cookies()
        goto_login()
        page.wait_for_selector("#login_email", timeout=10000)

    page.fill("#login_email", email)
    page.fill("#login_password", password)
    page.click(".btn-login")
    page.wait_for_url("**/desk**", timeout=60000)

    # Never cache an unverified session
    actual = get_logged_user(page)
    if actual.lower() != email.lower():
        raise RuntimeError(
            f"login_as({email}) ended up logged in as {actual!r} (url={page.url})"
        )
    page.context.storage_state(path=str(auth_file))


def portal_login_as(page: Page, email: str, password: str = DEFAULT_PASSWORD):
    """Login as a portal customer (Website User) — section-8.

    Same verified-cache discipline as login_as, but customers are redirected
    to /me (never /desk), so login_as's desk-bound wait would hang on them.
    """
    _reject_administrator(email, "portal_login_as")
    _SEATS[page] = email
    safe_name = email.replace("@", "_at_").replace(".", "_")
    auth_file = auth_state_dir() / f"{safe_name}.json"

    # Same jar-swap hygiene as login_as: stop the previous SPA's request
    # stream BEFORE touching cookies (late responses re-issue session ids).
    page.goto("about:blank")
    page.context.clear_cookies()

    if auth_file.exists():
        state = json.loads(auth_file.read_text())
        for cookie in state.get("cookies", []):
            page.context.add_cookies([cookie])
        if get_logged_user(page).lower() == email.lower():
            return
        # Dead or foreign session — drop the cache and the cookies it loaded
        auth_file.unlink(missing_ok=True)
        page.context.clear_cookies()

    # Cache-busted /login (same Chromium 301-cache gotcha as login_as).
    page.goto(
        f"{base_url()}/login?t={int(time.time() * 1000)}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_selector("#login_email", timeout=10000)
    page.fill("#login_email", email)
    page.fill("#login_password", password)
    page.click(".btn-login")
    # Website Users land on /me (or a role home page) — anywhere off /login.
    page.wait_for_url(lambda url: "/login" not in url, timeout=60000)

    actual = get_logged_user(page)
    if actual.lower() != email.lower():
        raise RuntimeError(
            f"portal_login_as({email}) ended up logged in as {actual!r} (url={page.url})"
        )
    page.context.storage_state(path=str(auth_file))
