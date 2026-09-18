# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Portal rate limiting (section-9, PLAN §8k).

Per-USER hourly buckets, deliberately NOT per-IP: PH carrier CGNAT puts
hundreds of real customers behind one address (the same reasoning that made
the D9 signup IP backstop generous). The portal endpoints are all
authenticated, so the session user is the honest identity.

Semantics mirror frappe's own rate_limit decorator on purpose:
- it applies ONLY inside an HTTP request (`frappe.request`), so backend unit
  tests that call the APIs directly are unaffected — the dedicated limit tests
  opt in with frappe.utils.set_request(), exactly like tests/test_signup.py;
- it raises frappe.RateLimitExceededError, which the request handler maps the
  same way core's limiter does.

The window is the wall-clock hour from the app's clock seam, so a monkeypatched
clock moves the bucket (tests can prove "next hour resets" without sleeping).
"""

import frappe
from frappe import _
from frappe.utils import cint

from court_booking_tech import clock

KEY_PREFIX = "cbt-rl"
WINDOW_SECONDS = 3900  # one hour + slack, so a bucket outlives its own window


def rate_key(action: str, user: str, now=None) -> str:
	"""The un-namespaced cache key. Seeds delete `KEY_PREFIX*` on every run —
	Redis counters survive snapshot_reset (they are not in the DB), and a
	stale bucket would flake the next suite run."""
	now = now or clock.now_dt()
	return f"{KEY_PREFIX}:{action}:{user}:{now.strftime('%Y%m%d%H')}"


def enforce_user_rate_limit(action: str, limit: int, user: str | None = None):
	"""Count one hit of `action` for the session user; throw past `limit`."""
	if not frappe.request:
		return  # direct/internal call — see module docstring
	limit = cint(limit)
	if limit <= 0:
		return
	user = user or frappe.session.user
	cache_key = frappe.cache.make_key(rate_key(action, user))
	if not frappe.cache.get(cache_key):
		frappe.cache.setex(cache_key, WINDOW_SECONDS, 0)
	if cint(frappe.cache.incrby(cache_key, 1)) > limit:
		frappe.throw(
			_(
				"You have made too many requests in the last hour. "
				"Please try again later, or contact the branch directly."
			),
			frappe.RateLimitExceededError,
		)


def clear_all_buckets():
	"""Drop every bucket on THIS site (seeds + test cleanup)."""
	frappe.cache.delete_keys(KEY_PREFIX)
