"""
E2E — the marketplace home page carries ONE labelled paid banner (Backlog B52).

The platform's own front door is the one page every customer of every tenant
passes through, so it is the only surface the platform can sell. What a customer
must be able to tell, without being told: that the banner is an advertisement.

LEDGER: THIS FILE CLAIMS NO DATE. It books nothing and signs in as nobody — the
whole point is what a STRANGER sees before they have an account.

⚠ The seed installs exactly ONE live ad and one EXPIRED one, deliberately:
`/find-court` picks at random from whatever is live that day, so a second live
row would make the render assertion flake on which one it drew. The expired row
is the negative control and can never be chosen.
"""
from playwright.sync_api import expect

AD = "[data-testid='home-ad']"
TAG = "[data-testid='home-ad-tag']"
LIVE_TITLE = "Rally Sports — 20% off paddles"
EXPIRED_TITLE = "Summer League 2020 (ended)"


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
                    width: r.width, height: r.height,
                    hit: !!hit && (el === hit || el.contains(hit) || hit.contains(el)),
                    hitTag: hit ? hit.tagName + '.' + hit.className : null,
                    text: el.innerText.trim()};
        }"""
    )


def test_a_signed_out_visitor_sees_one_banner_marked_sponsored(browser, base_url):
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)
        assert "/login" not in page.url, page.url

        # ONE, never a stack: three advertisers must not push the courts off a phone.
        expect(page.locator(AD)).to_have_count(1)
        ad = page.locator(AD).first

        # The word a customer needs, rendered — not merely rel="sponsored".
        tag = page.locator(TAG).first
        readable = _readable(tag)
        assert readable["text"] == "Sponsored", readable
        assert readable["hit"], f"the Sponsored label is not readable on screen: {readable}"
        assert readable["visibility"] == "visible" and readable["opacity"] == "1", readable

        # A real outbound link, opened away from the marketplace.
        assert ad.get_attribute("target") == "_blank", ad.get_attribute("target")
        rel = ad.get_attribute("rel") or ""
        assert "sponsored" in rel and "noopener" in rel, rel
        href = ad.get_attribute("href") or ""
        assert href.startswith("https://"), href

        # A real image with real alt text — the title IS the alt text.
        image = ad.locator("img.cbt-ad-img")
        expect(image).to_have_count(1)
        assert (image.get_attribute("alt") or "").strip(), "the banner has no alt text"
        box = _readable(image)
        assert box["width"] > 0 and box["height"] > 0, box
    finally:
        context.close()


def test_the_banner_never_pushes_the_search_results_off_a_phone(browser, base_url):
    """The page exists to show courts. An ad that buries them has taken the page."""
    context = browser.new_context(
        base_url=base_url, viewport={"width": 390, "height": 844}, ignore_https_errors=True
    )
    try:
        page = context.new_page()
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)

        ad_box = page.locator(AD).first.bounding_box()
        card_box = page.locator("#cbt-results .cbt-card[data-slug]").first.bounding_box()
        assert ad_box and card_box, (ad_box, card_box)
        assert ad_box["height"] <= 160, f"banner {ad_box['height']}px tall on a 390px phone"
        # The first court must still begin above the fold.
        assert card_box["y"] < 844, f"first court starts at y={card_box['y']} on an 844px screen"
        assert ad_box["width"] <= 390, ad_box
    finally:
        context.close()


def test_an_expired_ad_is_never_rendered(browser, base_url):
    """The negative control: a dated placement that has ended stops appearing.

    Asserted on the rendered TITLE, because that is the string a customer would
    actually see — the seeded expired row is a real CBT Ad with a real image, so
    nothing but the date window keeps it off the page.
    """
    context = _guest(browser, base_url)
    try:
        page = context.new_page()
        page.goto("/find-court", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#cbt-results .cbt-card[data-slug]", timeout=30000)

        alt = page.locator(f"{AD} img").first.get_attribute("alt") or ""
        assert alt == LIVE_TITLE, alt
        assert EXPIRED_TITLE not in page.content()
    finally:
        context.close()
