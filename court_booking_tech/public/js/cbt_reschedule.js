// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// Reschedule dialog (section-15) — ONE implementation, two desk entry points:
// the Court Board's detail/verification dialogs and the CBT Court Booking form.
//
// Loaded on demand with frappe.require (the cbt_branch.js Leaflet pattern), NOT
// app_include_js: this file has no business in every desk page load of a
// multi-tenant site, and the section's hooks.py delta stays None.
//
// The dialog's job is to make the move WYSIWYG. Staff must see what the new
// slot costs before they say it out loud, because a moved booking can
// legitimately cost differently once a court prices 18:00 apart from 17:00.

frappe.provide("cbt.reschedule");

cbt.reschedule.ASSET = "/assets/court_booking_tech/js/cbt_reschedule.js";

// Shown exactly where api/bookings.reschedule_booking accepts (its rejects are
// the backstop, not the UX). An extension child is excluded because moving it
// would detach it from the session it extends; an Extended parent is excluded
// because its child would be left behind.
cbt.reschedule.can_move = function (booking_status, extended_from) {
	return ["Reserved", "Confirmed"].includes(booking_status) && !extended_from;
};

/**
 * Open the reschedule dialog for a booking.
 *
 * Takes only a NAME — it fetches the booking detail itself, so both entry
 * points stay one line and the dialog is always built from data that is fresh
 * at open time (S7 doctrine). `opts.on_done(result)` fires after the server
 * accepts the move.
 */
cbt.reschedule.open = function (booking, opts) {
	opts = opts || {};
	frappe
		.call({
			method: "court_booking_tech.api.board.get_booking_detail",
			type: "GET",
			args: { booking: booking },
			freeze: true,
		})
		.then((r) => {
			if (!r.message) return;
			const detail = r.message;
			if (!cbt.reschedule.can_move(detail.booking_status, detail.extended_from)) {
				frappe.msgprint({
					title: __("Cannot reschedule"),
					indicator: "orange",
					message: __(
						"Only a Reserved or Confirmed booking that is not part of an extension can be moved."
					),
				});
				return;
			}
			return frappe.db
				.get_list("CBT Court", {
					filters: { company: detail.company, is_active: 1 },
					// `branch` is load-bearing, not decoration: get_availability
					// is keyed by BRANCH, and a cross-branch move needs the
					// target court's own branch to fetch its grid.
					fields: ["name", "court_name", "branch"],
					order_by: "name asc",
					limit: 500,
				})
				.then((courts) =>
					cbt.reschedule._build(detail, courts || [], opts)
				);
		});
};

cbt.reschedule._build = function (detail, courts, opts) {
	const by_name = {};
	courts.forEach((court) => {
		by_name[court.name] = court;
	});
	// Fail loud rather than silently dropping the current court from the list:
	// a staff user whose court list came back scoped differently must not be
	// shown a dialog that cannot express "leave the court alone".
	if (!by_name[detail.court]) {
		courts = courts.concat([
			{
				name: detail.court,
				court_name: detail.court_name || detail.court,
				branch: detail.branch,
			},
		]);
		by_name[detail.court] = courts[courts.length - 1];
	}
	const multi_branch = new Set(courts.map((c) => c.branch)).size > 1;
	const court_options = courts.map((court) => ({
		value: court.name,
		label: multi_branch
			? `${court.court_name} · ${court.branch}`
			: court.court_name,
	}));

	// Section-14: does the server re-price this booking at the new slot, or
	// carry its flat rate over? Exactly the branch reschedule_booking takes —
	// segments present means the seam re-resolves, segments empty means the
	// original's single rate is inherited. The quote below MUST follow the
	// same branch or the dialog shows a price the booking then contradicts —
	// so the inherited rate is SENT to get_quote (Backlog B15) rather than
	// multiplied here.
	const reprices = (detail.rate_segments || []).length > 0;
	// What reschedule_booking copies onto the replacement — sent as an explicit
	// value (0 included) so the server never resolves a membership instead.
	const discount = flt(detail.discount_percent) || 0;

	let quote = null;
	let quote_error = null;
	let slots_loaded = false;

	const dialog = new frappe.ui.Dialog({
		title: __("Reschedule {0}", [detail.name]),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options: `<div class="text-muted small" style="margin-bottom:.5rem">
					${__("Currently")}: <b>${frappe.utils.escape_html(
						detail.court_name || detail.court
					)}</b> ·
					${moment(detail.booking_date, "YYYY-MM-DD").format("ddd, D MMM YYYY")}
					${cbt.fmt.timeRange(detail.start_time, detail.end_time)} ·
					${format_currency(detail.total_amount, "PHP")}
					<div>${__(
						"The original is cancelled and replaced by a new booking. Payment already verified is carried over."
					)}</div>
				</div>`,
			},
			{
				fieldtype: "Select",
				fieldname: "court",
				label: __("Court"),
				options: court_options,
				default: detail.court,
				reqd: 1,
				change: () => reload_slots(),
			},
			{
				fieldtype: "Date",
				fieldname: "booking_date",
				label: __("Date"),
				default: detail.booking_date,
				reqd: 1,
				change: () => reload_slots(),
			},
			{
				fieldtype: "Select",
				fieldname: "start_time",
				label: __("Start Time"),
				options: [],
				reqd: 1,
				description: __(
					"Free slots on that court, plus the ones this booking already holds."
				),
				change: () => refresh_quote(),
			},
			{
				fieldtype: "Int",
				fieldname: "number_of_slots",
				label: __("Slots"),
				default: cint(detail.number_of_slots) || 1,
				reqd: 1,
				change: () => refresh_quote(),
			},
			{ fieldtype: "HTML", fieldname: "estimate" },
		],
		primary_action_label: __("Reschedule"),
		primary_action: (values) => {
			frappe
				.call({
					method: "court_booking_tech.api.bookings.reschedule_booking",
					args: {
						name: detail.name,
						court: values.court,
						booking_date: values.booking_date,
						start_time: values.start_time,
						number_of_slots: cint(values.number_of_slots) || 1,
					},
					freeze: true,
				})
				.then((r) => {
					if (!r.message) return;
					dialog.hide();
					frappe.show_alert({
						message: __("Moved to {0} — {1}", [
							r.message.name,
							__(r.message.booking_status),
						]),
						indicator: "green",
					});
					opts.on_done && opts.on_done(r.message);
				});
		},
	});

	// --- slot options -------------------------------------------------------

	const reload_slots = () => {
		const court = dialog.get_value("court");
		const date = dialog.get_value("booking_date");
		if (!court || !date) return Promise.resolve();
		const branch = (by_name[court] || {}).branch || detail.branch;
		// Sequence token, same discipline as the board's load(): a fast second
		// change must never let the first response win the render.
		const seq = (dialog._slot_seq = (dialog._slot_seq || 0) + 1);
		return frappe
			.xcall(
				"court_booking_tech.slots.get_availability",
				{ branch: branch, date: date },
				"GET" // xcall defaults to POST (S10 lesson 17a)
			)
			.then((data) => {
				if (seq !== dialog._slot_seq) return;
				const row = (data.courts || []).find((c) => c.court === court);
				const options = [];
				for (const slot of (row && row.slots) || []) {
					// B45: the option's LABEL is the app's one time language; its
					// VALUE stays the raw "HH:MM:SS" the server takes (helpers/
					// board and set_reschedule_target both select by value).
					const label = cbt.fmt.timeShort(slot.start_time);
					if (slot.booking === detail.name) {
						// The booking being moved still holds its own slots
						// until the server cancels it. Offering them labelled
						// is what makes "same slot, one hour longer" and "shift
						// it 30 minutes" expressible at all; the API's
						// identical-target reject catches the no-op.
						options.push({
							value: slot.start_time,
							label: `${label} · ${__("current")}`,
						});
					} else if (slot.status === "available") {
						options.push({ value: slot.start_time, label: label });
					} else if (slot.status === "past") {
						// Staff MAY book the past — _reject_past_for_customers
						// fires only for customer-created bookings. Offering
						// these keeps the dialog and the API agreeing rather
						// than refusing what the server would accept.
						options.push({
							value: slot.start_time,
							label: `${label} · ${__("past")}`,
						});
					}
				}
				const previous = dialog.get_value("start_time");
				const values = options.map((o) => o.value);
				let pick = null;
				if (previous && values.includes(previous)) {
					pick = previous;
				} else if (values.includes(detail.start_time)) {
					pick = detail.start_time;
				} else if (values.length) {
					pick = values[0];
				}
				dialog.set_df_property("start_time", "options", options);
				slots_loaded = true;
				if (!pick) {
					quote = null;
					quote_error = __(
						"No free slots on that court for this date — pick another date or court."
					);
					render_estimate();
					dialog._quote_settled = true;
					return;
				}
				return Promise.resolve(dialog.set_value("start_time", pick)).then(
					() => refresh_quote()
				);
			})
			.catch((err) => {
				console.error("CBT availability lookup failed", err);
				if (seq !== dialog._slot_seq) return;
				quote = null;
				quote_error = __("Could not load that day's slots — please retry.");
				render_estimate();
				dialog._quote_settled = true;
			});
	};

	// --- money --------------------------------------------------------------

	const refresh_quote = () => {
		if (!slots_loaded) return Promise.resolve();
		const court = dialog.get_value("court");
		const date = dialog.get_value("booking_date");
		const start = dialog.get_value("start_time");
		const n = cint(dialog.get_value("number_of_slots")) || 1;
		if (!court || !date || !start) return Promise.resolve();
		// Settle flag (test infrastructure, deliberately not polish). Both the
		// slot fetch and the quote are async, so an E2E that gated on "a
		// get_quote response arrived" would hang whenever set_value lands on
		// the value the field already holds and fires no change event. One
		// boolean that is false while ANY pricing round-trip is in flight and
		// true once the newest one has rendered is the honest gate.
		dialog._quote_settled = false;
		const seq = (dialog._quote_seq = (dialog._quote_seq || 0) + 1);
		// Both staff-gated params (section-18 + B15) — this dialog is desk-only,
		// so the session always holds the company binding the gate wants.
		// The `!= null` guard is load-bearing: flt(undefined) is 0, and the
		// server honours an explicit 0 as a real rate — a detail payload that
		// ever dropped the field would otherwise quote free court time under a
		// confident "New total". Missing → fall back to server re-pricing.
		const args = {
			court: court,
			number_of_slots: n,
			booking_date: date,
			start_time: start,
			discount_percent: discount,
			// Backlog B27: a move is not a new sale — the fee the booking
			// already carries travels with it (reschedule_booking copies it),
			// so the quote is told that fee rather than reading the tiers.
			// 0 is a real value here: "carries none".
			platform_fee: flt(detail.platform_fee) || 0,
		};
		if (!reprices && detail.hourly_rate != null) {
			args.hourly_rate = flt(detail.hourly_rate);
		}
		return frappe
			.xcall("court_booking_tech.api.portal.get_quote", args, "GET")
			.then((res) => {
				if (seq !== dialog._quote_seq) return;
				quote = res;
				quote_error = null;
				render_estimate();
				dialog._quote_settled = true;
			})
			.catch((err) => {
				if (seq !== dialog._quote_seq) return;
				// A run that overflows closing time is refused by the quote AND
				// by the insert — say so rather than showing a number the
				// server would never honour.
				console.error("CBT quote failed", err);
				quote = null;
				quote_error = __(
					"Could not price this selection — it may run past closing time."
				);
				render_estimate();
				dialog._quote_settled = true;
			});
	};

	const render_estimate = () => {
		const $wrapper = dialog.fields_dict.estimate.$wrapper;
		if (quote_error) {
			$wrapper.html(
				`<div class="text-danger small" style="text-align:right"
					data-testid="reschedule-total">${quote_error}</div>`
			);
			return;
		}
		if (!quote) {
			$wrapper.html(
				`<div class="text-muted small" style="text-align:right">${__(
					"Pricing…"
				)}</div>`
			);
			return;
		}

		// WYSIWYG money. Every figure here is the SERVER's (Backlog B15 closed
		// the last gap): the discount travels as `discount_percent` (section-18,
		// B4) and an inherited flat rate travels as `hourly_rate`, so
		// `quote.total_amount` is what reschedule_booking will charge — the
		// same single-rounding formula, the same rate branch. Nothing is
		// multiplied on this side any more, which is why the block says
		// "Total" and not "Estimated".
		const subtotal = flt(quote.subtotal);
		// COUPLING: get_quote forces `segments` EMPTY whenever `hourly_rate` was
		// sent, so the inherited branch always lands in the one-line `else`
		// below and the "rate carried over" span stays honest. If that server
		// rule ever changes, this render changes with it.
		const segments = quote.segments || [];
		let breakdown;
		if (segments.length > 1) {
			breakdown = segments
				.map(
					(segment) =>
						`<div data-testid="rate-breakdown">${cbt.fmt.timeRange(
							segment.start_time,
							segment.end_time
						)}
						${
							segment.label
								? "· " + frappe.utils.escape_html(segment.label) + " "
								: ""
						}· ${format_currency(segment.hourly_rate, "PHP")}/hr:
						${format_currency(segment.amount, "PHP")}</div>`
				)
				.join("");
		} else {
			breakdown = `<div>${format_currency(quote.hourly_rate, "PHP")}/hr ×
				${quote.duration_hours}: ${format_currency(subtotal, "PHP")}
				${
					reprices
						? ""
						: `<span class="text-muted">· ${__("rate carried over")}</span>`
				}</div>`;
		}
		const shown_discount = flt(quote.discount_percent);
		const fee = flt(quote.platform_fee);
		$wrapper.html(
			`<div class="text-muted" style="text-align:right">
				${breakdown}
				${
					shown_discount
						? `<div>${__("Less {0}%", [shown_discount])}: -${format_currency(
								flt(quote.discount_amount),
								"PHP"
						  )}</div>`
						: ""
				}
				${
					fee
						? `<div data-testid="booking-fee">${__("Booking fee")}: +${format_currency(
								fee,
								"PHP"
						  )} <span class="text-muted">· ${__("carried over")}</span></div>`
						: ""
				}
				<div data-testid="reschedule-total">${__("New total")}:
					<b>${format_currency(quote.total_amount, "PHP")}</b></div>
			</div>`
		);
	};

	render_estimate();
	reload_slots();
	dialog.show();
};
