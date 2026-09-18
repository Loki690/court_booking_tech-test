"""
Frappe page navigation helpers.

Route prefix: /desk/ (Frappe v16 — verified from router.js). Relative gotos
resolve against the per-worker base_url injected by conftest.
"""
from urllib.parse import quote

from playwright.sync_api import Page


def _slug(doctype: str) -> str:
    """Convert DocType name to URL slug: 'CBT Platform Settings' -> 'cbt-platform-settings'."""
    return doctype.lower().replace(" ", "-")


def wait_for_page_load(page: Page, timeout: int = 30000):
    """Wait for the Frappe desk page to finish loading."""
    page.wait_for_selector('body[data-ajax-state="complete"]', timeout=timeout)


def goto_form(page: Page, doctype: str, name: str):
    """Navigate to an existing document form."""
    page.goto(f"/desk/{_slug(doctype)}/{quote(name, safe='')}", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)


def goto_list(page: Page, doctype: str):
    """Navigate to a list view."""
    page.goto(f"/desk/{_slug(doctype)}", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)


def goto_single(page: Page, doctype: str):
    """Navigate to a single-type DocType (e.g., CBT Platform Settings)."""
    page.goto(f"/desk/{_slug(doctype)}", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)


def goto_workspace(page: Page, workspace: str):
    """Navigate to a Workspace by label (e.g., 'CBT Hub' -> /desk/cbt-hub)."""
    page.goto(f"/desk/{_slug(workspace)}", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
