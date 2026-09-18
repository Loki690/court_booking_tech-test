// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// CBT Company form (section-11): the platform's two levers on a tenant —
// suspension (the billing enforcement lever, PLAN §1) and the go-live
// onboarding checklist.
//
// Both are PLATFORM-only surfaces. `status` is already permlevel-1 so a tenant
// admin's write would be silently reverted server-side; hiding the button as
// well means they never see an action that could not work.

// Section-27 (Backlog B30): the Branches tab renders this company's branches
// in place — the same layout as the CBT Branch form, map included, inside
// the tab. Both files are shared with the CBT Branch form.
const CBT_COMPANY_FORM_ASSETS = [
	"/assets/court_booking_tech/js/cbt_branch_map.js",
	"/assets/court_booking_tech/js/cbt_facility_tab.js",
	"/assets/court_booking_tech/js/cbt_media_tab.js",
];

frappe.ui.form.on("CBT Company", {
	refresh(frm) {
		cbt_render_platform_actions(frm);
		cbt_render_onboarding(frm);
		frappe.require(CBT_COMPANY_FORM_ASSETS, () => {
			cbt_render_branches_tab(frm);
			cbt_media_tab.render({
				frm,
				tab: "media_tab",
				html: "media_html",
				doctype: "CBT Company",
				name: frm.doc.name,
			});
		});
	},
});

function cbt_render_branches_tab(frm) {
	cbt_facility_tab.render({
		frm,
		tab: "branches_tab",
		html: "branches_html",
		child: "CBT Branch",
		noun: __("branch"),
		plural: __("branches"),
		title_field: "branch_name",
		parent_field: "company",
		parent_value: frm.doc.name,
		list_method: "court_booking_tech.api.facilities.list_branches",
		list_arg: "company",
		// fetch_from does not run without a form — the editor is told the
		// value the branch would fetch.
		preset: { company_code: frm.doc.company_code },
		gate_text: __(
			"Branches for this company are managed by the platform. " +
				"Contact your platform administrator to change branch details."
		),
		empty_text: __("No branches yet."),
		columns: [
			{
				label: __("Branch"),
				col: "branch",
				render: (r, h) =>
					`<div style="font-weight:600">${h.esc(r.branch_name)}</div>
					 <div class="text-muted small"><code>${h.esc(r.name)}</code>${
						r.pinned ? "" : ` · ${__("no map pin")}`
					}</div>`,
			},
			{ label: __("Status"), col: "status", render: (r, h) => h.pill(r.is_active) },
			{ label: __("Phone"), col: "phone", render: (r, h) => h.esc(r.phone || "—") },
			{
				label: __("Courts"),
				col: "courts",
				render: (r) => {
					if (!r.courts) return __("No courts yet");
					const total = r.courts === 1 ? __("1 court") : __("{0} courts", [r.courts]);
					return r.active_courts === r.courts
						? total
						: `${total} <span class="text-muted small">(${__("{0} active", [
								r.active_courts,
						  ])})</span>`;
				},
			},
			{
				label: __("From"),
				col: "rate",
				render: (r) => (r.rate_from ? format_currency(r.rate_from, "PHP") : "—"),
			},
		],
	});
}

function cbt_is_platform() {
	const roles = frappe.user_roles || [];
	return (
		frappe.session.user === "Administrator" ||
		roles.includes("CBT Platform Admin") ||
		roles.includes("System Manager")
	);
}

function cbt_render_platform_actions(frm) {
	if (frm.is_new() || !cbt_is_platform()) return;

	const suspended = frm.doc.status === "Suspended";
	frm.add_custom_button(
		suspended ? __("Unsuspend") : __("Suspend"),
		() => cbt_toggle_suspension(frm, suspended),
		__("Platform")
	);

	// A suspended tenant is invisible to customers and rejects every booking
	// path — that must be obvious the moment the form opens, not something you
	// notice in a Select field halfway down a tab.
	if (suspended) {
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			`<span class="indicator red">${__(
				"Suspended — hidden from the marketplace and rejecting new bookings."
			)}</span>`
		);
	}
}

function cbt_toggle_suspension(frm, suspended) {
	const message = suspended
		? __(
				"Reactivate {0}? Its branches reappear in the marketplace and it can take bookings again.",
				[frm.doc.company_name]
		  )
		: __(
				"Suspend {0}?<br><br>Its branches are hidden from the marketplace immediately and every booking path — portal, board and open play — will refuse new bookings. Existing bookings are untouched.",
				[frm.doc.company_name]
		  );

	frappe.confirm(message, () => {
		frm.set_value("status", suspended ? "Active" : "Suspended");
		frm.save().then(() => {
			frappe.show_alert({
				message: suspended
					? __("{0} reactivated", [frm.doc.company_name])
					: __("{0} suspended", [frm.doc.company_name]),
				indicator: suspended ? "green" : "red",
			});
		});
	});
}

function cbt_render_onboarding(frm) {
	const wrapper = frm.get_field("onboarding_checklist_html");
	if (!wrapper) return;

	// The checklist is the PLATFORM's view of a tenant it is standing up; a
	// company admin has no business seeing their own scorecard (and the API
	// would refuse anyway — hide the tab rather than render an error).
	//
	// A Tab Break is a layout element, not a field: layout.js make_tab pushes
	// it to `frm.layout.tabs` and never to `fields_dict`, so the previous
	// `frm.get_field("onboarding_tab")` was undefined and this hide was a
	// silent no-op — every Company Admin saw the tab with "Could not load the
	// checklist." Measured and fixed 2026-08-28 (section-27).
	const tab = (frm.layout.tabs || []).find(
		(t) => t.df && t.df.fieldname === "onboarding_tab"
	);
	if (!cbt_is_platform() || frm.is_new()) {
		if (tab) tab.toggle(false);
		return;
	}
	if (tab) tab.toggle(true);

	// xcall defaults to POST; this endpoint is methods=["GET"] (S10 lesson 17a
	// — the same mistake silently broke the open play board).
	frappe
		.xcall(
			"court_booking_tech.api.onboarding.get_onboarding_checklist",
			{ company: frm.doc.name },
			"GET"
		)
		.then((data) => wrapper.$wrapper.html(cbt_checklist_html(data)))
		.catch((err) => {
			// Never let a friendly fallback be the only record of an error.
			console.error("CBT onboarding checklist failed", err);
			wrapper.$wrapper.html(
				`<div class="text-muted">${__("Could not load the checklist.")}</div>`
			);
		});
}

function cbt_checklist_html(data) {
	const esc = frappe.utils.escape_html;
	const rows = (data.steps || [])
		.map((step) => {
			const icon = step.done
				? '<span style="color:var(--green-600)">&#10003;</span>'
				: '<span style="color:var(--gray-500)">&#9675;</span>';
			const links = (step.links || [])
				.map(
					(link) =>
						`<div class="small" style="margin-top:2px">
							<code>${esc(link.url)}</code>
							<span class="text-muted"> — ${esc(link.label)}</span>
						</div>`
				)
				.join("");
			return `<div data-testid="onboarding-step-${esc(step.key)}"
					data-done="${step.done ? 1 : 0}"
					style="display:flex; gap:.6rem; padding:.5rem 0; border-bottom:1px solid var(--border-color)">
					<div style="width:1.2rem">${icon}</div>
					<div style="flex:1; min-width:0">
						<div><a href="${esc(step.route)}">${esc(step.label)}</a></div>
						<div class="text-muted small">${esc(step.hint || "")}</div>
						${links}
					</div>
				</div>`;
		})
		.join("");

	const banner = data.ready
		? `<div class="indicator green">${__("Ready to go live")}</div>`
		: `<div class="indicator orange">${__("{0} of {1} steps done", [
				data.done_count,
				data.total_count,
		  ])}</div>`;

	return `<div data-testid="onboarding-checklist" data-ready="${data.ready ? 1 : 0}">
			<div style="margin-bottom:.75rem">${banner}</div>
			${rows}
		</div>`;
}
