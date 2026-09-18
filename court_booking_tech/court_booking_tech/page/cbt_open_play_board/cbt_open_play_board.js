// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// Open Play Board (section-10) — the staff screen for a running session, and a
// TV display for the players waiting their turn.
//
// Discipline inherited from the Court Board (section-7), because every one of
// these cost a failed round there:
//   - countdowns come from a SERVER clock offset captured on every payload
//     (client clocks drift — solo-app lesson 3);
//   - programmatic field sets are depth-guarded so a slow change-event chain
//     can never hijack a later caller's load;
//   - a _load_seq token invalidates stale responses;
//   - auto-refresh is suspended while any dialog is open.
// New here: TV mode runs unattended for hours, so a dead desk session must
// degrade to a readable hint over the LAST GOOD DATA — never a blank screen,
// and never a modal storm every 15 seconds.

frappe.pages["cbt-open-play-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Open Play Board"),
		single_column: true,
	});
	wrapper.open_play_board = new OpenPlayBoard(page, wrapper);
};

frappe.pages["cbt-open-play-board"].on_page_show = function (wrapper) {
	wrapper.open_play_board && wrapper.open_play_board.on_show();
};

// SECTION-23 (Backlog B14): same three template-literal traps as the Court
// Board's style block — no CSS backslash escapes (JS eats them first), no stray
// backtick or ${ (it would silently delete this whole stylesheet), and load the
// board by eye after editing, because unstyled-but-present DOM passes the suite.
//
// TV MODE IS DELIBERATELY UNTOUCHED. Its palette is already dark-tuned for a
// wall screen, and e2e/tests/test_08_open_play.py probes three fixed viewport
// points with elementFromPoint to prove the overlay still covers the desk — the
// worst place in the app to move a colour "while we are here".
const CBT_OP_STYLES = `
.cbt-op { display: flex; gap: var(--margin-lg); align-items: flex-start; }
.cbt-op-main { flex: 1; min-width: 0; }
.cbt-op-side { width: 320px; flex-shrink: 0; }
@media (max-width: 1100px) {
	.cbt-op { flex-direction: column; }
	/* B31: in column direction align-items:flex-start stops the stretch, so the
	   main column shrank to its content (~3/4 of a phone). Say the width. */
	.cbt-op-main { width: 100%; }
	.cbt-op-side { width: 100%; }
}

.cbt-op-topbar {
	display: flex; align-items: center; gap: var(--margin-md);
	flex-wrap: wrap; margin-bottom: var(--margin-md);
}
.cbt-op-title { font-weight: 600; font-size: var(--text-lg); margin: 0; }
.cbt-op-sub { color: var(--text-muted); font-size: var(--text-sm); }
.cbt-op-updated { font-size: var(--text-xs); color: var(--text-muted); margin-left: auto; }

.cbt-op-session-list {
	display: grid; gap: var(--margin-md);
	grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}
.cbt-op-session-card {
	background: var(--card-bg); border: 1px solid var(--border-color);
	border-radius: var(--cbt-radius); padding: var(--padding-md);
	cursor: pointer; box-shadow: var(--cbt-shadow);
	transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.cbt-op-session-card:hover {
	border-color: var(--cbt-primary); box-shadow: var(--cbt-shadow-lift);
}

.cbt-op-courts { display: grid; gap: var(--margin-md); }
.cbt-op-court-flow {
	display: grid; gap: var(--margin-md);
	grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}
.cbt-op-floor-gap {
	border: 1px dashed var(--border-color); border-radius: var(--border-radius-md);
	min-height: 60px; opacity: 0.4;
}
.cbt-op-court-dim { opacity: 0.35; }

.cbt-op-court-card {
	background: var(--card-bg); border: 1px solid var(--border-color);
	border-radius: var(--cbt-radius); box-shadow: var(--cbt-shadow);
	display: flex; flex-direction: column; min-width: 0; overflow: hidden;
}
/* Tinted heads, same move as the Court Board's court cards: the two boards are
   one product and should read as one. --cbt-primary-soft inverts in dark mode,
   so frappe's own text colour on top stays readable in both themes. */
.cbt-op-court-head {
	display: flex; align-items: center; justify-content: space-between;
	gap: 8px; padding: 9px var(--padding-sm);
	border-bottom: 1px solid var(--cbt-primary-line); background: var(--cbt-primary-soft);
}
.cbt-op-court-name { font-weight: 650; letter-spacing: -0.01em; }
.cbt-op-count { font-variant-numeric: tabular-nums; font-weight: 600; }
.cbt-op-count-late { color: var(--text-on-red); background: var(--bg-red);
	border-radius: var(--border-radius-sm); padding: 0 6px; }
.cbt-op-players { padding: var(--padding-sm); display: grid; gap: 6px; }
.cbt-op-player {
	display: flex; align-items: center; gap: 6px;
	background: var(--bg-light-gray); border-radius: var(--border-radius-sm);
	padding: 4px 8px; min-width: 0;
}
.cbt-op-player-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* One node per REAL empty seat and never a decorative placeholder: file 08
   clicks .cbt-op-court-card[data-court] .cbt-op-vacancy strictly, so a ghost
   would be a strict-mode violation, not a cosmetic surprise.
   (No backticks in this comment, and none anywhere else inside this literal:
   one ends the string, and the board then loads with NO styles at all while
   node --check still reports the file as valid. Ask how we know.) */
.cbt-op-vacancy {
	border: 1px dashed var(--border-color); border-radius: var(--border-radius-sm);
	padding: 4px 8px; text-align: center; color: var(--text-muted); cursor: pointer;
}
.cbt-op-vacancy:hover {
	border-color: var(--cbt-primary); color: var(--cbt-primary);
	background: var(--cbt-primary-soft);
}
.cbt-op-court-idle { padding: var(--padding-sm); color: var(--text-muted); font-size: var(--text-sm); }

/* Section-20 (Backlog B2): a walk-in has no account, so no membership, no ban
   and no portal history. Staff need to see that at a glance — the chip is the
   only thing on the row that says so. */
.cbt-op-walkin {
	flex-shrink: 0; font-size: var(--text-xs); line-height: 1.4;
	padding: 0 6px; border-radius: 999px;
	background: var(--bg-light-gray); color: var(--text-muted);
	border: 1px solid var(--border-color);
}
.cbt-op-tv .cbt-op-walkin { background: #22303c; color: #9fb0c0; border-color: #2c3e4d; }

.cbt-op-next {
	background: var(--bg-blue); color: var(--text-on-blue);
	border-radius: var(--border-radius-md); padding: 8px var(--padding-sm);
	margin-bottom: var(--margin-md); font-size: var(--text-sm);
}

.cbt-op-panel {
	background: var(--card-bg); border: 1px solid var(--border-color);
	border-radius: var(--cbt-radius); overflow: hidden;
	box-shadow: var(--cbt-shadow);
}
.cbt-op-panel-head {
	display: flex; align-items: center; justify-content: space-between;
	padding: 9px var(--padding-sm); border-bottom: 1px solid var(--cbt-primary-line);
	font-weight: 650; background: var(--cbt-primary-soft);
}
.cbt-op-queue-row {
	display: flex; align-items: center; gap: 8px;
	padding: 6px var(--padding-sm); border-bottom: 1px solid var(--border-color);
}
.cbt-op-queue-row:last-child { border-bottom: none; }
.cbt-op-queue-next { background: var(--bg-green); color: var(--text-on-green); }
.cbt-op-queue-pos { width: 22px; text-align: right; color: var(--text-muted);
	font-variant-numeric: tabular-nums; }
.cbt-op-queue-name { flex: 1; min-width: 0; overflow: hidden;
	text-overflow: ellipsis; white-space: nowrap; }
.cbt-op-queue-games { font-size: var(--text-xs); color: var(--text-muted); }
.cbt-op-drag { cursor: grab; color: var(--text-muted); }
.cbt-op-divider {
	font-size: var(--text-xs); color: var(--text-muted); text-align: center;
	padding: 2px 0; background: var(--bg-light-gray);
}
.cbt-op-stats {
	display: flex; gap: 12px; flex-wrap: wrap; padding: 8px var(--padding-sm);
	font-size: var(--text-xs); color: var(--text-muted);
	border-top: 1px solid var(--border-color);
}

.cbt-op-bottom {
	position: sticky; bottom: 0; display: flex; gap: 8px; flex-wrap: wrap;
	padding: var(--padding-sm) 0; margin-top: var(--margin-md);
	background: var(--bg-color); border-top: 1px solid var(--border-color);
}

/* Matches the Court Board's .cbt-board-empty: a state the board MEANT to show,
   not a rendering failure. */
.cbt-op-empty {
	text-align: center; padding: 56px 12px; color: var(--text-muted);
	border: 2px dashed var(--cbt-primary-line); border-radius: var(--cbt-radius);
	background: var(--cbt-primary-soft); line-height: 1.5;
}
.cbt-op-empty-icon { font-size: 34px; margin-bottom: 10px; }
/* TV mode paints its own dark ground — the tinted box would be a bright hole
   in the middle of it. */
.cbt-op-tv .cbt-op-empty { background: transparent; border-color: #22303c; }

.cbt-op-banner {
	background: var(--bg-orange); color: var(--text-on-orange);
	border-radius: var(--border-radius-md); padding: 8px var(--padding-sm);
	margin-bottom: var(--margin-md); display: flex; align-items: center; gap: 8px;
}

/* ---- TV mode: dark, big, staff controls gone ----
   The board becomes a FIXED full-viewport overlay rather than trying to hide
   frappe's chrome piece by piece. Hiding .navbar/.page-head left v16's app
   sidebar and the page's own form controls on screen, and chasing those class
   names couples this page to another app's markup. Covering the viewport is
   both simpler and immune to frappe restyling its desk. */
.cbt-op-wrap.cbt-op-tv {
	position: fixed; inset: 0; z-index: 2000; overflow-y: auto;
}
body.cbt-op-tv-mode .navbar,
body.cbt-op-tv-mode .page-head,
body.cbt-op-tv-mode footer { display: none !important; }
.cbt-op-tv { background: #0b0f14; color: #f2f6fa; min-height: 100vh; padding: 18px; }
.cbt-op-tv .cbt-op-staff-only { display: none !important; }
.cbt-op-tv .cbt-op-title { font-size: 30px; color: #fff; }
.cbt-op-tv .cbt-op-sub, .cbt-op-tv .cbt-op-updated { color: #9fb0c0; }
.cbt-op-tv .cbt-op-court-card { background: #131a22; border-color: #22303c; }
.cbt-op-tv .cbt-op-court-head { background: #182430; border-color: #22303c; }
.cbt-op-tv .cbt-op-court-name { font-size: 22px; color: #fff; }
.cbt-op-tv .cbt-op-count { font-size: 26px; color: #7ee787; }
.cbt-op-tv .cbt-op-count-late { background: #7f1d1d; color: #fff; }
.cbt-op-tv .cbt-op-player { background: #1b2733; font-size: 19px; }
.cbt-op-tv .cbt-op-player-name { color: #eaf2f8; }
.cbt-op-tv .cbt-op-panel { background: #131a22; border-color: #22303c; }
.cbt-op-tv .cbt-op-panel-head { background: #182430; border-color: #22303c;
	color: #fff; font-size: 20px; }
.cbt-op-tv .cbt-op-queue-row { border-color: #22303c; font-size: 19px; }
.cbt-op-tv .cbt-op-queue-next { background: #14532d; color: #eaffef; }
.cbt-op-tv .cbt-op-divider { background: #182430; color: #9fb0c0; }
.cbt-op-tv .cbt-op-side { width: 380px; }
/* The next-up banner is a light chip by default — it must not glare on a TV. */
.cbt-op-tv .cbt-op-next { background: #14304a; color: #cfe8ff; font-size: 20px; }
.cbt-op-tv .cbt-op-empty, .cbt-op-tv .cbt-op-court-idle { color: #9fb0c0; }
.cbt-op-tv .cbt-op-stats { color: #9fb0c0; border-color: #22303c; }
.cbt-op-tv .cbt-op-queue-pos, .cbt-op-tv .cbt-op-queue-games { color: #9fb0c0; }
.cbt-op-tv-exit {
	position: fixed; right: 14px; bottom: 14px; opacity: 0.15; z-index: 10;
	transition: opacity 0.2s;
}
.cbt-op-tv-exit:hover { opacity: 1; }
/* CBT_STYLE_BLOCK_END — do not move. tests/test_board_assets.py reads the text
   between this file's FIRST two backticks and fails if this sentinel is not the
   last thing in it. One stray backtick or dollar-brace anywhere above ends the
   literal early, this comment falls outside it, and the whole board loads with
   NO stylesheet — silently, because the suite selects on classes. */
`;

const OP_PLAYERS_PER_COURT = 4;
const OP_REFRESH_MS = 15000;

class OpenPlayBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.company = null;
		this.session = null;
		this.state = null;
		this.clock_offset = 0;
		this.tv = false;
		this.floor_plan = false;

		this.inject_styles();
		this.build_skeleton();
		this.setup_toolbar();
		this.bind_events();
		this.start_timers();
		this.bootstrap_company();
	}

	inject_styles() {
		if (!document.getElementById("cbt-open-play-board-css")) {
			const style = document.createElement("style");
			style.id = "cbt-open-play-board-css";
			style.textContent = CBT_OP_STYLES;
			document.head.appendChild(style);
		}
	}

	build_skeleton() {
		this.$root = $(`
			<div class="cbt-op-wrap">
				<div class="cbt-op-banner" style="display:none"></div>
				<div class="cbt-op-topbar">
					<div>
						<div class="cbt-op-title"></div>
						<div class="cbt-op-sub"></div>
					</div>
					<span class="cbt-op-updated"></span>
				</div>
				<div class="cbt-op">
					<div class="cbt-op-main"></div>
					<div class="cbt-op-side"></div>
				</div>
				<div class="cbt-op-bottom cbt-op-staff-only"></div>
			</div>
		`).appendTo(this.page.main);

		this.$banner = this.$root.find(".cbt-op-banner");
		this.$title = this.$root.find(".cbt-op-title");
		this.$sub = this.$root.find(".cbt-op-sub");
		this.$updated = this.$root.find(".cbt-op-updated");
		this.$main = this.$root.find(".cbt-op-main");
		this.$side = this.$root.find(".cbt-op-side");
		this.$bottom = this.$root.find(".cbt-op-bottom");
	}

	setup_toolbar() {
		this.company_field = this.page.add_field({
			fieldtype: "Link",
			fieldname: "company",
			label: __("Company"),
			options: "CBT Company",
			change: () => {
				if (this._sync_depth) return;
				const value = this.company_field.get_value();
				if (value && value !== this.company) {
					this.company = value;
					this.session = null;
					this.load();
				}
			},
		});
		this.page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
	}

	_set_field_guarded(field, value) {
		// One increment PER chain, released on resolve AND reject — a boolean
		// would be cleared by the first chain while a slower one is still in
		// flight (the Court Board's bootstrap-hijack lesson).
		this._sync_depth = (this._sync_depth || 0) + 1;
		const release = () => {
			this._sync_depth--;
		};
		return Promise.resolve(field.set_value(value)).then(release, release);
	}

	bootstrap_company() {
		// The company list is permission-scoped server-side: tenant staff see
		// exactly one and get it locked in; platform admins choose.
		frappe.db
			.get_list("CBT Company", { fields: ["name"], limit: 500 })
			.then((rows) => {
				const names = (rows || []).map((r) => r.name);
				if (names.length === 1) {
					this.company_field.$input.prop("disabled", true);
				}
				const remembered = localStorage.getItem("cbt_open_play_company");
				let pick = null;
				if (names.length === 1) pick = names[0];
				else if (remembered && names.includes(remembered)) pick = remembered;
				else if (names.length) pick = names[0];

				if (pick && !this.company) {
					this.company = pick;
					this._set_field_guarded(this.company_field, pick);
					const routed = frappe.utils.get_url_arg("session");
					if (routed) this.session = routed;
					this.load();
				} else if (!pick) {
					this.render_empty(
						"🏸",
						__("No company is visible to you"),
						__("Ask your administrator for access.")
					);
				}
				this._bootstrapped = true;
			});
	}

	on_show() {
		if (!this._bootstrapped) return;
		const routed = frappe.utils.get_url_arg("session");
		if (routed && routed !== this.session) {
			this.session = routed;
		}
		this.load();
	}

	// Public entry (deep links + E2E): point the board at one session, and
	// optionally at a company. Platform users see several companies, so the
	// bootstrap pick is theirs to make — a caller that knows which tenant it
	// means must be able to say so instead of racing that choice.
	show(session, company) {
		if (company && company !== this.company) {
			this.company = company;
			this._set_field_guarded(this.company_field, company);
		}
		this.session = session || null;
		return this.load();
	}

	back_to_list() {
		this.session = null;
		this.floor_plan = false;
		return this.load();
	}

	bind_events() {
		this.$root.on("click", ".cbt-op-session-card", (e) => {
			this.show($(e.currentTarget).attr("data-session"));
		});
		this.$root.on("click", "[data-action='game-done']", (e) => {
			this.game_done($(e.currentTarget).attr("data-court"));
		});
		this.$root.on("click", "[data-action='return']", (e) => {
			e.stopPropagation();
			const $btn = $(e.currentTarget);
			this.return_to_queue($btn.attr("data-court"), $btn.attr("data-player"));
		});
		this.$root.on("click", ".cbt-op-vacancy", (e) => {
			this.open_backfill_dialog($(e.currentTarget).attr("data-court"));
		});
		this.$root.on("click", "[data-action='remove']", (e) => {
			e.stopPropagation();
			const $btn = $(e.currentTarget);
			this.remove_player(
				$btn.attr("data-player"),
				$btn.attr("data-player-name")
			);
		});
		this.$root.on("click", "[data-action='pay']", (e) => {
			this.confirm_payment($(e.currentTarget).attr("data-participant"));
		});
		this.$bottom.on("click", "[data-action='start']", () => this.start_session());
		this.$bottom.on("click", "[data-action='add']", () => this.open_add_players_dialog());
		this.$bottom.on("click", "[data-action='complete']", () => this.complete_session());
		this.$bottom.on("click", "[data-action='tv']", () => this.set_tv(true));
		this.$bottom.on("click", "[data-action='back']", () => this.back_to_list());
		this.$bottom.on("click", "[data-action='floor']", () => {
			this.floor_plan = !this.floor_plan;
			this.render();
		});
		this.$root.on("click", "[data-action='exit-tv']", () => this.set_tv(false));
		this.$root.on("click", "[data-action='reload']", () => {
			this._session_dead = false;
			this.$banner.hide();
			this.start_timers();
			this.load();
		});
	}

	start_timers() {
		clearInterval(this.refresh_timer);
		clearInterval(this.tick_timer);
		this.refresh_timer = setInterval(() => {
			if (!this.company || this._session_dead) return;
			if (!$(this.wrapper).is(":visible")) return;
			if (document.querySelector(".modal.show")) return;
			this.load({ quiet: true });
		}, OP_REFRESH_MS);
		this.tick_timer = setInterval(() => this.tick(), 1000);
	}

	// ------------------------------------------------------------------
	// Data
	// ------------------------------------------------------------------

	load(opts = {}) {
		if (!this.company) return Promise.resolve();
		const seq = (this._load_seq = (this._load_seq || 0) + 1);
		if (!opts.quiet) this.$updated.text(__("Loading…"));
		return frappe
			.xcall(
				"court_booking_tech.api.open_play.get_open_play_board",
				{ company: this.company, session: this.session || null },
				// xcall defaults to POST; the board payload is a GET-only
				// endpoint, and a verb mismatch here fails EVERY refresh.
				"GET"
			)
			.then((message) => {
				if (seq !== this._load_seq) return;
				this.payload = message;
				this.state = message.session;
				this.clock_offset =
					frappe.datetime.str_to_obj(message.server_now).getTime() - Date.now();
				localStorage.setItem("cbt_open_play_company", this.company);
				// Re-assert the selector on every EXPLICIT load, so the last
				// write always belongs to the payload actually on screen. A
				// slower bootstrap chain used to land after a caller had
				// re-pointed the board, leaving the box naming one tenant while
				// the board showed another — the worst thing a multi-tenant
				// screen can say. Skipped on the quiet 15s refresh so it can
				// never yank a selector the user is typing into.
				if (!opts.quiet) {
					this._set_field_guarded(this.company_field, this.company);
				}
				this.$banner.hide();
				this.render();
				// Section-18 (Backlog B9): the SERVER's clock, like the queue and
				// game timers below. This board runs unattended on a TV, which is
				// exactly the device whose own clock nobody checks.
				this.$updated.text(
					__("Updated {0}", [
						moment(this.server_now_ms()).format("HH:mm:ss"),
					])
				);
			})
			.catch((error) => {
				if (seq !== this._load_seq) return;
				// The banner is for whoever walks past a TV; the console line
				// is for whoever has to debug it. Swallowing the reason turned
				// a plain GET/POST mismatch into a silent "screen looks fine
				// but never fills in" — never let that happen again.
				console.error("[cbt-open-play-board] board refresh failed:", error);
				// TV mode runs unattended: keep the last good screen, stop the
				// polling loop (one banner, not a modal every 15s) and say what
				// to do about it.
				this._session_dead = true;
				clearInterval(this.refresh_timer);
				this.show_banner(
					__("Live updates stopped — this screen may be signed out."),
					__("Reload")
				);
			});
	}

	show_banner(message, action_label) {
		this.$banner
			.html(
				`<span>⚠️ ${frappe.utils.escape_html(message)}</span>
				<button class="btn btn-xs btn-default" data-action="reload">
					${frappe.utils.escape_html(action_label)}</button>`
			)
			.show();
	}

	server_now_ms() {
		return Date.now() + this.clock_offset;
	}

	// ------------------------------------------------------------------
	// Rendering
	// ------------------------------------------------------------------

	render_empty(icon, title, hint) {
		this.$main.html(`
			<div class="cbt-op-empty">
				<div class="cbt-op-empty-icon">${icon}</div>
				<div style="font-weight:600">${title}</div>
				<div style="font-size:var(--text-sm)">${hint || ""}</div>
			</div>
		`);
		this.$side.empty();
		this.$bottom.empty();
	}

	render() {
		this.$root.toggleClass("cbt-op-tv", this.tv);
		$(document.body).toggleClass("cbt-op-tv-mode", this.tv);
		if (this.state) this.render_session();
		else this.render_session_list();
	}

	render_session_list() {
		const sessions = (this.payload && this.payload.sessions) || [];
		this.$title.text(__("Open Play"));
		this.$sub.text(
			__("Sessions for {0}", [
				moment(this.payload.today, "YYYY-MM-DD").format("ddd, D MMM YYYY"),
			])
		);
		this.$side.empty();

		if (!sessions.length) {
			this.render_empty(
				"🏸",
				__("No open play sessions today"),
				__("Create one, then add players as they arrive.")
			);
		} else {
			const esc = frappe.utils.escape_html;
			const cards = sessions
				.map(
					(s) => `
					<div class="cbt-op-session-card" data-session="${esc(s.name)}">
						<div style="display:flex; justify-content:space-between; gap:8px">
							<b>${esc(s.title)}</b>
							<span class="indicator-pill ${OP_STATUS_COLOR[s.status] || "gray"}">${__(s.status)}</span>
						</div>
						<div class="text-muted" style="font-size:var(--text-sm)">
							${esc(s.branch_name || s.branch)} · ${cbt.fmt.timeRange(s.start_time, s.end_time)}
						</div>
						<div style="margin-top:6px; font-size:var(--text-sm)">
							👥 ${cint(s.current_participants)} ·
							${format_currency(s.total_revenue, "PHP")} ·
							${__(s.rotation_mode)}
						</div>
					</div>`
				)
				.join("");
			this.$main.html(`<div class="cbt-op-session-list">${cards}</div>`);
		}

		// Set LAST: render_empty clears the bottom bar.
		this.$bottom.html(
			`<button class="btn btn-primary btn-sm" data-action="new-session">
				+ ${__("New Session")}</button>`
		);
		this.$bottom.find("[data-action='new-session']").on("click", () => {
			frappe.new_doc("CBT Open Play Session", { company: this.company });
		});
	}

	render_session() {
		const state = this.state;
		const esc = frappe.utils.escape_html;
		this.$title.text(state.title);
		this.$sub.html(
			`${esc(state.branch_name || state.branch)} ·
			${moment(state.session_date, "YYYY-MM-DD").format("ddd, D MMM YYYY")}
			${cbt.fmt.timeRange(state.start_time, state.end_time)} ·
			<span class="indicator-pill ${OP_STATUS_COLOR[state.status] || "gray"}">${__(state.status)}</span>`
		);

		this.$main.empty();
		this.render_next_matchup();
		this.render_courts();
		this.render_queue();
		this.render_bottom();
		this.tick_countdowns();
	}

	render_next_matchup() {
		const waiting = this.waiting_rows();
		if (!waiting.length || this.state.status !== "Open") return;
		const next = waiting.slice(0, OP_PLAYERS_PER_COURT);
		const names = next.map((r) => short_name(r.customer_name)).join(", ");
		const label =
			next.length === OP_PLAYERS_PER_COURT
				? __("Next up: {0}", [names])
				: __("Waiting for {0} more player(s) — {1}", [
						OP_PLAYERS_PER_COURT - next.length,
						names,
				  ]);
		$(`<div class="cbt-op-next">🎯 ${frappe.utils.escape_html(label)}</div>`).appendTo(
			this.$main
		);
	}

	render_courts() {
		const state = this.state;
		const by_court = {};
		for (const court of state.courts) {
			by_court[court.court] = this.render_court_card(court);
		}
		const layout = state.layout || {};
		const use_floor =
			this.floor_plan && layout.rows && layout.columns && (layout.cells || []).length;

		let $grid;
		if (use_floor) {
			$grid = $(
				`<div class="cbt-op-courts" style="grid-template-columns:repeat(${layout.columns}, minmax(0, 1fr))"></div>`
			);
			const cell_map = {};
			for (const cell of layout.cells || []) {
				cell_map[`${cell.row_index}:${cell.col_index}`] = cell;
			}
			for (let row = 1; row <= layout.rows; row++) {
				for (let col = 1; col <= layout.columns; col++) {
					const cell = cell_map[`${row}:${col}`];
					if (cell && by_court[cell.court]) {
						$grid.append(by_court[cell.court]);
					} else if (cell && cell.court) {
						// A court of this branch that is NOT in the session — shown
						// dimmed so the physical layout still reads correctly.
						$grid.append(
							`<div class="cbt-op-court-card cbt-op-court-dim">
								<div class="cbt-op-court-head">
									<span class="cbt-op-court-name">${frappe.utils.escape_html(cell.court)}</span>
								</div>
								<div class="cbt-op-court-idle">${__("Not in this session")}</div>
							</div>`
						);
					} else {
						$grid.append('<div class="cbt-op-floor-gap"></div>');
					}
				}
			}
		} else {
			$grid = $('<div class="cbt-op-court-flow"></div>');
			state.courts.forEach((c) => $grid.append(by_court[c.court]));
		}
		this.$main.append($grid);
	}

	render_court_card(court) {
		const esc = frappe.utils.escape_html;
		const state = this.state;
		const assignment = (state.assignments || []).find((a) => a.court === court.court);
		const $card = $(`
			<div class="cbt-op-court-card" data-court="${esc(court.court)}">
				<div class="cbt-op-court-head">
					<span class="cbt-op-court-name">${esc(court.court_name || court.court)}</span>
					<span class="cbt-op-court-clock"></span>
				</div>
				<div class="cbt-op-players"></div>
			</div>
		`);
		const $clock = $card.find(".cbt-op-court-clock");
		const $players = $card.find(".cbt-op-players");

		if (!assignment) {
			$players.replaceWith(
				`<div class="cbt-op-court-idle">${__("Idle — start the session or backfill players.")}</div>`
			);
			return $card;
		}

		if (state.rotation_mode === "Timed" && assignment.ends_at) {
			$clock.html(
				`<span class="cbt-op-count" data-deadline="${esc(assignment.ends_at)}"></span>`
			);
		} else {
			$clock.text(__("Playing to {0}", [cint(state.rally_points)]));
		}

		for (const player of assignment.players) {
			// Section-20: addressed by KEY, never by account — a walk-in has no
			// account, and `player.customer` would be null for all of them at
			// once. For an account player the key IS the user id, which is what
			// keeps every pre-B2 selector and assertion true.
			$players.append(`
				<div class="cbt-op-player" data-player="${esc(player.key)}"
					${player.customer ? `data-customer="${esc(player.customer)}"` : ""}
					data-player-name="${esc(player.name)}">
					<span class="cbt-op-player-name">${esc(player.name)}</span>
					${player.customer ? "" : op_walkin_chip()}
					<button class="btn btn-xs btn-default cbt-op-staff-only" data-action="return"
						data-court="${esc(court.court)}" data-player="${esc(player.key)}"
						title="${__("Back to queue")}">✕</button>
				</div>
			`);
		}
		for (let i = 0; i < cint(assignment.vacancies); i++) {
			$players.append(
				`<div class="cbt-op-vacancy cbt-op-staff-only" data-court="${esc(court.court)}">
					+ ${__("Add player")}</div>`
			);
		}
		if (state.status === "Open") {
			$players.append(`
				<button class="btn btn-sm btn-primary cbt-op-staff-only" data-action="game-done"
					data-court="${esc(court.court)}">${__("Game Done")}</button>
			`);
		}
		return $card;
	}

	waiting_rows() {
		return ((this.state && this.state.queue) || []).filter((r) => r.status === "Waiting");
	}

	render_queue() {
		const esc = frappe.utils.escape_html;
		const state = this.state;
		const waiting = this.waiting_rows();
		const $panel = $(`
			<div class="cbt-op-panel">
				<div class="cbt-op-panel-head">
					<span>${__("Rotation Queue")}</span>
					<span>${waiting.length}</span>
				</div>
				<div class="cbt-op-queue-body"></div>
				<div class="cbt-op-stats"></div>
			</div>
		`);
		const $body = $panel.find(".cbt-op-queue-body");

		if (!waiting.length) {
			$body.html(
				`<div class="cbt-op-court-idle">${__("Nobody is waiting.")}</div>`
			);
		}
		waiting.forEach((row, index) => {
			if (index === OP_PLAYERS_PER_COURT) {
				$body.append(`<div class="cbt-op-divider">── ${__("next up")} ──</div>`);
			}
			// `data-customer` is rendered ONLY for a player who HAS an account:
			// a walk-in has none, and an empty attribute would still satisfy a
			// `[data-customer]` presence selector and read back as "".
			// `data-player-name` carries the FULL typed name — the visible chip
			// is deliberately shortened for TV legibility.
			$body.append(`
				<div class="cbt-op-queue-row ${index < OP_PLAYERS_PER_COURT ? "cbt-op-queue-next" : ""}"
					data-queue-name="${esc(row.name)}" data-player-key="${esc(row.player_key)}"
					${row.customer ? `data-customer="${esc(row.customer)}"` : ""}
					data-player-name="${esc(row.customer_name)}">
					<span class="cbt-op-drag cbt-op-staff-only" title="${__("Drag to reorder")}">☰</span>
					<span class="cbt-op-queue-pos">${cint(row.queue_position)}</span>
					<span class="cbt-op-queue-name" title="${esc(row.customer_name)}">${esc(
						short_name(row.customer_name)
					)}</span>
					${row.customer ? "" : op_walkin_chip()}
					<span class="cbt-op-queue-games" title="${__("Games played")}">🏸 ${cint(row.games_played)}</span>
					<button class="btn btn-xs btn-default cbt-op-staff-only" data-action="remove"
						data-player="${esc(row.player_key)}"
						data-player-name="${esc(row.customer_name)}"
						title="${__("Leave session")}">✕</button>
				</div>
			`);
		});

		const courts = (state.courts || []).length || 1;
		const rounds = Math.ceil(waiting.length / (OP_PLAYERS_PER_COURT * courts));
		const stats = [
			__("Waiting: {0}", [waiting.length]),
			__("Games: {0}", [
				(state.queue || []).reduce((sum, r) => sum + cint(r.games_played), 0),
			]),
		];
		if (state.rotation_mode === "Timed") {
			stats.push(__("Est. wait: {0} min", [rounds * cint(state.rotation_minutes)]));
		}
		$panel.find(".cbt-op-stats").html(stats.map((s) => `<span>${s}</span>`).join(""));

		this.$side.empty().append($panel);
		this.render_unpaid_panel();
		this.setup_sortable($body);
	}

	render_unpaid_panel() {
		const unpaid = ((this.state && this.state.participants) || []).filter(
			(p) => p.payment_status === "Unpaid"
		);
		if (!unpaid.length) return;
		const esc = frappe.utils.escape_html;
		const rows = unpaid
			.map(
				(p) => `
			<div class="cbt-op-queue-row">
				<span class="cbt-op-queue-name">${esc(short_name(p.customer_name))}</span>
				<span class="cbt-op-queue-games">${format_currency(
					flt(p.fee) * (1 - flt(p.discount_percent) / 100),
					"PHP"
				)}${p.proof_ref ? " 📎" : ""}</span>
				<button class="btn btn-xs btn-success" data-action="pay"
					data-participant="${esc(p.name)}">${__("Mark Paid")}</button>
			</div>`
			)
			.join("");
		this.$side.append(`
			<div class="cbt-op-panel cbt-op-staff-only" style="margin-top:var(--margin-md)">
				<div class="cbt-op-panel-head">
					<span>${__("Awaiting Payment")}</span><span>${unpaid.length}</span>
				</div>
				${rows}
			</div>
		`);
	}

	setup_sortable($body) {
		// Sortable ships with frappe's libs bundle — no vendoring, and no drag
		// handles at all if it is ever missing (the board still works).
		if (this.tv || !window.Sortable || this.state.status !== "Open") return;
		this._sortable = new window.Sortable($body.get(0), {
			handle: ".cbt-op-drag",
			draggable: ".cbt-op-queue-row",
			animation: 150,
			onEnd: () => this.persist_queue_order(),
		});
	}

	persist_queue_order() {
		const ordered = this.$side
			.find(".cbt-op-queue-row[data-queue-name]")
			.map((_, el) => $(el).attr("data-queue-name"))
			.get();
		frappe
			.xcall("court_booking_tech.api.open_play.reorder_queue", {
				session: this.state.name,
				ordered: JSON.stringify(ordered),
			})
			.then(() => this.load({ quiet: true }))
			.catch(() => this.load({ quiet: true }));
	}

	render_bottom() {
		const state = this.state;
		const started = (state.assignments || []).length > 0;
		const buttons = [];
		if (state.status === "Open" && !started) {
			buttons.push(
				`<button class="btn btn-primary btn-sm" data-action="start">▶ ${__("Start Session")}</button>`
			);
		}
		if (state.status === "Open") {
			buttons.push(
				`<button class="btn btn-default btn-sm" data-action="add">+ ${__("Add Players")}</button>`,
				`<button class="btn btn-default btn-sm" data-action="complete">${__("Complete Session")}</button>`
			);
		}
		buttons.push(
			`<button class="btn btn-default btn-sm" data-action="floor">${
				this.floor_plan ? __("Card View") : __("Floor Plan")
			}</button>`,
			`<button class="btn btn-default btn-sm" data-action="tv">📺 ${__("TV Mode")}</button>`,
			`<button class="btn btn-default btn-sm" data-action="back">← ${__("Back")}</button>`
		);
		this.$bottom.html(buttons.join(""));

		this.page.clear_menu();
		if (["Scheduled", "Open"].indexOf(state.status) > -1) {
			this.page.add_menu_item(__("Cancel Session"), () => this.open_cancel_dialog());
		}
		this.page.add_menu_item(__("Open Session Form"), () =>
			frappe.set_route("Form", "CBT Open Play Session", state.name)
		);
	}

	set_tv(on) {
		this.tv = !!on;
		this.$root.find(".cbt-op-tv-exit").remove();
		if (this.tv) {
			// Move the board OUT of the desk's DOM and into <body>. A fixed
			// overlay nested in the page container still loses to the app
			// sidebar's stacking context — it covered the viewport on paper
			// while visibly clipping the first court. As a direct child of
			// body it sits in the root stacking context and nothing can paint
			// over it, with no dependency on frappe's chrome class names.
			this.$root.appendTo(document.body);
			$(`<button class="btn btn-default btn-sm cbt-op-tv-exit" data-action="exit-tv">
				${__("Exit TV Mode")}</button>`).appendTo(this.$root);
		} else {
			this.$root.appendTo(this.page.main);
		}
		this.render();
	}

	// ------------------------------------------------------------------
	// Timers
	// ------------------------------------------------------------------

	tick() {
		// The desk is a single page app: TV mode lives on <body>, so routing
		// away would otherwise leave a fullscreen overlay pinned over whatever
		// the user opened next. The page container going invisible is the
		// signal that we left.
		if (this.tv && !$(this.wrapper).is(":visible")) {
			this.set_tv(false);
			return;
		}
		this.tick_countdowns();
		this.maybe_auto_rotate();
	}

	tick_countdowns() {
		const now = this.server_now_ms();
		this.$root.find(".cbt-op-count").each((_, el) => {
			const $el = $(el);
			const deadline = frappe.datetime.str_to_obj($el.attr("data-deadline")).getTime();
			const remaining = deadline - now;
			$el.toggleClass("cbt-op-count-late", remaining < 60 * 1000);
			$el.text(remaining <= 0 ? __("TIME!") : format_clock(remaining));
		});
	}

	maybe_auto_rotate() {
		const state = this.state;
		if (!state || state.status !== "Open") return;
		if (!cint(state.auto_rotate) || state.rotation_mode !== "Timed") return;
		if (this._rotating || document.querySelector(".modal.show")) return;
		const now = this.server_now_ms();
		const due = (state.assignments || []).some(
			(a) => a.ends_at && frappe.datetime.str_to_obj(a.ends_at).getTime() <= now
		);
		if (!due) return;
		// The SERVER decides what rotates — the board only says "time is up".
		this._rotating = true;
		frappe
			.xcall("court_booking_tech.api.open_play.auto_rotate_tick", {
				session: state.name,
			})
			.then(() => this.load({ quiet: true }))
			.catch(() => {})
			.then(() => {
				this._rotating = false;
			});
	}

	// ------------------------------------------------------------------
	// Actions
	// ------------------------------------------------------------------

	_act(method, args, message, indicator) {
		return frappe
			.xcall(`court_booking_tech.api.open_play.${method}`, args)
			.then((r) => {
				if (message) frappe.show_alert({ message: message, indicator: indicator || "green" });
				return this.load().then(() => r);
			});
	}

	start_session() {
		return this._act(
			"start_session",
			{ session: this.state.name },
			__("Session started"),
			"green"
		);
	}

	game_done(court) {
		return this._act("game_done", { session: this.state.name, court: court });
	}

	return_to_queue(court, player) {
		return this._act("return_to_queue", {
			session: this.state.name,
			court: court,
			player: player,
		});
	}

	remove_player(player, label) {
		// The confirm names the PERSON. `player` is a key, and a walk-in's key
		// is a row hash — asking staff to confirm "Remove 4f2a9c1e8b?" is how a
		// wrong ✕ gets clicked.
		frappe.confirm(
			__("Remove {0} from this session? They keep their place only if you add them again.", [
				frappe.utils.escape_html(label || player),
			]),
			() =>
				this._act(
					"remove_from_session",
					{ session: this.state.name, player: player },
					__("Player removed"),
					"orange"
				)
		);
	}

	confirm_payment(participant) {
		// Backlog B29: the one moment staff can say WHERE the transfer landed
		// (the ruling: staff correct the channel when they confirm). The Select
		// is pre-set to the channel on record; the participant is a Fund
		// Transfer row by construction (only Unpaid rows reach this panel).
		const row = ((this.state && this.state.participants) || []).find(
			(p) => p.name === participant
		);
		const dialog = new frappe.ui.Dialog({
			title: __("Mark paid — {0}", [
				frappe.utils.escape_html((row && row.customer_name) || participant),
			]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "note",
					options: `<div class="text-muted small">${__(
						"Their billing document flips to Paid & Verified."
					)}</div>`,
				},
				{
					fieldtype: "Select",
					fieldname: "payment_channel",
					label: __("Received via"),
					options: [],
					description: __(
						"The channel the money actually arrived through — change it if the receipt says otherwise."
					),
				},
			],
			primary_action_label: __("Mark paid"),
			primary_action: (values) => {
				dialog.hide();
				this._act(
					"confirm_participant_payment",
					{
						session: this.state.name,
						participant: participant,
						payment_channel: values.payment_channel || null,
					},
					__("Payment confirmed"),
					"green"
				);
			},
		});
		window.cbt_sync_channel_select(
			dialog,
			this.company,
			(row && row.payment_method) || "Fund Transfer",
			row && row.payment_channel
		);
		dialog.show();
	}

	complete_session() {
		frappe.confirm(
			__("Complete this session? All games end and the queue closes."),
			() =>
				this._act(
					"complete_session",
					{ session: this.state.name },
					__("Session completed"),
					"blue"
				)
		);
	}

	open_cancel_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Cancel {0}", [this.state.name]),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__(
						"The dedicated courts become bookable again. Billing documents are never deleted — cancelling them keeps the record with its number consumed."
					)}</p>`,
				},
				{
					fieldtype: "Check",
					fieldname: "cancel_billing",
					label: __("Also cancel the participants' billing documents"),
					description: __(
						"Leave this off if payments have already been reconciled. Cancelling PAID players' documents is a refund — a Company Admin's action, with a reason."
					),
				},
				{
					// Section-26: the reason lands on every PAID document the
					// cancel reverses; the server refuses a non-admin seat.
					fieldtype: "Small Text",
					fieldname: "reason",
					label: __("Reason for the refund"),
					depends_on: "cancel_billing",
					mandatory_depends_on: "cancel_billing",
				},
			],
			primary_action_label: __("Cancel Session"),
			primary_action: (values) => {
				dialog.hide();
				this._act(
					"cancel_session",
					{
						session: this.state.name,
						cancel_billing: values.cancel_billing ? 1 : 0,
						reason: values.cancel_billing ? values.reason : undefined,
					},
					__("Session cancelled"),
					"red"
				).then(() => this.back_to_list());
			},
		});
		dialog.show();
	}

	open_add_players_dialog() {
		const state = this.state;
		const free_session = flt(state.entry_fee) <= 0;
		let added = 0;
		// Players arrive at the desk one at a time, so the dialog STAYS OPEN
		// and clears itself after each add — the staff member keeps typing
		// names as the queue forms instead of reopening a grid per person.
		// (The API takes a list; this just sends one-element batches.)
		const dialog = new frappe.ui.Dialog({
			title: __("Add Players"),
			fields: [
				{
					// Section-20 (Backlog B2): the same stranger-with-cash the
					// Court Board learned to serve in section-13. Joining a
					// session no longer requires an account.
					fieldtype: "Check",
					fieldname: "walk_in",
					label: __("Walk-in (no account)"),
					default: 0,
					description: __(
						"The name below is what the queue, the board and the billing statement show."
					),
					change: () => sync_hint(),
				},
				{
					fieldtype: "Link",
					fieldname: "customer",
					label: __("Customer"),
					options: "User",
					// NO static `reqd`. field_group.get_values() checks `reqd`
					// with no hidden/depends_on exemption, so a hard reqd here
					// would make the walk-in path unsubmittable — get_values()
					// returns null and primary_action never runs. layout.js
					// maps mandatory_depends_on onto reqd dynamically instead;
					// this is the S13 quick-book shape verbatim.
					depends_on: "eval:!doc.walk_in",
					mandatory_depends_on: "eval:!doc.walk_in",
					get_query: () => ({
						query: "court_booking_tech.api.board.customer_query",
					}),
					change: () => sync_hint(),
				},
				{
					fieldtype: "Data",
					fieldname: "customer_name",
					label: __("Walk-in Name"),
					depends_on: "eval:doc.walk_in",
					mandatory_depends_on: "eval:doc.walk_in",
					description: __("Printed on their billing statement."),
				},
				{
					fieldtype: "Data",
					fieldname: "customer_phone",
					label: __("Phone (optional)"),
					depends_on: "eval:doc.walk_in",
					description: __("So the desk can reach them about the session."),
				},
				{
					fieldtype: "Select",
					fieldname: "payment_method",
					label: __("Payment Method"),
					options: ["Cash", "Fund Transfer", "Free"].join("\n"),
					default: free_session ? "Free" : "Cash",
					reqd: 1,
					description: __(
						"Cash and Free are paid on the spot; Fund Transfer stays unpaid until you confirm it."
					),
					change: () => {
						sync_hint();
						sync_channels();
					},
				},
				{
					// Backlog B29: the same "Paid via" picker as the court board —
					// the company's enabled channels of the method's kind.
					fieldtype: "Select",
					fieldname: "payment_channel",
					label: __("Paid via"),
					options: [],
					depends_on: "eval:doc.payment_method!=='Free'",
				},
				{ fieldtype: "HTML", fieldname: "member_hint" },
				{ fieldtype: "HTML", fieldname: "tally" },
			],
			primary_action_label: __("Add Player"),
			primary_action: (values) => {
				const walk_in = cint(values.walk_in);
				if (walk_in && !(values.customer_name || "").trim()) {
					frappe.msgprint(__("Type the walk-in's name first."));
					return;
				}
				if (!walk_in && !values.customer) {
					frappe.msgprint(__("Pick a customer first."));
					return;
				}
				frappe
					.xcall("court_booking_tech.api.open_play.add_players", {
						session: state.name,
						players: JSON.stringify([
							{
								// EXACTLY ONE identity reaches the server — the
								// API refuses both, so the toggle decides and
								// the stale other half is never sent (S13).
								customer: walk_in ? null : values.customer,
								customer_name: walk_in ? values.customer_name : null,
								customer_phone: walk_in ? values.customer_phone : null,
								payment_method: values.payment_method,
								payment_channel:
									values.payment_method === "Free"
										? null
										: values.payment_channel || null,
							},
						]),
					})
					.then(() => {
						added += 1;
						// Clear the identity, KEEP the toggle: a desk that is
						// taking walk-ins is usually taking several in a row.
						dialog.set_value("customer", "");
						dialog.set_value("customer_name", "");
						dialog.set_value("customer_phone", "");
						dialog.fields_dict.tally.$wrapper.html(
							`<div class="text-muted">${__("Added this round: {0}", [
								added,
							])}</div>`
						);
						this.load({ quiet: true });
					});
			},
			secondary_action_label: __("Done"),
			secondary_action: () => {
				dialog.hide();
				this.load();
			},
		});

		// What this player will actually be charged, said out loud before staff
		// commit to it (WYSIWYG money, S11). It is worth saying here because
		// open play prices membership from the SESSION — `member_discount_percent`
		// on the session doc, NOT the member's own booking discount (S11
		// doctrine, enforced in add_players) — which is genuinely surprising and
		// was, until now, invisible to the person taking the payment.
		//
		// The node is also test infrastructure (S13 as-built 13): the E2E gates
		// its submit on it, so it must NEVER be left empty.
		const render_hint = (body) => {
			dialog.fields_dict.member_hint.$wrapper.html(
				`<div class="text-muted small" data-testid="op-member-hint">${body}</div>`
			);
		};
		const money = (fee, discount) =>
			format_currency(flt(fee) * (1 - flt(discount) / 100), "PHP");

		const sync_hint = () => {
			const walk_in = cint(dialog.get_value("walk_in"));
			const customer = dialog.get_value("customer");
			const method = dialog.get_value("payment_method");
			const fee = method === "Free" ? 0 : flt(state.entry_fee);
			const session_rate = flt(state.member_discount_percent);
			dialog._hint_settled = false;

			if (method === "Free") {
				render_hint(__("Free entry — nothing to collect."));
				dialog._hint_settled = true;
				return;
			}
			if (walk_in) {
				// A walk-in has no account, so no membership can apply — SAY
				// so rather than leaving the money area blank.
				render_hint(
					__("Walk-in — no membership. Pays {0}.", [money(fee, 0)])
				);
				dialog._hint_settled = true;
				return;
			}
			if (!customer) {
				render_hint(
					__("Pick a customer, or tick Walk-in. Entry is {0}.", [
						money(fee, 0),
					])
				);
				dialog._hint_settled = true;
				return;
			}
			const seq = (dialog._hint_seq = (dialog._hint_seq || 0) + 1);
			frappe
				.xcall(
					"court_booking_tech.membership.get_member_discount_for",
					{ company: this.company, customer: customer },
					"GET" // xcall defaults to POST (S10 lesson 17a)
				)
				.then((res) => {
					if (seq !== dialog._hint_seq) return;
					if (res.has_membership) {
						render_hint(
							`<span class="indicator green">${__("{0} member", [
								frappe.utils.escape_html(res.tier || ""),
							])}</span> ${__(
								"— session member rate {0}% applied. Pays {1}.",
								[session_rate, money(fee, session_rate)]
							)}`
						);
					} else {
						render_hint(
							__("No membership at this company. Pays {0}.", [
								money(fee, 0),
							])
						);
					}
					dialog._hint_settled = true;
				})
				.catch((err) => {
					if (seq !== dialog._hint_seq) return;
					console.error("CBT open play membership lookup failed", err);
					render_hint(
						`<span class="text-danger">${__(
							"Membership lookup failed — confirm the fee before adding."
						)}</span>`
					);
					dialog._hint_settled = true;
				});
		};

		// Backlog B29: the channel picker follows the method (the desk-wide
		// helper from public/js/cbt_payment_channels.js — one implementation,
		// three pages).
		const sync_channels = () =>
			window.cbt_sync_channel_select(
				dialog,
				this.company,
				dialog.get_value("payment_method")
			);

		sync_hint();
		sync_channels();
		dialog.show();
	}

	open_backfill_dialog(court) {
		const waiting = this.waiting_rows();
		if (!waiting.length) {
			frappe.msgprint(__("Nobody is waiting in the queue."));
			return;
		}
		// {value, label} options: the VALUE is the player key the server needs,
		// the LABEL is the person staff are looking at. Before section-20 this
		// Select rendered raw email addresses as its labels, and a walk-in has
		// no email to render at all.
		const options = waiting.map((r) => ({
			value: r.player_key,
			label: r.customer ? r.customer_name : `${r.customer_name} (${__("walk-in")})`,
		}));
		const dialog = new frappe.ui.Dialog({
			title: __("Fill the open slot"),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "player",
					label: __("Player"),
					reqd: 1,
					options: options,
					default: options[0].value,
				},
			],
			primary_action_label: __("Add to court"),
			primary_action: (values) => {
				dialog.hide();
				this.backfill(court, values.player);
			},
		});
		dialog.show();
	}

	backfill(court, player) {
		return this._act("backfill", {
			session: this.state.name,
			court: court,
			player: player,
		});
	}
}

const OP_STATUS_COLOR = {
	Scheduled: "orange",
	Open: "green",
	Completed: "blue",
	Cancelled: "red",
};

function op_walkin_chip() {
	return `<span class="cbt-op-walkin" data-testid="op-walkin-chip"
		title="${__("Walk-in — no account, no membership")}">${__("walk-in")}</span>`;
}

function short_name(full_name) {
	// "Juan Bautista" -> "Juan B." — the queue must stay readable from across
	// the room in TV mode.
	const parts = String(full_name || "").trim().split(/\s+/);
	if (parts.length < 2) return parts[0] || "";
	return `${parts[0]} ${parts[parts.length - 1][0]}.`;
}

function format_clock(ms) {
	const total = Math.floor(ms / 1000);
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m}:${String(s).padStart(2, "0")}`;
}
