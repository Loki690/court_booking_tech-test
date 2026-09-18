// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// CBT Platform Month Close form (Backlog B21(a)): the frozen figures are what
// a Platform Admin is looking at when they decide to bill, so the "Issue
// statements" action lives here, on the record it issues from — and the
// headline says what has already been issued for the month.

frappe.ui.form.on("CBT Platform Month Close", {
	refresh(frm) {
		if (frm.is_new()) return;
		cbt_close_statement_headline(frm);
		frm.add_custom_button(__("Issue statements"), () => cbt_close_issue_statements(frm));
	},
});

function cbt_close_statement_headline(frm) {
	frappe.db
		.get_list("CBT Platform Statement", {
			filters: { period: frm.doc.name },
			fields: ["name", "status"],
			limit: 0,
		})
		.then((rows) => {
			const live = rows.filter((r) => r.status !== "Cancelled");
			const paid = live.filter((r) => r.status === "Paid").length;
			frm.dashboard.clear_headline();
			if (!live.length) {
				frm.dashboard.set_headline(
					`<span class="indicator orange">${__(
						"No statements issued yet — Issue statements gives every tenant with an Amount Due a numbered statement they can read and print."
					)}</span>`
				);
				return;
			}
			const route = `/desk/cbt-platform-statement?period=${encodeURIComponent(frm.doc.name)}`;
			frm.dashboard.set_headline(
				`<span class="indicator ${paid === live.length ? "green" : "blue"}">${__(
					"{0} statement(s) issued for this month, {1} paid",
					[live.length, paid]
				)} — <a href="${route}">${__("open them")}</a></span>`
			);
		});
}

function cbt_close_issue_statements(frm) {
	frappe.confirm(
		__(
			"Issue statements for {0}?<br><br>One numbered statement per tenant with an Amount Due, copied from the frozen figures on this record. Tenants already holding a live statement for this month are skipped.",
			[frm.doc.name]
		),
		() => {
			frappe
				.xcall(
					"court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement.issue_statements",
					{ period: frm.doc.name }
				)
				.then((result) => {
					const list = (names) => (names && names.length ? names.join(", ") : "—");
					frappe.msgprint({
						title: __("Statements issued"),
						indicator: result.issued.length ? "green" : "orange",
						message: __("Issued: {0}<br>Already issued: {1}<br>Nothing due: {2}", [
							list(result.issued),
							list(result.already_issued),
							list(result.nothing_due),
						]),
					});
					frm.reload_doc();
				});
		}
	);
}
