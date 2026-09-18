// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// CBT Platform Statement form (Backlog B21(a)): every field is frozen, so the
// form's whole job is the status in the page head and the two platform-only
// actions — Mark paid and Cancel statement. A tenant admin opens the same form
// and sees the figures, the status and the Print menu, and no buttons.

frappe.ui.form.on("CBT Platform Statement", {
	refresh(frm) {
		if (frm.is_new()) return;
		const colors = { Issued: "orange", Paid: "green", Cancelled: "red" };
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
		if (!cbt_statement_is_platform()) return;

		if (frm.doc.status === "Issued") {
			frm.add_custom_button(__("Mark paid"), () => cbt_statement_mark_paid(frm));
		}
		if (frm.doc.status !== "Cancelled") {
			frm.add_custom_button(__("Cancel statement"), () =>
				cbt_statement_cancel(frm)
			);
		}
	},
});

function cbt_statement_is_platform() {
	const roles = frappe.user_roles || [];
	return (
		frappe.session.user === "Administrator" ||
		roles.includes("CBT Platform Admin") ||
		roles.includes("System Manager")
	);
}

function cbt_statement_mark_paid(frm) {
	// Backlog B29: the PLATFORM's own channels are fetched first so the dialog
	// opens with the list in place (a dialog that fills in after it opens is
	// how an E2E — and a hurried admin — submits an empty Select).
	frappe
		.xcall("court_booking_tech.api.channels.list_platform_channels", {}, "GET")
		.then((rows) => {
			const channels = (rows || []).map((r) => ({ value: r.name, label: r.label }));
			const dialog = new frappe.ui.Dialog({
				title: __("Mark {0} paid", [frm.doc.name]),
				fields: [
					{
						fieldname: "paid_on",
						fieldtype: "Date",
						label: __("Paid On"),
						reqd: 1,
						default: frappe.datetime.get_today(),
						description: __("The day the tenant's payment arrived."),
					},
					{
						fieldname: "payment_channel",
						fieldtype: "Select",
						label: __("Received Via"),
						options: channels,
						default: channels.length ? channels[0].value : "",
						description: __(
							"The platform's channel the payment arrived through — printed on the statement and posted in the platform's books."
						),
					},
					{
						fieldname: "payment_reference",
						fieldtype: "Data",
						label: __("Payment Reference"),
						description: __(
							"The transfer or deposit reference the tenant sent — printed on the statement."
						),
					},
				],
				primary_action_label: __("Mark paid"),
				primary_action(values) {
					frappe
						.xcall(
							"court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement.mark_paid",
							{
								name: frm.doc.name,
								paid_on: values.paid_on,
								payment_reference: values.payment_reference || "",
								payment_channel: values.payment_channel || "",
							}
						)
						.then(() => {
							dialog.hide();
							frappe.show_alert({ message: __("Marked paid"), indicator: "green" });
							frm.reload_doc();
						});
				},
			});
			dialog.show();
		});
}

function cbt_statement_cancel(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Cancel {0}", [frm.doc.name]),
		fields: [
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason"),
				reqd: 1,
				description: __(
					"The statement stays on record with its number consumed and this reason printed across it. Issue statements on the month close again to re-issue it under the next number."
				),
			},
		],
		primary_action_label: __("Cancel statement"),
		primary_action(values) {
			frappe
				.xcall(
					"court_booking_tech.court_booking_tech.doctype.cbt_platform_statement.cbt_platform_statement.cancel_statement",
					{ name: frm.doc.name, reason: values.reason }
				)
				.then(() => {
					dialog.hide();
					frappe.show_alert({ message: __("Statement cancelled"), indicator: "red" });
					frm.reload_doc();
				});
		},
	});
	dialog.show();
}
