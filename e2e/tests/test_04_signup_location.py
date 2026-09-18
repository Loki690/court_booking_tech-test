"""
E2E file 04 — customer signup + location (section-8; PLAN §10 row 04).

Covers the D9 manual-registration path end-to-end (real Turnstile round-trip
with Cloudflare's official always-pass TEST keys), the login-page link-card
(no token-less form post can fire), the /my-profile Leaflet home pin, and
nearest-first ordering from the SESSION user's saved pin (the page-level
ordering assertion is section-9, file 05 — recorded there).

Email discipline: NO SMTP anywhere — the queued welcome mail is asserted (and
the set-password link minted) server-side via the allow_tests-gated
court_booking_tech.testing helpers, because frappe v16 redacts the queued
body and stores the reset key hashed (section-8 as-built deviation).
"""
import uuid

import pytest

from helpers.auth import get_logged_user, portal_login_as
from helpers.worker_routing import bench_execute

PIA = "cust.pia@example.com"
NOEL = "cust.noel@example.com"  # the pin-move test uses Noel — Pia's pin is
# the canonical origin for the ordering assertion below (seeds force-restore).

# Cloudflare's official always-pass TEST keys (§8v) — never real defaults.
TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
TURNSTILE_TEST_SECRET = "1x0000000000000000000000000000000AA"

SEEDED_ORDER = ["bgc", "makati", "timog", "annex"]  # from Pia's BGC pin


def _new_context(browser, base_url):
    return browser.new_context(
        base_url=base_url,
        viewport={"width": 1400, "height": 960},
        ignore_https_errors=True,
    )


def test_signup_turnstile_to_first_login(browser, base_url):
    """/signup with the always-pass Turnstile -> success screen -> queued
    welcome mail -> set password -> logged in. The full D9 manual path."""
    bench_execute(
        "court_booking_tech.testing.set_turnstile",
        f'[1, "{TURNSTILE_TEST_SITE_KEY}", "{TURNSTILE_TEST_SECRET}"]',
    )
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        email = f"e2e.signup.{uuid.uuid4().hex[:10]}@example.com"

        page.goto("/signup", wait_until="domcontentloaded", timeout=60000)
        page.fill("#cbt-fullname", "Zeta E2ESignup")
        page.fill("#cbt-email", email)
        # The test site key auto-passes; wait for the widget to mint a token.
        page.wait_for_function(
            "() => !!document.querySelector('[name=cf-turnstile-response]')?.value",
            timeout=30000,
        )
        page.click("#cbt-signup-btn")
        # Wait for EITHER outcome so a server-side reject fails with the
        # actual message instead of a silent success-card timeout.
        page.wait_for_selector(
            "#cbt-signup-done:not([hidden]), #cbt-signup-error:not([hidden])",
            timeout=20000,
        )
        error_box = page.locator("#cbt-signup-error")
        if error_box.is_visible():
            pytest.fail(f"signup rejected server-side: {error_box.text_content()!r}")

        # Queue row asserted + link minted server-side (gated helper).
        result = bench_execute(
            "court_booking_tech.testing.get_signup_link", f'["{email}"]'
        )
        link_line = [
            line for line in result.stdout.splitlines() if "/update-password" in line
        ]
        assert link_line, f"no set-password link in: {result.stdout!r}"
        path = link_line[-1][link_line[-1].index("/update-password") :].strip()

        page.goto(path, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#new_password", timeout=15000)
        page.fill("#new_password", "E2e!Signup#2026x")
        page.fill("#confirm_password", "E2e!Signup#2026x")
        # The page's strength check is bound to "keyup paste" (debounced) —
        # fill() fires neither, so nudge it or Confirm never enables.
        page.dispatch_event("#new_password", "keyup")
        page.dispatch_event("#confirm_password", "keyup")
        page.wait_for_function(
            "() => !document.querySelector('#update').disabled", timeout=15000
        )
        page.click("#update")
        # Completing the reset logs the new customer in.
        page.wait_for_url(lambda url: "update-password" not in url, timeout=60000)
        assert get_logged_user(page).lower() == email
    finally:
        context.close()
        bench_execute("court_booking_tech.testing.set_turnstile", "[0]")


def test_signing_up_with_an_existing_address_mails_the_owner(browser, base_url):
    """Backlog B22, through the real form. The branch returns the SAME generic
    success as a fresh signup (anti-enumeration, PLAN §2 D9) — so the only
    observable difference is the mail, and until now nothing drove this path in
    a browser at all: file 04's signup test mints a fresh UUID address every run.

    Asserted as a DELTA, because Pia is a seeded customer other files mail.
    """
    bench_execute("court_booking_tech.testing.clear_rate_buckets", "[]")
    before = bench_execute(
        "court_booking_tech.testing.get_address_mail", f'["{PIA}"]'
    ).stdout

    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/signup", wait_until="domcontentloaded", timeout=60000)
        page.fill("#cbt-fullname", "Pia Duplicate")
        page.fill("#cbt-email", PIA)
        page.click("#cbt-signup-btn")

        # The generic success card, identical to a fresh address: an error here
        # would itself be the enumeration leak this branch exists to deny.
        page.wait_for_selector(
            "#cbt-signup-done:not([hidden]), #cbt-signup-error:not([hidden])",
            timeout=20000,
        )
        error_box = page.locator("#cbt-signup-error")
        if error_box.is_visible():
            pytest.fail(
                "existing address was REFUSED — that is an enumeration oracle: "
                f"{error_box.text_content()!r}"
            )
    finally:
        context.close()

    after = bench_execute(
        "court_booking_tech.testing.get_address_mail", f'["{PIA}"]'
    ).stdout
    assert after != before, "the existing-address branch queued no mail"
    assert "/login" in after, f"the mail must point at /login: {after[-600:]!r}"
    # No credential is minted on this path — that is the whole shape of the fix.
    assert "update-password" not in after and "key=" not in after, after[-600:]


def test_login_page_signup_panel_is_link_card(browser, base_url):
    """PLAN §7 gotcha: the login page's signup panel must carry NO <form> —
    frappe's login.js would post token-less. Clicking lands on /signup and no
    sign_up request fires from the panel."""
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        signup_posts = []
        page.on(
            "request",
            lambda request: signup_posts.append(request.url)
            if "sign_up" in request.url
            else None,
        )
        page.goto("/login#signup", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("div.form-signup:not(.hide)", timeout=15000)
        assert page.locator("form.form-signup").count() == 0, (
            "login page still renders a signup <form> — login.js will post token-less"
        )
        page.click("div.form-signup a[href='/signup']")
        page.wait_for_url("**/signup**", timeout=30000)
        assert not signup_posts, f"unexpected sign_up call(s): {signup_posts}"
    finally:
        context.close()


def test_my_profile_requires_login(browser, base_url):
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/my-profile", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_url("**/login**", timeout=30000)
    finally:
        context.close()


def test_profile_pin_map_click_persists(browser, base_url):
    """Map click -> coords populate -> save -> reload -> persisted. Uses NOEL:
    Pia's pin is the load-bearing origin of the ordering test below."""
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        portal_login_as(page, NOEL)
        page.goto("/my-profile", wait_until="domcontentloaded", timeout=60000)
        # Leaflet marks the container ITSELF — compound selector, no space.
        page.wait_for_selector("#cbt-profile-map.leaflet-container", timeout=15000)

        page.click("#cbt-profile-map", position={"x": 220, "y": 140})
        lat = page.input_value("#cbt-lat")
        lng = page.input_value("#cbt-lng")
        assert lat and lng, "map click did not populate the coordinate fields"

        page.click("#cbt-profile-save")
        page.wait_for_selector("#cbt-profile-status:not([hidden])", timeout=15000)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#cbt-profile-map.leaflet-container", timeout=15000)
        assert float(page.input_value("#cbt-lat")) == pytest.approx(float(lat), abs=1e-5)
        assert float(page.input_value("#cbt-lng")) == pytest.approx(float(lng), abs=1e-5)
    finally:
        context.close()


def test_nearest_first_from_pia_session_pin(customer_page):
    """geo.get_branches with NO explicit origin orders from the session
    user's saved pin (section-8 extension): Pia (BGC-adjacent) sees
    bgc, makati, timog, then pin-less annex last."""
    resp = customer_page.request.get(
        "/api/method/court_booking_tech.geo.get_branches", timeout=15000
    )
    assert resp.ok, f"get_branches: HTTP {resp.status}"
    slugs = [row["branch_slug"] for row in resp.json()["message"]]
    positions = [slugs.index(slug) for slug in SEEDED_ORDER]
    assert positions == sorted(positions), (
        f"expected {SEEDED_ORDER} in order within {slugs}"
    )
    # And Pia's pin actually produced distances (fallback engaged).
    first = resp.json()["message"][slugs.index("bgc")]
    assert first["distance_km"] is not None and first["distance_km"] < 1.0
