// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

frappe.ui.form.on("CBT Slot Block", {
	setup(frm) {
		frm.set_query("court", () => {
			const filters = { is_active: 1 };
			if (frm.doc.branch) filters.branch = frm.doc.branch;
			return { filters };
		});
	},

	refresh(frm) {
		frm.set_intro(
			frm.doc.court
				? __("This block closes one court for the chosen window.")
				: __(
						"No court selected — this block closes the WHOLE branch " +
							"for the chosen window (e.g. a holiday)."
					),
			frm.doc.court ? "blue" : "orange"
		);
	},

	onload(frm) {
		// Frappe pre-fills ANY field named `company` from the site default —
		// `create_new.js` keys that off `df.fieldname`, never `df.options`, so a
		// field pointing at CBT Company is born holding an ERPNext Company. Drop
		// it on a fresh form so a save with no branch yields this app's honest
		// "Branch is required." instead of an opaque link error about a company
		// nobody chose.
		if (frm.is_new() && !frm.doc.branch && frm.doc.company) {
			frm.set_value("company", null);
		}
	},

	branch(frm) {
		if (frm.doc.court) frm.set_value("court", null);
		// ALWAYS mirror — company is DERIVED from the branch, and the server
		// CANNOT defend this one. `CBTSlotBlock._mirror_company` runs from
		// before_insert/validate, but frappe calls `_validate_links()` FIRST
		// (document.py:456, before run_method("before_insert") on :457) — so the
		// bogus site-default company is rejected before any controller hook can
		// correct it, and the desk form 417s with "Could not find Company: X".
		// There is no server hook earlier than the link check, so this client
		// mirror is the only available fix, not a convenience.
		//
		// Branch-driven ONLY: the sibling CBT Court Booking form also mirrors
		// from its court, but a slot block's company is derived from the branch
		// alone (cbt_slot_block.py::_mirror_company) and the court is cleared
		// above — a second writer would only add a race.
		if (!frm.doc.branch) return;
		frappe.db.get_value("CBT Branch", frm.doc.branch, ["company"]).then((r) => {
			if (r.message && r.message.company && frm.doc.company !== r.message.company) {
				frm.set_value("company", r.message.company);
			}
		});
	},

	court(frm) {
		frm.trigger("refresh");
	},
});
