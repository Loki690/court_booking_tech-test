"""
Playwright E2E test fixtures for court_booking_tech.

Runs on WINDOWS HOST (Git Bash). Each xdist worker hits its OWN site via
helpers.worker_routing (gw0 -> localhost:8004, gw1 -> :8005, gw2 -> :8003).
Requires the site-pinned dev servers (shared/scripts/start_multisite.sh) or
`bench start` for a serial single-site run.

Session trust model: cached auth state (auth_state/*.json) is NEVER trusted on
file age — snapshot_reset / bench restarts kill server-side sessions while the
files stay fresh, silently downgrading tests to Guest. Every restore is
verified against /api/method/frappe.auth.get_logged_user.

SEAT MODEL (Backlog B43, user ruling 2026-09-05: *"not ever use Admin for court
booking tech. we do not need it as admin!"*). Every test context starts as the
seeded PLATFORM seat, `cbt.admin@example.com` — CBT Platform Admin and nothing
else. Administrator is not a fallback here, it is refused: `helpers.auth`
rejects it by name, `helpers.worker_routing.auth_state_file` refuses to hand out
an `admin.json`, and `verify_session` below fails any desk page that somehow
ends up on it. Arranges no product seat may perform (deleting a CBT Platform
Statement, voiding a CBT Customer Credit) go through `bench execute` helpers in
`court_booking_tech.testing` — those run as the system and are not a seat.
"""
import time

import pytest
from playwright.sync_api import Page, Playwright

from helpers.worker_routing import (
    auth_state_file,
    base_url as _base_url,
    bench_execute,
    drop_banned_states,
)

LIVENESS_TIMEOUT = 5000  # ms — liveness probes must never stall 30s

EXPECTED_TIMEZONE = "Asia/Manila"  # PLAN §8i — the product runs on PH time


@pytest.fixture(scope="session")
def base_url():
    return _base_url()


@pytest.fixture(scope="session", autouse=True)
def bench_health():
    """Fail fast (and clearly) if the bench isn't up and stable.

    Requires 3 consecutive pongs 1s apart: the werkzeug watcher restarts the
    dev server ~15s after ANY file edit under apps/ (including e2e test
    files) — a single successful ping can land mid-restart.
    """
    import requests as req

    deadline = time.time() + 60
    consecutive = 0
    last_error = "no attempt made"
    while time.time() < deadline:
        try:
            resp = req.get(f"{_base_url()}/api/method/ping", timeout=5)
            if resp.status_code == 200 and resp.json().get("message") == "pong":
                consecutive += 1
                if consecutive >= 3:
                    return
            else:
                consecutive = 0
                last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            consecutive = 0
            last_error = repr(exc)
        time.sleep(1)
    pytest.exit(
        f"Bench at {_base_url()} not healthy: {last_error}. Are the site-pinned "
        "servers (start_multisite) running in Docker? Note: editing any file "
        "under apps/ (including e2e/) triggers a ~15s dev-server restart — "
        "wait it out and retry.",
        returncode=1,
    )


@pytest.fixture(scope="session", autouse=True)
def real_clock(bench_health):
    """Guarantee this worker's site starts on the REAL clock (section-17).

    The test-clock offset lives in redis, and nothing here restores redis for
    us: `snapshot_reset` rebuilds the DATABASE, and a hard-killed run (Ctrl-C, a
    dead container, a killed pytest) never reaches the module teardown that
    would have cleared it. A leaked offset would make every time-dependent
    assertion on this worker lie about when it ran — the exact class of defect
    section-17 exists to remove — so the suite clears it once, up front, before
    any test runs.

    Defensive only. The file that pins the clock still clears it in its own
    teardown, and the key itself carries a TTL; this is the last line of
    defence, not the first.
    """
    # check=False so the assert is REACHABLE: bench_execute defaults to
    # check=True, and the CalledProcessError it raises carries the exit status
    # but not stderr — which would leave the fixture gating all 83 tests failing
    # with a one-line opaque error.
    result = bench_execute(
        "court_booking_tech.testing.set_test_clock_offset", "[0]", check=False
    )
    assert result.returncode == 0, (
        "could not clear the test clock offset on this worker's site — refusing "
        f"to run a time-dependent suite on an unknown clock: {result.stderr}"
    )


@pytest.fixture(scope="session")
def platform_seat(bench_health) -> str:
    """The seeded PLATFORM seat, guaranteed able to log in on THIS worker's site.

    `cbt.admin@example.com` carries the CBT Platform Admin role and nothing
    else (no System Manager). Rows that assert platform behaviour log in as it
    instead of riding the Administrator context every test starts from —
    Administrator bypasses DocPerms, so it can prove nothing about the role
    (user ruling, 2026-08-28; section-27).

    The seed sets this password on every run (seed_test_data._seed_platform_admin),
    and snapshot_reset's STEP 7 re-seeds dev.localhost from the working tree —
    but the cloned worker sites (dev2/dev3, multisite_reset) are restored from
    the snapshot WITHOUT that step, so on a worker the seat may be password-
    less. Like `real_clock`, the lane guarantees its own precondition instead
    of trusting which site it landed on. Idempotent, one `bench execute` per
    worker session, commits.
    """
    import json

    from helpers.auth import DEFAULT_PASSWORD, PLATFORM_ADMIN

    result = bench_execute(
        "frappe.utils.password.update_password",
        json.dumps([PLATFORM_ADMIN, DEFAULT_PASSWORD]),
        check=False,
    )
    assert result.returncode == 0, (
        f"could not set the platform seat's password on {_base_url()}: {result.stderr}"
    )
    return PLATFORM_ADMIN


def _platform_session_alive(playwright: Playwright) -> bool:
    """True if platform.json's cookies still belong to a live platform session."""
    from helpers.auth import PLATFORM_ADMIN, PLATFORM_STATE

    auth_file = auth_state_file(PLATFORM_STATE)
    if not auth_file.exists():
        return False
    api = playwright.request.new_context(
        base_url=_base_url(), storage_state=str(auth_file)
    )
    try:
        resp = api.get(
            "/api/method/frappe.auth.get_logged_user", timeout=LIVENESS_TIMEOUT
        )
        return resp.ok and resp.json().get("message") == PLATFORM_ADMIN
    except Exception:
        return False
    finally:
        api.dispose()


@pytest.fixture(scope="session", autouse=True)
def platform_auth(playwright: Playwright, base_url, platform_seat):
    """Ensure platform.json holds a VERIFIED live session for the PLATFORM seat.

    Backlog B43: this replaced `admin_auth` and, with it, the last Administrator
    storage state in the suite. Depends on `platform_seat` (not merely
    bench_health) because that fixture is what guarantees the seat's password on
    THIS worker's site — a cloned worker site is restored from a snapshot
    without the seeding step.
    """
    from helpers.auth import DEFAULT_PASSWORD, PLATFORM_ADMIN, PLATFORM_STATE

    # Any admin.json this worker carries from an older run still holds a LIVE
    # session — delete it rather than leave a loaded gun on disk.
    for path in drop_banned_states():
        print(f"B43: dropped stale Administrator state {path}")

    if _platform_session_alive(playwright):
        return

    # Fresh form login (cache-busted /login: Chromium can cache a permanent
    # 301 /login -> /app from an earlier authed visit)
    browser = playwright.chromium.launch()
    context = browser.new_context(
        viewport={"width": 1400, "height": 960},
        ignore_https_errors=True,
    )
    page = context.new_page()
    page.goto(
        f"{base_url}/login?t={int(time.time() * 1000)}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_selector("#login_email", timeout=10000)
    page.fill("#login_email", PLATFORM_ADMIN)
    page.fill("#login_password", DEFAULT_PASSWORD)
    page.click(".btn-login")
    page.wait_for_url("**/desk**", timeout=60000)

    # Never cache an unverified session
    resp = page.request.get(
        f"{base_url}/api/method/frappe.auth.get_logged_user",
        timeout=LIVENESS_TIMEOUT,
    )
    actual = resp.json().get("message") if resp.ok else "<no response>"
    if actual != PLATFORM_ADMIN:
        browser.close()
        raise RuntimeError(
            f"platform_auth: form login ended up as {actual!r} — refusing to cache"
        )
    context.storage_state(path=str(auth_state_file(PLATFORM_STATE)))
    browser.close()


@pytest.fixture(scope="session", autouse=True)
def server_timezone(browser, base_url, platform_auth):
    """Assert ONCE per session that the server runs Asia/Manila.

    Every later countdown/deadline assertion in this suite inherits this
    invariant (section-1 gotcha; PLAN §8i). oneshot_install sets the TZ at
    full_reset time — if this fails, the ENVIRONMENT regressed: root-cause it,
    never paper over it here.

    Read from `frappe.boot`, which any logged-in desk user can see (helpers/
    gestures.py already reads date_format the same way), rather than from
    System Settings: since B43 this runs as the platform seat, which holds no
    System Settings DocPerm — and pushing the check out to `bench execute`
    would grow the one exemption B43 is trying to keep small.
    """
    from helpers.auth import PLATFORM_STATE

    context = browser.new_context(
        base_url=base_url,
        ignore_https_errors=True,
        storage_state=str(auth_state_file(PLATFORM_STATE)),
    )
    page = context.new_page()
    try:
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("() => window.frappe && frappe.boot", timeout=30000)
        tz = page.evaluate("() => (frappe.boot.sysdefaults || {}).time_zone")
    finally:
        context.close()
    assert tz == EXPECTED_TIMEZONE, (
        f"Server time_zone is {tz!r}, expected {EXPECTED_TIMEZONE!r} "
        f"(site {_base_url()}). The environment regressed — fix the site, "
        "not this assertion."
    )


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url, platform_auth):
    """Every test context starts authenticated as the PLATFORM seat.

    Backlog B43. Administrator was the default until 2026-09-05, and it was
    stated here as a fact rather than a decision — which is how 22 of 41 files
    came to arrange their world as a user this product does not ship. A file
    that needs a tenant seat now says so, with `login_as`.

    Depends on platform_auth explicitly so platform.json is guaranteed verified
    (and to exist on a cold start) before any context is built. base_url is
    injected so relative page.goto() calls resolve to THIS worker's site.
    """
    from helpers.auth import PLATFORM_STATE

    return {
        **browser_context_args,
        "base_url": base_url,
        "viewport": {"width": 1400, "height": 960},
        "ignore_https_errors": True,
        "storage_state": str(auth_state_file(PLATFORM_STATE)),
    }


CUSTOMER_EMAIL = "cust.pia@example.com"  # seeded portal customer (section-8)


@pytest.fixture(scope="session")
def customer_page(browser, base_url, bench_health):
    """A Page authenticated as the seeded portal customer Pia (section-8).

    Own context — deliberately NOT the admin storage_state every desk test
    context starts from. Cached per-worker by portal_login_as and VERIFIED
    on reuse (same trust model as admin_auth: never trust file age).
    """
    from helpers.auth import portal_login_as

    context = browser.new_context(
        base_url=base_url,
        viewport={"width": 1400, "height": 960},
        ignore_https_errors=True,
    )
    page = context.new_page()
    portal_login_as(page, CUSTOMER_EMAIL)
    yield page
    context.close()


@pytest.fixture(autouse=True)
def verify_session(page: Page):
    """Per-test guard on every DESK page: heal a dead session, then prove the
    seat is not Administrator.

    One ~20ms GET in the healthy case. Two jobs:

    1. HEAL. Covers the window platform_auth (session-scoped) can't: a bench
       restart mid-suite. It heals back to the seat THIS page last logged in as,
       not to a fixed user — the previous version healed to Administrator, so a
       staff-seat test whose session died silently finished as a superuser and
       still went green.
    2. TRIPWIRE (Backlog B43). Administrator is refused at the door by
       `helpers.auth`, but a cached cookie jar or a hand-rolled context could
       still land one here. Failing on the page itself is what stops the default
       quietly re-infecting new files, which is how this drifted for 41 files.

    SCOPE, stated honestly: this fixture only ever sees the `page` fixture. The
    `customer_page` context and the cleanup context built inside
    `helpers/board.cancel_active_bookings` are not covered by it — they are
    covered by `helpers.auth`'s own rejection and by platform.json being the
    only storage state the suite can ask for.
    """
    from helpers.auth import (
        DEFAULT_PASSWORD,
        PLATFORM_ADMIN,
        current_seat,
        forget_seat,
        get_logged_user,
        login_as,
    )

    who = get_logged_user(page)
    if who == "Guest":
        login_as(page, current_seat(page) or PLATFORM_ADMIN, DEFAULT_PASSWORD)
        who = get_logged_user(page)
    assert who.lower() != "administrator", (
        "this test context is authenticated as Administrator, which "
        "court_booking_tech's E2E suite never uses (Backlog B43): it bypasses "
        "DocPerms, so an arrange made with it can build a world no real seat "
        "could, and the assertion that follows proves nothing. Log in as a "
        "real seat, or use a court_booking_tech.testing helper via "
        "bench_execute for an arrange no seat can perform."
    )
    yield
    forget_seat(page)
