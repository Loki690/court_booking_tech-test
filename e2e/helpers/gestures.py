"""
Drive frappe forms and dialogs the way a human does — clicking and typing.

WHY THIS EXISTS. `cur_frm.set_value(...)` inside `page.evaluate` writes the model
directly. It sails past a field that is hidden, read-only, or behind a `depends_on` that
never fires, so the test goes green on a form nobody can operate. That is not
hypothetical: CBT Open Play Session shipped a desk form no operator could save (a
mandatory, read-only, HIDDEN `company`) and the suite could not fail on it, because
nothing drove that form by hand. `tests/test_e2e_gesture_discipline.py` now enforces the
rule; this module is what makes obeying it cheap.

MEASURED DOM FACTS (v16, captured from a live form — do not "simplify" these away):

  * Every control wrapper carries BOTH `data-fieldname` and `data-fieldtype`, so a
    generic filler can dispatch on type without the caller knowing the schema.
  * Awesomplete options are `<div role="option">` nested INSIDE the
    `<ul role="listbox">` — NOT `<li>`. Every `li` selector misses structurally.
  * `aria-activedescendant` names an id (`awesomplete_list_N_item_0`) that no option
    element actually carries, so matching by id is impossible. Match on role + text.
  * The list is reached via the input's `aria-owns`; it lives under
    `.frappe-control > .control-input > .link-field.ui-front > .awesomplete > ul`.
  * frappe appends three `__link_option` pseudo-entries ("Create a new ...",
    "Advanced Search"). With `autoFirst`, one blind ArrowDown picks the wrong row.
  * Awesomplete debounces ~500ms: the FIRST non-empty option list can still be the
    UNFILTERED one. Matching the option by its visible TEXT is immune to that.
  * A title-link doctype (`show_title_field_in_link:1`) renders the TITLE, while the
    server searches `name` — so the QUERY you type and the LABEL you click are
    different strings. CBT Branch is one: type `AYALA-bgc`, click `BGC Courts`.
  * Closed frappe dialogs stay in the DOM, so every dialog locator must be scoped to
    `.modal.show` or it can match a PREVIOUS dialog and "succeed" instantly.
"""

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

DIALOG = ".modal.show"

# "the save has committed", for BOTH a new and an existing document.
#
# `!__islocal` alone is a trap: it is only ever true BEFORE a new doc's first save, so
# on an EXISTING document it is already false and the wait returns instantly — the test
# then reads the doc back before the round trip finished and sees the OLD value. (On
# this app that surfaced as a `KeyError` rather than a wrong value, because
# `/api/resource` drops NULL keys.) `__unsaved` is the half that covers an edit: frappe
# sets it on any dirty field and clears it when the save lands.
_SAVED = (
    "() => window.cur_frm && cur_frm.doc"
    " && !cur_frm.doc.__islocal && !cur_frm.doc.__unsaved"
)

# Frappe control types whose value is typed into a plain <input>/<textarea>.
_TEXTLIKE = {
    "Data", "Currency", "Float", "Int", "Percent", "Small Text", "Text",
    "Text Editor", "Date", "Time", "Datetime", "Duration", "Password", "Phone",
    "Read Only",
}

# Frappe control types whose value is a file behind the Attach button.
_ATTACHISH = {"Attach", "Attach Image"}


def control(page: Page, fieldname: str, scope: str = ""):
    sel = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    return page.locator(sel).first


def field_type(page: Page, fieldname: str, scope: str = "") -> str:
    return control(page, fieldname, scope).get_attribute("data-fieldtype") or ""


def fill_link(
    page: Page,
    fieldname: str,
    query: str,
    label: str | None = None,
    scope: str = "",
    timeout: int = 20000,
) -> str:
    """Type into a Link control and CLICK the matching option.

    `query` is what the server searches (usually the doc NAME). `label` is what the
    option renders. On a title-link doctype the two differ — but the option's second
    line is built from `search_fields` and carries the name anyway
    (`BGC Courts` / `AYALA-bgc, ayala-courts`), so an option matches if EITHER string
    appears in it. Passing `label` is therefore optional and only sharpens the match;
    the frappe pseudo-entries ("Create a new ...", "Advanced Search") contain neither.

    Returns the clicked option's text so a changed label surfaces as a changed string
    in the failure, not as a mystery timeout.
    """
    needles = [n for n in (label, query) if n]
    root = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    box = page.locator(f"{root} input").first
    box.click()
    box.fill("")
    box.press_sequentially(query, delay=60)

    owns = box.get_attribute("aria-owns")
    scopes = ([f"ul#{owns} [role='option']"] if owns else []) + [
        f"{root} [role='option']",
        f"{scope} ul[role='listbox'] [role='option']".strip(),
    ]
    # Wait until the FILTERED list actually contains a match. Awesomplete debounces
    # ~500ms, and `link_field_results_limit` caps the list at 10 rows — so the first
    # non-empty list is routinely the UNFILTERED one, and "wait for any option, then
    # match once" silently reads the wrong list whenever the target is not in the first
    # page of results. Waiting on the match itself is immune to the timing.
    try:
        page.wait_for_function(
            """([sels, needles]) => {
                for (const sel of sels) {
                    for (const node of document.querySelectorAll(sel)) {
                        const text = (node.innerText || '').toLowerCase();
                        if (needles.some((n) => text.includes(n))) return true;
                    }
                }
                return false;
            }""",
            arg=[scopes, [n.lower() for n in needles]],
            timeout=timeout,
        )
    except PlaywrightError:
        pass  # fall through to the enumerating diagnostic below

    tried = {}
    for sel in scopes:
        options = page.locator(sel)
        if not options.count():
            tried[sel] = "<none present>"
            continue
        texts = []
        for i in range(options.count()):
            try:
                texts.append((options.nth(i).inner_text() or "").strip())
            except PlaywrightError:
                texts.append("<stale>")
        tried[sel] = texts
        for i, text in enumerate(texts):
            if any(n.lower() in text.lower() for n in needles):
                options.nth(i).click()
                # Prove it COMMITTED. A Link that silently fails to land shows up
                # thirty seconds later as a Missing Fields hang, far from the cause.
                page.wait_for_function(
                    """([fieldname, scope]) => {
                        const host = scope ? document.querySelector(scope) : document;
                        const el = (host || document).querySelector(
                            `.frappe-control[data-fieldname='${fieldname}'] input`);
                        return !!el && el.value.trim().length > 0;
                    }""",
                    arg=[fieldname, scope or None],
                    timeout=10000,
                )
                return text.replace("\n", " | ")
    raise AssertionError(
        f"no option matching {needles!r} for {fieldname!r} (typed {query!r}, "
        f"aria-owns={owns!r}); options seen: {tried}"
    )


def select(page: Page, fieldname: str, value: str, scope: str = ""):
    """Choose a <select> option by value — a real change event, not a model write."""
    root = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    page.locator(f"{root} select").first.select_option(value)


def check(page: Page, fieldname: str, on: bool = True, scope: str = ""):
    """Click a Check control only if it is not already in the wanted state.

    Clicking blindly TOGGLES, so a second call would undo the first. This is the
    control that matters most: a Check driving `depends_on` decides whether other
    fields exist in the layout at all, which is exactly what a model write skips.
    """
    root = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    box = page.locator(f"{root} input[type='checkbox']").first
    box.wait_for(state="visible", timeout=15000)
    if box.is_checked() != on:
        box.click()


def type_value(page: Page, fieldname: str, value, scope: str = ""):
    """Type into a text-like control (Data / Currency / Int / Time / Date / ...).

    The trailing `change` is load-bearing. Playwright's `fill()` fires only an INPUT
    event, while frappe's controls commit to the model on CHANGE — which a browser
    normally raises on blur. Without this, a typed value reaches the DOM and never
    reaches `cur_frm.doc`, and the test "works" only when some later click happens to
    blur the field. That is how the quick-book dialog's re-quote silently stopped
    firing: nothing blurred `number_of_slots` before the assertion.

    `dispatch_event` rather than a Tab press on purpose: it commits without moving
    focus, so it cannot reformat or clear a Date control the way a real blur can.
    """
    root = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    target = page.locator(f"{root} input, {root} textarea").first
    target.fill("" if value is None else str(value))
    target.dispatch_event("change")


def type_date(page: Page, fieldname: str, iso_date: str, scope: str = ""):
    """Type an ISO date into a Date control using the SITE's user format.

    A Date control is a text input parsed against `date_format`, so typing `2026-11-16`
    into a `mm-dd-yyyy` site is silently misread. The format is read from boot rather
    than hardcoded, because it is a site setting and this bench runs `mm-dd-yyyy`.
    """
    fmt = page.evaluate(
        "() => (frappe.boot.sysdefaults && frappe.boot.sysdefaults.date_format) "
        "|| 'yyyy-mm-dd'"
    )
    year, month, day = iso_date.split("-")
    text = fmt.replace("yyyy", year).replace("mm", month).replace("dd", day)
    type_value(page, fieldname, text, scope=scope)


def fill(page: Page, fieldname: str, value, scope: str = "", label: str | None = None):
    """Dispatch on the control's own `data-fieldtype`.

    Callers do not need to know the schema — which is the point, because the schema is
    what drifts. An unknown fieldtype fails loudly rather than silently doing nothing.
    """
    ftype = field_type(page, fieldname, scope)
    if ftype == "Link":
        return fill_link(page, fieldname, str(value), label=label, scope=scope)
    if ftype == "Select":
        return select(page, fieldname, str(value), scope=scope)
    if ftype == "Check":
        return check(page, fieldname, bool(value), scope=scope)
    if ftype == "Date" and isinstance(value, str) and value.count("-") == 2 and len(value) == 10:
        return type_date(page, fieldname, value, scope=scope)
    if ftype in _ATTACHISH:
        return attach_file(page, fieldname, value, scope=scope)
    if ftype in _TEXTLIKE:
        return type_value(page, fieldname, value, scope=scope)
    raise AssertionError(
        f"gestures.fill has no handler for fieldtype {ftype!r} (field {fieldname!r}); "
        "add one rather than falling back to a model write"
    )


def fill_dialog(page: Page, values: dict, order: list | None = None):
    """Fill the OPEN dialog by gesture. `order` forces sequence where it matters.

    Sequence is load-bearing when a field drives `depends_on`: the controls it reveals
    do not exist in the layout until it flips, so they cannot be filled before it.
    """
    for fieldname in (order or list(values)):
        if fieldname in values:
            fill(page, fieldname, values[fieldname], scope=DIALOG)


def click_form_action(page: Page, label: str, group: str | None = None, timeout: int = 15000):
    """Click a form's custom button, through its group menu when it has one.

    `frm.add_custom_button(label, fn, group)` renders into `.custom-actions`: with a
    group it becomes a dropdown (`.inner-group-button[data-label=<group>]`) holding an
    `<a>` per action; without one it is a plain button. Driving this is the only way to
    prove the action is REACHABLE — calling its handler proves the handler works and
    says nothing about whether an operator can find it.
    """
    # `:is(...)`, never a bare comma. A comma splits the ENTIRE selector, so
    # ".custom-actions, .page-actions .inner-group-button ... a" means
    # ".custom-actions" OR "the item", and `.first` resolves to the CONTAINER —
    # whose centre happens to sit on the group button. That made the first click
    # open the menu and the second click toggle it shut, so the action never fired
    # and nothing failed until the confirm dialog was missing.
    actions = ":is(.custom-actions, .page-actions)"
    # Never click a form action through a closing modal's backdrop (section-29).
    try:
        page.wait_for_selector(".modal-backdrop", state="detached", timeout=timeout)
    except PlaywrightError:
        raise AssertionError(
            f"a modal backdrop is still covering the page, so {label!r} cannot be "
            "clicked. A dialog was left open by an earlier step."
        ) from None
    if group:
        opener = page.locator(
            f"{actions} .inner-group-button[data-label='{group}'] button"
        ).first
        try:
            opener.wait_for(state="visible", timeout=timeout)
        except PlaywrightError:
            raise AssertionError(
                f"no form-action group labelled {group!r}. Actions DOM: "
                f"{_actions_dump(page)}"
            ) from None
        opener.click()
        # Wait for the dropdown to actually OPEN before reaching for the item.
        # Bootstrap toggles it on click and the menu carries no `.show` until then,
        # so clicking straight through races the toggle and silently no-ops.
        page.wait_for_selector(
            f"{actions} .inner-group-button[data-label='{group}'] "
            "button[aria-expanded='true']",
            timeout=timeout,
        )
        # By data-label, not by text: frappe stamps the action's own label there,
        # and the visible text carries an icon and whitespace.
        item = page.locator(
            f"{actions} .inner-group-button[data-label='{group}'] "
            f"a.dropdown-item[data-label='{label}']"
        ).first
    else:
        item = page.locator(f"{actions} button:has-text('{label}')").first
    try:
        item.wait_for(state="visible", timeout=timeout)
    except PlaywrightError:
        raise AssertionError(
            f"no form action labelled {label!r} under group {group!r}. Actions DOM: "
            f"{_actions_dump(page)}"
        ) from None
    import os as _os

    _dbg = _os.environ.get("CBT_ACTION_DEBUG")
    if _dbg:
        page.screenshot(path=f"{_dbg}/action_menu_open.png", full_page=True)
    item.click()
    if _dbg:
        page.screenshot(path=f"{_dbg}/action_after_click.png", full_page=True)


def confirm_yes(page: Page, timeout: int = 15000):
    """Accept frappe's confirm modal by CLICKING its primary button.

    Tests routinely stub `frappe.confirm = (_msg, onyes) => onyes()`, which skips the
    dialog entirely — so nothing ever proves the confirm renders, or that its accept
    button is reachable. This clicks it for real.
    """
    modal = page.locator(".modal.show").last
    try:
        modal.wait_for(state="visible", timeout=timeout)
    except PlaywrightError:
        raise AssertionError(
            "no confirm dialog appeared. Modals on page: "
            + str(
                page.evaluate(
                    """() => Array.from(document.querySelectorAll('.modal')).map(
                        (m) => ({cls: m.className,
                                 text: (m.innerText || '').trim().slice(0, 120)}))"""
                )
            )
            + "; cur_dialog: "
            + str(page.evaluate("() => !!window.cur_dialog"))
            + "; actions DOM: "
            + _actions_dump(page)
        ) from None
    button = modal.locator(".btn-primary, .btn-modal-primary").first
    button.wait_for(state="visible", timeout=timeout)
    button.click()


def _actions_dump(page: Page) -> str:
    return str(
        page.evaluate(
            """() => Array.from(
                document.querySelectorAll('.custom-actions, .page-actions'))
                .map((el) => el.innerHTML.slice(0, 900))"""
        )
    )


def grid_row_count(page: Page, grid_fieldname: str, scope: str = "") -> int:
    root = f"{scope} .frappe-control[data-fieldname='{grid_fieldname}']".strip()
    return page.locator(f"{root} .grid-body .grid-row").count()


def grid_add_row(page: Page, grid_fieldname: str, scope: str = "") -> int:
    """Click the grid's own Add Row button. Returns the new row's 1-based idx."""
    root = f"{scope} .frappe-control[data-fieldname='{grid_fieldname}']".strip()
    before = grid_row_count(page, grid_fieldname, scope)
    button = page.locator(f"{root} .grid-add-row").first
    button.wait_for(state="visible", timeout=15000)
    button.click()
    page.wait_for_function(
        """([sel, before]) => document.querySelectorAll(
            `${sel} .grid-body .grid-row`).length === before + 1""",
        arg=[root, before],
        timeout=15000,
    )
    return before + 1


def grid_fill(page: Page, grid_fieldname: str, idx: int, values: dict, scope: str = ""):
    """Fill one grid row by clicking into each cell, the way a human edits a table.

    A grid cell is NOT a form control until you click it: frappe renders a static
    `.grid-static-col` and swaps in a real `.frappe-control` on click. So each field is
    clicked, then filled through the normal dispatcher scoped to that row.

    Checks are the exception — a grid renders them as a live checkbox in the static
    column, so they are clicked in place.
    """
    root = f"{scope} .frappe-control[data-fieldname='{grid_fieldname}']".strip()
    row = f"{root} .grid-row[data-idx='{idx}']"
    for field, value in values.items():
        cell = page.locator(f"{row} [data-fieldname='{field}']").first
        try:
            cell.wait_for(state="visible", timeout=10000)
        except PlaywrightError:
            raise AssertionError(
                f"grid {grid_fieldname!r} row {idx} has no visible cell for {field!r}. "
                f"Grid DOM: {_grid_dump(page, root)}"
            ) from None
        checkbox = page.locator(f"{row} [data-fieldname='{field}'] input[type='checkbox']")
        if checkbox.count():
            if checkbox.first.is_checked() != bool(value):
                checkbox.first.click()
            continue
        cell.click()
        control = page.locator(f"{row} .frappe-control[data-fieldname='{field}']")
        if not control.count():
            raise AssertionError(
                f"clicking the {field!r} cell of {grid_fieldname!r} row {idx} did not "
                f"open an editable control. Grid DOM: {_grid_dump(page, root)}"
            )
        fill(page, field, value, scope=row)


def _grid_dump(page: Page, root: str) -> str:
    """What the grid actually contains — so a wrong assumption reports itself."""
    return str(
        page.evaluate(
            """(root) => {
                const g = document.querySelector(root);
                if (!g) return 'grid control not found';
                const row = g.querySelector('.grid-body .grid-row');
                return {
                    addRowButtons: Array.from(g.querySelectorAll('button, .grid-add-row'))
                        .map((b) => (b.className || '') + '|' + (b.innerText || '').trim())
                        .slice(0, 6),
                    rowCount: g.querySelectorAll('.grid-body .grid-row').length,
                    firstRowHTML: row ? row.outerHTML.slice(0, 1200) : null,
                };
            }""",
            root,
        )
    )


def save_form(page: Page, timeout: int = 30000) -> str:
    """Ctrl+S — the keystroke a human uses — and wait for the document to commit.

    A FIRST save renames the route, and that navigation can destroy the JS execution
    context before `evaluate` returns. That is the save WORKING, so it is caught rather
    than raised. Returns the saved docname.
    """
    page.keyboard.press("Control+s")
    try:
        page.wait_for_function(
            _SAVED,
            timeout=timeout,
        )
        return page.evaluate("() => cur_frm.doc.name")
    except PlaywrightError as exc:
        if "Execution context was destroyed" not in str(exc):
            raise
        page.wait_for_function(
            _SAVED,
            timeout=timeout,
        )
        return page.evaluate("() => cur_frm.doc.name")


def save_form_expecting_failure(page: Page, settle_ms: int = 4000) -> dict:
    """For rows that assert a save is REFUSED. Returns the visible outcome."""
    page.keyboard.press("Control+s")
    page.wait_for_timeout(settle_ms)
    return page.evaluate(
        """() => ({
            is_new: (window.cur_frm && cur_frm.doc)
                ? (cur_frm.doc.__islocal ? 1 : 0) : null,
            docname: (window.cur_frm && cur_frm.doc) ? cur_frm.doc.name : null,
            modals: Array.from(document.querySelectorAll('.modal.show'))
                .map((m) => (m.innerText || '').trim()).filter(Boolean).slice(0, 3),
        })"""
    )


def grid_fill_row_form(page: Page, grid_fieldname: str, idx: int, values: dict, scope: str = ""):
    """Fill a grid row through its EXPANDED row form, then collapse it again.

    Some grids open the row form on click, which sets the inline data-row to
    display:none — so `grid_fill` can place the first value and then lose the rest.
    """
    root = f"{scope} .frappe-control[data-fieldname='{grid_fieldname}']".strip()
    row = page.locator(f"{root} .grid-body .grid-row").nth(idx - 1)
    if "grid-row-open" not in (row.get_attribute("class") or ""):
        row.locator(".btn-open-row").first.click()
    form = f"{root} .grid-row-open .grid-form-body"
    page.locator(form).first.wait_for(state="visible", timeout=15000)
    for fieldname, value in values.items():
        fill(page, fieldname, value, scope=form)
    page.locator(f"{root} .grid-collapse-row").first.click()
    page.wait_for_function(
        "(sel) => !document.querySelector(sel + ' .grid-row-open')",
        arg=root,
        timeout=15000,
    )


def grid_remove_row(page: Page, grid_fieldname: str, idx: int, scope: str = ""):
    """Tick a grid row's own checkbox and press the grid's Delete, as a person does.

    NONE is otherwise unreachable on any grid the server pre-fills — CBT Branch
    appends seven Court Hours rows to every new branch.
    """
    root = f"{scope} .frappe-control[data-fieldname='{grid_fieldname}']".strip()
    before = grid_row_count(page, grid_fieldname, scope)
    row = page.locator(f"{root} .grid-body .grid-row").nth(idx - 1)
    row.locator(".grid-row-check").first.click()
    remove = page.locator(f"{root} .grid-remove-rows, {root} .grid-footer .btn-danger")
    assert remove.count(), (
        f"{grid_fieldname}: no Delete control appeared after ticking row {idx} — "
        f"{_grid_dump(page, grid_fieldname, scope)}"
    )
    remove.first.click()
    page.wait_for_function(
        "(a) => document.querySelectorAll(a.sel + ' .grid-body .grid-row').length === a.n",
        arg={"sel": root, "n": before - 1},
        timeout=15000,
    )


def dismiss_modals(page: Page, timeout: int = 20000):
    """Close every open dialog by CLICKING its close control, then prove none is left.

    Escape is not reliable on frappe's msgprint: it leaves the modal up often enough
    to make any test that types afterwards intermittently red, which is worse than
    a test that is simply broken.
    """
    deadline = time.time() + (timeout / 1000.0)
    while time.time() < deadline:
        shown = page.locator(f"{DIALOG} >> visible=true")
        if not shown.count():
            break
        closer = shown.last.locator(
            ".btn-modal-close, .modal-header .close, button[data-dismiss='modal']"
        )
        if closer.count():
            closer.first.click()
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    page.wait_for_function("() => !document.querySelector('.modal.show')", timeout=10000)
    page.locator("#freeze").wait_for(state="hidden", timeout=timeout)


def field_is_on_screen(page: Page, fieldname: str, scope: str = "") -> bool:
    """Is the control WRAPPER visible? frappe hides the wrapper, not the <input>.

    Asserting on the input alone returns a hidden-but-present element for every
    `depends_on` field, which is how "it disappeared" quietly becomes "it passed".
    """
    root = control(page, fieldname, scope)
    return bool(root.count()) and root.is_visible()


def settle_attach(page: Page, fieldname: str, scope: str = "", timeout: int = 20000):
    """Wait until the control has RENDERED one state or the other before reading it.

    Both states are absent for a beat after a reload, so an immediate read returns
    "empty" for a field that holds a file — a race that makes the empty-state
    assertion pass on a full field. Found by deliberately breaking the clear step.
    """
    sel = f"{scope} .frappe-control[data-fieldname='{fieldname}']".strip()
    page.wait_for_function(
        """(sel) => {
            const r = document.querySelector(sel);
            const vis = (e) => !!(e && e.offsetParent !== null);
            return !!r && (vis(r.querySelector('a.attached-file-link'))
                || vis(r.querySelector('button.btn-attach')));
        }""",
        arg=sel,
        timeout=timeout,
    )


def attached_url(page: Page, fieldname: str, scope: str = ""):
    """The file URL a person can READ on the form, or None when the field is empty.

    Reads the rendered link, never the model — the user's rule is that the screen is
    the claim (`attach.js:15-26` builds `.attached-file > a.attached-file-link`).
    """
    settle_attach(page, fieldname, scope)
    link = control(page, fieldname, scope).locator("a.attached-file-link").first
    if not link.count() or not link.is_visible():
        return None
    return (link.get_attribute("href") or link.inner_text() or "").strip()


def attach_is_empty(page: Page, fieldname: str, scope: str = "") -> bool:
    """The empty state a person sees: the Attach BUTTON is back on screen."""
    settle_attach(page, fieldname, scope)
    button = control(page, fieldname, scope).locator("button.btn-attach").first
    return bool(button.count()) and button.is_visible()


def attach_file(page: Page, fieldname: str, path, scope: str = "", timeout: int = 45000):
    """Attach a file the way a person does: Attach -> My Device -> the OS picker.

    NOT `set_input_files` on the hidden input — that is the model-write of file fields
    and stays green when the button, the dialog or the tab breaks. frappe saves the
    form itself on completion (`attach.js:137-144`), so never press Ctrl+S after this.
    """
    page.locator("#freeze").wait_for(state="hidden", timeout=timeout)
    settle_attach(page, fieldname, scope, timeout=timeout)
    control(page, fieldname, scope).locator("button.btn-attach").first.click()
    dialog = page.locator(DIALOG).last
    dialog.wait_for(state="visible", timeout=20000)
    with page.expect_file_chooser(timeout=timeout) as chooser:
        dialog.get_by_text("My Device", exact=True).first.click()
    chooser.value.set_files(str(path))
    confirm = dialog.get_by_role("button", name="Upload")
    try:
        confirm.first.wait_for(state="visible", timeout=5000)
        confirm.first.click()
    except PlaywrightError:
        pass
    dialog.wait_for(state="detached", timeout=timeout)
    page.wait_for_function(_SAVED, timeout=timeout)


def clear_attachment(page: Page, fieldname: str, scope: str = "", timeout: int = 30000):
    """Clear a file the way a person does: Clear, then accept frappe's confirm.

    `clear_attachment()` opens a `frappe.confirm` (`attach.js:36-55`) and then saves
    the form itself. A test that clicks Clear and waits for the value to drop hangs
    on the un-accepted modal — which is exactly how the first version of this failed.
    """
    control(page, fieldname, scope).locator("[data-action='clear_attachment']").first.click()
    confirm_yes(page, timeout=timeout)
    page.wait_for_function(_SAVED, timeout=timeout)


def read_value(page: Page, fieldname: str, scope: str = ""):
    """Read what a HUMAN sees on the form, dispatching on the control's own type.

    ⛔ Never reads `cur_frm.doc`. Reading the model is what keeps a suite green while
    the screen is wrong, which is the whole reason these safeguard rows exist.
    """
    ftype = field_type(page, fieldname, scope)
    root = control(page, fieldname, scope)
    if ftype in _ATTACHISH:
        return attached_url(page, fieldname, scope)
    if ftype == "Check":
        return root.locator("input[type='checkbox']").first.is_checked()
    if ftype == "Select":
        return (root.locator("select option:checked").first.inner_text() or "").strip()
    box = root.locator("input, textarea")
    if box.count():
        return box.first.input_value()
    # A read_only control renders static text instead of an input.
    return (root.locator(".control-value").first.inner_text() or "").strip()
