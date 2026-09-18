// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// The branch pin picker (PLAN D4, section-3): vendored Leaflet + OSM tiles for
// PICKING the pin, a keyless Google embed for the customer-facing preview. No
// CDN, no API key.
//
// Section-27 (Backlog B30) lifted it out of cbt_branch.js so the SAME map
// renders on the CBT Branch form AND inside the Branches tab's in-place editor
// on the CBT Company form. It is written against a HOST, not a form:
//
//   host.doc()              -> {latitude, longitude} as currently held
//   host.wrapper(fieldname) -> the jQuery wrapper of an HTML field
//   host.set_pin(lat, lng)  -> write both coordinates back to the host
//   host.can_edit()         -> may this seat move the pin
//   host.state              -> a per-host object the map keeps its Leaflet
//                              instance/marker on (a form's `frm`, an editor's
//                              own state — never a global)
//
// Loaded with frappe.require from both scripts; nothing here runs on load.

(function () {
	const LEAFLET_ASSETS = [
		"/assets/court_booking_tech/vendor/leaflet/leaflet.css",
		"/assets/court_booking_tech/vendor/leaflet/leaflet.js",
	];
	const METRO_MANILA = [14.5995, 120.9842];

	function has_pin(host) {
		const doc = host.doc() || {};
		return Boolean(doc.latitude && doc.longitude);
	}

	function render(host) {
		frappe.require(LEAFLET_ASSETS, () => {
			render_pin_map(host);
			render_embed_preview(host);
		});
	}

	function sync(host) {
		sync_marker_from_fields(host);
		render_embed_preview(host);
	}

	function render_pin_map(host) {
		const $wrapper = host.wrapper("pin_map_html");
		if (!$wrapper) return;
		const state = host.state;
		const stale = !state._cbt_map || !document.body.contains(state._cbt_map.getContainer());
		if (stale) {
			$wrapper.html(
				`<div class="cbt-branch-map" style="height: 320px; border-radius: 8px; z-index: 0;"></div>
				 <p class="text-muted small" style="margin-top: 6px;">
					${__("Click the map (or drag the pin) to set this branch's location.")}
				 </p>`
			);
			L.Icon.Default.imagePath = "/assets/court_booking_tech/vendor/leaflet/images/";
			state._cbt_map = L.map($wrapper.find(".cbt-branch-map")[0]);
			L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
				maxZoom: 19,
				attribution: "&copy; OpenStreetMap contributors",
			}).addTo(state._cbt_map);
			state._cbt_map.on("click", (e) => {
				if (!host.can_edit()) return;
				set_pin(host, e.latlng.lat, e.latlng.lng);
			});
			state._cbt_marker = null;
		}

		const doc = host.doc() || {};
		if (has_pin(host)) {
			state._cbt_map.setView([doc.latitude, doc.longitude], 16);
		} else {
			state._cbt_map.setView(METRO_MANILA, 11);
		}
		sync_marker_from_fields(host);
		// Leaflet renders gray tiles when initialised in a hidden/resizing
		// container — recalc once the layout settles.
		setTimeout(() => state._cbt_map && state._cbt_map.invalidateSize(), 200);
	}

	function set_pin(host, lat, lng) {
		host.set_pin(Number(lat.toFixed(7)), Number(lng.toFixed(7)));
	}

	function sync_marker_from_fields(host) {
		const state = host.state;
		if (!state._cbt_map) return;
		if (!has_pin(host)) {
			if (state._cbt_marker) {
				state._cbt_marker.remove();
				state._cbt_marker = null;
			}
			return;
		}
		const doc = host.doc();
		const pos = [doc.latitude, doc.longitude];
		if (!state._cbt_marker) {
			state._cbt_marker = L.marker(pos, { draggable: host.can_edit() }).addTo(state._cbt_map);
			state._cbt_marker.on("dragend", () => {
				const ll = state._cbt_marker.getLatLng();
				set_pin(host, ll.lat, ll.lng);
			});
		} else {
			state._cbt_marker.setLatLng(pos);
		}
	}

	function render_embed_preview(host) {
		const $wrapper = host.wrapper("embed_preview_html");
		if (!$wrapper) return;
		if (!has_pin(host)) {
			$wrapper.html(
				`<p class="text-muted">${__("Drop a pin to see the customer-facing map preview.")}</p>`
			);
			return;
		}
		const doc = host.doc();
		const lat = doc.latitude;
		const lng = doc.longitude;
		$wrapper.html(
			`<iframe src="https://maps.google.com/maps?q=${lat},${lng}&output=embed"
				style="width: 100%; height: 300px; border: 0; border-radius: 8px;"
				loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
			 <p style="margin-top: 6px;">
				<a href="https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}"
					target="_blank" rel="noopener">${__("Get directions")} →</a>
			 </p>`
		);
	}

	window.cbt_branch_map = { render, sync };
})();
