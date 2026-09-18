"""
Section-1 harness smoke 3/3 — CBT Platform Settings form (see
test_smoke_desk.py for why the smoke is split across three files).
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import login_as
from helpers.navigation import goto_single


@pytest.mark.e2e
class TestSmokeSettings:

    def test_platform_settings_form_opens(self, page: Page, platform_seat: str):
        """The Single form opens with the authoritative fields present — for
        the seeded PLATFORM seat (CBT Platform Admin, nothing else), not the
        Administrator context, which bypasses the DocPerm this proves.

        v16 gotcha: the page header has NO .title-text node (titles live in
        the breadcrumbs) — assert the document title + field controls instead.
        """
        login_as(page, platform_seat)
        goto_single(page, "CBT Platform Settings")
        expect(page).to_have_title(
            re.compile("CBT Platform Settings"), timeout=15000
        )
        for fieldname in (
            "default_verification_hold_hours",
            "max_active_proof_holds_per_customer",
            "enable_turnstile",
        ):
            expect(
                page.locator(f'.frappe-control[data-fieldname="{fieldname}"]').first
            ).to_be_attached(timeout=15000)
