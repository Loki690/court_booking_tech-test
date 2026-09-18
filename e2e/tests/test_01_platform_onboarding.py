"""
Section-11 file 01 — platform onboarding: standing up a brand-new tenant.

The business model made operable (PLAN §1: "we host the tech; facility
companies subscribe"). One flow, start to finish, as the Platform Admin:

    company -> admin user -> branch with a map pin -> priced court
            -> the go-live checklist turns green
            -> the deep link handed to the client actually works for a shopper

The checklist is DERIVED from the data, so this file also proves it tells the
truth in both directions: red before the work, green after it.

Every desk row logs in as the seeded PLATFORM seat (`cbt.admin@example.com`,
the CBT Platform Admin role and nothing else — Batch 17): the Administrator
context every test starts from bypasses DocPerms, so "the Platform Admin can
stand a tenant up" was never proven by it. Cleanup stays on the admin API.

Self-contained: it creates its own company (`demo-club`) and cleans it up, so
it cannot disturb the AYALA/QCSM fixtures every other file leans on. The branch
is pinned in CEBU, far from Pia's BGC pin — it therefore sorts after timog and
before the pin-less annex, leaving file 05's relative ordering assertion intact
on a shared worker site.
"""
import json

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from helpers import board, gestures
from helpers.auth import login_as
from helpers.desk_form import wait_for_new_form
from helpers.navigation import wait_for_page_load

SLUG = "demo-club"
CODE = "DEMO"
ADMIN_EMAIL = "admin.democlub@example.com"
BRANCH_SLUG = "main"
BRANCH_NAME = f"{CODE}-{BRANCH_SLUG}"
COURT_NAME = f"{BRANCH_NAME}-court-1"

# Cebu — deliberately far from every seeded Metro-Manila pin (see module docs).
CEBU_LAT, CEBU_LNG = 10.3157, 123.8854


def _open_onboarding_tab(page: Page):
    """Activate the Onboarding tab before asserting on its contents.

    Frappe hides inactive tab panes with CSS, so the checklist resolves in the
    DOM while being invisible — the same trap as section-10's TV-mode controls
    (note 17b). Assertions stay visibility-based; the test just has to open the
    tab a human would click.

    Driven through the form's own tab API rather than a DOM selector: frappe
    v16 renders the tab control as a NON-anchor element with a generated id
    (`<doctype>-<fieldname>-tab`), so a tag-qualified `a[...]` selector silently
    matches nothing and times out.
    """
    page.wait_for_function(
        """() => window.cur_frm && cur_frm.layout && cur_frm.layout.tabs
            && cur_frm.layout.tabs.some(t => t.df && t.df.fieldname === 'onboarding_tab')""",
        timeout=20000,
    )
    page.evaluate(
        """() => cur_frm.layout.tabs
            .find(t => t.df && t.df.fieldname === 'onboarding_tab')
            .set_active()"""
    )
    page.locator("[data-testid='onboarding-checklist']").wait_for(
        state="visible", timeout=20000
    )


def _new_form(page: Page, doctype_route: str, ready_field: str):
    """Open a NEW desk form and wait until its CONTROLS are rendered.

    `cur_frm.doc` exists well before the field controls do, and setting a Link
    value while its control is missing silently does nothing — the value never
    lands, and the save then dies on a client-side "Missing Fields" check that
    never reaches the server (presenting as a hung save). Waiting on a rendered
    control is the pattern test_03_branch_gate already uses on this same form.

    B11: the new-doc route can resolve TWICE under load, and the first write lands on a
    document that is then discarded. That used to be REPAIRED by re-running the whole
    `set_value` chain — but this file now TYPES, and typed keystrokes plus a clicked
    option cannot be replayed onto the surviving form. So the prevention is no longer
    optional: `wait_for_new_form` asserts the router has stopped moving, and it goes
    LAST, because a rendered control and `cur_frm.doc` are both true of the doc that is
    about to be thrown away.
    """
    page.goto(f"/desk/{doctype_route}/new", wait_until="domcontentloaded", timeout=60000)
    wait_for_page_load(page)
    page.wait_for_selector(
        f".frappe-control[data-fieldname='{ready_field}']", timeout=20000
    )
    page.wait_for_function("() => window.cur_frm && cur_frm.doc", timeout=30000)
    # "cbt-open-play-session" -> "CBT Open Play Session"
    wait_for_new_form(
        page, " ".join(w.upper() if w == "cbt" else w.title() for w in doctype_route.split("-"))
    )


def _save_new_form(page: Page) -> dict:
    """Save and report the OUTCOME loudly.

    frm.save() can RESOLVE even when the server rejects (S4 as-built 10), so a
    bare state-wait turns a real validation error into a mute timeout. Mirrors
    test_03_branch_gate._save_form — capture the result plus any modal text and
    decide from that.
    """
    # Ctrl+S — the keystroke a human presses. `gestures.save_form` owns the
    # execution-context guard: a first save RENAMES the route
    # (/new-cbt-company-x -> /demo-club) and that navigation tears down the JS
    # context, which for a new doc IS the success signal.
    try:
        outcome = {"name": gestures.save_form(page), "is_new": 0}
    except PlaywrightError as exc:
        outcome = {"rejected": str(exc)[:300], "is_new": 1}

    # `.modal.show` — closed frappe dialogs stay in the DOM, so a bare `.modal`
    # reports a PREVIOUS dialog's text as if it were this save's error.
    outcome["modals"] = page.evaluate(
        """() => Array.from(document.querySelectorAll('.modal.show'))
            .map(m => (m.innerText || '').trim()).filter(Boolean).slice(0, 3)"""
    )
    assert not outcome.get("is_new"), f"save failed: {outcome}"
    return outcome


def _delete(api, doctype: str, name: str):
    api.delete(
        f"/api/resource/{doctype}/{name}",
        headers={"X-Frappe-CSRF-Token": board.api_csrf(api)},
    )


@pytest.fixture(scope="module", autouse=True)
def clean_demo_company(playwright):
    """Make the file re-runnable without a reset, and leave no tenant behind.

    Teardown order is the reverse of creation — courts and branches link to the
    company, and frappe refuses to delete a linked document.
    """
    api = board.platform_api(playwright)

    def purge():
        for doctype, filters in (
            ("CBT Court Booking", [["company", "=", SLUG]]),
            ("CBT Court", [["company", "=", SLUG]]),
            ("CBT Branch", [["company", "=", SLUG]]),
            ("CBT Company User", [["company", "=", SLUG]]),
        ):
            resp = api.get(
                f"/api/resource/{doctype}", params={"filters": json.dumps(filters)}
            )
            if resp.ok:
                for row in resp.json()["data"]:
                    _delete(api, doctype, row["name"])
        _delete(api, "CBT Company", SLUG)
        _delete(api, "User", ADMIN_EMAIL)

    purge()
    try:
        yield api
    finally:
        purge()
        api.dispose()


@pytest.mark.e2e
class TestPlatformOnboarding:

    def test_create_company_and_the_checklist_starts_red(self, page: Page, platform_seat: str):
        """A fresh tenant is not ready to trade, and the checklist says so."""
        login_as(page, platform_seat)
        _new_form(page, "cbt-company", "slug")

        # Typed field by field, in the order a human meets them. The chained
        # `set_value` promise this replaced existed to dodge a race that only
        # exists BETWEEN model writes — typing has no such problem, and it proves
        # each control is actually reachable and editable.
        gestures.fill(page, "slug", SLUG)
        gestures.fill(page, "company_code", CODE)
        gestures.fill(page, "company_name", "Demo Club")
        gestures.fill(page, "registered_name", "Demo Club Sports Inc.")
        gestures.fill(page, "vat_registration", "NON-VAT")
        _save_new_form(page)
        assert page.evaluate("() => cur_frm.doc.name") == SLUG

        _open_onboarding_tab(page)
        checklist = page.locator("[data-testid='onboarding-checklist']")
        expect(checklist).to_be_visible(timeout=20000)
        expect(checklist).to_have_attribute("data-ready", "0")
        for key in ("admin_user", "branch_pinned", "court_priced"):
            expect(
                page.locator(f"[data-testid='onboarding-step-{key}']")
            ).to_have_attribute("data-done", "0")
        # Green from creation since B31 — see section-11 addendum.
        expect(
            page.locator("[data-testid='onboarding-step-payment_instructions']")
        ).to_have_attribute("data-done", "1")

    def test_create_the_company_admin_user(self, page: Page, platform_seat: str):
        """Through the guarded API the platform actually uses — it enforces
        own-company + Staff/Admin-only (never Platform Admin) — called BY the
        platform seat's own session, not the admin API context."""
        login_as(page, platform_seat)
        resp = page.request.post(
            "/api/method/court_booking_tech.api.company_users.create_company_user",
            headers={
                "X-Frappe-CSRF-Token": board.csrf(page),
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "email": ADMIN_EMAIL,
                    "full_name": "Dexter DemoAdmin",
                    "company_role": "Company Admin",
                    "company": SLUG,
                }
            ),
        )
        assert resp.ok, f"create_company_user: HTTP {resp.status} {resp.text()}"

        page.goto(f"/desk/cbt-company/{SLUG}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("() => window.cur_frm && cur_frm.doc.name", timeout=30000)
        _open_onboarding_tab(page)
        expect(
            page.locator("[data-testid='onboarding-step-admin_user']")
        ).to_have_attribute("data-done", "1", timeout=20000)

    def test_add_a_pinned_branch_and_a_priced_court(self, page: Page, platform_seat: str):
        """The branch is pinned by CLICKING the Leaflet map — the same gesture
        the platform admin makes (section-3's picker), not a field write."""
        login_as(page, platform_seat)
        # Precondition, asserted rather than assumed: a missing company would
        # make the Link below clear itself and the save would die on a
        # client-side "Missing Fields" check — which presents as a hung save,
        # not as "the company is gone".
        pre = page.request.get(f"/api/resource/CBT Company/{SLUG}")
        assert pre.ok, f"{SLUG} must exist before its branch: HTTP {pre.status}"

        _new_form(page, "cbt-branch", "slug")
        # The Link must have SURVIVED. Asserting it here turns a silently-empty
        # link into an obvious failure instead of a Missing-Fields hang twenty
        # seconds later — and the fill goes through the shared helper because
        # THIS is the assertion Backlog B6 breaks intermittently under 3-worker
        # load. The helper carries one full re-fill plus the diagnostics; read
        # its docstring before touching either.
        gestures.fill_link(page, "company", SLUG)
        gestures.fill(page, "slug", BRANCH_SLUG)
        gestures.fill(page, "branch_name", "Demo Club Main")
        gestures.fill(page, "address_text", "Cebu Business Park")
        # Drive the picker's OWN click handler (frm._cbt_map, section-3) rather
        # than writing lat/lng behind its back — a pin set by bypassing the
        # picker would not prove the picker works. Exact coordinates matter
        # here (Cebu, far from every seeded pin), so the handler is fired with
        # a latlng; test_branch_form_geo covers the pixel-click path.
        page.wait_for_selector(".cbt-branch-map.leaflet-container", timeout=20000)
        page.evaluate(
            """([lat, lng]) => {
                cur_frm._cbt_map.fire('click', { latlng: { lat: lat, lng: lng } });
            }""",
            [CEBU_LAT, CEBU_LNG],
        )
        page.wait_for_function(
            "() => cur_frm.doc.latitude && cur_frm.doc.longitude", timeout=15000
        )
        _save_new_form(page)
        assert page.evaluate("() => cur_frm.doc.name") == BRANCH_NAME

        # --- a court with a rate (nothing is bookable without one) ----------
        _new_form(page, "cbt-court", "court_name")
        # The court form is the surface Backlog B17 claimed no staff member could
        # save. Driving it by gesture is what makes this row able to FAIL on that —
        # the model write it replaces could not.
        gestures.fill_link(page, "branch", BRANCH_NAME)
        gestures.fill(page, "court_name", "Court 1")
        gestures.fill(page, "court_type", "Pickleball")
        gestures.fill(page, "hourly_rate", 200)
        _save_new_form(page)
        assert page.evaluate("() => cur_frm.doc.name") == COURT_NAME

    def test_checklist_turns_green_and_hands_over_a_working_deep_link(
        self, page: Page, platform_seat: str
    ):
        """The point of the whole flow: the checklist is ready and the URL it
        prints is one a shopper can actually book from."""
        login_as(page, platform_seat)
        page.goto(f"/desk/cbt-company/{SLUG}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("() => window.cur_frm && cur_frm.doc.name", timeout=30000)

        # Finish the remaining human steps the way an admin would. Office hours
        # are one of them: they decide how long staff get to verify a transfer
        # proof, so a tenant without them is genuinely not ready to trade — the
        # checklist refuses to go green until they exist, which is the whole
        # point of deriving it from the data instead of ticking a box.
        # Office hours are entered ROW BY ROW through the grid's own Add Row button
        # and its cells. The clear_table + add_child chain this replaces built the
        # rows in the model and proved nothing about whether an admin can actually
        # add one — which matters here precisely because the checklist refuses to go
        # green without these rows, so the grid is on the critical path to trading.
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            idx = gestures.grid_add_row(page, "office_hours")
            gestures.grid_fill(
                page,
                "office_hours",
                idx,
                {
                    "day": day,
                    "is_open": 1,
                    "opening_time": "09:00:00",
                    "closing_time": "18:00:00",
                },
            )
        gestures.fill(page, "tin", "123-456-789-000")
        gestures.fill(page, "payment_instructions", "GCash 0917-000-0000 (Demo Club)")
        gestures.save_form(page)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.cur_frm && cur_frm.doc.name", timeout=30000)

        _open_onboarding_tab(page)
        checklist = page.locator("[data-testid='onboarding-checklist']")
        expect(checklist).to_be_visible(timeout=20000)
        expect(checklist).to_have_attribute("data-ready", "1", timeout=20000)
        expect(checklist).to_contain_text(f"/book?c={SLUG}")

    def test_the_new_tenant_is_bookable_on_the_public_marketplace(self, page: Page):
        """A guest following the printed link reaches a real, bookable grid —
        which is the only proof that onboarding actually finished."""
        page.goto(
            f"/book?c={SLUG}&b={BRANCH_SLUG}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        expect(page.locator("#cbt-company-name")).to_have_text("Demo Club")
        expect(page.locator("[data-testid='facility-unavailable']")).to_have_count(0)
        page.wait_for_selector(".cbt-matrix .cbt-slot", timeout=20000)
