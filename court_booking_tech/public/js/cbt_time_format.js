/* The app's ONE client-side time language (Backlog B45).
 *
 * THE RULING IT IMPLEMENTS (the user, 2026-09-04, restated 2026-09-05 as
 * "DIDN'T I SAY THE FORMATTING SHOULD BE REUSABLE BEFORE"):
 *
 *     6 AM · 6:30 AM · 12 MN · 12 NN
 *
 * MN and NN are the pair that makes each other legible — "12 AM" and "12 PM"
 * are the two labels people actually misread — so a surface that says one
 * without the other invites exactly that mistake at the other end of the day.
 *
 * WHY THIS FILE EXISTS. The convention was implemented in ONE PAGE
 * (www/cbt-book.html) instead of one function, so every surface built after it
 * silently opted out. By 2026-09-05 there were SIX implementations:
 * timeutil.label_short, timeutil._fmt_time, api/portal._fmt_12h,
 * cbt-book.labelShort, cbt-book.label, cbt-booking-detail.label,
 * cbt-my-bookings.label and cbt.time_label — and the desk called NONE of them,
 * printing 06:00 - 07:00 at sixteen render sites while the customer's phone
 * said 6 AM - 7 AM.
 *
 * ONE of those forks was also WRONG: cbt-my-bookings.label parsed the hour
 * without `% 24`, so a booking ending at 24:00:00 (a 23:59 closing means
 * MIDNIGHT — S4 as-built 9) printed "12:00 PM". Noon, on the customer's own
 * list, for a midnight booking. That is what a fork costs.
 *
 * There are now TWO implementations and they are pinned to each other:
 * court_booking_tech/timeutil.py on the server, this file on the client, and
 * e2e/tests/test_time_language.py drives BOTH over the same table.
 *
 * LOADED BY BOTH FACES, and that is the whole point:
 *   - the DESK through hooks.app_include_js (a desk-only hook — see
 *     cbt_time_control.js's header for why that distinction matters);
 *   - the PORTAL through templates/includes/cbt_portal_head.html, the same
 *     include that already carries cbt_portal.js.
 *
 * ⚠ `window.cbt` is SHARED with cbt_time_control.js, which is loaded by a
 * different mechanism and may arrive either side of this file. Every writer
 * must merge (`window.cbt = window.cbt || {}`) — a bare assignment silently
 * deletes the other half — and every reader must resolve AT CALL TIME.
 *
 * No frappe dependency: the portal's website layer loads this before any desk
 * bundle exists, and it must never throw there.
 */
(function () {
	"use strict";

	window.cbt = window.cbt || {};
	var fmt = (window.cbt.fmt = window.cbt.fmt || {});

	/* frappe's __() where it exists (desk, and the website bundle), the literal
	 * where it does not. The portal templates used to spell these as Jinja
	 * {{ _('MN') }}, which cannot work from a shared .js file. */
	function t(text) {
		return typeof window.__ === "function" ? window.__(text) : text;
	}

	function two(number) {
		return ("0" + number).slice(-2);
	}

	/**
	 * "18:00:00" -> "6 PM", "06:30" -> "6:30 AM", "24:00:00" -> "12 MN".
	 *
	 * Mirrors court_booking_tech.timeutil.label_short EXACTLY, including the
	 * two edges that have each cost a round here:
	 *   - `% 24` — a midnight END arrives as "24:00:00", and 24 % 12 || 12 is
	 *     12 with an hour >= 12, i.e. "12:00 PM". Noon. Wrong by twelve hours.
	 *   - a single-digit hour — frappe renders a Time with str(timedelta), so
	 *     the server ships "7:30:00", never "07:30:00" (data.py 2679).
	 *
	 * Anything unparseable comes back UNCHANGED rather than as "Invalid date":
	 * a formatter must never be the thing that puts nonsense in front of a user.
	 */
	fmt.timeShort = function (value) {
		if (value === null || value === undefined || value === "") return "";
		var bits = String(value).split(":");
		var hour = parseInt(bits[0], 10);
		var minute = parseInt(bits[1], 10);
		if (isNaN(hour)) return String(value);
		if (isNaN(minute)) minute = 0;
		hour = ((hour % 24) + 24) % 24;
		if (minute === 0) {
			if (hour === 0) return t("12 MN");
			if (hour === 12) return t("12 NN");
		}
		var shown = hour % 12 || 12;
		return (
			(minute === 0 ? String(shown) : shown + ":" + two(minute)) +
			" " +
			(hour < 12 ? "AM" : "PM")
		);
	};

	/**
	 * "6 AM – 7 AM" — an hour on a grid is a SPAN, and a bare start leaves the
	 * reader working out where it ends (user ruling 2026-09-04).
	 *
	 * ⚠ The separator is an EN DASH with a space either side, and it is
	 * load-bearing: the desk's matrix time column and the portal's must render
	 * the SAME STRING, which is the one assertion that would have caught B45.
	 */
	fmt.timeRange = function (start, end) {
		return fmt.timeShort(start) + " – " + fmt.timeShort(end);
	};

	/**
	 * "₱400" on the hour, "₱412.50" when a rate really has centavos.
	 *
	 * A grid cell is ~124 px on a phone and "₱ 400.00" fills it, which is why
	 * the portal has rendered the compact form since B33. Centavos are kept
	 * when they exist, so nothing is ever rounded away on screen.
	 */
	fmt.moneyShort = function (value) {
		var number = Number(value || 0);
		return (
			"₱" +
			number.toLocaleString("en-PH", {
				minimumFractionDigits: number % 1 ? 2 : 0,
				maximumFractionDigits: 2,
			})
		);
	};

	/** "₱400.00" — the full form, for a total someone reads out loud. */
	fmt.money = function (value) {
		return (
			"₱" +
			Number(value || 0).toLocaleString("en-PH", {
				minimumFractionDigits: 2,
				maximumFractionDigits: 2,
			})
		);
	};
})();
