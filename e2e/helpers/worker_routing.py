"""
Per-worker routing for the site-per-worker E2E parallelization.

Under pytest-xdist each worker owns its OWN Frappe site/DB, served by its own
pinned `bench --site devN.localhost serve --port P`. Every HTTP URL, auth-state
path, and `bench ... execute` call MUST resolve to the CURRENT worker's slot —
never a hardcoded host/port/site — or workers cross-contaminate each other's
databases. This module is the SINGLE source of those bindings.

All values are computed at CALL TIME (never import time) from
PYTEST_XDIST_WORKER, so import order can't freeze a stale slot. An unmapped
worker id raises KeyError (fail-loud) instead of silently falling back onto
gw0's site — a silent fallback is exactly the cross-site corruption this design
exists to prevent.

Serial (`-n0`) runs leave PYTEST_XDIST_WORKER unset -> defaults to gw0 ->
localhost:8004 / dev.localhost == the single-site behaviour, byte-identical.
"""
import os
import subprocess
from pathlib import Path

# xdist worker id -> (published host port, Frappe site). Each site's own
# `bench serve` is pinned to that port by shared/scripts/start_multisite.sh. All
# ports are published on the container; only 8004 is used pre-parallel. To add a
# worker, add a slot here, provision the site (multisite_reset.ps1), and raise
# the --e2e-workers cap in run_all_tests.sh — an unmapped worker MUST fail loud.
_WORKER_MAP = {
    "gw0": (8004, "dev.localhost"),
    "gw1": (8005, "dev2.localhost"),
    "gw2": (8003, "dev3.localhost"),
}

# Shared container/bench literals — centralized here so no test hardcodes them.
_CONTAINER = "frappe_docker-frappe-1"
_BENCH_DIR = "/workspace/frappe_docker/development/frappe-bench"

_AUTH_STATE_ROOT = Path(__file__).parent.parent / "auth_state"


def worker_id() -> str:
    """The current xdist worker id ('gw0'..); 'gw0' when unset (-n0 / no xdist)."""
    return os.environ.get("PYTEST_XDIST_WORKER", "gw0")


def _slot():
    wid = worker_id()
    try:
        return _WORKER_MAP[wid]
    except KeyError:
        raise KeyError(
            f"worker_routing: no site/port mapped for xdist worker {wid!r}. "
            f"Mapped: {sorted(_WORKER_MAP)}. Cap --e2e-workers at "
            f"{len(_WORKER_MAP)}, or extend _WORKER_MAP and provision the site."
        )


def worker_port() -> int:
    return _slot()[0]


def worker_site() -> str:
    return _slot()[1]


def base_url() -> str:
    """This worker's site base URL. localhost resolves for Python requests,
    Playwright's Node request context, and Chromium (devN.localhost does not
    resolve on Windows), so we route by port to a site-pinned `bench serve`."""
    return f"http://localhost:{worker_port()}"


def auth_state_dir() -> Path:
    """Per-worker auth_state dir (auth_state/<worker>/). Isolated so concurrent
    workers never write the same cookie-jar file or reuse a cross-site sid."""
    d = _AUTH_STATE_ROOT / worker_id()
    d.mkdir(parents=True, exist_ok=True)
    return d


# Backlog B43: the suite holds no Administrator storage state. Asking for one is
# a mistake, not a fallback — a stale admin.json left on disk from an older run
# still carries a LIVE session, so a surviving reference would silently
# re-authenticate as Administrator and prove nothing about any role.
BANNED_STATES = {"admin.json"}


def auth_state_file(name: str) -> Path:
    if name in BANNED_STATES:
        raise AssertionError(
            f"Backlog B43: {name!r} is not available — court_booking_tech's E2E "
            "suite never runs as Administrator. Use PLATFORM_STATE "
            "(helpers.auth) or login_as() with a real seat."
        )
    return auth_state_dir() / name


def drop_banned_states() -> list:
    """Delete any Administrator cookie jar this worker left behind."""
    dropped = []
    for name in BANNED_STATES:
        path = auth_state_dir() / name
        if path.exists():
            path.unlink()
            dropped.append(str(path))
    return dropped


def bench_execute(method: str, args: str, check: bool = True):
    """Run `bench execute <method> --args <args>` against THIS worker's site.

    The only place that constructs a container/bench invocation, so no test can
    hardcode a `--site` (which would hit the container default = dev.localhost =
    gw0 and silently write the wrong site). Returns the CompletedProcess;
    callers read `.stdout`.
    """
    return subprocess.run(
        [
            "docker", "exec", "-w", _BENCH_DIR, _CONTAINER,
            "bench", "--site", worker_site(), "execute", method, "--args", args,
        ],
        check=check, capture_output=True, text=True,
    )


def bench_json(method: str, args: list):
    """`bench execute` a court_booking_tech.testing helper and parse its result.

    Backlog B43: the arranges no product seat may perform (deleting a CBT
    Platform Statement, voiding a CBT Customer Credit) come through here instead
    of through an Administrator HTTP context. `bench execute` prints the return
    value as one line of JSON (frappe/commands/utils.py) and ONLY `if ret`, so a
    helper that returns nothing is indistinguishable from a crash — every
    helper behind this returns a dict, and this fails loudly with both streams
    when one does not.

    check=False deliberately: bench_execute's default raises CalledProcessError,
    whose message carries the exit status but NOT stderr, which would leave the
    real reason invisible.
    """
    import json

    result = bench_execute(method, json.dumps(args), check=False)
    assert result.returncode == 0, (
        f"{method}{args!r} exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for line in reversed(result.stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(
        f"{method}{args!r} printed no JSON — a silent empty result would make "
        f"this a green gate.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
