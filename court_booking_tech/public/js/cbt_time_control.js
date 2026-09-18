// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// Section-21 (Backlog B12) — human time input.
//
// frappe's stock ControlTime is an Air-Datepicker in timepicker-only mode, and
// with the default HH:mm:ss system format that is THREE range sliders — hour,
// minute AND second (controls/time.js hides the seconds slider only when the
// site's time format drops seconds). Nobody sets 6:00 PM with sliders. The solo
// `court_booking` app never asks for a time in a dialog, which is why this was
// only ever felt here.
//
// This hands the job to the browser's own control: typed 2-digit segments plus a
// native picker. The DISPLAY follows the viewer's locale — an en-US desk shows
// `--:-- --` with an AM/PM segment, which is how Philippine staff actually think
// about 6:00 PM — while `.value` is ALWAYS 24-hour "HH:mm[:ss]". So we get the
// friendlier rendering for free and still never parse a localised string.
//
// LOADED SITE-WIDE via app_include_js (hooks.py). Two consequences to know
// before editing:
//
//   * app_include_js is a DESK hook, not an app-wide one (www/desk.py:38 ->
//     desk.html). The customer portal is served by the website layer and keeps
//     stock controls — it asks for no times, so nothing there changes. The
//     section-21 spec called this "app-wide"; that wording is wrong.
//   * every OTHER app on the site gets this control too. Verified clean when
//     this shipped: neither ph_payroll's nor fleet_mgt's E2E suites touch a Time
//     widget or assert on a datepicker. Re-check that before adding an app.
//
// The whole file is guarded and never throws: an exception out of an
// app_include_js script takes down every desk page, for every app, on the site.

(function () {
	if (!window.frappe || !frappe.ui || !frappe.ui.form || !frappe.ui.form.ControlData) {
		// Fail SOFT and loud. Leaving the stock slider in place is a bad day;
		// breaking every desk page on the site is a worse one.
		console.error(
			"[CBT] frappe.ui.form.ControlData is not loaded — keeping the stock " +
				"Time control (section-21, Backlog B12)."
		);
		return;
	}

	// MERGE, never assign: cbt_time_format.js writes cbt.fmt onto the same
	// global through a different loader (app_include_js here, the portal head
	// include there), and a bare `window.cbt = {}` from either side would
	// silently delete the other half. frappe.provide creates only the levels
	// that are missing, which is exactly the merge we want.
	frappe.provide("cbt");

	// Every shape a CBT time value reaches us in. The 12-hour entries are
	// defensive: a native time input's value is always 24-hour, but `parse` is
	// also handed MODEL values, and moment in non-strict mode would happily read
	// "6:00 PM" as 06:00 if the list did not name the format.
	const CBT_TIME_FORMATS = ["HH:mm:ss", "HH:mm", "h:mm:ss A", "h:mm A"];

	/**
	 * "18:00:00" -> "6 PM".
	 *
	 * BACKLOG B45: this used to be moment's `h:mm A` — "6:00 PM" — which was a
	 * SEVENTH rendering of the same hour. It now delegates to the app's one time
	 * language (public/js/cbt_time_format.js, mirroring timeutil.label_short),
	 * so this control, the board, the portal grid and the printed statement all
	 * say "6 PM".
	 *
	 * Resolved AT CALL TIME, deliberately: the two files load through different
	 * mechanisms and either can arrive first, so a by-reference alias
	 * (`cbt.time_label = cbt.fmt.timeShort`) would capture `undefined` on the
	 * unlucky ordering. The fallback keeps a read-only Time rendering SOMETHING
	 * if the module is ever missing, rather than blanking every grid on the site.
	 *
	 * Returns the input unchanged if it is not a time, so a caller can never
	 * render "Invalid date" at a user.
	 */
	cbt.time_label = function (value) {
		if (!value) return "";
		if (window.cbt && cbt.fmt && cbt.fmt.timeShort) {
			return cbt.fmt.timeShort(value);
		}
		const parsed = moment(value, CBT_TIME_FORMATS);
		return parsed.isValid() ? parsed.format("h:mm A") : String(value);
	};

	frappe.ui.form.ControlTime = class ControlTime extends frappe.ui.form.ControlData {
		// ControlData.make_input reads this off the constructor (data.js:10-13),
		// which is the idiomatic way to swap the element type — the same hook
		// ControlPassword uses.
		static input_type = "time";

		// NOT inherited, and NOT decoration. ControlData sets this TRUE (data.js:6)
		// while ControlDate — the class the stock ControlTime extended — sets it
		// FALSE (date.js:2). Subclassing ControlData without re-declaring it would
		// newly bind a debounced `input` listener (data.js:184-187) that writes the
		// model on every keystroke, including the empty string a half-typed time
		// reports. The stock control never did that; neither does this one.
		static trigger_change_on_input_event = false;

		make_input() {
			super.make_input();
			// The HTML default, stated rather than assumed: minute granularity,
			// no seconds segment. Do not lower it without re-reading
			// format_for_input — the two are coupled.
			this.$input.attr("step", 60);
		}

		parse(value) {
			// Handed BOTH input values ("HH:mm", always, from the native control)
			// and MODEL values. The latter reach the client through
			// frappe.utils.format_timedelta, whose format string is
			// `f"{int(hours):01}:{int(minutes):02}:{seconds:02}"` (data.py:2679) —
			// so any single-digit hour arrives as "7:30:00", with NO leading zero.
			if (!value) return value;
			if (value === "Invalid date") return "";
			const parsed = moment(value, CBT_TIME_FORMATS);
			return parsed.isValid() ? parsed.format("HH:mm:ss") : "";
		}

		format_for_input(value) {
			// MUST normalise; passing the stored value through is NOT safe, and
			// the section-21 spec's instruction to do so is wrong (see as-built).
			// A native time input accepts only "HH:mm[:ss]" with a TWO-digit hour,
			// so the "7:30:00" above is rejected and the browser BLANKS the field
			// silently — no error, no value, no clue.
			//
			// Truncating to minutes is safe on the way in: a value carrying
			// seconds is only ever rewritten if the user actually edits the field,
			// because an untouched input fires no change event and this method
			// never touches `this.value`.
			if (!value) return "";
			const parsed = moment(value, CBT_TIME_FORMATS);
			return parsed.isValid() ? parsed.format("HH:mm") : "";
		}

		set_description() {
			// Kept verbatim from the stock control: on a site whose timezone is
			// not the system one, a bare time is ambiguous and the field says so.
			const { description } = this.df;
			const { time_zone } = frappe.sys_defaults;
			if (!frappe.datetime.is_system_time_zone()) {
				if (!description) {
					this.df.description = time_zone;
				} else if (!description.includes(time_zone)) {
					this.df.description += "<br>" + time_zone;
				}
			}
			super.set_description();
		}

		// DELIBERATELY NOT OVERRIDDEN — `validate`. ControlData's is a no-op for a
		// Time docfield (it branches on df.options, which Time fields do not
		// carry), and a native time input cannot hold an invalid time in the first
		// place, so the stock control's "Time {0} must be in format {1}" msgprint
		// has nothing left to fire on.
		//
		// DELIBERATELY DROPPED — ControlDate.eval_expression, which the stock
		// parse() routed through. That is what allowed relative arithmetic typed
		// into a Time field ("+30m", "-2h") and user-format parsing via
		// frappe.datetime.user_to_str. Neither is reachable through a native time
		// input, so both go. Recorded rather than silently omitted.
		//
		// NOT DROPPED, because it was never there — the `t`-for-today shortcut.
		// set_t_for_today is called only from ControlDate.make_picker (date.js:10),
		// and the stock ControlTime overrode make_picker without calling it.
	};

	// --- read-only Time DISPLAY, scoped to this app's doctypes ----------------
	//
	// SECTION-23, closing the inconsistency section-21 knowingly created and
	// handed forward: an editable Time now renders in the viewer's locale
	// ("6:00 AM", from the native control above) while a READ-ONLY or static one
	// still renders frappe's `HH:mm:ss`. In a Court Hours grid the row being
	// edited and the rows below it visibly disagree about the same kind of value.
	//
	// SCOPED ON PURPOSE. frappe.form.formatters.Time reaches every read-only
	// Time on the site — every app's list views, reports and print formats — and
	// section-21 declined the sweep for exactly that reason. This wrapper only
	// takes over when the docfield belongs to a doctype named "CBT ...", which
	// covers this app's doctypes AND its child tables (a DocField's `parent` is
	// the doctype that owns it, so CBT Business Hours and CBT Rate Rule qualify
	// too). Anything else — including a synthesised report column, which carries
	// no `parent` — falls through to the stock formatter untouched.
	//
	// The reach is wider than "a read-only field on a form", and deliberately:
	// a LIST VIEW or REPORT column on a CBT doctype resolves through
	// frappe.meta.get_docfield, which returns a real docfield with `parent` set,
	// so those render 6:00 AM too. That is the point — the inconsistency
	// section-21 left was between two renderings of the same hour, and half a
	// fix would just move the seam.
	//
	// The billing statement stays untouched by a different route: it is a jinja
	// print format that formats server-side and never reaches this code, which
	// is what keeps section-23's "the print format is out of scope" true.
	if (frappe.form && frappe.form.formatters && frappe.form.formatters.Time) {
		const stock_time_formatter = frappe.form.formatters.Time;
		frappe.form.formatters.Time = function (value, df) {
			const owner = df && df.parent;
			if (value && owner && String(owner).indexOf("CBT ") === 0) {
				return cbt.time_label(value);
			}
			// `arguments` rather than (value, df): the stock formatter takes
			// four parameters in other branches of this object and a future
			// frappe may widen this one.
			return stock_time_formatter.apply(this, arguments);
		};
	} else {
		// Fail SOFT and LOUD, the same way the ControlData guard at the top of
		// this file does. Silently skipping the wrapper would revert every
		// read-only CBT Time to HH:mm:ss with nothing to grep for — a UI
		// regression that looks like nobody's fault.
		console.error(
			"[CBT] frappe.form.formatters.Time is not loaded — read-only Time " +
				"fields will render HH:mm:ss (section-23)."
		);
	}

	// --- one-click editing of a Time cell in a child grid ---------------------
	//
	// grid_row.js focuses `input[type="Text"]:first` when a grid cell is clicked
	// (grid_row.js:1102). Per the HTML spec the `type` attribute selector is ASCII
	// case-insensitive, so that matched the stock control's text input and does
	// NOT match `type="time"` — leaving the row editable but unfocused, and
	// costing a second click on every Court Hours and Rate Rule edit.
	//
	// That regression would be introduced BY this section, whose entire subject is
	// time-input UX, so it is fixed here rather than left for a bug report.
	//
	// IT MUST BE A CAPTURE-PHASE NATIVE LISTENER. A jQuery delegated handler on
	// `document` — the obvious implementation — is DEAD CODE for the only click
	// that matters, and this was measured, not guessed: a probe bound exactly that
	// way never fired once.
	//
	// grid_row.js's own click handler on the cell ends with `return out`, where
	// `out` is the return value of `toggle_editable_row()` — and that method
	// returns **false** on the branch that makes a row editable. jQuery treats a
	// handler returning false as `preventDefault()` PLUS `stopPropagation()`, so
	// the event never reaches `document` and no delegated handler runs. It only
	// stops on the FIRST click of a row (the second finds the row already
	// editable, leaves `out` undefined, and propagates) — i.e. it breaks exactly
	// the click this fix exists for.
	//
	// Capture runs before the target's own handlers, and a later
	// stopPropagation() cannot retroactively cancel it. The control does not
	// exist yet at capture time — grid_row.js builds it in the handler we are
	// front-running — so the focus is deferred by one animation frame, by which
	// point that synchronous handler has finished.
	if (!window.__cbt_time_grid_focus_bound) {
		window.__cbt_time_grid_focus_bound = true;
		document.addEventListener(
			"click",
			function (event) {
				const cell =
					event.target &&
					event.target.closest &&
					event.target.closest(".grid-static-col[data-fieldtype='Time']");
				if (!cell) return;
				// Read-only Time cells are skipped exactly as grid_row.js's own
				// trigger_focus skips them. Live, not hypothetical: CBT Booking
				// Rate Segment's start/end are read-only Times in a grid.
				const df = $(cell).data("df");
				if (df && df.read_only) return;
				requestAnimationFrame(() => {
					// `:not(:disabled)` covers a control rendered read-only by
					// refresh_input; a null result covers the grid HEADER row,
					// which carries the same classes but holds no control.
					const input = cell.querySelector("input[type='time']:not(:disabled)");
					if (input && document.activeElement !== input) input.focus();
				});
			},
			true
		);
	}
})();
