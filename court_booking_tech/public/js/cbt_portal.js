/* Shared portal fetch helper (PLAN §11.5).
 *
 * Every CBT portal page calls the server through cbtFetch — never raw
 * fetch — so the CSRF "None"-string edge case (solo-app lesson: web pages
 * render frappe.csrf_token as the literal string "None" for guests) is
 * handled in exactly one place.
 */
(function () {
	"use strict";

	function csrfToken() {
		if (typeof frappe === "undefined") return null;
		var token = frappe.csrf_token;
		if (!token || token === "None") return null;
		return token;
	}

	function extractError(body, fallback) {
		try {
			if (body && body._server_messages) {
				var msgs = JSON.parse(body._server_messages);
				if (msgs.length) {
					var first = JSON.parse(msgs[0]);
					var text = (first.message || "").replace(/<[^>]*>/g, "").trim();
					if (text) return text;
				}
			}
			if (body && body.exception) {
				var parts = String(body.exception).split(":");
				return parts[parts.length - 1].trim();
			}
		} catch (e) {
			/* fall through to fallback */
		}
		return fallback;
	}

	function resolve(response) {
		return response
			.json()
			.catch(function () {
				return {};
			})
			.then(function (body) {
				if (!response.ok) {
					throw new Error(
						extractError(body, "Something went wrong. Please try again.")
					);
				}
				return body.message;
			});
	}

	/** POST /api/method/<method> with JSON args; resolves response.message. */
	window.cbtFetch = function (method, args) {
		var headers = { "Content-Type": "application/json", Accept: "application/json" };
		var token = csrfToken();
		if (token) headers["X-Frappe-CSRF-Token"] = token;

		return fetch("/api/method/" + method, {
			method: "POST",
			headers: headers,
			body: JSON.stringify(args || {}),
		}).then(resolve);
	};

	/** GET /api/method/<method>?params; resolves response.message. */
	window.cbtGet = function (method, params) {
		var query = new URLSearchParams();
		Object.keys(params || {}).forEach(function (key) {
			var value = params[key];
			if (value !== null && value !== undefined && value !== "") {
				query.append(key, value);
			}
		});
		var suffix = query.toString();
		return fetch("/api/method/" + method + (suffix ? "?" + suffix : ""), {
			method: "GET",
			headers: { Accept: "application/json" },
		}).then(resolve);
	};

	/** Multipart POST (proof upload). Content-Type is deliberately NOT set —
	 *  the browser must add its own multipart boundary. */
	window.cbtUpload = function (method, formData) {
		var headers = { Accept: "application/json" };
		var token = csrfToken();
		if (token) headers["X-Frappe-CSRF-Token"] = token;

		return fetch("/api/method/" + method, {
			method: "POST",
			headers: headers,
			body: formData,
		}).then(resolve);
	};

	/** Live countdown driven by the SERVER clock: pass the server's "now" once
	 *  and every tick is computed from the offset, never the device clock. */
	window.cbtCountdown = function (element, targetIso, serverNowIso, onDone) {
		var target = new Date(String(targetIso).replace(" ", "T")).getTime();
		var serverNow = new Date(String(serverNowIso).replace(" ", "T")).getTime();
		var skew = Date.now() - serverNow;
		var timer = null;

		function tick() {
			var remaining = target - (Date.now() - skew);
			if (remaining <= 0) {
				element.textContent = "expired";
				element.classList.add("cbt-countdown-done");
				if (timer) clearInterval(timer);
				if (onDone) onDone();
				return;
			}
			var totalSeconds = Math.floor(remaining / 1000);
			var hours = Math.floor(totalSeconds / 3600);
			var minutes = Math.floor((totalSeconds % 3600) / 60);
			var seconds = totalSeconds % 60;
			element.textContent =
				(hours ? hours + "h " : "") +
				(hours || minutes ? minutes + "m " : "") +
				seconds + "s";
			element.classList.toggle("cbt-countdown-urgent", remaining < 5 * 60 * 1000);
		}

		tick();
		timer = setInterval(tick, 1000);
		return function stop() {
			if (timer) clearInterval(timer);
		};
	};

	/* QR lightbox (2026-09-03, B50 2026-09-09). The control is the WRAP —
	 * `[data-qr-zoom]` around the tile AND its "Tap or click to enlarge" words —
	 * and every wrap gets a DIRECT click listener (bindQrTiles): a click
	 * delegated at `document` never reached an iPhone tap. The delegated
	 * handlers stay as the keyboard path. The modal is built from the card's
	 * own label / account line / note read from the DOM, ONE per page, appended
	 * to <body> — never inside the checkout card, whose <label> would turn every
	 * click in the modal into a radio toggle. A throw here must never take
	 * cbtFetch down with it. */
	(function () {
		var modal = null;
		var opener = null;

		function textOf(card, selector) {
			var node = card && card.querySelector(selector);
			return node ? node.textContent : "";
		}

		function element(tag, className, id) {
			var node = document.createElement(tag);
			if (className) node.className = className;
			if (id) node.id = id;
			return node;
		}

		function build() {
			var existing = document.getElementById("cbt-qr-modal");
			if (existing) return existing;

			var shell = element("div", "cbt-modal", "cbt-qr-modal");
			shell.hidden = true;
			var body = element("div", "cbt-modal-body cbt-modal-body--qr");
			body.setAttribute("role", "dialog");
			body.setAttribute("aria-modal", "true");
			body.setAttribute("aria-labelledby", "cbt-qr-title");

			var head = element("div", "cbt-card-head");
			var title = element("h2", "cbt-card-title", "cbt-qr-title");
			title.style.flex = "1";
			var close = element("button", "btn btn-sm", "cbt-qr-close");
			close.type = "button";
			close.setAttribute("aria-label", "Close");
			close.innerHTML = "&times;";
			head.appendChild(title);
			head.appendChild(close);

			var image = element("img", "cbt-qr-large", "cbt-qr-image");
			image.alt = "QR";
			var meta = element("p", "cbt-channel-meta cbt-qr-meta", "cbt-qr-meta");
			var note = element("p", "cbt-channel-note cbt-qr-note", "cbt-qr-note");

			body.appendChild(head);
			body.appendChild(image);
			body.appendChild(meta);
			body.appendChild(note);
			shell.appendChild(body);
			document.body.appendChild(shell);

			close.addEventListener("click", hide);
			shell.addEventListener("click", function (event) {
				if (event.target === shell) hide();
			});
			document.addEventListener("keydown", function (event) {
				if (event.key === "Escape" && modal && !modal.hidden) hide();
			});
			return shell;
		}

		function show(tile) {
			modal = build();
			// The direct and the delegated listener both fire on one tap.
			if (!modal.hidden && opener === tile) return;
			var card = tile.closest(".cbt-channel");
			document.getElementById("cbt-qr-title").textContent =
				textOf(card, ".cbt-channel-label") || "QR";
			var image = document.getElementById("cbt-qr-image");
			// The picture inside the wrap; the attribute, not `currentSrc`, so the
			// tile and the lightbox carry the SAME src string (the E2E compares).
			var picture = tile.querySelector("img.cbt-channel-qr") || tile;
			image.src = picture.getAttribute("src");
			image.alt = picture.alt || "QR";
			var meta = document.getElementById("cbt-qr-meta");
			meta.textContent = textOf(card, ".cbt-channel-meta");
			meta.hidden = !meta.textContent;
			var note = document.getElementById("cbt-qr-note");
			note.textContent = textOf(card, ".cbt-channel-note");
			note.hidden = !note.textContent;
			opener = tile;
			modal.hidden = false;
			document.getElementById("cbt-qr-close").focus();
		}

		function hide() {
			if (!modal || modal.hidden) return;
			modal.hidden = true;
			if (opener && typeof opener.focus === "function") opener.focus();
			opener = null;
		}

		function tileOf(event) {
			var target = event.target;
			if (!target || !target.closest) return null;
			return target.closest("[data-qr-zoom]");
		}

		function open(tile) {
			try { show(tile); } catch (error) { /* the card still shows the QR */ }
		}

		// B50: bind each wrap DIRECTLY (idempotent). Called by every page after it
		// renders a channel card; the initial pass covers server-rendered ones.
		function bind(root) {
			var tiles = (root || document).querySelectorAll("[data-qr-zoom]:not([data-qr-bound])");
			Array.prototype.forEach.call(tiles, function (tile) {
				tile.setAttribute("data-qr-bound", "1");
				tile.addEventListener("click", function () { open(tile); });
			});
		}
		window.cbt = window.cbt || {};
		window.cbt.bindQrTiles = bind;
		if (document.readyState === "loading") {
			document.addEventListener("DOMContentLoaded", function () { bind(document); });
		} else {
			bind(document);
		}

		document.addEventListener("click", function (event) {
			var tile = tileOf(event);
			if (!tile) return;
			open(tile);
		});
		// Keyboard: Enter/Space open it (Space would otherwise scroll). A key
		// press does NOT activate the wrapping <label> — only a click picks the
		// channel — which is fine: the radio stays reachable on its own.
		document.addEventListener("keydown", function (event) {
			if (event.key !== "Enter" && event.key !== " ") return;
			var tile = tileOf(event);
			if (!tile) return;
			event.preventDefault();
			open(tile);
		});
	})();

	// ---- the facility gallery -------------------------------------------
	// One implementation for both banners: the marketplace card and the
	// booking page hero. Photos are fetched on the FIRST tap, never on load.
	(function () {
		var box, state = { photos: [], index: 0, opener: null }, cache = {};

		function el(id) {
			return document.getElementById(id);
		}

		function text(node, value) {
			if (node) node.textContent = value;
		}

		function paint() {
			var photo = state.photos[state.index] || {};
			var image = el("cbt-photo-viewer-image");
			if (image) {
				image.src = photo.file_url || "";
				image.alt = photo.caption || "";
			}
			text(el("cbt-photo-viewer-caption"), photo.caption || "");
			text(
				el("cbt-photo-viewer-title"),
				(box.dataset.labelCounter || "{0} / {1}")
					.replace("{0}", state.index + 1)
					.replace("{1}", state.photos.length)
			);
			el("cbt-photo-viewer-prev").disabled = state.index === 0;
			el("cbt-photo-viewer-next").disabled = state.index >= state.photos.length - 1;

			var strip = el("cbt-photo-viewer-strip");
			strip.innerHTML = "";
			state.photos.forEach(function (item, i) {
				var button = document.createElement("button");
				button.type = "button";
				button.className = "cbt-gallery-thumb";
				button.dataset.testid = "viewer-thumb";
				button.setAttribute("aria-current", i === state.index ? "true" : "false");
				var thumb = document.createElement("img");
				thumb.src = item.file_url;
				thumb.alt = "";
				thumb.loading = "lazy";
				button.appendChild(thumb);
				button.addEventListener("click", function () {
					state.index = i;
					paint();
				});
				strip.appendChild(button);
			});
		}

		function step(delta) {
			var next = state.index + delta;
			if (next < 0 || next >= state.photos.length) return;
			state.index = next;
			paint();
		}

		function close() {
			box.hidden = true;
			if (state.opener && state.opener.focus) state.opener.focus();
		}

		function show(photos) {
			if (!photos || !photos.length) return;
			state.photos = photos;
			state.index = 0;
			box.hidden = false;
			paint();
			el("cbt-photo-viewer-close").focus();
		}

		function open(company, branch) {
			if (!box) return;
			var key = company + "/" + (branch || "");
			state.opener = document.activeElement;
			if (cache[key]) return show(cache[key]);
			var params = { company: company };
			if (branch) params.branch = branch;
			cbtGet("court_booking_tech.api.portal.get_facility_photos", params).then(
				function (data) {
					cache[key] = (data && data.photos) || [];
					show(cache[key]);
				}
			);
		}

		function bind() {
			box = el("cbt-photo-viewer");
			if (!box) return;
			// Server-rendered banners say which facility they are; the
			// marketplace card builds its own buttons and calls open() directly.
			Array.prototype.forEach.call(
				document.querySelectorAll(".cbt-card-photo-btn[data-company]"),
				function (button) {
					button.addEventListener("click", function (event) {
						event.preventDefault();
						open(button.dataset.company, button.dataset.branch || null);
					});
				}
			);
			el("cbt-photo-viewer-close").addEventListener("click", close);
			el("cbt-photo-viewer-prev").addEventListener("click", function () { step(-1); });
			el("cbt-photo-viewer-next").addEventListener("click", function () { step(1); });
			box.addEventListener("click", function (event) {
				if (event.target === box) close();
			});
			document.addEventListener("keydown", function (event) {
				if (box.hidden) return;
				if (event.key === "Escape") close();
				if (event.key === "ArrowLeft") step(-1);
				if (event.key === "ArrowRight") step(1);
			});
		}

		window.cbtGallery = { open: open, close: close };
		if (document.readyState === "loading") {
			document.addEventListener("DOMContentLoaded", bind);
		} else {
			bind();
		}
	})();
})();
