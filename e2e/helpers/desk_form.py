"""
Shared desk-form fill for the files that stand up NEW records through the form
(file 01 platform onboarding, file 03 branch gate).

Both fill a new CBT Branch form as a chained `frm.set_value` sequence and then
assert the Company LINK actually landed. That assertion is deliberate: a Link
that silently fails to land makes the SAVE die on a client-side "Missing
Fields" check twenty seconds later, which reads as a hung save rather than as a
cause. It is also the assertion that fails intermittently under 3-worker load
(Backlog B6), which is why the fill lives here — with exactly one retry, and
with the diagnostics that should finally explain it.
"""
import json
import warnings

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# What the desk knows about the form at the moment a link did not land. This is
# the measurement, not decoration — see fill_and_confirm_link's docstring for
# what each field discriminates.
_FORM_STATE_JS = """
(fields) => {
    if (!window.cur_frm || !cur_frm.doc) return {cur_frm: null};
    const dt = cur_frm.doctype;
    const bucket = (window.locals && locals[dt]) || {};
    const values = {};
    (fields || []).forEach((f) => {
        values[f] = cur_frm.doc[f] === undefined ? '<undefined>' : cur_frm.doc[f];
    });
    return {
        doctype: dt,
        docname: cur_frm.docname,
        doc_name: cur_frm.doc.name,
        islocal: !!cur_frm.doc.__islocal,
        // frappe.model.set_value resolves its target as
        // `locals[doctype][docname]` (model.js:503) and SILENTLY no-ops —
        // resolving its promise — when that lookup misses. So this boolean is
        // the discriminator between "something cleared the value" and "the
        // write never happened".
        doc_is_the_locals_doc: bucket[cur_frm.docname] === cur_frm.doc,
        // More than one `new-<doctype>-*` here means the new-doc route was
        // resolved twice and the form is no longer the doc the chain wrote to.
        local_names: Object.keys(bucket),
        values: values,
    };
}
"""


def _state(page: Page, fields) -> str:
    try:
        return json.dumps(
            page.evaluate(_FORM_STATE_JS, list(fields)), default=str, sort_keys=True
        )
    except Exception as exc:  # never let diagnostics mask the real failure
        return f"<form state unavailable: {exc!r}>"


def wait_for_new_form(page: Page, doctype: str, timeout: int = 20000):
    """Wait until `/desk/<doctype>/new` has SETTLED on one document.

    This is Backlog B11 — PREVENTION — applied to one file rather than to the
    whole suite. `fill_and_confirm_link` below REPAIRS the same race by re-running
    the fill; this stops it happening by not touching the form until the router
    has stopped moving.

    Prevention is what a TYPED interaction needs, and that is why this exists.
    A chained `set_value` fill is idempotent and cheap to re-run, so repair is
    adequate there. Driving a real awesomplete dropdown is neither: if the route
    re-resolves mid-type, the input you typed into belongs to the discarded form
    and the option click writes to a doc nothing will read — which surfaces as a
    bare timeout with no discriminator, in a suite whose gate is clean
    determinism passes.

    Two conditions, and each rules out a different half of the race:

      * `locals[doctype][cur_frm.docname] === cur_frm.doc` — the IDENTITY check.
        `frappe.model.set_value` resolves its target as
        `locals[doctype][docname]` (model.js:503) and SILENTLY no-ops when that
        misses, so this is the direct proof that a write will reach the document
        the assertions then read. It is the same discriminator `_FORM_STATE_JS`
        collects at a failure.
      * `cur_frm.docname` unchanged across two animation frames — identity can
        hold for a doc the router is about to replace. Two frames is the smallest
        wait that observes the swap rather than guessing at a duration.

    Deliberately NOT adopted by files 01/03 in section-18: that would change how
    every desk form in the suite opens, and this section's gate is a clean
    full-suite pass. Doing it there is the rest of B11 — check the `[B6-RETRY]`
    count in the run logs first to confirm the race is still firing.
    """
    page.wait_for_function(
        """(doctype) => new Promise((resolve) => {
            const stamp = () => {
                if (!window.cur_frm || !cur_frm.doc) return null;
                if (cur_frm.doctype !== doctype) return null;
                const bucket = (window.locals && locals[doctype]) || {};
                if (bucket[cur_frm.docname] !== cur_frm.doc) return null;
                return cur_frm.docname;
            };
            const before = stamp();
            if (!before) { resolve(false); return; }
            requestAnimationFrame(() => requestAnimationFrame(() => {
                resolve(stamp() === before);
            }));
        })""",
        arg=doctype,
        timeout=timeout,
    )


def fill_and_confirm_link(
    page: Page,
    fill_js: str,
    fill_args,
    *,
    link_field: str,
    expected: str,
    fields,
    timeout: int = 15000,
):
    """Run a chained `frm.set_value` fill, then prove `link_field` survived.

    ONE retry on the confirm, and it re-runs the WHOLE fill rather than just
    the link.

    BACKLOG B6 — the recorded root cause was WRONG, and the correction matters
    more than the original note. Section-15 as-built 14 said the desk Link
    control validates through `frappe.client.validate_link_and_fetch` (a SEARCH,
    not a read), gets `{}` when the search misses a just-committed row under
    load, and silently blanks the field. The v16 source rules that out:

      * `frm.set_value` routes to `frappe.model.set_value` (form.js:1838),
        which writes `doc[key] = value` SYNCHRONOUSLY (model.js:520), before
        any trigger runs;
      * the link validation is fired from the model watcher as a BARE
        expression statement — `field.validate(value)` (form.js:296-300) — so
        its promise is discarded and its result can never be written back into
        `doc`.

    WHAT ACTUALLY HAPPENS — confirmed by this helper's own diagnostics on their
    first outing; section-17 as-built 1 carries the captured dump. **The new-doc
    route resolves TWICE, and the first write lands on the document that is then
    discarded.** `frm.set_value` passes `me.doc.name` into
    `frappe.model.set_value`, which resolves
    `doc = locals[doctype] && locals[doctype][docname]` (model.js:503), and each
    `.then(() => cur_frm.set_value(...))` re-reads the global `cur_frm`. So the
    first field goes to doc A, doc A leaves `locals`, and the rest go to doc B —
    the doc the assertion then reads.

    Three observations pin it, and each one kills an alternative: the missing
    field reads back `undefined`, not `null` or `""` (frappe CLEARING a field
    writes an empty value, so this was never assigned at all); the later fields
    of the same chain ARE present on the current doc; and an unrendered control
    is impossible, because `frm.set_value` throws for one (form.js:1848-1850),
    which would have failed the `page.evaluate` rather than the wait 15 seconds
    later. Note the consequence: the race is FORM-side, not search-side, so
    whether the link's target row is freshly created or long seeded is
    irrelevant — which is why file 03's assertion on a SEEDED company needs this
    too.

    Re-running the ENTIRE chain rather than just the link is the point: if the
    swap lands later in the chain then the fields after it are the missing ones,
    and re-setting only the link would trade a clean labelled failure for a
    Missing-Fields hang at save time. Repeating is safe — each `set_value`
    re-reads the form at call time, and model.js skips a field that already
    holds its value. The second miss is FATAL by design: the assertion exists to
    make a silently-empty Link obvious.

    This is a REPAIR, not a prevention. The fix that stops the race is to wait
    for the new-doc route to SETTLE before filling — tracked as Backlog B11, and
    deliberately not done in section-17 because it changes how every file opens a
    form, and that section's gate is three full-suite determinism passes.

    THE MARKER IS A WARNING, NOT A PRINT. The harness runs pytest without `-s`,
    so captured stdout is replayed only for FAILING tests — a marker on the
    (green) retry path would never reach the run log, defeating the point of
    having one. Warnings land in pytest's summary, which IS tee'd into
    `run_<UTC>.log`, and xdist forwards worker warnings to the controller. So
    `grep '\\[B6-RETRY\\]'` over the run logs counts occurrences for free, and
    the state dump beside each one should settle the mechanism on the next hit.
    """
    confirm_js = (
        f"(v) => window.cur_frm && cur_frm.doc && cur_frm.doc.{link_field} === v"
    )

    page.evaluate(fill_js, fill_args)
    try:
        page.wait_for_function(confirm_js, arg=expected, timeout=timeout)
        return
    except PlaywrightTimeoutError:
        pass

    first_miss = _state(page, fields)
    warnings.warn(
        f"[B6-RETRY] {link_field}={expected!r} did not land on the desk form — "
        f"re-running the whole fill once. Form state at the miss: {first_miss}"
    )

    page.evaluate(fill_js, fill_args)
    try:
        page.wait_for_function(confirm_js, arg=expected, timeout=timeout)
    except PlaywrightTimeoutError as exc:
        raise AssertionError(
            f"[B6-RETRY] {link_field}={expected!r} did not land even after one "
            f"full re-fill (Backlog B6). Fatal by design: a silently-empty "
            f"Link must be an obvious failure here, at the cause, not a "
            f"Missing-Fields hang at save time.\n"
            f"  at the first miss: {first_miss}\n"
            f"  after the re-fill: {_state(page, fields)}"
        ) from exc
