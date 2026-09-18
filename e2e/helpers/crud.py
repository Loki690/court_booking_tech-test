"""READ -> MODIFY(ALL FIELDS) -> READ, driven from the schema on disk.

Every field is enumerated from the JSON, given a strategy by fieldtype, and asserted
`after == typed AND after != before`. A field with no strategy FAILS. See section-30.
"""

from helpers import schema


def client_fieldnames(page, doctype: str) -> list:
    """What the BROWSER thinks the fields are — permission-filtered, hence suspect."""
    return page.evaluate(
        "(dt) => (frappe.get_meta(dt).fields || []).map((f) => f.fieldname)",
        doctype,
    )


def assert_client_meta_matches_disk(page, doctype: str):
    """A field in the file but missing from the client meta FAILS, never vanishes."""
    on_disk = [f["fieldname"] for f in schema.all_entries(doctype)]
    in_page = set(client_fieldnames(page, doctype) or [])
    missing = [f for f in on_disk if f not in in_page]
    assert not missing, (
        f"{doctype}: {len(missing)} field(s) are in the schema on disk and invisible "
        f"to this seat in the browser — coverage would have narrowed silently: {missing}"
    )


def _next_select(field: dict, before):
    real = [o for o in (field.get("options") or "").split("\n") if o.strip()]
    assert real, f"{field['fieldname']}: a Select with no options"
    for option in real:
        if option != (before or "").strip():
            return option
    return real[0]


def _next_number(field: dict, before):
    try:
        current = float(before or 0)
    except ValueError:
        current = 0.0
    nudged = current + 1
    if field.get("fieldtype") == "Percent" and nudged > 100:
        nudged = 1
    if field.get("fieldtype") == "Int":
        return int(nudged)
    return nudged


def value_for(field: dict, before, supplied: dict):
    """The value to type: caller's if given, else invented to differ from `before`."""
    name = field["fieldname"]
    if name in supplied:
        return supplied[name]
    ftype = field.get("fieldtype")
    if ftype == "Check":
        return not bool(before)
    if ftype == "Select":
        return _next_select(field, before)
    if ftype in ("Int", "Float", "Currency", "Percent"):
        return _next_number(field, before)
    if ftype in ("Data", "Small Text", "Text"):
        stem = (str(before or "").strip() or name)[:24]
        return f"{stem} edited" if not stem.endswith(" edited") else f"{stem} again"
    raise AssertionError(
        f"no read/write strategy for field {name!r} (fieldtype {ftype!r}) on this form; "
        "add a generator, supply a value, or put it in EXCLUDED with a reason"
    )


def _as_number(raw):
    text = str(raw if raw is not None else "").replace(",", "").strip()
    for symbol in ("₱", "%", "$"):
        text = text.replace(symbol, "")
    try:
        return round(float(text.strip() or 0), 4)
    except ValueError:
        return None


def rendered_matches(field: dict, typed, read) -> bool:
    """Compare what was typed with what the SCREEN shows, per fieldtype.

    frappe renders a Currency of 1.0 as "1.00" and an Int with separators, so a raw
    string compare would go red on every number for a reason that is not a defect.
    """
    ftype = field.get("fieldtype")
    if ftype == "Check":
        return bool(read) == bool(typed)
    if ftype in ("Int", "Float", "Currency", "Percent"):
        return _as_number(read) == _as_number(typed)
    return str(read if read is not None else "").strip() == str(typed).strip()


def plan_fields(doctype: str, excluded: dict) -> list:
    """Every data field on disk that is not explicitly excluded, in schema order."""
    return [f for f in schema.data_fields(doctype) if f["fieldname"] not in excluded]


def assert_exclusions_are_pinned(doctype: str, excluded: dict, expected: frozenset):
    """Pin the NAMES, not the count — a count lets one field be swapped for another."""
    assert frozenset(excluded) == expected, (
        f"{doctype}: the excluded-field set moved. Was {sorted(expected)}, "
        f"now {sorted(excluded)}. Every exclusion is a field nobody is testing."
    )
    for name, reason in excluded.items():
        assert isinstance(reason, str) and len(reason) > 20, (
            f"{doctype}.{name} is excluded without a real reason: {reason!r}"
        )


def assert_total_fields(doctype: str, expected_data: int):
    """A DELETED field shrinks the loop's domain and would otherwise fail nothing."""
    actual = schema.counts(doctype)["data"]
    assert actual == expected_data, (
        f"{doctype}: the schema now has {actual} data fields, not {expected_data}. "
        "A field was added or removed — decide what happens to it, then update this."
    )
