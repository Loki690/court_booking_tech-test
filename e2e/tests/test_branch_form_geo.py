"""
Section-3 pull-forward — CBT Branch form: Leaflet pin picker + embed preview.

Automates the section-3 Definition-of-Done "manual desk check" (user rule:
every change ships with E2E). The self-management-gate E2E stays in file 03
(section-7); this file only proves the geo form UI renders and works.

Assertion rules (offline-dev safe):
- Google embed: assert the iframe `src` ATTRIBUTE only — never wait on
  cross-origin content.
- Leaflet: assert container/marker DOM presence — never tile images (the OSM
  tile server is external; offline dev renders gray tiles by design).
"""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from helpers.navigation import goto_form

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"


@pytest.mark.e2e
class TestBranchFormGeo:

    def test_pinned_branch_shows_map_and_embed(self, page: Page):
        """AYALA-bgc (seeded pin): Leaflet map + marker render, embed iframe
        points at the exact seeded coordinates."""
        goto_form(page, "CBT Branch", "AYALA-bgc")

        # L.map() stamps leaflet-container onto the .cbt-branch-map div itself.
        map_container = page.locator(".cbt-branch-map.leaflet-container")
        expect(map_container).to_be_visible(timeout=15000)
        expect(page.locator(".cbt-branch-map .leaflet-marker-icon")).to_be_visible(
            timeout=10000
        )

        iframe = page.locator("[data-fieldname='embed_preview_html'] iframe")
        expect(iframe).to_have_count(1)
        src = iframe.get_attribute("src") or ""
        assert "maps.google.com/maps" in src, src
        assert "output=embed" in src, src
        assert "14.5507" in src and "121.0494" in src, src

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / "branch_form_geo.png"), full_page=True
        )

    def test_map_click_sets_coordinates(self, page: Page):
        """QCSM-annex (seeded WITHOUT a pin): clicking the map fills the
        latitude/longitude fields client-side. Not saved — the pin-less
        branch is a load-bearing sort-last fixture for geo tests."""
        goto_form(page, "CBT Branch", "QCSM-annex")

        # L.map() stamps leaflet-container onto the .cbt-branch-map div itself.
        map_container = page.locator(".cbt-branch-map.leaflet-container")
        expect(map_container).to_be_visible(timeout=15000)
        lat_input = page.locator(
            ".frappe-control[data-fieldname='latitude'] input"
        ).first
        lng_input = page.locator(
            ".frappe-control[data-fieldname='longitude'] input"
        ).first
        # Pin-less fixture renders as "" or a zero formatting variant —
        # assert the CHANGE, not an exact empty string.
        initial_lat = lat_input.input_value()
        initial_lng = lng_input.input_value()

        # Leaflet needs a settled layout before click coords map to latlng.
        page.wait_for_timeout(500)
        map_container.click(position={"x": 200, "y": 150})

        expect(lat_input).not_to_have_value(initial_lat, timeout=5000)
        expect(lng_input).not_to_have_value(initial_lng, timeout=5000)
        lat = float(lat_input.input_value())
        lng = float(lng_input.input_value())
        # Anywhere on a Metro-Manila-centered map — sanity, not precision.
        assert 4 < lat < 22, lat
        assert 114 < lng < 128, lng

        # Marker appears on the dropped pin.
        expect(page.locator(".cbt-branch-map .leaflet-marker-icon")).to_be_visible()

        # Leave the dirty form via a hard goto (Playwright auto-accepts
        # beforeunload) so the unsaved state can't wedge later navigation.
        page.goto("/app", wait_until="domcontentloaded", timeout=60000)
