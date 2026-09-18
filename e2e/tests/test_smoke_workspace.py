"""
Section-1 harness smoke 2/3 — CBT Hub workspace (see test_smoke_desk.py for
why the smoke is split across three files).
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.auth import login_as
from helpers.navigation import goto_workspace


@pytest.mark.e2e
class TestSmokeWorkspace:

    def test_cbt_hub_workspace_renders(self, page: Page, platform_seat: str):
        """The CBT Hub workspace exists (JSON synced) and shows our shortcut —
        proves workspace + sidebar shipped, not an auto-generated shell. Seen
        by the seeded PLATFORM seat, not the Administrator context."""
        login_as(page, platform_seat)
        goto_workspace(page, "CBT Hub")
        expect(
            page.get_by_text("CBT Platform Settings").first
        ).to_be_visible(timeout=15000)
