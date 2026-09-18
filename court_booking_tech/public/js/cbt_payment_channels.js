// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// Backlog B29 (section-25): fill a dialog's `payment_channel` Select with the
// company's ENABLED channels for a payment method — Cash → the cash drawers,
// Fund Transfer → GCash and the banks, Free → none — pre-selecting `preselect`
// when it is offered (or appending it as "(disabled)" when it is not, so a
// payment already made through a since-disabled channel keeps it), else the
// first option.
//
// Loaded desk-wide through hooks.app_include_js, because FOUR surfaces on
// THREE different pages use it (the court board's extension dialog, the open
// play board's Add Players and Mark Paid dialogs, the booking form's extension
// dialog) and a page script's `window.` global is only defined on that page —
// the first draft put it in cbt_court_board.js and the open play board's Add
// Players dialog threw before it could open. Same desk-only, site-wide reach
// as cbt_time_control.js: keep it lean.
//
// `_channels_settled` is the E2E gate (the `_quote_settled` idiom): false the
// moment a fetch starts, true once the options have rendered — on the failure
// path too, so a dead endpoint fails an assertion instead of a timeout.
window.cbt_sync_channel_select = function (dialog, company, payment_method, preselect) {
	const control = dialog.fields_dict.payment_channel;
	if (!control) return Promise.resolve();
	dialog._channels_settled = false;
	const seq = (dialog._channels_seq = (dialog._channels_seq || 0) + 1);
	if (!payment_method || payment_method === "Free") {
		control.df.options = [];
		control.refresh();
		dialog.set_value("payment_channel", "");
		dialog._channels_settled = true;
		return Promise.resolve();
	}
	return frappe
		.xcall(
			"court_booking_tech.api.channels.list_company_channels",
			{ company: company, payment_method: payment_method },
			"GET" // xcall defaults to POST (S10 lesson 17a)
		)
		.then((rows) => {
			if (seq !== dialog._channels_seq) return;
			const options = (rows || []).map((r) => ({ value: r.name, label: r.label }));
			if (preselect && !options.some((o) => o.value === preselect)) {
				// A since-disabled channel on record: offer it under its LABEL,
				// not its docname (ducky finding 7). Staff hold read on their
				// own channels, disabled ones included.
				return frappe.db
					.get_value("CBT Payment Channel", preselect, "label")
					.then((r) => {
						const label = (r && r.message && r.message.label) || preselect;
						options.push({ value: preselect, label: `${label} (${__("disabled")})` });
						return options;
					})
					.catch(() => {
						options.push({ value: preselect, label: `${preselect} (${__("disabled")})` });
						return options;
					});
			}
			return options;
		})
		.then((options) => {
			if (!options || seq !== dialog._channels_seq) return;
			control.df.options = options;
			control.refresh();
			const pick =
				preselect && options.some((o) => o.value === preselect)
					? preselect
					: options.length
					? options[0].value
					: "";
			dialog.set_value("payment_channel", pick);
			dialog._channels_settled = true;
		})
		.catch((err) => {
			if (seq !== dialog._channels_seq) return;
			console.error("CBT channel list failed", err);
			control.df.options = [];
			control.refresh();
			dialog._channels_settled = true;
		});
};
