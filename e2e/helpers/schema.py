"""Read a DocType's field list from the JSON ON DISK, never from the browser.

`frappe.get_meta` in the page is filtered by the seat's own permissions, so an
enumerator built on it shares the filter it is meant to police. See section-30.
"""

import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]

# frappe fieldtypes that hold no value of their own — pure layout or display.
LAYOUT_TYPES = frozenset({
    "Section Break", "Column Break", "Tab Break", "HTML", "Button",
    "Image", "Fold", "Heading",
})

_CACHE: dict = {}


def snake(doctype: str) -> str:
    return doctype.strip().lower().replace(" ", "_").replace("-", "_")


def json_path(doctype: str) -> Path:
    """Locate `<module>/doctype/<snake>/<snake>.json` without hardcoding the module."""
    name = snake(doctype)
    hits = sorted(APP_ROOT.glob(f"**/doctype/{name}/{name}.json"))
    if not hits:
        raise AssertionError(
            f"no DocType JSON on disk for {doctype!r} (looked for "
            f"**/doctype/{name}/{name}.json under {APP_ROOT})"
        )
    return hits[0]


def definition(doctype: str) -> dict:
    if doctype not in _CACHE:
        _CACHE[doctype] = json.loads(json_path(doctype).read_text(encoding="utf-8"))
    return _CACHE[doctype]


def all_entries(doctype: str) -> list:
    """Every entry in the JSON's `fields` array, layout breaks included."""
    return list(definition(doctype).get("fields") or [])


def data_fields(doctype: str) -> list:
    """The entries that actually hold a value — what "ALL FIELDS" has to mean."""
    return [f for f in all_entries(doctype) if f.get("fieldtype") not in LAYOUT_TYPES]


def child_doctype(field: dict):
    """The child DocType behind a Table field, so the loop can recurse into grids."""
    if field.get("fieldtype") in ("Table", "Table MultiSelect"):
        return (field.get("options") or "").strip() or None
    return None


def is_single(doctype: str) -> bool:
    return bool(definition(doctype).get("issingle"))


def counts(doctype: str) -> dict:
    """The arithmetic a plan can be checked against, derived rather than claimed."""
    entries = all_entries(doctype)
    data = data_fields(doctype)
    return {
        "entries": len(entries),
        "layout": len(entries) - len(data),
        "data": len(data),
        "operable": len([f for f in data if not f.get("hidden") and not f.get("read_only")]),
    }
