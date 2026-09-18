// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// The facilities tree IN PLACE (section-27, Backlog B30).
//
// `cbt_facility_tab.render(opts)` fills an HTML field on a parent form with
// its children — CBT Company → its branches, CBT Branch → its courts — as an
// OWNED list (section-23 tokens, no frappe list chrome) with, per row, Edit
// (opens the child IN PLACE) and Open (the child's own form), a New button,
// and, on a child-of-a-child tab, the way back UP the tree.
//
// THE EDITOR IS THE CHILD'S WHOLE LAYOUT. The user's ruling (2026-08-28):
// *"everything … it would look like the same but inside a tab … but of course
// CBT Company field is readonly and disabled since it is presumed"*. So the
// editor is a `frappe.ui.FieldGroup` built from the child DocType's OWN meta
// — every section, column, control and child table the standalone form
// shows, the map included — with the parent link preset and read-only. Saves
// go through `api.facilities.save_facility`, which re-derives the tenant
// server-side and lets frappe's permission hooks and `check_if_latest` do
// their work; nothing here writes the parent form's model, and the parent's
// Ctrl+S still saves the PARENT.
//
// Measured facts this leans on (frappe v16 source, 2026-08-28):
//   * a form-less Grid reads and writes `df.data` (grid.js:775-777) and takes
//     its columns from `df.fields` (grid.js:703-706) — so each Table docfield
//     is a per-instance copy carrying both;
//   * meta docfields are SHARED objects (grid.js:719-722) — every df here is
//     copied before it is touched;
//   * `FieldGroup.get_values()` drops blanks (field_group.js:155) — the
//     payload is built from the meta's fieldname list with explicit nulls;
//   * `df.change` fires for form-less controls (base_control.js:239-243);
//   * `fetch_from` does not run without a form — callers pass `preset`.
//
// Test hooks: data-testid = cbt-facility-list / -row / -new / -edit / -open /
// -parent / -gate / -editor / -save / -cancel; the editor's `data-state`
// walks loading → editing → saving → saved, and carries `data-name`.

(function () {
	const esc = frappe.utils.escape_html;
	const LAYOUT_TYPES = new Set(["Section Break", "Column Break", "Tab Break", "Fold", "Page Break"]);
	const SKIP_TYPES = new Set(["Tab Break", "Fold", "Page Break"]);

	function pill(active) {
		return cint(active)
			? `<span style="display:inline-block;padding:.1rem .55rem;border-radius:var(--cbt-radius-pill);background:var(--cbt-confirmed-bg);color:var(--cbt-confirmed-ink);font-size:.8em;font-weight:600">${__(
					"Active"
			  )}</span>`
			: `<span style="display:inline-block;padding:.1rem .55rem;border-radius:var(--cbt-radius-pill);background:var(--cbt-past-bg);color:var(--cbt-past-ink);font-size:.8em;font-weight:600">${__(
					"Inactive"
			  )}</span>`;
	}

	const HELPERS = { esc, pill };

	// A Tab Break is a layout element, not a field: layout.js make_tab pushes it
	// to `layout.tabs` and never to `fields_dict`, so `frm.get_field(tab)` is
	// undefined and any `.tab.toggle` hung off it is a silent no-op (measured
	// 2026-08-28 — the Onboarding tab had the same latent no-op since S11).
	function find_tab(frm, fieldname) {
		return ((frm.layout && frm.layout.tabs) || []).find(
			(tab) => tab.df && tab.df.fieldname === fieldname
		);
	}

	function route_for(doctype, name) {
		return `/desk/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
	}

	function kebab(text) {
		return (text || "")
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-+|-+$/g, "");
	}

	// ------------------------------------------------------------------
	// Entry point
	// ------------------------------------------------------------------

	function render(opts) {
		const frm = opts.frm;
		const html_field = frm.get_field(opts.html);
		const tab = find_tab(frm, opts.tab);
		if (!html_field) return;
		if (frm.is_new()) {
			// Nothing to list under a document that does not exist yet.
			html_field.$wrapper.empty();
			if (tab) tab.toggle(false);
			return;
		}
		if (!frm.__cbt_facility) frm.__cbt_facility = {};
		const state = frm.__cbt_facility[opts.html] || (frm.__cbt_facility[opts.html] = {});
		state.opts = opts;
		state.frm = frm;
		state.html_field = html_field;
		state.tab = tab;
		load_list(state);
	}

	function load_list(state) {
		const o = state.opts;
		// xcall defaults to POST; the list endpoints are GET (S10 lesson 17a).
		frappe
			.xcall(o.list_method, { [o.list_arg]: o.parent_value }, "GET")
			.then((data) => {
				state.data = data;
				paint(state);
				// Content first, then the tab — tab.js re-hides an empty tab.
				if (state.tab) state.tab.toggle(true);
				if (state.editing) open_editor(state, state.editing === "__new" ? null : state.editing);
			})
			.catch((err) => {
				console.error("CBT facility tab failed", err);
				state.html_field.$wrapper.html(
					`<div class="text-muted">${__("Could not load the {0}.", [o.plural])}</div>`
				);
			});
	}

	// ------------------------------------------------------------------
	// The list
	// ------------------------------------------------------------------

	function paint(state) {
		const o = state.opts;
		const data = state.data || { rows: [] };
		const rows = data.rows || [];
		const can_manage = !!data.can_manage;

		const head_cells = o.columns.map((c) => `<th style="text-align:left;padding:.45rem .6rem;color:var(--cbt-ink-muted);font-weight:600;font-size:.85em;border-bottom:1px solid var(--cbt-line)">${esc(c.label)}</th>`).join("");
		const body = rows.length
			? rows
					.map((r) => {
						const cells = o.columns
							.map(
								(c) =>
									`<td data-col="${esc(c.col)}" style="padding:.55rem .6rem;border-bottom:1px solid var(--cbt-line);vertical-align:top">${c.render(
										r,
										HELPERS
									)}</td>`
							)
							.join("");
						const edit = can_manage
							? `<button type="button" class="btn btn-xs btn-default cbt-facility-edit" data-testid="cbt-facility-edit" data-name="${esc(
									r.name
							  )}">${__("Edit")}</button> `
							: "";
						return `<tr data-testid="cbt-facility-row" data-name="${esc(r.name)}">${cells}
							<td style="padding:.45rem .6rem;border-bottom:1px solid var(--cbt-line);white-space:nowrap;text-align:right">
								${edit}<a class="btn btn-xs btn-default" data-testid="cbt-facility-open" data-name="${esc(
									r.name
								)}" href="${route_for(o.child, r.name)}">${__("Open")}</a>
							</td></tr>`;
					})
					.join("")
			: `<tr><td colspan="${o.columns.length + 1}" class="text-muted" style="padding:.8rem .6rem" data-testid="cbt-facility-empty">${esc(
					o.empty_text || __("Nothing here yet.")
			  )}</td></tr>`;

		const back = o.back && o.back.name
			? `<a data-testid="cbt-facility-parent" href="${route_for(o.back.doctype, o.back.name)}" style="color:var(--cbt-primary);font-weight:600">← ${__(
					"Back to {0}",
					[esc(o.back.name)]
			  )}</a>`
			: "";
		const gate = data.managed_by_platform && o.gate_text
			? `<div data-testid="cbt-facility-gate" style="margin:.4rem 0 .8rem;padding:.6rem .8rem;border-radius:var(--cbt-radius-sm);background:var(--cbt-accent-soft);color:var(--cbt-accent-ink)">${esc(
					o.gate_text
			  )}</div>`
			: "";
		const new_button = can_manage
			? `<button type="button" class="btn btn-sm btn-primary cbt-facility-new" data-testid="cbt-facility-new">${__(
					"New {0}",
					[o.noun]
			  )}</button>`
			: "";

		state.html_field.$wrapper.html(`
			<div class="cbt-facility" data-testid="cbt-facility-list" data-child="${esc(o.child)}" data-count="${rows.length}">
				<div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:.5rem">
					<div style="display:flex;gap:1rem;align-items:baseline">
						<strong style="font-size:1.05em">${rows.length === 1 ? __("1 {0}", [o.noun]) : __("{0} {1}", [rows.length, o.plural])}</strong>
						${back}
					</div>
					<div>${new_button}</div>
				</div>
				${gate}
				<div style="overflow-x:auto;background:var(--cbt-card);border:1px solid var(--cbt-line);border-radius:var(--cbt-radius);box-shadow:var(--cbt-shadow)">
					<table style="width:100%;border-collapse:collapse;min-width:520px">
						<thead><tr>${head_cells}<th style="border-bottom:1px solid var(--cbt-line)"></th></tr></thead>
						<tbody>${body}</tbody>
					</table>
				</div>
				<div class="cbt-facility-editor-host" style="margin-top:1rem"></div>
			</div>`);

		if (o.back && o.back.name && o.back.title_field) {
			frappe.db.get_value(o.back.doctype, o.back.name, o.back.title_field).then((r) => {
				const title = r && r.message && r.message[o.back.title_field];
				if (title) {
					state.html_field.$wrapper
						.find("[data-testid='cbt-facility-parent']")
						.text(`← ${__("Back to {0}", [title])}`);
				}
			});
		}

		const $w = state.html_field.$wrapper;
		$w.find(".cbt-facility-new").on("click", () => open_editor(state, null));
		$w.find(".cbt-facility-edit").on("click", (e) =>
			open_editor(state, $(e.currentTarget).attr("data-name"))
		);
	}

	// ------------------------------------------------------------------
	// The editor — the child's own layout, in place
	// ------------------------------------------------------------------

	function open_editor(state, name) {
		const o = state.opts;
		state.editing = name || "__new";
		const $host = state.html_field.$wrapper.find(".cbt-facility-editor-host");
		$host.html(`
			<div class="cbt-facility-editor" data-testid="cbt-facility-editor" data-child="${esc(
				o.child
			)}" data-state="loading" data-name="${esc(name || "")}" style="background:var(--cbt-card);border:1px solid var(--cbt-primary-line);border-radius:var(--cbt-radius);box-shadow:var(--cbt-shadow-lift);padding:1rem 1.25rem">
				<div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:.25rem">
					<strong class="cbt-facility-editor-title" style="font-size:1.05em">${
						name ? esc(name) : __("New {0}", [o.noun])
					}</strong>
					<div>
						<button type="button" class="btn btn-sm btn-default cbt-facility-cancel" data-testid="cbt-facility-cancel">${__(
							"Cancel"
						)}</button>
						<button type="button" class="btn btn-sm btn-primary cbt-facility-save" data-testid="cbt-facility-save">${__(
							"Save {0}",
							[o.noun]
						)}</button>
					</div>
				</div>
				<div class="cbt-facility-editor-body"></div>
			</div>`);
		$host.find(".cbt-facility-cancel").on("click", () => close_editor(state));

		frappe.model.with_doctype(o.child, () => {
			const load = name
				? frappe.xcall(
						"court_booking_tech.api.facilities.get_facility",
						{ doctype: o.child, name },
						"GET"
				  )
				: Promise.resolve(new_doc(state));
			load
				.then((doc) => build_editor(state, doc))
				.catch((err) => {
					console.error("CBT facility editor failed", err);
					$host.find(".cbt-facility-editor").attr("data-state", "error");
					$host
						.find(".cbt-facility-editor-body")
						.html(`<div class="text-muted">${__("Could not load {0}.", [esc(name || o.noun)])}</div>`);
				});
		});
	}

	function close_editor(state) {
		state.editing = null;
		state.group = null;
		state.doc = null;
		state.html_field.$wrapper.find(".cbt-facility-editor-host").empty();
	}

	function new_doc(state) {
		const o = state.opts;
		const doc = { doctype: o.child, __islocal: 1 };
		doc[o.parent_field] = o.parent_value;
		Object.assign(doc, o.preset || {});
		return doc;
	}

	function build_editor(state, doc) {
		const o = state.opts;
		const meta = frappe.get_meta(o.child);
		const is_new = !!doc.__islocal;
		const $editor = state.html_field.$wrapper.find(".cbt-facility-editor");
		if (!$editor.length) return; // the tab was repainted underneath us
		const $body = $editor.find(".cbt-facility-editor-body").empty();

		// Per-field copies: meta docfields are shared with the real form. The
		// editor is the child's DETAILS tab — everything up to its first Tab
		// Break (a branch's own Courts tab is a list of ITS children, not part
		// of the branch).
		const fields = [];
		for (const src of meta.fields) {
			if (src.fieldtype === "Tab Break") break;
			if (SKIP_TYPES.has(src.fieldtype)) continue;
			const df = Object.assign({}, src);
			delete df.change;
			// A form-less Link builds its fetch map from the layout's own
			// `fetch_from` docfields (link.js fetch_map_for_quick_entry) and then
			// calls `this.layout?.set_value` UNBOUND and `this.frm.refresh_field`
			// unguarded (link.js:913-923, :894-897) — two TypeErrors that killed
			// the set-values chain (measured 2026-08-28). The editor presets every
			// fetched value itself (`opts.preset`), so the copies carry no fetch.
			delete df.fetch_from;
			delete df.fetch_if_empty;
			if (df.fieldname === o.parent_field) {
				// "presumed" — shown, never editable.
				df.read_only = 1;
				df.reqd = 0;
			}
			if (o.preset && Object.prototype.hasOwnProperty.call(o.preset, df.fieldname)) {
				df.read_only = 1;
			}
			if (o.child === "CBT Branch" && df.fieldname === "slug" && !is_new) {
				df.read_only = 1; // immutable after creation (S3 as-built 2)
			}
			if (df.fieldtype === "Table") {
				const child_meta = frappe.get_meta(df.options);
				df.fields = ((child_meta && child_meta.fields) || []).map((f) => Object.assign({}, f));
				df.data = (doc[df.fieldname] || []).map((r) => Object.assign({}, r));
				df.cannot_add_rows = 0;
			}
			fields.push(df);
		}

		const group = new frappe.ui.FieldGroup({
			fields,
			parent: $body,
			no_submit_on_enter: true,
			no_focus: true,
		});
		group.make();
		state.group = group;
		state.doc = doc;
		state.fields = fields;
		state.map = {};

		const scalar = {};
		for (const df of fields) {
			if (LAYOUT_TYPES.has(df.fieldtype) || df.fieldtype === "Table" || df.fieldtype === "HTML") continue;
			if (doc[df.fieldname] === undefined) continue;
			scalar[df.fieldname] = doc[df.fieldname];
		}
		group.set_values(scalar).then(() => {
			group.refresh_dependency();
			wire_editor(state, is_new);
			$editor.attr("data-state", state.just_saved ? "saved" : "editing");
			state.just_saved = false;
		});

		$editor.find(".cbt-facility-save").off("click").on("click", () => save_editor(state, is_new));
	}

	function wire_editor(state, is_new) {
		const o = state.opts;
		const group = state.group;

		if (o.child === "CBT Branch") {
			// The slug follows the name until the operator types one themselves.
			if (is_new) {
				const name_field = group.get_field("branch_name");
				const slug_field = group.get_field("slug");
				if (name_field && slug_field) {
					name_field.df.change = () => {
						if (state.slug_touched) return;
						group.set_value("slug", kebab(group.get_value("branch_name")));
					};
					slug_field.$input && slug_field.$input.on("input", () => (state.slug_touched = true));
				}
			}
			if (window.cbt_branch_map) {
				const host = editor_map_host(state);
				cbt_branch_map.render(host);
				for (const fieldname of ["latitude", "longitude"]) {
					const field = group.get_field(fieldname);
					if (field) field.df.change = () => cbt_branch_map.sync(host);
				}
			}
		}
	}

	function editor_map_host(state) {
		const group = state.group;
		return {
			doc: () => ({
				latitude: flt(group.get_value("latitude")),
				longitude: flt(group.get_value("longitude")),
			}),
			wrapper: (fieldname) => {
				const field = group.get_field(fieldname);
				return field ? field.$wrapper : null;
			},
			set_pin: (lat, lng) => {
				group.set_value("latitude", lat);
				group.set_value("longitude", lng);
			},
			can_edit: () => true, // the editor only opens for a seat that may manage
			state: state.map,
		};
	}

	function strip_row(row) {
		const out = {};
		for (const key of Object.keys(row)) {
			if (key.startsWith("__") || key === "_sortable") continue;
			out[key] = row[key];
		}
		return out;
	}

	function save_editor(state, is_new) {
		const o = state.opts;
		const group = state.group;
		const $editor = state.html_field.$wrapper.find(".cbt-facility-editor");
		if (!group || !$editor.length) return;

		// Missing mandatory values: frappe's own "Missing Values Required"
		// message, and no request.
		const values = group.get_values();
		if (!values) return;

		const payload = { doctype: o.child };
		for (const df of state.fields) {
			if (LAYOUT_TYPES.has(df.fieldtype) || df.fieldtype === "HTML") continue;
			if (df.fieldtype === "Table") {
				const field = group.get_field(df.fieldname);
				payload[df.fieldname] = ((field && field.grid && field.grid.get_data()) || []).map(strip_row);
				continue;
			}
			const value = values[df.fieldname];
			payload[df.fieldname] = value === undefined ? null : value;
		}
		if (!is_new) {
			payload.name = state.doc.name;
			payload.modified = state.doc.modified;
		}

		$editor.attr("data-state", "saving");
		frappe
			.xcall("court_booking_tech.api.facilities.save_facility", { doc: payload })
			.then((saved) => {
				frappe.show_alert({
					message: __("{0} saved", [saved[o.title_field] || saved.name]),
					indicator: "green",
				});
				state.editing = saved.name;
				state.just_saved = true;
				state.slug_touched = false;
				load_list(state);
			})
			.catch(() => {
				// frappe already showed the server's sentence.
				$editor.attr("data-state", "editing");
			});
	}

	window.cbt_facility_tab = { render, find_tab };
})();
