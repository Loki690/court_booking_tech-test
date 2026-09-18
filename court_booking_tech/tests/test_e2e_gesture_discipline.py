"""
Gesture discipline for the Playwright E2E suite.

THE DEFECT THIS PREVENTS. A test that drives a form or dialog by writing the model —
`cur_frm.set_value(...)` inside `page.evaluate` — proves the SERVER accepted the value.
It proves nothing about whether a human can produce it. JS writes straight past a field
that is hidden, read-only, or behind a `depends_on` that never fires, so the test goes
green on a form nobody can operate. That is not hypothetical here: CBT Open Play
Session's desk form could not be saved by any operator (a mandatory, read-only, hidden
`company`), and no E2E test could fail on it, because nothing drove that form by hand.

WHAT IS BANNED — form/dialog MODEL WRITES only:

    cur_frm.set_value / frm.set_value / cur_dialog.set_value / dialog.set_value
    frappe.model.set_value / frappe.model.add_child
    cur_frm.add_child / clear_table  (child-table rows built without touching the grid)
    cur_frm.save() / frm.save()

`add_child` and `clear_table` were added after the first report-only run showed the
rule missing them: `test_01` built five office-hours rows with `clear_table` +
`add_child` and the checker said nothing, even though a grid populated that way proves
nothing about whether its Add-row button, cells or option lists work. Same defect
class, so the same ban.

WHAT IS NOT BANNED, deliberately. `frappe.call`, `frappe.xcall`, `frappe.client.*` and
`page.request.*` are test ARRANGE — seeding the state a test needs before the
interesting part. A static checker cannot tell arrange from act, and banning them would
produce false positives on every legitimate fixture. Reads are fine too: a
`page.evaluate` that returns state for an assertion is how you observe the app.

HOW IT READS THE FILES. The payloads are JavaScript inside Python string literals, so
this is substring matching over string nodes, not JS analysis. Three consequences that
are easy to get wrong and are handled explicitly below:
  * f-strings are `ast.JoinedStr` and keep their literal text in nested `Constant`
    nodes — a naive `Constant` walk misses every interpolated payload;
  * scoping the scan to "arguments of page.evaluate" is defeated by one indirection
    (`js = "cur_frm.set_value(...)"; page.evaluate(js)`), so EVERY string constant in
    the file is scanned regardless of where it is used;
  * docstrings are `Constant` nodes, so a docstring explaining this very rule would
    trip it — docstring positions are skipped.

THE RATCHET. `ALLOWLIST` maps (file, function) -> reason. An entry whose violation no
longer exists FAILS the suite, so the list can only shrink; growing it is a visible diff
in review. Delete the entry in the SAME commit as the conversion, never as a follow-up.

REPORT-ONLY. `bench --site <site> execute
court_booking_tech.tests.test_e2e_gesture_discipline.report` prints every violation
without asserting anything — use it to size the work before editing the allowlist.
"""

import ast
import os
import re
import unittest

# (relative posix path, qualified function name) -> why it is still here.
# EMPTY IS THE GOAL. Every entry is a form or dialog a human has never been proven able
# to operate.
ALLOWLIST: dict[tuple[str, str], str] = {}

# Model writes. Ordered longest-first so `cur_frm.set_value` is not also reported as
# `frm.set_value`.
BANNED = (
    ("frappe.model.set_value", re.compile(r"frappe\.model\.set_value\s*\(")),
    ("frappe.model.add_child", re.compile(r"frappe\.model\.add_child\s*\(")),
    ("cur_dialog.set_value", re.compile(r"\bcur_dialog\.set_value\s*\(")),
    ("cur_frm.set_value", re.compile(r"\bcur_frm\.set_value\s*\(")),
    ("cur_frm.add_child", re.compile(r"\bcur_frm\.add_child\s*\(")),
    ("cur_frm.clear_table", re.compile(r"\bcur_frm\.clear_table\s*\(")),
    ("cur_frm.save", re.compile(r"\bcur_frm\.save\s*\(")),
    ("dialog.set_value", re.compile(r"(?<!cur_)\bdialog\.set_value\s*\(")),
    ("frm.set_value", re.compile(r"(?<!cur_)\bfrm\.set_value\s*\(")),
    ("frm.add_child", re.compile(r"(?<!cur_)\bfrm\.add_child\s*\(")),
    ("frm.clear_table", re.compile(r"(?<!cur_)\bfrm\.clear_table\s*\(")),
    ("frm.save", re.compile(r"(?<!cur_)\bfrm\.save\s*\(")),
)

SCAN_DIRS = ("tests", "helpers")


def _docstring_nodes(tree):
    """Positions of every docstring, so prose about the rule cannot trip the rule."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                out.add((first.value.lineno, first.value.col_offset))
    return out


def _string_nodes(tree):
    """Every string literal with its enclosing qualified function name.

    Includes f-strings: `JoinedStr` holds its literal text in nested `Constant` nodes,
    which a plain `Constant` walk would attribute to nothing.
    """
    skip = _docstring_nodes(tree)
    found = []

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, scope + [child.name])
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if (child.lineno, child.col_offset) not in skip:
                    found.append((".".join(scope) or "<module>", child.lineno, child.value))
            elif isinstance(child, ast.JoinedStr):
                text = "".join(
                    v.value
                    for v in child.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                found.append((".".join(scope) or "<module>", child.lineno, text))
                continue
            walk(child, scope)

    walk(tree, [])
    return found


def scan_source(source: str, relpath: str):
    """Violations in one file's source. Returns [(relpath, func, line, token)]."""
    tree = ast.parse(source, filename=relpath)
    hits = []
    for func, lineno, text in _string_nodes(tree):
        for token, pattern in BANNED:
            if pattern.search(text):
                hits.append((relpath, func, lineno, token))
                break
    return hits


def _e2e_root():
    here = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.dirname(os.path.dirname(here))  # .../apps/court_booking_tech
    return os.path.join(app_root, "e2e")


def scan_suite():
    """Every violation in e2e/tests and e2e/helpers, plus the file count scanned."""
    root = _e2e_root()
    hits, scanned = [], 0
    for sub in SCAN_DIRS:
        directory = os.path.join(root, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            scanned += 1
            hits.extend(scan_source(source, f"{sub}/{name}"))
    return hits, scanned


def report():
    """Report-only. `bench execute ...test_e2e_gesture_discipline.report`"""
    hits, scanned = scan_suite()
    by_site = {}
    for relpath, func, lineno, token in hits:
        by_site.setdefault((relpath, func), []).append((lineno, token))
    print(f"scanned {scanned} files under e2e/{{{','.join(SCAN_DIRS)}}}")
    print(f"{len(hits)} violations across {len(by_site)} (file, function) sites\n")
    for (relpath, func), rows in sorted(by_site.items()):
        lines = ", ".join(f"L{ln}:{tok}" for ln, tok in rows)
        print(f"  {relpath}::{func}  ({len(rows)})  {lines}")
    print(f"\nallowlisted today: {len(ALLOWLIST)}")
    return {"violations": len(hits), "sites": len(by_site), "files_scanned": scanned}


class TestE2EGestureDiscipline(unittest.TestCase):
    """No module-scope work anywhere in this file.

    `tests/run_all.py` prints `SKIP <module>: <err>` and CONTINUES on an import error,
    so a module that raises at import time vanishes from a GREEN suite. Everything that
    can fail — path resolution, the directory walk, parsing — happens inside a test.
    """

    def test_suite_is_actually_scanned(self):
        """A checker that finds no files must fail, not pass."""
        hits, scanned = scan_suite()
        self.assertGreater(
            scanned,
            10,
            f"only {scanned} e2e files scanned from {_e2e_root()} — the checker is "
            "looking in the wrong place and would pass vacuously",
        )

    def test_detector_can_fail(self):
        """Permanent self-test: prove the detector still detects.

        A gate nobody has seen go red is not a gate. This keeps that true forever,
        unlike deleting an allowlist entry by hand once.
        """
        sample = (
            "def t(page):\n"
            "    page.evaluate(\"() => cur_frm.set_value('branch', 'X')\")\n"
        )
        self.assertEqual(
            [(r, f, t) for r, f, _, t in scan_source(sample, "synthetic.py")],
            [("synthetic.py", "t", "cur_frm.set_value")],
        )

        interpolated = (
            "def t(page, fieldname):\n"
            "    page.evaluate(f\"() => cur_frm.set_value('{fieldname}', 1)\")\n"
        )
        self.assertTrue(
            scan_source(interpolated, "synthetic.py"),
            "f-string payloads must be detected — they are ast.JoinedStr, not Constant",
        )

        indirect = (
            "def t(page):\n"
            "    js = \"() => cur_frm.save()\"\n"
            "    page.evaluate(js)\n"
        )
        self.assertTrue(
            scan_source(indirect, "synthetic.py"),
            "a payload assigned to a variable first must still be detected",
        )

    def test_detector_does_not_fire_on_arrange_or_prose(self):
        """False positives would make the gate something people switch off."""
        arrange = (
            "def t(page):\n"
            '    """Never use cur_frm.set_value here — that is the whole point."""\n'
            "    page.request.post('/api/method/x')\n"
            "    page.evaluate(\"() => frappe.call({method: 'x'})\")\n"
            "    page.evaluate(\"() => cur_frm.doc.company\")\n"
        )
        self.assertEqual(scan_source(arrange, "synthetic.py"), [])

    def test_no_unallowlisted_model_writes(self):
        """THE GATE."""
        hits, _ = scan_suite()
        offenders = sorted(
            {(relpath, func) for relpath, func, _, _ in hits} - set(ALLOWLIST)
        )
        self.assertEqual(
            offenders,
            [],
            "these E2E sites drive a form or dialog by writing the model instead of "
            "by clicking and typing, so they cannot fail on a form a human cannot "
            "operate. Convert them to gestures, or add an ALLOWLIST entry with a "
            f"reason: {offenders}",
        )

    def test_allowlist_has_no_stale_entries(self):
        """The ratchet — an entry must correspond to a violation that still exists."""
        hits, _ = scan_suite()
        live = {(relpath, func) for relpath, func, _, _ in hits}
        stale = sorted(set(ALLOWLIST) - live)
        self.assertEqual(
            stale,
            [],
            "these ALLOWLIST entries no longer match any violation — delete them so "
            f"the list can only shrink: {stale}",
        )
