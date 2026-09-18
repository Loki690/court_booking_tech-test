"""Stand-in for the browser's location permission bubble, which Playwright cannot draw.

The init script counts every ask on window.__geo, holds the callbacks there, and answers
navigator.permissions.query for geolocation with the given state.
"""
from helpers.worker_routing import bench_json

CARDS = "#cbt-results .cbt-card[data-slug]"
GET_BRANCHES = "/api/method/court_booking_tech.geo.get_branches"
LEVER = "court_booking_tech.testing.set_bypass_gps_request"
PROMPT_NOTE = "Showing all courts. Allow location for nearest-first results."
PROMPT_STUB = (
    "(() => {"
    "  const held = {calls: 0, success: null, error: null, query: null};"
    "  held.api = {"
    "    getCurrentPosition(success, error) { held.calls += 1; held.success = success; held.error = error; },"
    "    watchPosition() { held.calls += 1; return 0; },"
    "    clearWatch() {},"
    "  };"
    "  window.__geo = held;"
    "  Object.defineProperty(Navigator.prototype, 'geolocation', {get: () => held.api, configurable: true});"
    "  if (navigator.permissions) {"
    "    const query = navigator.permissions.query.bind(navigator.permissions);"
    "    held.query = (d) =>"
    "      d && d.name === 'geolocation' ? Promise.resolve({state: '__STATE__'}) : query(d);"
    "    navigator.permissions.query = held.query;"
    "  }"
    "})();"
)


def prompt_stub(permission: str = "prompt") -> str:
    return PROMPT_STUB.replace("__STATE__", permission)


def stub_installed(page) -> bool:
    return page.evaluate(
        "() => !!window.__geo && navigator.geolocation === window.__geo.api"
        " && (!navigator.permissions || navigator.permissions.query === window.__geo.query)"
    )


def asks(page) -> int:
    return page.evaluate("() => window.__geo.calls")


def branch_rows(request, **origin) -> list:
    resp = request.get(GET_BRANCHES, params=origin or None, timeout=15000)
    assert resp.ok, f"get_branches: HTTP {resp.status}"
    return resp.json()["message"]


def card_slugs(page) -> list:
    return page.eval_on_selector_all(CARDS, "els => els.map(e => e.dataset.slug)")


def set_bypass_gps_request(enabled: int) -> None:
    bench_json(LEVER, [enabled])
