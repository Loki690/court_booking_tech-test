// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt

frappe.ui.form.on("CBT Open Play Session", {
	onload(frm) {
		// Frappe pre-fills ANY field named `company` from a user default —
		// `create_new.js` keys that off `df.fieldname`, never `df.options`, so a
		// field pointing at CBT Company can be born holding an ERPNext Company.
		// Drop it on a fresh form so a save with no branch yields this app's honest
		// "Branch is required." instead of a link error about a company nobody chose.
		//
		// MEASURED 2026-08-18 on a fresh bench: the prefill did NOT fire here (the
		// new doc opened with company = null), so this arm is defensive rather than
		// the live defect — the live defect is the `branch` handler below. It stays
		// because the prefill is seat-dependent (a seat holding a stale `company`
		// user default and no CBT Company User Permission still gets one), and
		// because the server cannot defend it: `_validate_links()` runs at
		// document.py:456, BEFORE `before_insert` on :457.
		if (frm.is_new() && !frm.doc.branch && frm.doc.company) {
			frm.set_value("company", null);
		}
	},

	branch(frm) {
		// THE FIX. `company` on this doctype is reqd:1 + read_only:1 with NO
		// `fetch_from`, and the site default `hide_empty_read_only_fields` means an
		// empty read-only field is not rendered at all. So before this handler
		// existed the operator hit a wall that could not be climbed from the UI:
		//
		//   "Missing Fields — Please fill the following mandatory fields before
		//    saving: Company is required."
		//
		// ...for a field they can neither see nor edit, with the save aborting
		// CLIENT-side so the request never even reached the server (measured
		// 2026-08-18: no HTTP request at all, not the HTTP 417 Backlog B17 claimed).
		//
		// The sibling CBT Slot Block carries the same mirror for the same reason
		// (cbt_slot_block.js) — company is DERIVED from the branch and the server
		// cannot correct it in time. CBT Court escapes this only because its
		// `company` field declares `fetch_from: "branch.company"`.
		//
		// Guarded on the freeze: `company` and `branch` are in
		// FROZEN_AFTER_SCHEDULED (cbt_open_play_session.py), so writing company on a
		// session that has already left Scheduled would be rejected by the
		// controller — mirror only while it is still editable.
		if (!frm.doc.branch) return;
		if (!frm.is_new() && frm.doc.status !== "Scheduled") return;
		frappe.db.get_value("CBT Branch", frm.doc.branch, ["company"]).then((r) => {
			if (r.message && r.message.company && frm.doc.company !== r.message.company) {
				frm.set_value("company", r.message.company);
			}
		});
	},
});
