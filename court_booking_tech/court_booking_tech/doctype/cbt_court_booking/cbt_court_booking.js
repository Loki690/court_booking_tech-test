// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

// Desk UX for the booking record (the Court Board page arrives in section-7).
// Status changes go ONLY through the action buttons -> whitelisted APIs; the
// booking_status field itself is read-only. The client-side end-time/total
// preview is a courtesy — the server recomputes authoritatively in validate.

frappe.ui.form.on("CBT Court Booking", {
	setup(frm) {
		frm.set_query("branch", () => {
			const filters = { is_active: 1 };
			if (frm.doc.company) filters.company = frm.doc.company;
			return { filters };
		});
		frm.set_query("court", () => {
			const filters = { is_active: 1 };
			if (frm.doc.branch) filters.branch = frm.doc.branch;
			return { filters };
		});
		// The staff-gated, relationship-scoped picker (B3, rescoped by B38) —
		// never frappe's stock user_query. See docs/sections/section-13.md.
		frm.set_query("customer", () => ({
			query: "court_booking_tech.api.board.customer_query",
		}));
		// Backlog B29: only this company's ENABLED channels of the method's
		// kind. The server refuses anything else; this keeps the picker honest.
		frm.set_query("payment_channel", () => {
			const filters = { enabled: 1, scope: "Tenant" };
			if (frm.doc.company) filters.company = frm.doc.company;
			if (frm.doc.payment_method) {
				filters.kind = frm.doc.payment_method === "Cash" ? "Cash" : "Transfer";
			}
			return { filters };
		});
	},

	payment_method(frm) {
		// A method change makes the picked channel the wrong KIND — clear it and
		// let the server default it (Free carries none).
		if (frm.doc.payment_channel) frm.set_value("payment_channel", null);
	},

	refresh(frm) {
		frm._slot_cfg = null;
		// Cleared unconditionally, then re-set by _preview if it still applies:
		// a saved booking must never keep the "priced when you save" notice.
		frm.set_intro("");
		if (frm.doc.branch) frm.trigger("_load_slot_cfg");
		if (frm.doc.court) frm.trigger("_load_rate_rules");
		if (frm.is_new()) return;

		const status = frm.doc.booking_status;
		if (status === "Reserved") {
			frm.add_custom_button(__("Confirm Payment"), () => {
				frappe.confirm(
					__("Confirm this booking as paid & verified?"),
					() => frm.trigger("_call_api_confirm")
				);
			}).addClass("btn-primary");
		}
		if (status === "Confirmed") {
			frm.add_custom_button(__("Extend Session"), () =>
				frm.trigger("_extend_dialog")
			).addClass("btn-primary");
		}
		// Section-15: the SAME dialog the Court Board opens — one
		// implementation, two entry points, loaded on demand (the Leaflet
		// pattern from cbt_branch.js). Shown exactly where
		// api/bookings.reschedule_booking accepts: an extension child is
		// excluded because moving it would detach it from the session it
		// extends, and an Extended parent because its child would be orphaned.
		if (["Reserved", "Confirmed"].includes(status) && !frm.doc.extended_from) {
			frm.add_custom_button(__("Reschedule"), () => {
				frappe.require(
					"/assets/court_booking_tech/js/cbt_reschedule.js",
					() => {
						cbt.reschedule.open(frm.doc.name, {
							// The original is Cancelled by the move, so
							// staying on it would leave the user staring at a
							// dead record. Follow the booking.
							on_done: (result) =>
								frappe.set_route(
									"Form",
									"CBT Court Booking",
									result.name
								),
						});
					}
				);
			});
		}
		// Section-16 attendance. Check in is what keeps a paid booking off the
		// release path; Undo is the way back once it has been released.
		if (["Confirmed", "Extended"].includes(status) && !frm.doc.checked_in_at) {
			frm.add_custom_button(__("Check In"), () =>
				frm.trigger("_call_api_check_in")
			);
		}
		if (status === "No Show") {
			frm.add_custom_button(__("Undo No-show"), () => {
				frappe.confirm(
					__(
						"Put this booking back on the board? It fails if the slot has already been resold."
					),
					() => frm.trigger("_call_api_undo_no_show")
				);
			}).addClass("btn-primary");
		}
		if (["Reserved", "Confirmed", "Extended", "No Show"].includes(status)) {
			// Section-26: Confirmed / Extended / No Show carry a PAID document,
			// so their cancel is a refund — a reason is typed, and the server
			// refuses any seat but a Company Admin with its own sentence (the
			// board reads that verdict from the payload; the form just asks).
			const paid = status !== "Reserved";
			frm.add_custom_button(paid ? __("Cancel & Refund…") : __("Cancel Booking"), () => {
				if (paid) {
					frappe.prompt(
						[
							{
								fieldtype: "HTML",
								options: `<p>${__(
									status === "No Show"
										? "This booking was already released, so no slot is freed — its payment is written off and its billing statement stops counting as revenue."
										: "This booking is PAID. Cancelling it is a refund: the slot becomes available again and its payment is reversed in the books."
								)}</p>`,
							},
							{
								fieldtype: "Small Text",
								fieldname: "reason",
								label: __("Reason for the refund"),
								reqd: 1,
							},
						],
						(values) => {
							frm._refund_reason = values.reason;
							frm.trigger("_call_api_cancel");
						},
						__("Cancel & refund"),
						__("Cancel & Refund")
					);
					return;
				}
				frappe.confirm(
					__("Cancel this booking? The slot becomes available again."),
					() => {
						frm._refund_reason = null;
						frm.trigger("_call_api_cancel");
					}
				);
			});
		}
		if (frm.doc.billing_doc) {
			// The company's printable billing statement (section-6) — opens
			// the print view directly so the desk can hand it over in one
			// click; printable in BOTH paid and unpaid states.
			frm.add_custom_button(__("Billing Statement"), () => {
				const url =
					"/printview?doctype=" +
					encodeURIComponent("CBT Booking Invoice") +
					"&name=" +
					encodeURIComponent(frm.doc.billing_doc) +
					"&format=" +
					encodeURIComponent("CBT Billing Statement") +
					"&no_letterhead=1";
				window.open(url, "_blank");
			});
		}
	},

	_call_api_confirm(frm) {
		frappe
			.call({
				method: "court_booking_tech.api.bookings.confirm_booking",
				args: { name: frm.doc.name },
				freeze: true,
			})
			.then(() => frm.reload_doc());
	},

	_call_api_check_in(frm) {
		frappe
			.call({
				method: "court_booking_tech.api.bookings.check_in",
				args: { name: frm.doc.name },
				freeze: true,
			})
			// reload_doc is NOT optional: check_in writes through db.set_value,
			// which bumps `modified`, so an open form would 417 on its next save
			// (TimestampMismatchError — the billing.py scar).
			.then(() => frm.reload_doc());
	},

	_call_api_undo_no_show(frm) {
		frappe
			.call({
				method: "court_booking_tech.api.bookings.undo_no_show",
				args: { name: frm.doc.name },
				freeze: true,
			})
			.then(() => frm.reload_doc());
	},

	_call_api_cancel(frm) {
		frappe
			.call({
				method: "court_booking_tech.api.bookings.cancel_booking",
				args: { name: frm.doc.name, reason: frm._refund_reason || undefined },
				freeze: true,
			})
			.then(() => frm.reload_doc());
	},

	_extend_dialog(frm) {
		const d = new frappe.ui.Dialog({
			title: __("Extend Session"),
			fields: [
				{
					fieldname: "slots",
					fieldtype: "Int",
					label: __("Additional Slots"),
					default: 1,
					reqd: 1,
				},
				{
					fieldname: "payment_method",
					fieldtype: "Select",
					label: __("Payment Method"),
					options: "Cash\nFund Transfer\nFree",
					default: frm.doc.payment_method,
					reqd: 1,
					change: () => sync_channels(),
				},
				{
					// Backlog B29: the extension's own channel (same picker as
					// the board's, filled by the desk-wide helper).
					fieldname: "payment_channel",
					fieldtype: "Select",
					label: __("Paid via"),
					options: [],
					depends_on: "eval:doc.payment_method!=='Free'",
				},
			],
			primary_action_label: __("Extend"),
			primary_action(values) {
				d.hide();
				frappe
					.call({
						method: "court_booking_tech.api.bookings.extend_booking",
						args: {
							name: frm.doc.name,
							slots: values.slots,
							payment_method: values.payment_method,
							payment_channel:
								values.payment_method === "Free"
									? null
									: values.payment_channel || null,
						},
						freeze: true,
					})
					.then((r) => {
						if (r.message) {
							frappe.set_route("Form", "CBT Court Booking", r.message);
						} else {
							frm.reload_doc();
						}
					});
			},
		});
		// The same Select filler the two boards use — a desk-wide asset
		// (hooks.app_include_js → public/js/cbt_payment_channels.js), so it
		// exists on this form without any page script being loaded.
		const sync_channels = () =>
			window.cbt_sync_channel_select(d, frm.doc.company, d.get_value("payment_method"));
		sync_channels();
		d.show();
	},

	company(frm) {
		if (
			frm.doc.branch &&
			frm.doc.company &&
			frm.doc.__branch_company &&
			frm.doc.__branch_company !== frm.doc.company
		) {
			frm.set_value("branch", null);
			frm.set_value("court", null);
		}
	},

	branch(frm) {
		if (!frm.doc.branch) return;
		frappe.db.get_value("CBT Branch", frm.doc.branch, ["company"]).then((r) => {
			if (r.message && r.message.company) {
				frm.doc.__branch_company = r.message.company;
				// ALWAYS mirror — company is DERIVED from the branch/court
				// chain. Frappe pre-fills any Link field named `company` from
				// the site default (e.g. an ERPNext Company), which is not
				// even a CBT Company; trusting it breaks the save.
				if (frm.doc.company !== r.message.company) {
					frm.set_value("company", r.message.company);
				}
			}
		});
		frm.trigger("_load_slot_cfg");
	},

	court(frm) {
		if (!frm.doc.court) return;
		frappe.db
			.get_value("CBT Court", frm.doc.court, ["branch", "company"])
			.then((r) => {
				if (!r.message) return;
				if (!frm.doc.branch) frm.set_value("branch", r.message.branch);
				// Same always-mirror rule as the branch trigger.
				if (frm.doc.company !== r.message.company) {
					frm.set_value("company", r.message.company);
				}
			});
		frm.trigger("_load_rate_rules");
	},

	_load_rate_rules(frm) {
		// Section-14: does this court price by the hour of day? The whole doc
		// is fetched because rate_rules is a child table — staff hold read on
		// CBT Court and the tenancy layer scopes it, so this is the same
		// permission surface the Link picker already used.
		frm._court_has_rules = false;
		frm._court_base_rate = null;
		if (!frm.doc.court) {
			frm.trigger("_preview");
			return;
		}
		frappe
			.call({
				method: "frappe.client.get",
				args: { doctype: "CBT Court", name: frm.doc.court },
			})
			.then((r) => {
				if (!r.message) return;
				frm._court_has_rules = (r.message.rate_rules || []).length > 0;
				frm._court_base_rate = flt(r.message.hourly_rate);
				frm.trigger("_preview");
			})
			.catch((err) => console.error("CBT rate-rule lookup failed", err));
	},

	customer(frm) {
		// Section-11: pre-fill the membership discount on NEW bookings only.
		// A saved booking's discount is the agreement already struck with the
		// customer — re-deriving it on a later edit would silently rewrite the
		// price of a booking someone has already been quoted.
		if (!frm.is_new() || !frm.doc.customer || !frm.doc.company) return;
		if (flt(frm.doc.discount_percent)) return; // staff typed one first
		frappe
			.xcall(
				"court_booking_tech.membership.get_member_discount_for",
				{ company: frm.doc.company, customer: frm.doc.customer },
				"GET" // xcall defaults to POST (S10 lesson 17a)
			)
			.then((res) => {
				if (!res || !res.has_membership) return;
				frm.set_value("discount_percent", res.discount_percent);
				frappe.show_alert({
					message: __("{0} member — {1}% applied", [
						res.tier,
						res.discount_percent,
					]),
					indicator: "green",
				});
			})
			.catch((err) => console.error("CBT membership lookup failed", err));
	},

	start_time(frm) {
		frm.trigger("_preview");
	},
	number_of_slots(frm) {
		frm.trigger("_preview");
	},
	hourly_rate(frm) {
		frm.trigger("_preview");
	},
	discount_percent(frm) {
		frm.trigger("_preview");
	},

	_load_slot_cfg(frm) {
		if (!frm.doc.branch) return;
		frappe.db
			.get_value("CBT Branch", frm.doc.branch, [
				"slot_duration_minutes",
				"buffer_minutes",
			])
			.then((r) => {
				if (!r.message) return;
				const branch_minutes = cint(r.message.slot_duration_minutes);
				const buffer = cint(r.message.buffer_minutes);
				if (branch_minutes) {
					frm._slot_cfg = { duration: branch_minutes, buffer };
					frm.trigger("_preview");
					return;
				}
				frappe.db
					.get_single_value(
						"CBT Platform Settings",
						"default_slot_duration_minutes"
					)
					.then((v) => {
						frm._slot_cfg = { duration: cint(v) || 60, buffer };
						frm.trigger("_preview");
					});
			});
	},

	_preview(frm) {
		// Live end-time/total preview while drafting (server is authoritative).
		if (!frm.is_new() || !frm._slot_cfg || !frm.doc.start_time) return;
		const n = Math.max(cint(frm.doc.number_of_slots) || 1, 1);
		const { duration, buffer } = frm._slot_cfg;
		const total_minutes = n * duration + (n - 1) * buffer;
		const end = frappe.datetime.get_datetime_as_string(
			moment(frm.doc.start_time, "HH:mm:ss")
				.add(total_minutes, "minutes")
				.toDate()
		);
		frm.set_value("end_time", end.split(" ")[1]);
		const hours = (n * duration) / 60;
		frm.set_value("duration_hours", hours);

		// Section-14: on a court with rate rules, `hourly_rate × hours` is not
		// a price anyone is charged — the server prices each slot at its own
		// rate on save. Showing the base-rate product would put a number on
		// screen that the saved booking then contradicts, which is exactly the
		// invisibly-mispriced-booking failure S11 named. A blank with an
		// explanation is more honest than a confident wrong figure.
		//
		// Deliberately NOT a get_quote call: four field triggers land here, and
		// writing the server's blended rate back into `hourly_rate` would make
		// the controller read it as a staff OVERRIDE and price the booking flat
		// at the blend — the same trap that bit extend_booking.
		//
		// The rate is only left to the rules while it still EQUALS the court's
		// base. The moment staff type their own, the flat preview is correct
		// again, because an explicit rate IS an override.
		const rate = flt(frm.doc.hourly_rate);
		const overridden =
			frm._court_base_rate !== null && rate !== flt(frm._court_base_rate);
		if (frm._court_has_rules && !overridden) {
			frm.set_value("total_amount", null);
			frm.set_intro(
				__(
					"This court prices by time of day — the total is calculated when you save. Type your own Hourly Rate to override it."
				),
				"blue"
			);
			return;
		}
		frm.set_intro("");
		const disc = flt(frm.doc.discount_percent);
		frm.set_value("total_amount", rate * hours * (1 - disc / 100));
	},
});
