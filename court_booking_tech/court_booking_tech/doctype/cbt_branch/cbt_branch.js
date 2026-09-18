// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// CBT Branch form: the self-management gate mirror (section-7), the Leaflet
// pin picker (PLAN D4 — now in public/js/cbt_branch_map.js so the Branches tab
// on CBT Company renders the same map), and the Courts tab (section-27,
// Backlog B30): this branch's courts, read and edited in place.

const CBT_BRANCH_FORM_ASSETS = [
	"/assets/court_booking_tech/js/cbt_branch_map.js",
	"/assets/court_booking_tech/js/cbt_facility_tab.js",
	"/assets/court_booking_tech/js/cbt_media_tab.js",
];

frappe.ui.form.on("CBT Branch", {
	refresh(frm) {
		frm.trigger("apply_self_management_gate");
		frappe.require(CBT_BRANCH_FORM_ASSETS, () => {
			cbt_branch_map.render(cbt_branch_map_host(frm));
			cbt_render_courts_tab(frm);
			// Photos are NOT gated on branch management — the tab stays live
			// even when disable_form() has switched the rest of the form off.
			cbt_media_tab.render({
				frm,
				tab: "media_tab",
				html: "media_html",
				doctype: "CBT Branch",
				name: frm.doc.name,
			});
		});
	},

	latitude(frm) {
		window.cbt_branch_map && cbt_branch_map.sync(cbt_branch_map_host(frm));
	},

	longitude(frm) {
		window.cbt_branch_map && cbt_branch_map.sync(cbt_branch_map_host(frm));
	},

	company(frm) {
		// A gated admin filling a NEW branch form must learn immediately, not
		// at save time (section-7 file 03 asserts this).
		frm.trigger("apply_self_management_gate");
	},

	apply_self_management_gate(frm) {
		// UX mirror of the server gate (tenancy.branch_has_permission) — the
		// server hook cannot gray out the form, so do it here. Runs on refresh
		// (existing docs) AND on company change (new docs).
		if (!frm.doc.company) return;
		const platform =
			frappe.session.user === "Administrator" ||
			frappe.user.has_role("System Manager") ||
			frappe.user.has_role("CBT Platform Admin");
		if (platform) return;
		frappe.db
			.get_value("CBT Company", frm.doc.company, "allow_self_branch_management")
			.then((r) => {
				if (r?.message && !cint(r.message.allow_self_branch_management)) {
					frm.disable_form();
					frm.set_intro(
						__(
							"Branches for this company are managed by the platform. " +
								"Contact your platform administrator to change branch details."
						),
						"orange"
					);
				}
			});
	},
});

// The form as a map HOST (see cbt_branch_map.js). Leaflet state lives on the
// frm object under the SAME names as before the split — `frm._cbt_map` /
// `frm._cbt_marker` — because E2E file 01 fires the picker's own click
// handler through `cur_frm._cbt_map` (its way of proving the picker, not a
// field write, sets the pin).
function cbt_branch_map_host(frm) {
	return {
		doc: () => frm.doc,
		wrapper: (fieldname) => {
			const field = frm.get_field(fieldname);
			return field ? field.$wrapper : null;
		},
		set_pin: (lat, lng) => {
			frm.set_value("latitude", lat);
			frm.set_value("longitude", lng);
		},
		can_edit: () => !!(frm.perm && frm.perm[0] && frm.perm[0].write),
		state: frm,
	};
}

function cbt_render_courts_tab(frm) {
	cbt_facility_tab.render({
		frm,
		tab: "courts_tab",
		html: "courts_html",
		child: "CBT Court",
		noun: __("court"),
		plural: __("courts"),
		title_field: "court_name",
		parent_field: "branch",
		parent_value: frm.doc.name,
		list_method: "court_booking_tech.api.facilities.list_courts",
		list_arg: "branch",
		preset: { company: frm.doc.company },
		// The way BACK up the tree — a court is reached from its branch, a
		// branch from its company; the tab says so rather than leaving the
		// operator to the breadcrumb (which goes to the list, not the parent).
		back: { doctype: "CBT Company", name: frm.doc.company, title_field: "company_name" },
		empty_text: __("No courts yet. Nothing here is bookable until at least one active court has a rate."),
		columns: [
			{
				label: __("Court"),
				col: "court",
				render: (r, h) =>
					`<div style="font-weight:600">${h.esc(r.court_name)}</div>
					 <div class="text-muted small"><code>${h.esc(r.name)}</code></div>`,
			},
			{ label: __("Type"), col: "type", render: (r, h) => h.esc(r.court_type || "") },
			{
				label: __("Base rate"),
				col: "rate",
				render: (r) =>
					format_currency(r.hourly_rate, "PHP") +
					(r.rate_rules
						? ` <span class="text-muted small">+${r.rate_rules} ${__("rate rules")}</span>`
						: ""),
			},
			{ label: __("Status"), col: "status", render: (r, h) => h.pill(r.is_active) },
		],
	});
}
