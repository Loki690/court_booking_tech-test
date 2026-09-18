# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
The clock seam (section-4). EVERY time read in this app goes through now_dt(),
which is what lets ONE lever move the sweep, the availability engine, both
boards and the portal to the same "now".

Two kinds of test move it, and they need different mechanisms:

  * BACKEND tests monkeypatch the symbol
    (`mock.patch("court_booking_tech.clock.now_dt")`). In-process only.
  * E2E drives a REAL HTTP server in another process, so it can patch nothing.
    It sets the test-clock offset below instead (section-17, Backlog B8).
    Before that lever existed, `testing.age_booking_clocks` and
    `testing.backdate_booking_start` had to move the DATA because the CLOCK
    could not be moved — which left E2E file 14's release rows with an
    undeclared wall-clock precondition (they failed between 23:00 and 00:10
    Manila, and cost section-16 two of its three budget passes). A test whose
    result depends on WHEN you run it is unsound, not flaky.

Consumer convention (required for BOTH mechanisms to reach you):

    from court_booking_tech import clock
    ...
    now = clock.now_dt()   # NEVER `from ... import now_dt`

Grep-enforceable rule (asserted by tests/test_slots.py): no direct
`frappe.utils.now_datetime()` / `nowdate()` call outside this module.

THE TEST CLOCK OFFSET — two gates, both required:

  1. `frappe.conf.allow_tests` — static, present only on test/dev sites (the
     same gate `testing._guard` and `tasks.run_expiry_sweep` use). A production
     request short-circuits here and never touches redis.
  2. the redis key CLOCK_OFFSET_KEY — the lever itself, written only by
     `testing.set_test_clock_offset`.

Two gates rather than one because a leaked offset makes an entire site lie
about time, and that is worth a belt AND braces.

REDIS, NOT `frappe.conf` — deliberate, and recorded here because the Backlog
B8 row originally sketched a conf key. In v16 a WEB REQUEST loads `local.conf`
through a 60-second in-memory TTL cache (`frappe/config.py:141` —
`_cached_get_site_config = site_cache(ttl=60, ...)`; `frappe/__init__.py:186` —
`cached=bool(frappe.request)`). A conf-based offset would therefore (a) take up
to a minute to reach the running web workers and (b) leave a <=60s stale
pretend-time tail on the NEXT E2E file even after a clean teardown. Redis is
namespaced per site by `db_name` (`frappe/utils/redis_wrapper.py:52-62`, which
is what isolates the site-per-worker E2E sites from each other) and is
immediate on write AND on delete.

COST: `RedisWrapper.get_value` memoises in `frappe.local.cache` — misses
included (`redis_wrapper.py:104-110`) — and `frappe.local.cache` is rebuilt per
`frappe.init()`, i.e. per request. So a request pays AT MOST ONE redis GET no
matter how many times it reads the clock (`api.board.get_board_data` reads it
three times), and every read inside one request agrees about "now". Production
pays nothing at all: gate 1 short-circuits. A redis-cache that is merely
UNREACHABLE degrades safely — `get_value` swallows the `ConnectionError` and
returns None, so the site falls back to REAL time. That is not the same as redis
being unconfigured: `frappe.cache` is `None` until `frappe.init` builds it, so
calling this with no cache connection raises `AttributeError` instead of falling
back. Deliberate — a test site that cannot read its own lever should say so
loudly rather than quietly report a time it did not check — and gate 1 means
production never reaches the attribute at all.

WHAT THE OFFSET DOES *NOT* MOVE, because it moves this seam and nothing else:
frappe's own `creation` / `modified` stamps, and the desk board's default date
and "Today" button, which come from the BROWSER
(`cbt_court_board.js:218,342` — `frappe.datetime.get_today()`). Callers must
therefore keep pretend-now on the SAME CALENDAR DAY as the real one; see
`testing.set_test_clock_offset` for the full contract.
"""

from datetime import timedelta

import frappe
import frappe.utils
from frappe.utils import cint

# Signed offset in MINUTES, added to real site time on test sites. Absent =
# real time. Written only by testing.set_test_clock_offset; 0 deletes it.
CLOCK_OFFSET_KEY = "cbt_test_clock_offset_minutes"


def now_dt():
	"""Current site datetime (Asia/Manila — PLAN §8i, no DST).

	On a test site carrying a clock offset this returns the PRETEND now. See
	the module docstring for the two gates and why the lever lives in redis.
	"""
	now = frappe.utils.now_datetime()
	if frappe.conf.get("allow_tests"):
		offset = frappe.cache.get_value(CLOCK_OFFSET_KEY)
		if offset:
			now += timedelta(minutes=cint(offset))
	return now
