// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
/* eslint-disable */

const CBT_MONTHS = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
];

// Backlog B21(b): the month-close gesture lives on the report a Platform Admin
// is already reading. The indicator in the page head says whether the month on
// screen is LIVE (recomputed on every run) or CLOSED (frozen — the report reads
// the CBT Platform Month Close record instead). Both read the server, never a
// client-side guess: `get_close_status` is the same seam the tests pin.
function cbt_close_filters(report) {
	return {
		year: cint(report.get_filter_value("year")),
		month: cint(report.get_filter_value("month")),
	};
}

function cbt_refresh_close_status(report) {
	const filters = cbt_close_filters(report);
	// Clear FIRST: a run that renders no datatable would otherwise leave the
	// previous month's pill in the head, saying "Closed on …" about a month
	// that is live. "No claim" beats "wrong claim" while the answer is in flight.
	report.page.clear_indicator();
	if (!filters.year || !filters.month) return;
	frappe
		.xcall(
			"court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close.get_close_status",
			filters,
			"GET"
		)
		.then((status) => {
			if (status.closed) {
				report.page.set_indicator(
					__("Closed on {0} by {1}", [
						frappe.datetime.str_to_user(status.closed_at),
						status.closed_by,
					]),
					"orange"
				);
			} else {
				report.page.set_indicator(__("Live — not closed"), "green");
			}
		})
		.catch((err) => {
			// A tenant seat never reaches this report (the server refuses it),
			// so a failure here is a real error worth seeing, not a state.
			console.error("CBT month-close status failed", err);
			report.page.clear_indicator();
		});
}

function cbt_close_month(report) {
	const filters = cbt_close_filters(report);
	const label = `${__(CBT_MONTHS[filters.month - 1])} ${filters.year}`;
	frappe.confirm(
		__(
			"Close {0}?<br><br>Every tenant's figures for that month are copied into a CBT Platform Month Close and FROZEN — this report will read that record from now on, so a booking confirmed or cancelled later can no longer change the month. Nothing is issued to any tenant. To reopen, delete the close.",
			[label]
		),
		() => {
			frappe
				.xcall(
					"court_booking_tech.court_booking_tech.doctype.cbt_platform_month_close.cbt_platform_month_close.close_month",
					filters
				)
				.then((result) => {
					frappe.show_alert({
						message: __("{0} closed — {1} tenant rows frozen", [
							result.name,
							result.rows,
						]),
						indicator: "orange",
					});
					report.refresh();
				});
			// A refusal (month not over, already closed, not a platform seat)
			// surfaces as frappe's own error dialog with the server's sentence.
		}
	);
}

frappe.query_reports["CBT Platform Revenue"] = {
	filters: [
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			// Value is the month NUMBER (the report reads an int); the label is
			// the name, so the filter reads like a human wrote it.
			options: CBT_MONTHS.map((name, i) => ({ value: i + 1, label: __(name) })),
			default: new Date().getMonth() + 1,
			reqd: 1,
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Int",
			default: new Date().getFullYear(),
			reqd: 1,
		},
	],

	onload(report) {
		report.page.add_inner_button(__("Close this month"), () => cbt_close_month(report));
		cbt_refresh_close_status(report);
	},

	after_datatable_render() {
		// Fires after every refresh — a filter change, a close, a reload — so
		// the indicator always describes the month on screen.
		cbt_refresh_close_status(frappe.query_report);
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// The total row is built server-side (add_total_row sums every numeric
		// column, including the commission RATE, which is meaningless) — so it
		// arrives as a normal row and has to be styled as a total here.
		if (data && data.is_total_row) {
			return `<span style="font-weight:600">${value}</span>`;
		}
		// The evasion signal is the reason this report exists — make a non-zero
		// count impossible to scroll past, but never style it as an error: a
		// genuine unverified proof looks exactly the same and a human decides.
		if (
			data &&
			column.fieldname === "expired_with_proof_count" &&
			cint(data.expired_with_proof_count) > 0
		) {
			value = `<span style="color: var(--orange-600); font-weight: 600">${value}</span>`;
		}
		if (data && column.fieldname === "status" && data.status === "Suspended") {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
