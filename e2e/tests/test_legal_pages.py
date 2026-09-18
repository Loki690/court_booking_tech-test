"""E2E — the privacy policy and terms pages Google's OAuth review requires.

LEDGER: THIS FILE CLAIMS NO DATE. It books nothing and signs in as nobody.
"""
from playwright.sync_api import expect

PRIVACY = "[data-testid='link-privacy']"
TERMS = "[data-testid='link-terms']"
BRAND = "BookPickleBall"


def _guest(browser, base_url):
	return browser.new_context(
		base_url=base_url, viewport={"width": 1400, "height": 960}, ignore_https_errors=True
	)


def _readable(locator) -> dict:
	"""Unoccluded at rest. elementFromPoint is viewport-relative — scroll first."""
	locator.scroll_into_view_if_needed()
	return locator.evaluate(
		"""(el) => {
			const cs = getComputedStyle(el);
			const r = el.getBoundingClientRect();
			const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
			return {opacity: cs.opacity, visibility: cs.visibility,
					hit: !!hit && (el === hit || el.contains(hit) || hit.contains(el)),
					text: el.innerText.trim()};
		}"""
	)


def test_a_signed_out_visitor_reaches_both_pages_from_the_home_footer(browser, base_url):
	"""Google's reviewer follows the links from the home page, signed out."""
	context = _guest(browser, base_url)
	try:
		page = context.new_page()
		page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
		page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
		assert "/login" not in page.url, page.url

		for selector, label, route, heading in (
			(PRIVACY, "Privacy Policy", "/privacy-policy", "Privacy Policy"),
			(TERMS, "Terms of Service", "/terms-of-service", "Terms of Service"),
		):
			link = page.locator(selector)
			readable = _readable(link)
			assert readable["hit"], f"{label} not clickable on screen: {readable}"
			assert readable["text"] == label, readable
			link.click()
			page.wait_for_load_state("domcontentloaded")
			assert page.url.endswith(route), page.url
			expect(page.locator("h1")).to_have_text(heading)
			page.go_back(wait_until="domcontentloaded")
			page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
	finally:
		context.close()


def test_both_pages_state_what_google_sign_in_reads_and_what_is_never_stored(browser, base_url):
	"""The two claims that must not silently drift out of the copy."""
	context = _guest(browser, base_url)
	try:
		page = context.new_page()
		page.goto("/privacy-policy", wait_until="domcontentloaded", timeout=60000)
		privacy = page.locator(".cbt-legal-page").inner_text()
		assert "openid" in privacy, privacy[:400]
		assert "no card or bank account numbers" in privacy.lower(), privacy[:400]
		assert "BookPickleBall" in privacy, privacy[:400]

		page.goto("/terms-of-service", wait_until="domcontentloaded", timeout=60000)
		terms = page.locator(".cbt-legal-page").inner_text()
		# B53: refund policy is the facility's, so this page must promise none.
		assert "own cancellation and refund policy" in terms, terms[:400]
		assert "BookPickleBall" in terms, terms[:400]
	finally:
		context.close()


def test_the_home_page_renders_the_name_google_compares_against(browser, base_url):
	"""Google refuses OAuth validation when the consent-screen App name and the
	name rendered on the home page differ. One character is enough."""
	context = _guest(browser, base_url)
	try:
		page = context.new_page()
		page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
		brand = page.locator("[data-testid='brand']")
		expect(brand).to_have_text(BRAND)
		readable = _readable(brand)
		assert readable["hit"], f"brand not readable on screen: {readable}"
		assert BRAND in page.title(), page.title()
	finally:
		context.close()


def test_the_footer_links_are_on_every_portal_page(browser, base_url):
	"""One shared include — proven shared, not assumed."""
	context = _guest(browser, base_url)
	try:
		page = context.new_page()
		for route in ("/find-court", "/book?c=ayala-courts"):
			page.goto(route, wait_until="domcontentloaded", timeout=60000)
			expect(page.locator(PRIVACY)).to_be_visible()
			expect(page.locator(TERMS)).to_be_visible()
	finally:
		context.close()
