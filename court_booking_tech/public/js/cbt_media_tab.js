// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// The Media tab on CBT Company and CBT Branch (section-29). A sibling of
// cbt_facility_tab, never a reuse: that one is one save per row.

(function () {
	const esc = frappe.utils.escape_html;

	function find_tab(frm, fieldname) {
		return ((frm.layout && frm.layout.tabs) || []).find(
			(tab) => tab.df && tab.df.fieldname === fieldname
		);
	}

	function photos(n) {
		return n === 1 ? __("1 photo") : __("{0} photos", [n]);
	}

	// frappe throws land in _server_messages; tell the person WHICH file and WHY.
	function server_reason(body, file_name) {
		let reason = "";
		try {
			const messages = JSON.parse((body && body._server_messages) || "[]");
			reason = messages.map((m) => JSON.parse(m).message).join(" ");
		} catch (e) {
			reason = "";
		}
		const plain = document.createElement("div");
		plain.innerHTML = reason;
		reason = (plain.textContent || "").trim();
		return reason
			? __("{0}: {1}", [file_name, reason])
			: __("{0} could not be uploaded.", [file_name]);
	}

	// Said BEFORE the first upload, which replaces the inherited set silently.
	function resolution_text(data) {
		const n = data.company_photo_count;
		const own = data.rows.length;
		if (!data.is_branch) {
			const total = photos(n);
			return own
				? __("Your banner plus these {0} — customers see {1} in all. The banner is set on the Details tab, and these photos show on every branch that has none of its own.", [own, total])
				: __("No photos yet. Your banner on the Details tab is what customers see; photos you add here appear after it.");
		}
		if (data.inherited_from_company) {
			return n
				? __("This branch is showing the company's {0}. The first photo you add here replaces them.", [photos(n)])
				: __("No photos yet. The first one you add becomes the cover on the marketplace card.");
		}
		return n
			? __("This branch shows its own {0}. The company's {1} are not shown here.", [photos(own), photos(n)])
			: __("This branch shows its own {0}.", [photos(own)]);
	}

	function tile(item, index, editable, has_cover) {
		const cover = has_cover && index === 0;
		const badge = cover
			? `<span data-testid="cbt-media-cover" style="position:absolute;top:.4rem;left:.4rem;padding:.1rem .5rem;border-radius:var(--cbt-radius-pill);background:var(--cbt-confirmed-bg);color:var(--cbt-confirmed-ink);font-size:.75rem;font-weight:600">${__("Cover")}</span>`
			: "";
		const actions = editable
			? `<div style="display:flex;gap:.35rem;margin-top:.35rem">
					${!has_cover || cover ? "" : `<button class="btn btn-xs btn-default" data-act="cover" data-i="${index}" data-testid="cbt-media-make-cover">${__("Make cover")}</button>`}
					<button class="btn btn-xs btn-default" data-act="remove" data-i="${index}" data-testid="cbt-media-remove" aria-label="${__("Remove photo")}">${__("Remove")}</button>
				</div>`
			: "";
		const caption = editable
			? `<input class="form-control input-xs" data-act="caption" data-i="${index}"
					data-testid="cbt-media-caption" placeholder="${__("Caption (optional)")}"
					value="${esc(item.caption || "")}" style="margin-top:.35rem">`
			: item.caption
			  ? `<div class="text-muted small" style="margin-top:.35rem">${esc(item.caption)}</div>`
			  : "";
		return `<div data-testid="cbt-media-tile" data-i="${index}" ${editable ? 'draggable="true"' : ""}
				style="width:11rem;padding:.5rem;border:1px solid var(--border-color);border-radius:var(--cbt-radius-md, 8px);background:var(--card-bg)">
				<div style="position:relative">
					<img src="${esc(item.image)}" alt="" loading="lazy"
						style="width:100%;aspect-ratio:4/3;max-width:100%;object-fit:cover;border-radius:var(--cbt-radius-sm, 4px);display:block">
					${badge}
				</div>
				${caption}
				${actions}
			</div>`;
	}

	function render(opts) {
		const frm = opts.frm;
		const field = frm.get_field(opts.html);
		const tab = find_tab(frm, opts.tab);
		if (!field) return;
		if (frm.is_new()) {
			field.$wrapper.empty();
			if (tab) tab.toggle(false);
			return;
		}
		if (tab) tab.toggle(true);

		const state = { items: [], data: null, dirty: false, busy: false };

		function paint() {
			const d = state.data;
			const editable = d.can_manage;
			const grid = state.items.length
				? state.items.map((item, i) => tile(item, i, editable, d.is_branch)).join("")
				: "";

			const drop = editable
				? `<div data-testid="cbt-media-drop" tabindex="0" role="button"
						style="border:2px dashed var(--border-color);border-radius:var(--cbt-radius-md, 8px);padding:1.25rem;text-align:center;cursor:pointer">
						<div style="font-weight:600">${__("Drop photos here, or choose files")}</div>
						<div class="text-muted small">${__("You can pick several at once. Up to {0}.", [d.max_photos])}</div>
						<input type="file" accept="image/*" multiple hidden data-testid="cbt-media-input">
					</div>`
				: `<div data-testid="cbt-media-gate" class="text-muted">${__("Photos for this company are managed by the platform.")}</div>`;

			const save = editable
				? `<div style="display:flex;align-items:center;gap:.6rem;margin-top:.75rem">
						<button class="btn btn-sm btn-primary" data-act="save" data-testid="cbt-media-save" ${state.dirty ? "" : "disabled"}>${__("Save photos")}</button>
						<span class="text-muted small" data-testid="cbt-media-status">${state.dirty ? __("Not saved yet.") : __("Saved.")}</span>
					</div>`
				: "";

			field.$wrapper.html(`<div data-testid="cbt-media-tab" data-count="${state.items.length}">
					<p data-testid="cbt-media-resolution">${esc(resolution_text({ ...d, rows: state.items }))}</p>
					${drop}
					<div data-testid="cbt-media-grid" style="display:flex;flex-wrap:wrap;gap:.75rem;margin-top:.75rem">${grid}</div>
					${save}
				</div>`);
			if (editable) wire(field.$wrapper[0]);
		}

		function mark_dirty() {
			state.dirty = true;
		}

		function upload(files) {
			const room = state.data.max_photos - state.items.length;
			const chosen = Array.from(files).slice(0, Math.max(room, 0));
			if (!chosen.length) {
				frappe.show_alert({ message: __("That is as many photos as this facility holds."), indicator: "orange" });
				return;
			}
			state.busy = true;
			// NOT frappe's upload_file: that needs write on the parent, which a
			// branch refuses while self branch management is off.
			const endpoint = "/api/method/court_booking_tech.api.facilities.upload_media_photo";
			const jobs = chosen.map((file) => {
				const form = new FormData();
				form.append("file", file, file.name);
				form.append("doctype", opts.doctype);
				form.append("name", opts.name);
				return fetch(endpoint, {
					method: "POST",
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
					body: form,
				})
					.then((r) => r.json().then((body) => ({ ok: r.ok, body })))
					.then(({ ok, body }) => {
						if (!ok) throw new Error(server_reason(body, file.name));
						return body.message && body.message.file_url;
					});
			});
			Promise.all(jobs)
				.then((urls) => {
					urls.filter(Boolean).forEach((url) => state.items.push({ image: url, caption: null }));
					state.busy = false;
					mark_dirty();
					paint();
				})
				.catch((err) => {
					state.busy = false;
					console.error("CBT media upload failed", err);
					frappe.msgprint({
						title: __("That photo was not added"),
						message: `<div data-testid="cbt-media-error">${esc(err.message)}</div>`,
						indicator: "red",
					});
				});
		}

		function wire(root) {
			const zone = root.querySelector('[data-testid="cbt-media-drop"]');
			const input = root.querySelector('[data-testid="cbt-media-input"]');
			if (zone && input) {
				zone.addEventListener("click", () => input.click());
				zone.addEventListener("keydown", (e) => {
					if (e.key === "Enter" || e.key === " ") {
						e.preventDefault();
						input.click();
					}
				});
				input.addEventListener("change", () => upload(input.files));
				["dragover", "dragenter"].forEach((ev) =>
					zone.addEventListener(ev, (e) => {
						e.preventDefault();
						zone.style.borderColor = "var(--primary)";
					})
				);
				["dragleave", "drop"].forEach((ev) =>
					zone.addEventListener(ev, (e) => {
						e.preventDefault();
						zone.style.borderColor = "";
					})
				);
				zone.addEventListener("drop", (e) => e.dataTransfer && upload(e.dataTransfer.files));
			}

			root.querySelectorAll("[data-act]").forEach((el) => {
				const act = el.dataset.act;
				const i = parseInt(el.dataset.i, 10);
				if (act === "caption") {
					el.addEventListener("change", () => {
						state.items[i].caption = el.value.trim() || null;
						mark_dirty();
						paint();
					});
				} else if (act === "cover") {
					el.addEventListener("click", () => {
						state.items.unshift(state.items.splice(i, 1)[0]);
						mark_dirty();
						paint();
					});
				} else if (act === "remove") {
					el.addEventListener("click", () => {
						state.items.splice(i, 1);
						mark_dirty();
						paint();
					});
				} else if (act === "save") {
					el.addEventListener("click", () => commit());
				}
			});

			// Drag is the shortcut; "Make cover" is the mechanism, so a touch
			// device and a keyboard both reach the same job.
			let from = null;
			root.querySelectorAll('[data-testid="cbt-media-tile"]').forEach((el) => {
				el.addEventListener("dragstart", () => (from = parseInt(el.dataset.i, 10)));
				el.addEventListener("dragover", (e) => e.preventDefault());
				el.addEventListener("drop", () => {
					const to = parseInt(el.dataset.i, 10);
					if (from === null || from === to) return;
					state.items.splice(to, 0, state.items.splice(from, 1)[0]);
					from = null;
					mark_dirty();
					paint();
				});
			});
		}

		function commit() {
			if (state.busy) return;
			state.busy = true;
			frappe
				.xcall("court_booking_tech.api.facilities.save_media", {
					doctype: opts.doctype,
					name: opts.name,
					rows: JSON.stringify(state.items),
				})
				.then((data) => {
					state.data = data;
					state.items = data.rows.slice();
					state.dirty = false;
					state.busy = false;
					paint();
					frappe.show_alert({ message: __("Photos saved."), indicator: "green" });
				})
				.catch((err) => {
					state.busy = false;
					console.error("CBT media save failed", err);
				});
		}

		field.$wrapper.html(`<div class="text-muted">${__("Loading photos…")}</div>`);
		frappe
			.xcall("court_booking_tech.api.facilities.list_media", {
				doctype: opts.doctype,
				name: opts.name,
			}, "GET")
			.then((data) => {
				state.data = data;
				state.items = data.rows.slice();
				paint();
			})
			.catch((err) => {
				console.error("CBT media tab failed", err);
				field.$wrapper.html(`<div class="text-muted">${__("Could not load the photos.")}</div>`);
			});
	}

	window.cbt_media_tab = { render, find_tab };
})();
