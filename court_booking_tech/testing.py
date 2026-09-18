# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Test-site-only helpers, invoked via `bench execute` from the E2E suite
(helpers/worker_routing.bench_execute). Hard-gated on allow_tests, same as
tasks.run_expiry_sweep — never callable on a real site.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint

from court_booking_tech import clock
from court_booking_tech.timeutil import _as_timedelta


def _guard():
	if not frappe.conf.get("allow_tests"):
		frappe.throw(
			_("court_booking_tech.testing is available only on test sites (allow_tests)."),
			frappe.PermissionError,
		)


def set_turnstile(enabled: int = 0, site_key: str = "", secret_key: str = ""):
	"""Flip the Turnstile config on THIS worker's site. Skips the save-time
	siteverify round-trip (skip_turnstile_validation) so E2E setup never
	depends on Cloudflare being reachable at toggle time."""
	_guard()
	doc = frappe.get_single("CBT Platform Settings")
	doc.enable_turnstile = int(enabled)
	doc.turnstile_site_key = site_key
	if secret_key:
		doc.turnstile_secret_key = secret_key
	doc.flags.skip_turnstile_validation = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"enable_turnstile": doc.enable_turnstile}


def set_refund_policy(company: str, policy: str = "Refund", fault_by: str = ""):
	"""Flip a company's B53 policy for one E2E scenario, and put it back after.

	Both fields are permlevel 1 — the platform writes them, a tenant only reads
	them — so no E2E seat can arrange this through the UI. That is exactly what
	this module is for: an arrange no seat can perform goes through a helper via
	`bench_json`, never through a widened DocPerm.

	Returns the values as stored, so a test can capture them before it changes
	anything and hand the same dict back in teardown.
	"""
	_guard()
	before = frappe.db.get_value(
		"CBT Company", company, ["refund_policy", "facility_fault_cancel_by"], as_dict=True
	)
	frappe.db.set_value("CBT Company", company, "refund_policy", policy)
	if fault_by:
		frappe.db.set_value("CBT Company", company, "facility_fault_cancel_by", fault_by)
	frappe.db.commit()
	return {
		"before": before,
		"refund_policy": policy,
		"facility_fault_cancel_by": fault_by or before.get("facility_fault_cancel_by"),
	}


def age_booking_clocks(booking: str, expire_base: int = 0, expire_deadline: int = 0):
	"""Backdate a booking's clocks so the sweep sees them as lapsed.

	E2E's alternative to sleeping (section-9 gotcha: a wall-clock sleep for
	expiry is a review-rejectable defect — it burns the ≤10-min budget and
	flakes). The server clock stays REAL; only the stored deadlines move, so
	the sweep, the availability engine and the portal all agree.

	expire_base / expire_deadline are minutes to push each clock INTO THE PAST
	(0 = leave that clock untouched — file 07's "deadline honored" scenario
	ages only the base clock).
	"""
	_guard()
	updates = {}
	now = clock.now_dt()
	if int(expire_base or 0):
		updates["reservation_expires_at"] = now - timedelta(minutes=int(expire_base))
	if int(expire_deadline or 0):
		updates["verification_deadline_at"] = now - timedelta(
			minutes=int(expire_deadline)
		)
	if not updates:
		frappe.throw(_("Nothing to age: pass expire_base and/or expire_deadline."))
	# set_value, not doc.save: the scheduling fields are immutable through
	# validate (S4 as-built 3) and this must not fire billing doc_events.
	frappe.db.set_value("CBT Court Booking", booking, updates)
	frappe.db.commit()
	return frappe.db.get_value(
		"CBT Court Booking",
		booking,
		["booking_status", "reservation_expires_at", "verification_deadline_at"],
		as_dict=True,
	)


def set_no_show_release(company: str, minutes: int = 0):
	"""Flip a company's no-show release knob (section-16). 0 = off.

	db.set_value + clear_document_cache rather than doc.save(): the knob is the
	only field changing, CBT Company.validate re-runs slug/code/office-hours
	checks for nothing, and the cache clear is what stops the next request
	reading the old value out of redis.
	"""
	_guard()
	frappe.db.set_value(
		"CBT Company", company, "no_show_release_minutes", cint(minutes)
	)
	frappe.clear_document_cache("CBT Company", company)
	frappe.db.commit()
	return cint(
		frappe.db.get_value("CBT Company", company, "no_show_release_minutes")
	)


def set_test_clock_offset(minutes: int = 0):
	"""Move THIS worker's site clock to a pretend "now" (section-17, B8).

	`minutes` is a SIGNED offset added to real site time by `clock.now_dt()`, so
	one call makes the sweep, the availability engine, both board payloads and
	the portal agree about a time of day that is not the real one. 0 — or any
	falsy value — DELETES the key and restores real time; there is deliberately
	no separate "unset" sentinel to get wrong.

	WHY REDIS AND NOT `frappe.conf`: see clock.py's module docstring. Short
	version — a web request reads site config through a 60-second TTL cache, so
	a conf key would land up to a minute late AND leave a stale pretend-time
	tail on the next E2E file even after a clean teardown.

	SAME-CALENDAR-DAY RULE — the caller's contract, and it is not optional.
	Pin pretend-now to a time on TODAY, never to another date. Two reasons, and
	both bite silently:
	  * every date constant in the E2E suite is computed HOST-side with
	    `date.today()`, and the ledger that keeps fourteen files from colliding
	    is a map of `today + N`;
	  * the offset moves THIS seam only — frappe's `creation`/`modified` stamps
	    and the desk board's default date (`frappe.datetime.get_today()`, the
	    BROWSER's) do not follow it.
	A cross-midnight offset would therefore re-date the whole ledger, and the
	failures would read as product bugs.

	NO `frappe.db.commit()` HERE, unlike every sibling in this module. Those
	write to the database and are called from `bench execute`, so they must
	commit; this one touches redis only. Adding a commit would make a helper a
	BACKEND test can legitimately call commit that test's open transaction,
	which is how a test-only lever turns into a dirty site.

	REDIS IS NOT ROLLED BACK by a test transaction, and `snapshot_reset`
	restores the DATABASE, not provably redis. Three layers therefore stop an
	offset outliving its file: the key carries a 1-hour TTL, every caller clears
	it in teardown, and the E2E session fixture clears it once up front. The TTL
	is the one that covers a HARD kill (Ctrl-C, a dead container) leaving a dev
	site lying about time until somebody next runs the suite. It is ~50x the
	longest E2E file, so nothing real can reach it — but a caller that could
	ever hold the clock for an hour must pass its own, because an offset that
	expires mid-file is a nasty failure.

	Returns the offset actually applied plus the resulting server now, so a
	caller can ASSERT the lever landed rather than trust it — `bench execute`
	runs in its own process, and the return value is the only thing the caller
	can see of what the web workers will read.
	"""
	_guard()
	minutes = cint(minutes)
	if minutes:
		frappe.cache.set_value(
			clock.CLOCK_OFFSET_KEY, minutes, expires_in_sec=3600
		)
	else:
		frappe.cache.delete_value(clock.CLOCK_OFFSET_KEY)
	return {"offset_minutes": minutes, "server_now": str(clock.now_dt())}


def set_google_login(
	enabled: int = 0,
	client_id: str = "e2e-google-client",
	client_secret: str = "e2e-google-secret",
):
	"""Backlog B51: give THIS worker's site a Google key with DUMMY credentials,
	or disable it. The button renders from the key alone — the round trip to
	Google is never automated. Site-wide state: the module fixture that arms it
	owns the disable, and a killed run leaves the button on until the lane's
	own reset."""
	_guard()
	from court_booking_tech.sso import configure_google

	on = cint(enabled)
	status = configure_google(
		client_id if on else "", client_secret if on else "", enable=on
	)
	frappe.db.commit()
	return status


def peek_oauth_state(state: str) -> dict:
	"""READ the redirect_to frappe >= 16.29 keeps in the cache behind the
	single-use OAuth `state` token (600s TTL). Never consume it: frappe deletes
	it on the Google callback, and a Playwright row only inspects the link.
	Returns a dict (bench_json needs one JSON line); None when the token is
	unknown or expired."""
	_guard()
	from frappe.utils.oauth import OAUTH_LOGIN_FLOW_CACHE_PREFIX

	return {"redirect_to": frappe.cache.get_value(f"{OAUTH_LOGIN_FLOW_CACHE_PREFIX}:{state}")}


def set_bypass_gps_request(enabled: int = 0):
	"""Flip the platform's Bypass GPS Request switch on THIS worker's site. A row
	ticks it on the form by gesture; this is the reset its teardown owns."""
	_guard()
	doc = frappe.get_single("CBT Platform Settings")
	doc.bypass_gps_request = cint(enabled)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"bypass_gps_request": cint(doc.bypass_gps_request)}


def backdate_booking_start(booking: str, minutes: int = 10):
	"""Move a booking so it STARTED `minutes` ago and is still running
	(section-16) — the age_booking_clocks sibling for the no-show sweep.

	Semantics are ABSOLUTE, not a relative nudge: the booking's start becomes
	`now - minutes` and its end follows by the original duration. The sweep's
	window is `start + grace < now <= end`, so a test needs to place the start
	at a known distance in the past; shifting the stored time by a fixed amount
	would leave that distance depending on when the run happened to start.

	It also CLEARS the check-in stamps, and that is not tidiness: a booking the
	desk creates on an already-started slot is auto-checked-in at insert
	(_auto_check_in), so without this the lever would appear to work and the
	sweep would correctly ignore its own fixture.

	MIDNIGHT. A booking stores ONE `booking_date` for both its times, so the
	shifted start and end must land on the SAME day as each other and as today.
	Silently writing an end that has rolled past midnight would store a row
	whose TIMESTAMP(booking_date, end_time) precedes its own start, and the
	sweep's safety rail would then pass on a fiction. It throws instead, loudly.

	THIS HELPER's own dead window WAS `[24:00 − duration + minutes, 00:00 +
	minutes]` — `[23:10, 00:10]` for a 60-minute booking backdated 10 minutes —
	and it is now **UNREACHABLE under the pinned test clock (section-17)**. The
	only caller that needs an in-progress booking, E2E file 14, pins pretend-now
	to the early afternoon before it mints anything, so both shifted edges land
	mid-afternoon whatever the real time is. The guard below stays as defence in
	depth: it is cheap, and a future caller that forgets to pin the clock
	deserves a loud throw rather than a row whose
	`TIMESTAMP(booking_date, end_time)` precedes its own start. The throw
	computes and prints both edges rather than describing them, because an
	approximate window in an error message is worse than none: the next person
	to hit it will trust the number.

	Note also what the window never was: a claim that the scenario is
	unconstructible. This helper writes raw times through db.set_value and knows
	nothing about grids or closing times, so even without the test clock a
	40-minute backdate at 23:30 places a perfectly valid in-progress booking at
	22:50–23:50. The only instant the DATA MODEL forbids is the final minute of
	the day.

	Two harmless artefacts, written down so nobody debugs them: the booking's
	`rate_segments` still describe the ORIGINAL window, and a re-sync of an
	unsegmented booking re-renders its invoice line description from the NEW
	times. Neither affects a total.
	"""
	_guard()
	row = frappe.db.get_value(
		"CBT Court Booking",
		booking,
		["booking_date", "start_time", "end_time"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Booking {0} not found.").format(booking))

	now = clock.now_dt()
	old_start = _as_timedelta(row.start_time)
	duration = _as_timedelta(row.end_time) - old_start
	new_start = now - timedelta(minutes=int(minutes))
	new_end = new_start + duration

	if new_start.date() != now.date() or new_end.date() != new_start.date():
		# Both edges COMPUTED, never described. The first version of this
		# message stated the window as "23:00 minus the backdate", which is
		# 22:50 for the default arguments — 20 minutes earlier than the truth,
		# in the one sentence a future debugger has no reason to doubt.
		duration_minutes = int(duration.total_seconds() // 60)
		midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
		opens_again = midnight + timedelta(minutes=int(minutes))
		closes = midnight + timedelta(
			minutes=1440 - duration_minutes + int(minutes)
		)
		frappe.throw(
			_(
				"backdate_booking_start cannot place a {0}-minute booking {1} "
				"minutes before {2}: one end would cross midnight, and a "
				"booking stores ONE date for both its times. With these "
				"arguments the helper is unusable from {3} to {4} — pass a "
				"different backdate (a LARGER one works late in the evening: "
				"it pulls both ends back into today) or run outside that "
				"window."
			).format(
				duration_minutes,
				int(minutes),
				now.strftime("%H:%M"),
				closes.strftime("%H:%M"),
				opens_again.strftime("%H:%M"),
			)
		)

	updates = {
		"booking_date": new_start.date(),
		"start_time": timedelta(
			hours=new_start.hour, minutes=new_start.minute, seconds=new_start.second
		),
		"end_time": timedelta(
			hours=new_end.hour, minutes=new_end.minute, seconds=new_end.second
		),
		"checked_in_at": None,
		"checked_in_by": None,
	}
	# set_value, not doc.save: the scheduling fields are immutable through
	# validate (S4 as-built 3) and this must not fire billing doc_events.
	frappe.db.set_value("CBT Court Booking", booking, updates)
	frappe.db.commit()
	return frappe.db.get_value(
		"CBT Court Booking",
		booking,
		["booking_status", "booking_date", "start_time", "end_time", "checked_in_at"],
		as_dict=True,
	)


def get_booking_mail(booking: str) -> dict:
	"""What was QUEUED for a booking (section-19 E2E, Backlog B5).

	**There is NO `subject` column on Email Queue** — the subject is a header
	inside the MIME `message`, which is why section-9's own
	`test_notifications._queued()` reads `message` too. Asking for `subject`
	raises inside DatabaseQuery, and `bench execute` then re-runs the whole call
	through its `eval` fallback, so the real error is REPLACED by
	`NameError: name 'court_booking_tech' is not defined` — which reads like a
	module-path problem and is not one. Recorded because that masking cost a
	4-minute suite run to see.

	Never assert a LINK out of the body: core can redact a queued body after
	queuing (S8 as-built 1). Wording is fair game — section-9 asserts on it.

	Returns a DICT, always, including for a booking that queued nothing. That is
	not a style choice: `bench execute` prints its return value only `if ret`
	(frappe/commands/utils.py), so a helper that returned `[]` would print
	NOTHING and a "the walk-in was not emailed" assertion would be
	indistinguishable from a crashed call — the exact shape of the trap S13
	as-built 6 records for negative mail assertions. A dict is always truthy and
	always starts with `{`, which is also what the suite's stdout parser scans
	for (test_14/test_15 idiom).
	"""
	_guard()
	rows = []
	for row in frappe.get_all(
		"Email Queue",
		filters={"reference_doctype": "CBT Court Booking", "reference_name": booking},
		fields=["name", "status", "message"],
		order_by="creation asc",
	):
		rows.append(
			{
				"name": row.name,
				"status": row.status,
				"message": row.message,
				# Recipients are a CHILD table (Email Queue Recipient), not a
				# column — the same shape get_signup_link had to use.
				"recipients": frappe.get_all(
					"Email Queue Recipient",
					filters={"parent": row.name},
					pluck="recipient",
				),
			}
		)
	return {"booking": booking, "count": len(rows), "rows": rows}


def get_address_mail(email: str) -> dict:
	"""Every queued mail to `email` — get_booking_mail's sibling for a message
	with no booking behind it (Backlog B22).

	⚠ Recipients are a CHILD table, not a column, and Email Queue has NO
	`subject` — assert the MIME `message`. Returns a dict even when empty:
	`bench execute` prints its return only `if ret`, so a falsy result is
	indistinguishable from a crashed call.
	"""
	_guard()
	rows = frappe.get_all(
		"Email Queue",
		filters=[["Email Queue Recipient", "recipient", "=", email]],
		fields=["name", "status", "message"],
		order_by="creation asc",
	)
	return {"email": email, "count": len(rows), "rows": rows}


def void_customer_credits(company: str, customer: str) -> dict:
	"""Void every Active credit a customer holds at a company (Backlog B43).

	`CBT Customer Credit` is engine-written and carries NO write DocPerm for any
	role — the E2E arrange that voids leftovers only ever worked because it ran as
	Administrator. Section-27 records why that is the finding, not the obstacle.
	"""
	_guard()
	names = frappe.get_all(
		"CBT Customer Credit",
		filters={"company": company, "customer": customer, "status": "Active"},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("CBT Customer Credit", name, "status", "Void")
	frappe.db.commit()
	return {"voided": len(names), "names": names}


def purge_platform_statements(period: str, issued_by: str) -> dict:
	"""Delete a period's statements and its month close (Backlog B43).

	`CBT Platform Statement` carries NO delete DocPerm for any role BY DESIGN —
	the E2E cleanup that removes them only ever worked because it ran as
	Administrator (that file's own comment said so). The SAFETY the cleanup had
	is kept, not dropped: a row issued or closed by anyone but the suite's own
	seat is somebody's real work on a shared bench, and this refuses rather than
	deleting it.
	"""
	_guard()
	rows = frappe.get_all(
		"CBT Platform Statement",
		filters={"period": period},
		fields=["name", "issued_by"],
	)
	foreign = [row.name for row in rows if row.issued_by != issued_by]
	if foreign:
		frappe.throw(
			_(
				"statements for {0} were issued by someone other than {1}: {2} — "
				"not this suite's to delete; cancel or delete them by hand."
			).format(period, issued_by, ", ".join(foreign))
		)
	for row in rows:
		frappe.delete_doc("CBT Platform Statement", row.name, force=True)

	closed_by = frappe.db.get_value("CBT Platform Month Close", period, "closed_by")
	if closed_by and closed_by != issued_by:
		frappe.throw(
			_(
				"{0} was closed by {1}, not by {2} — not this suite's to reopen."
			).format(period, closed_by, issued_by)
		)
	if closed_by:
		frappe.delete_doc("CBT Platform Month Close", period, force=True)
	frappe.db.commit()
	return {"period": period, "statements": len(rows), "close": bool(closed_by)}


def clear_rate_buckets() -> dict:
	"""Drop this site's rate-limit buckets. B22's account-exists mail is one per
	address per hour, so a re-run would otherwise be suppressed and read as a
	missing mail."""
	_guard()
	from court_booking_tech.throttle import clear_all_buckets

	clear_all_buckets()
	return {"cleared": 1}


def time_label_table() -> dict:
	"""The SERVER's answer for every row of the ruled time convention.

	Backlog B45. `court_booking_tech/timeutil.py` and
	`public/js/cbt_time_format.js` are two implementations of ONE ruling, and the
	whole point of the row is that they cannot drift. `tests/test_timeutil.py`
	pins the Python against the ruling; `e2e/tests/test_time_language.py` drives
	the SAME inputs through `cbt.fmt.timeShort` in a real browser and compares
	them to what this returns.

	The table is imported from the unit test rather than restated, so there is
	one list, not a third copy — restating it is the exact mistake B45 exists to
	undo.
	"""
	_guard()
	from court_booking_tech.tests.test_timeutil import SHORT_LABELS
	from court_booking_tech.timeutil import label_short

	return {
		"rows": [
			{"value": value, "expected": expected, "server": label_short(value)}
			for value, expected in SHORT_LABELS
		]
	}


def get_signup_link(email: str) -> str:
	"""A fresh /update-password link for `email`, equivalent to the one the
	welcome mail carried.

	The emailed link is deliberately UNRECOVERABLE in frappe v16: core
	redacts the Email Queue body after queuing (user.py
	send_welcome_mail_to_user) and stores only a sha256 of the key on the
	User row — so E2E regenerates via the same core path
	(User._reset_password), which the emailed link also came from. The E2E
	asserts the queue ROW separately; this helper only mints the key.
	"""
	_guard()
	user_name = frappe.db.get_value("User", {"email": email})
	if not user_name:
		frappe.throw(_("No user found for {0}.").format(email))
	# The queue row IS the email assertion surface (no SMTP in dev) — a
	# signup that queued nothing must fail here, not silently mint a link.
	queued = frappe.get_all(
		"Email Queue",
		filters=[["Email Queue Recipient", "recipient", "=", email]],
		limit=1,
	)
	if not queued:
		frappe.throw(_("No queued welcome email found for {0}.").format(email))
	link = frappe.get_doc("User", user_name)._reset_password()
	frappe.db.commit()
	return link
