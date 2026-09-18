"""
E2E — Google login (Backlog B51, 2026-09-09).

The user: *"please guide me too … I don't know how to test the SSO"*. The round
trip to Google is external and cannot run here; what CAN be pinned is every
piece the app controls, driven the way a customer meets it:

  1. `/login` offers **Login with Google**, and the link goes to Google's
     authorize endpoint with THIS site's callback, the userinfo scopes and the
     dummy client id;
  2. a `redirect-to` on the login page rides behind frappe's single-use state
     token (server-side since 16.29; read via `testing.peek_oauth_state`);
  3. a GUEST at the checkout clicks **Log in to book** and the Google button on
     the page that opens still carries the booking page they came from;
  4. `/signup` offers **Continue with Google**, pointed at `/find-court`;
  5. with the key disabled, neither page shows a Google button.

SITE-WIDE STATE: the module fixture ENABLES a dummy key on this worker's site
and DISABLES it in teardown. A killed run leaves the button on every later login
page until the lane's own reset (the same class of residue as the test clock).

LEDGER: THIS FILE CLAIMS NO DATE. Row 3 selects a slot and only GETs a quote —
no hold is minted.
"""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from helpers.worker_routing import bench_json

CALLBACK = "/api/method/frappe.integrations.oauth2_logins.login_via_google"
DUMMY_CLIENT = "e2e-google-client"
GOOGLE_BUTTON = "a.btn-google"  # frappe's login page (www/login.html) — DOM truth, for the absent check
GOOGLE_BUTTON_SHOWN = "a.btn-google:visible"  # frappe >= 16.29 renders the login body twice; the one a person sees
SIGNUP_BUTTON = "a.cbt-btn-social"  # our /signup
BOOK_URL = f"/book?c=ayala-courts&b=bgc&d={(date.today() + timedelta(days=7)).isoformat()}"


@pytest.fixture(scope="module", autouse=True)
def google_key():
    status = bench_json("court_booking_tech.testing.set_google_login", [1])
    assert status["enabled"], status
    yield
    bench_json("court_booking_tech.testing.set_google_login", [0])


def _new_context(browser, base_url):
    return browser.new_context(
        base_url=base_url, viewport={"width": 1400, "height": 960}, ignore_https_errors=True
    )


def _google_link(page, selector) -> dict:
    href = page.locator(selector).first.get_attribute("href") or ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    # frappe >= 16.29: `state` is a single-use token; redirect_to lives in the site's cache.
    token = query["state"][0]
    redirect_to = bench_json("court_booking_tech.testing.peek_oauth_state", [token])["redirect_to"]
    assert redirect_to is not None, f"oauth state {token} is gone from the cache (600s TTL)"
    return {
        "host": parsed.netloc,
        "path": parsed.path,
        "redirect_uri": query["redirect_uri"][0],
        "scope": query.get("scope", [""])[0],
        "client_id": query["client_id"][0],
        "redirect_to": redirect_to,
    }


def test_the_login_page_offers_google_pointed_at_this_sites_callback(browser, base_url):
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/login", wait_until="domcontentloaded", timeout=60000)
        button = page.locator(GOOGLE_BUTTON_SHOWN)
        expect(button).to_have_count(1, timeout=15000)
        expect(button).to_contain_text("Login with Google")
        link = _google_link(page, GOOGLE_BUTTON_SHOWN)
        assert link["host"] == "accounts.google.com" and link["path"] == "/o/oauth2/auth", link
        assert link["redirect_uri"].startswith("http") and link["redirect_uri"].endswith(CALLBACK), link
        assert "userinfo.email" in link["scope"] and "userinfo.profile" in link["scope"], link
        assert link["client_id"] == DUMMY_CLIENT, link
    finally:
        context.close()


def test_a_login_redirect_rides_through_google(browser, base_url):
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/login?redirect-to=/my-bookings", wait_until="domcontentloaded", timeout=60000)
        expect(page.locator(GOOGLE_BUTTON_SHOWN)).to_have_count(1, timeout=15000)
        link = _google_link(page, GOOGLE_BUTTON_SHOWN)
        assert link["redirect_to"].endswith("/my-bookings"), link
    finally:
        context.close()


def test_a_guest_at_the_checkout_reaches_google_and_keeps_the_booking_page(browser, base_url):
    """The click path a real customer takes: slot → Review & book → Log in to
    book → the Google button on the page that opens still knows where they
    were. A quote only; nothing is reserved."""
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto(BOOK_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".cbt-matrix .cbt-slot:not([disabled])", timeout=30000)
        page.locator(".cbt-matrix .cbt-slot:not([disabled])").first.click()
        page.locator("#cbt-checkoutbar").wait_for(state="visible", timeout=15000)
        page.locator("#cbt-review").click()
        cta = page.locator("#cbt-login-cta")
        expect(cta).to_be_visible(timeout=15000)
        cta.click()
        page.wait_for_url("**/login**", timeout=30000)
        expect(page.locator(GOOGLE_BUTTON_SHOWN)).to_have_count(1, timeout=15000)
        link = _google_link(page, GOOGLE_BUTTON_SHOWN)
        assert "/book?c=ayala-courts&b=bgc" in link["redirect_to"], link
    finally:
        context.close()


def test_signup_offers_continue_with_google_to_the_marketplace(browser, base_url):
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/signup", wait_until="domcontentloaded", timeout=60000)
        button = page.locator(SIGNUP_BUTTON)
        expect(button).to_be_visible(timeout=15000)
        expect(button).to_have_text("Continue with Google")
        link = _google_link(page, SIGNUP_BUTTON)
        assert link["redirect_to"] == "/find-court", link
        assert link["redirect_uri"].endswith(CALLBACK), link
    finally:
        context.close()


def test_no_key_no_button_anywhere(browser, base_url):
    """The negative control: a disabled key must take the button off BOTH pages,
    or a site without Google would advertise a login it cannot honour."""
    bench_json("court_booking_tech.testing.set_google_login", [0])
    context = _new_context(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/login", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#login_email", timeout=15000)
        assert page.locator(GOOGLE_BUTTON).count() == 0
        page.goto("/signup", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-signup-form", timeout=15000)
        assert page.locator(SIGNUP_BUTTON).count() == 0
    finally:
        context.close()
        bench_json("court_booking_tech.testing.set_google_login", [1])
