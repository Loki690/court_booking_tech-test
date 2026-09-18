"""
Section-1 harness smoke 1/3 — desk boot + verified auth.

The smoke is split across three files ON PURPOSE: run_all_tests.sh uses
`--dist loadfile`, so one file per worker is what actually exercises all
three site-pinned servers (a single file would pin every test to gw0 and
prove nothing about dev2/dev3 routing).
"""
import pytest
from playwright.sync_api import Page

from helpers.auth import PLATFORM_ADMIN, get_logged_user
from helpers.navigation import wait_for_page_load


@pytest.mark.e2e
class TestSmokeDesk:

    def test_desk_loads_authenticated(self, page: Page):
        """The desk SPA boots and the session really is the PLATFORM seat.

        Backlog B43: this row asserted "Administrator" until 2026-09-05 and was
        therefore the suite's own statement that the default was a superuser.
        It now pins the decision instead — a default that drifts back has to
        change this line to do it.
        """
        page.goto("/desk", wait_until="domcontentloaded", timeout=60000)
        wait_for_page_load(page)
        assert get_logged_user(page) == PLATFORM_ADMIN
