// Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
// For license information, please see license.txt
//
// Court Board (section-7) — the staff daily screen. One glance = the whole
// branch: floor-plan grid with color-coded slots, one-click quick-book /
// verify / extend / block, and the Pending Payments panel with live
// countdowns. Countdowns are computed from SERVER timestamps via a clock
// offset captured on every payload (solo-app lesson 3 — client clocks drift);
// auto-refresh (30s) is suspended while any dialog is open so modal state is
// never yanked out from under the user (or Playwright).

frappe.pages["cbt-court-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Court Board"),
		single_column: true,
	});
	wrapper.court_board = new CourtBoard(page, wrapper);
};

frappe.pages["cbt-court-board"].on_page_show = function (wrapper) {
	wrapper.court_board && wrapper.court_board.load();
};

// SECTION-23 (Backlog B14) NOTE FOR ANYONE EDITING THIS BLOCK — three traps,
// all of them silent:
//   1. This is an UNTAGGED template literal, so a CSS backslash escape is eaten
//      by JS first: content: "\2713" reaches the stylesheet as the text 2713.
//      Use the real character, or double the backslash.
//   2. One stray backtick or ${ ends the string and DELETES THE ENTIRE BOARD
//      STYLESHEET. The suite selects on classes, so unstyled-but-present DOM
//      still passes green — load the board by eye after every edit here.
//   3. The :available/:running hint rules further down are hand-tuned cascade.
//      Do not reorder or "tidy" them; the comment there explains why.
//
// The five SLOT-STATUS chips deliberately keep frappe's own --bg-*/--text-on-*
// PAIRS rather than moving onto the --cbt-* status tokens the portal uses.
// Those pairs are contrast-guaranteed in BOTH themes by the framework; swapping
// them for hand-rolled colours would owe a dark counterpart per chip and buy
// nothing a staff member can see. The court green arrives through structure
// instead — card heads, the panel, the legend, hover, focus and the empty state.
const CBT_BOARD_STYLES = `
.cbt-board { display: flex; gap: var(--margin-lg); align-items: flex-start; }
.cbt-board-main { flex: 1; min-width: 0; }

.cbt-board-topbar {
	display: flex; align-items: center; gap: var(--margin-md);
	flex-wrap: wrap; margin-bottom: var(--margin-md);
}
.cbt-date-nav { display: flex; align-items: center; gap: 4px; }
.cbt-date-nav .btn { padding: 2px 10px; }
.cbt-board-date-label {
	font-weight: 650; margin: 0 4px; white-space: nowrap;
	letter-spacing: -0.01em;
}

/* Height is load-bearing: .cbt-panel-body's max-height is calc(100vh - 220px),
   a number measured against THIS topbar. Restyle it, do not grow it. */
.cbt-legend {
	display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
	font-size: var(--text-sm); color: var(--text-muted); margin-left: auto;
}
.cbt-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.cbt-legend-dot {
	width: 11px; height: 11px; border-radius: 3px; display: inline-block;
	flex: none;
}

.cbt-board-updated {
	font-size: var(--text-xs); color: var(--text-muted); white-space: nowrap;
	font-variant-numeric: tabular-nums;
}

/* B33 removed the per-court CARD stack (.cbt-court-card / .cbt-floor-grid /
   .cbt-court-flow / .cbt-slot-list) — the matrix rules live at the end of this
   sheet. .cbt-slot itself is UNCHANGED: it is the cell contract ten E2E files
   select on. */
.cbt-slot {
	border-radius: var(--cbt-radius-sm); padding: 5px 9px; font-size: var(--text-sm);
	display: flex; align-items: center; gap: 8px; min-height: 30px;
	border: 1px solid var(--border-color); background: var(--card-bg);
	transition: border-color 0.15s ease, background 0.15s ease;
}
.cbt-slot-time { font-variant-numeric: tabular-nums; font-weight: 500; white-space: nowrap; }
.cbt-slot-body {
	display: flex; align-items: center; gap: 6px; min-width: 0; flex: 1;
	overflow: hidden;
}
.cbt-slot-who { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.cbt-slot[data-status="available"] { cursor: pointer; }
.cbt-slot[data-status="available"]:hover {
	border-color: var(--cbt-primary); background: var(--cbt-primary-soft);
}
.cbt-slot[data-status="available"] .cbt-slot-hint {
	margin-left: auto; color: var(--text-muted); font-size: var(--text-xs);
	opacity: 0; transition: opacity 0.15s ease;
}
.cbt-slot[data-status="available"]:hover .cbt-slot-hint { opacity: 1; }

.cbt-slot[data-status="booked"] { cursor: pointer; border-color: transparent; }
.cbt-slot.cbt-slot-reserved { background: var(--bg-yellow); color: var(--text-on-yellow); }
.cbt-slot.cbt-slot-confirmed { background: var(--bg-green); color: var(--text-on-green); }
.cbt-slot.cbt-slot-extended { background: var(--bg-blue); color: var(--text-on-blue); }
.cbt-slot[data-status="blocked"] {
	background: var(--bg-red); color: var(--text-on-red);
	border-color: transparent; cursor: pointer;
}
.cbt-slot[data-status="past"] { opacity: 0.45; background: var(--bg-gray); color: var(--text-on-gray); border-color: transparent; }

/* Section-18 (Backlog B7): the hour that is RUNNING RIGHT NOW is bookable —
   staff back-record a walk-in who turned up mid-session, which create_booking
   has always accepted (S4 as-built 6). It is deliberately NOT a plain green
   chip: booking it means the session has already started, so it gets its own
   state. data-status stays "available" — the click handler and the E2E
   selectors key on it, and the slot really is available.
   NOTE no backticks anywhere in this block: it lives inside the
   CBT_BOARD_STYLES template literal, so one would end the string.
   The 4-class selector on the hint is not style: the base rule
   .cbt-slot[data-status="available"] .cbt-slot-hint is also (0,3,0), so a
   3-class override would be decided by source order. This one wins outright,
   which is what makes the chip SAY "in progress" at rest instead of only on
   hover — an invisible state is not a state. */
.cbt-slot.cbt-slot-running { border-style: dashed; border-color: var(--cbt-primary); }
.cbt-slot[data-status="available"].cbt-slot-running .cbt-slot-hint { opacity: 1; }

.cbt-count {
	margin-left: auto; font-size: var(--text-xs); font-weight: 600;
	font-variant-numeric: tabular-nums; padding: 1px 7px; border-radius: 10px;
	background: rgba(0, 0, 0, 0.12); white-space: nowrap;
}
.cbt-count-late { background: var(--bg-red); color: var(--text-on-red); }
/* Section-16: the release countdown is a WARNING, not a deadline the desk owes
   anybody — amber until it goes late, when the shared .cbt-count-late wins. */
.cbt-count-release { background: var(--bg-orange); color: var(--text-on-orange); }
.cbt-checkin { margin-left: auto; font-weight: 600; }

.cbt-noshow-chip {
	border: 1px solid var(--cbt-accent); background: var(--cbt-accent-soft);
	color: var(--cbt-accent-ink);
	border-radius: var(--cbt-radius-pill); padding: 2px 12px; font-size: var(--text-xs);
	font-weight: 650; white-space: nowrap; cursor: pointer;
}
.cbt-noshow-chip:hover { background: var(--cbt-accent); color: var(--cbt-accent-contrast); }

/* The phone's jump to Pending Payments. Painted ONLY where the panel is out of
   sight (below the 1100px column flip, and only on a hand-sized screen) — on a
   wide board the panel is already beside the grid and this would be clutter. */
.cbt-jump-chip {
	display: none;
	border: 1px solid var(--cbt-primary-line); background: var(--cbt-primary-soft);
	color: var(--cbt-primary-600);
	border-radius: var(--cbt-radius-pill); padding: 2px 12px; font-size: var(--text-xs);
	font-weight: 650; white-space: nowrap; cursor: pointer;
}
.cbt-noshow-row {
	display: flex; align-items: center; gap: 10px; padding: 8px 0;
	border-bottom: 1px solid var(--border-color);
}
.cbt-noshow-row:last-child { border-bottom: none; }
.cbt-noshow-row .cbt-noshow-who { flex: 1; min-width: 0; }
.cbt-noshow-row .cbt-noshow-when {
	font-variant-numeric: tabular-nums; color: var(--text-muted);
	font-size: var(--text-sm); white-space: nowrap;
}
.cbt-proof-badge {
	font-size: var(--text-xs); padding: 1px 6px; border-radius: 10px;
	background: rgba(0, 0, 0, 0.12); white-space: nowrap;
}

/* A designed nothing. The old rule drew a thin grey dashed box that read as a
   rendering failure; this one is clearly a state the board MEANT to show. */
.cbt-board-empty {
	border: 2px dashed var(--cbt-primary-line); border-radius: var(--cbt-radius);
	background: var(--cbt-primary-soft);
	padding: 56px 24px; text-align: center; color: var(--text-muted);
	line-height: 1.5;
}
.cbt-board-empty .cbt-empty-icon { font-size: 34px; margin-bottom: 10px; }
.cbt-board-empty .cbt-empty-title {
	font-weight: 650; font-size: var(--text-md); color: var(--cbt-primary-600);
	margin-bottom: 2px;
}

.cbt-pending-panel {
	width: 350px; flex-shrink: 0; background: var(--card-bg);
	border: 1px solid var(--border-color); border-radius: var(--cbt-radius);
	box-shadow: var(--cbt-shadow); overflow: hidden;
}
.cbt-panel-head {
	padding: 10px 14px; border-bottom: 1px solid var(--cbt-primary-line);
	background: var(--cbt-primary-soft);
	display: flex; align-items: center; gap: 8px; font-weight: 650;
}
.cbt-panel-count {
	background: var(--bg-orange); color: var(--text-on-orange);
	border-radius: 10px; padding: 0 8px; font-size: var(--text-xs); font-weight: 600;
}
/* The magic 220px is measured against the board topbar above — if that ever
   grows, this is the number that quietly stops matching. */
.cbt-panel-body { max-height: calc(100vh - 220px); overflow-y: auto; }
.cbt-pending-item {
	padding: 10px 14px; border-bottom: 1px solid var(--border-color);
	transition: background 0.15s ease;
}
.cbt-pending-item:hover { background: var(--cbt-primary-soft); }
.cbt-pending-item:last-child { border-bottom: none; }
.cbt-pending-top { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.cbt-pending-ref { font-size: var(--text-xs); color: var(--text-muted); font-variant-numeric: tabular-nums; }
.cbt-pending-cust { font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cbt-pending-where { font-size: var(--text-sm); color: var(--text-muted); }
.cbt-pending-row {
	display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap;
}
.cbt-pending-amount {
	font-weight: 700; font-variant-numeric: tabular-nums;
	color: var(--cbt-primary-600);
}
.cbt-pending-thumb {
	width: 34px; height: 34px; border-radius: var(--border-radius);
	object-fit: cover; border: 1px solid var(--border-color); cursor: pointer;
}
.cbt-pending-thumb:hover { border-color: var(--cbt-primary); }
.cbt-pending-actions { display: flex; gap: 6px; margin-top: 8px; }
.cbt-pending-actions .btn { padding: 2px 10px; font-size: var(--text-xs); }
.cbt-panel-empty { padding: 28px 14px; text-align: center; color: var(--text-muted); }

.cbt-detail-grid {
	display: grid; grid-template-columns: auto 1fr; gap: 3px 14px;
	font-size: var(--text-sm); margin-bottom: 10px;
}
.cbt-detail-grid dt { color: var(--text-muted); }
.cbt-detail-grid dd { margin: 0; font-weight: 500; }
.cbt-proof-card {
	display: flex; gap: 10px; align-items: flex-start; padding: 8px;
	border: 1px solid var(--border-color); border-radius: var(--cbt-radius-sm);
	margin-bottom: 6px;
}
.cbt-proof-thumb {
	width: 72px; height: 72px; object-fit: cover; border-radius: var(--cbt-radius-sm);
	border: 1px solid var(--border-color); cursor: pointer; flex-shrink: 0;
}
.cbt-proof-thumb:hover { border-color: var(--cbt-primary); }
.cbt-proof-pdf {
	width: 72px; height: 72px; display: flex; align-items: center; justify-content: center;
	border: 1px solid var(--border-color); border-radius: var(--border-radius);
	font-weight: 700; color: var(--text-muted); flex-shrink: 0; text-decoration: none;
}
.cbt-proof-meta { font-size: var(--text-sm); min-width: 0; }
.cbt-proof-meta .ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Backlog B33 — the time x court matrix. Same cell contract as the portal's
   grid (www/cbt-book.html): the horizontal scroll belongs to the WRAPPER, never
   the page (B31's allowlist), and the time column is sticky so a row stays
   readable at the right-hand end of a wide branch. */
.cbt-matrix-wrap {
	overflow-x: auto; border: 1px solid var(--border-color);
	border-radius: var(--cbt-radius-sm); background: var(--card-bg);
	max-width: 100%; min-width: 0;
}
.cbt-matrix { border-collapse: separate; border-spacing: 0; width: 100%; }
.cbt-matrix th, .cbt-matrix td {
	border-bottom: 1px solid var(--border-color); padding: 4px; vertical-align: top;
}
/* The tinted head the per-court CARD used to carry: it is what makes a wall of
   cells read as courts, and it is the board's proof that app_include_css
   arrived (test_19 asserts this exact colour). */
.cbt-matrix thead th {
	position: sticky; top: 0; z-index: 3; background: var(--cbt-primary-soft);
	border-bottom: 1px solid var(--cbt-primary-line);
	text-align: left; padding: 8px;
}
.cbt-timecell {
	position: sticky; left: 0; z-index: 2; white-space: nowrap;
	background: var(--cbt-surface-2, var(--fg-color)); font-weight: 600;
	font-size: var(--text-sm); color: var(--text-muted); padding: 8px;
}
.cbt-matrix-corner { z-index: 4; }
.cbt-colhead-name { margin: 0; font-weight: 650; }
.cbt-colhead-meta { margin: 0; font-size: var(--text-xs); color: var(--text-muted); }
.cbt-matrix td.cbt-cell { min-width: 132px; }
.cbt-matrix .cbt-slot { margin: 0; width: 100%; }
/* The row header carries the time, so the per-cell repeat is noise on screen —
   kept in the DOM because the suite reads it. */
.cbt-matrix .cbt-slot-time { display: none; }
/* ⚠ The base rule hides the "+ Book" hint until hover, which was legible when a
   card row showed the TIME beside it. A matrix cell has nothing else, so an
   available slot would render BLANK — caught by looking at a screenshot, not by
   an assertion. 4 classes beats the (0,3,0) base rule outright. */
.cbt-matrix .cbt-slot[data-status="available"] .cbt-slot-hint { opacity: 1; }
.cbt-matrix .cbt-slot {
	display: grid; grid-template-columns: 1fr auto; align-items: center;
	gap: 2px 6px; padding: 6px 8px; min-height: 42px;
}
.cbt-matrix .cbt-slot-rate {
	font-variant-numeric: tabular-nums; font-weight: 600; font-size: var(--text-sm);
	grid-column: 1; justify-self: start;
}
.cbt-matrix .cbt-slot-body { grid-column: 1 / -1; font-size: var(--text-xs); }
.cbt-matrix .cbt-slot-hint { margin-left: 0 !important; }
.cbt-col--focus { background: var(--cbt-primary-subtle, var(--bg-light-gray)); }

/* The Court Layout ships COLLAPSED (user ruling 2026-09-05): the matrix is the
   working grid, the sketch is the "which one is by the door?" answer. */
.cbt-layout-details { margin-bottom: var(--margin-md); }
.cbt-layout-details > summary { cursor: pointer; font-weight: 600; padding: 6px 0; }
.cbt-sketch-grid { display: grid; gap: 6px; margin: 8px 0; }
.cbt-sketch-court {
	border: 1px solid var(--border-color); border-radius: var(--cbt-radius-sm);
	padding: 10px 6px; text-align: center; font-size: var(--text-sm);
	cursor: pointer; background: var(--fg-color);
}
.cbt-sketch-court:hover { border-color: var(--cbt-primary); }
.cbt-sketch-court--on { border-color: var(--cbt-primary); font-weight: 650; }
.cbt-sketch-gap { border: 1px dashed var(--border-color); border-radius: var(--cbt-radius-sm); opacity: .4; }

@media (max-width: 1100px) {
	/* ⚠ align-items MUST become stretch. The row layout uses flex-start; keep it
	   in the column layout and .cbt-board-main shrink-wraps to the MATRIX's
	   min-content width, so the whole board scrolls sideways on a phone instead
	   of the matrix scrolling inside its own frame (B31's allowlist, caught by
	   test_mobile_portal). */
	.cbt-board { flex-direction: column; align-items: stretch; }
	.cbt-board-main { width: 100%; min-width: 0; }
	.cbt-pending-panel { width: 100%; }
	.cbt-panel-body { max-height: none; }
}

/* ---- the cart (Backlog B46) ---------------------------------------------
   A picked cell is a PRESSED toggle, and it has to read as one at a glance
   across a busy grid — the tint alone is the same tint hover uses, so the
   inset ring is what makes "chosen" different from "under the mouse". */
.cbt-slot--picked {
	border-color: var(--cbt-primary); background: var(--cbt-primary-soft);
	box-shadow: inset 0 0 0 1px var(--cbt-primary);
}
.cbt-slot--picked .cbt-slot-hint { font-weight: 650; color: var(--cbt-primary-600); }

/* Sticky at the FOOT of the board: the operator selects downward through the
   day, and a bar that scrolled away with the hours would make the last tap a
   hunt. It carries no money — the price is the dialog's, in its footer, where
   B42 put it, and a second figure here could disagree with it the moment a
   member or a discount is named. */
.cbt-cart-bar {
	position: sticky; bottom: 0; z-index: 5;
	display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
	margin-top: var(--margin-md); padding: 10px 14px;
	border: 1px solid var(--cbt-primary-line); border-radius: var(--cbt-radius);
	background: var(--cbt-primary-soft); box-shadow: var(--cbt-shadow);
}
.cbt-cart-count { font-weight: 650; }
.cbt-cart-where { color: var(--text-muted); font-size: var(--text-sm); }
.cbt-cart-bar [data-action="cart-clear"] { margin-left: auto; }

.cbt-cart-lines { margin-bottom: 10px; }
.cbt-cart-line {
	display: flex; align-items: flex-start; gap: 10px; padding: 8px 0;
	border-bottom: 1px solid var(--border-color);
}
.cbt-cart-line:last-child { border-bottom: none; }
.cbt-cart-line-main { flex: 1; min-width: 0; }
.cbt-cart-line-where { font-weight: 600; }
.cbt-cart-line-when { font-size: var(--text-sm); color: var(--cbt-ink-muted); }
.cbt-cart-seg { font-size: var(--text-xs); color: var(--text-muted); }
.cbt-cart-line-amount {
	font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap;
}

/* ---- the phone (user report 2026-09-05: "not mobile friendly") -----------
   MEASURED on iPhone 13 (390x664, WebKit) and Pixel 7 (412x839, Blink) before
   a line of this was written. The board did NOT scroll sideways — B33's fix
   holds — and the Quick Book total stayed on screen, so B42's holds too. What
   was actually wrong:

     * 21 of 48 slot cells were 124x42, under Apple's 44 pt floor;
     * the date nav's arrows were 22x29 — a 22-PIXEL prev-day button;
     * ALL NINE Pending Payments buttons (Accept / Reject / Open) were 52x18.
       That is the money action on this screen, eighteen pixels tall;
     * 75 elements rendered at 12 px, under the 13 px floor this app's own
       phone lane defines;
     * the topbar ate 126 px of a 664 px screen — a two-line colour legend and
       a seconds-precise "Updated" stamp — and with frappe's two page-form
       fields above it the first bookable hour started at 423 px, leaving room
       for FOUR hours on the busiest screen in the building;
     * the Pending Payments panel began at y=1370, two screens below the fold,
       with no way to reach it but scrolling past the whole grid.

   700px, not 1100: the column flip at 1100 is a TABLET rule, and a tablet has
   the room these rules give back. */
@media (max-width: 700px) {
	/* Thumbs. 44px is Apple's HIG floor; Material's 48 is the stricter
	   follow-up and is deliberately not enforced. */
	.cbt-matrix .cbt-slot { min-height: 44px; }
	.cbt-date-nav .btn { min-height: 44px; min-width: 44px; padding: 2px 12px; }
	.cbt-pending-actions .btn { min-height: 44px; padding: 4px 14px; flex: 1; }
	.cbt-pending-actions { gap: 8px; }
	.cbt-layout-details > summary { min-height: 44px; display: flex; align-items: center; }
	.cbt-noshow-chip { min-height: 44px; }
	/* The base rule hides it; THIS is the only place it is ever painted. The
	   JS sets an inline display:none when the panel is empty, and an inline
	   style beats a media query, so an empty panel stays chip-less here too. */
	.cbt-jump-chip {
		display: inline-flex; align-items: center; min-height: 44px;
		font-size: 13px;
	}
	.cbt-sketch-court { min-height: 44px; display: flex; align-items: center; justify-content: center; }
	.cbt-cart-bar .btn { min-height: 44px; padding: 4px 16px; }
	/* Quick Book's own buttons. Measured 51x29 on both engines — the LAST tap
	   in the whole flow, and the smallest. */
	.cbt-qb .modal-footer .btn, .cbt-qb .modal-footer button {
		min-height: 44px; padding: 6px 18px;
	}
	.cbt-cart-line-drop { min-height: 44px; min-width: 44px; }

	/* Text floor. Every one of these renders at 12px from a --text-xs/sm token
	   that is right on a desktop and too small in a hand.
	   ⚠ THE HINT NEEDS FOUR CLASSES, not one. Its base rule — the one on
	   .cbt-slot[data-status="available"] .cbt-slot-hint — is (0,3,0), so a bare
	   .cbt-slot-hint here loses the cascade, and the first pass measured 27 of
	   them still at 12px. Same trap the B33 comment above records for opacity. */
	.cbt-matrix .cbt-slot-body,
	.cbt-matrix .cbt-slot[data-status="available"] .cbt-slot-hint,
	.cbt-slot-who,
	.cbt-colhead-meta,
	.cbt-pending-ref, .cbt-pending-where, .cbt-proof-badge,
	.cbt-cart-seg { font-size: 13px; }

	/* THIS page's own actions (Refresh, Block Slots) — 28px targets in the
	   page head. The sidebar toggle beside them is frappe's shell, not ours,
	   and the phone lane records the shell as an observation rather than
	   asserting on it. */
	.cbt-board-page .page-actions .btn { min-height: 44px; min-width: 44px; }

	/* Vertical budget. The legend becomes ONE line that scrolls inside its own
	   box (B31's allowlist: the scroll belongs to the box, never the page), and
	   the "Updated" stamp drops its seconds and joins the date row. */
	.cbt-board-topbar { gap: 8px; margin-bottom: 8px; }
	.cbt-legend {
		margin-left: 0; width: 100%; flex-wrap: nowrap;
		overflow-x: auto; -webkit-overflow-scrolling: touch;
	}
	.cbt-legend-item { flex: none; }
	.cbt-board-updated { margin-left: auto; }
	/* frappe's own Branch/Date controls stack full-width with a label above
	   each, which is ~110px of a 664px screen before the board starts. Scoped
	   to THIS page through the class the board puts on its own container —
	   never a bare .page-form, which every other app on this bench shares. */
	.cbt-board-page .page-form .frappe-control { width: 50%; }
	.cbt-board-page .page-form .control-label { font-size: 11px; margin-bottom: 0; }
}

/* ---- the Quick Book dialog (Backlog B42) --------------------------------
   Scoped to .cbt-qb, which open_cart_book puts on its own wrapper — these
   rules must never reach another dialog on the desk.
   NOTE no backticks and no dollar-brace anywhere in this block: it lives
   inside the CBT_BOARD_STYLES template literal and either would end the
   string, taking the ENTIRE board stylesheet with it, silently.

   THE MEASURED DEFECT. The body had max-height:none and overflow-y:visible,
   so the form had no height budget at all: at one Cash slot the dialog just
   fitted a 960px screen (959 of 960), and the moment it grew — walk-in adds
   two rows, Fund Transfer adds the channel row, a rate rule adds breakdown
   lines — the TOTAL went off the bottom of the screen. Staff were clicking
   Book without being able to see the price, which is the exact opposite of
   the WYSIWYG-money promise (S18/B4). Two rules fix it structurally rather
   than by hoping the form stays short: the body scrolls, and the money block
   is moved OUT of the body into the footer, where it cannot scroll away. */
.cbt-qb .modal-body {
	max-height: calc(100vh - 13rem); overflow-y: auto;
}
/* justify-content is restated because the money block below is a full-width
   flex line: without it the wrapped line resets the row and the Book button
   lands bottom-LEFT, which is where the first pass put it. */
.cbt-qb .modal-footer { flex-wrap: wrap; justify-content: flex-end; }
/* order:-1 puts the money ABOVE the button row without depending on where in
   the footer the node was inserted. */
.cbt-qb .modal-footer .frappe-control[data-fieldname="estimate"] {
	width: 100%; order: -1; margin: 0 0 10px;
}
/* Notes is the least-used control on the form and took 150px of it.
   MEASURED, not guessed: ControlSmallText writes an INLINE style="height:150px"
   on the textarea, so no stylesheet rule can win without !important — the only
   one in this file, and it is here rather than fighting an inline style from JS
   on every render. */
.cbt-qb .frappe-control[data-fieldname="notes"] textarea.form-control {
	height: 56px !important; min-height: 56px;
}
/* A section whose only visible control is Notes still reserved a column's worth
   of vertical padding, which read as a hole in the form. */
.cbt-qb .form-section { margin-bottom: 0; }
.cbt-qb .form-section .section-head {
	font-size: var(--text-sm); font-weight: 600; color: var(--cbt-ink-muted);
	margin-bottom: 6px;
}
.cbt-quote {
	background: var(--cbt-primary-soft); border: 1px solid var(--cbt-primary-line);
	border-radius: var(--cbt-radius-sm); padding: 8px 12px;
}
.cbt-quote-line {
	display: flex; justify-content: space-between; gap: 16px;
	font-size: var(--text-sm); color: var(--cbt-ink-muted);
}
.cbt-quote-line span:last-child { font-variant-numeric: tabular-nums; }
.cbt-quote-total {
	display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
	margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--cbt-primary-line);
}
.cbt-quote-label { font-weight: 600; color: var(--cbt-ink); }
.cbt-quote-amount {
	font-size: 24px; font-weight: 700; line-height: 1.1;
	color: var(--cbt-primary-600); font-variant-numeric: tabular-nums;
}
/* When credit is applied, DUE NOW is the number said out loud, so it carries
   the weight and the total steps back to a line item. */
.cbt-quote--paid .cbt-quote-total .cbt-quote-amount {
	font-size: var(--text-base); font-weight: 600; color: var(--cbt-ink-muted);
}
.cbt-quote-due { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
.cbt-quote-fail { color: var(--text-danger); font-size: var(--text-sm); }
/* CBT_STYLE_BLOCK_END — do not move. tests/test_board_assets.py reads the text
   between this file's FIRST two backticks and fails if this sentinel is not the
   last thing in it.

   THIS TRAP IS NOT HYPOTHETICAL AND node --check DOES NOT CATCH IT. On
   2026-09-05 a backtick typed inside a CSS COMMENT in this very block ended the
   literal, the file still parsed as a script, and the board loaded with no
   stylesheet at all — frappe evaluates a Page script with new Function(), which
   threw "Invalid left-hand side in assignment" and left the page blank. */
`;

const SLOT_STATUS_CLASS = {
	Reserved: "cbt-slot-reserved",
	Confirmed: "cbt-slot-confirmed",
	Extended: "cbt-slot-extended",
};

// Backlog B29: the extension dialog's "Paid via" Select is filled by
// `window.cbt_sync_channel_select` — a DESK-WIDE asset (hooks.app_include_js,
// public/js/cbt_payment_channels.js), not a global defined here: the open play
// board and the booking form use it too, and a page script's global does not
// exist on their pages. The quick-book dialog keeps its own copy because it
// also drives the quote.

const BLOCK_REASONS = ["Maintenance", "Event", "Open Play", "Holiday", "Other"];
const REJECT_REASONS = [
	"Invalid / suspected fake",
	"Unreadable",
	"Wrong amount",
	"Wrong reference",
];

class CourtBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.branch = null;
		this.date = frappe.datetime.get_today();
		this.clock_offset = 0;
		this.board = null;
		this.pending = null;
		// Backlog B46: the desk's CART, in the portal's exact shape —
		// { "<court>|<date>": [start_time, …] }. The client tracks SLOTS; the
		// server merges them into bookings (api/portal._normalise_cart), which
		// is what makes "two hours on court 1" ONE booking and "10am here, 3pm
		// there" two, without either side deciding it twice.
		this.cart = {};

		this.inject_styles();
		this.build_skeleton();
		this.setup_toolbar();
		this.bind_events();
		this.start_timers();
		this.bootstrap_branch();
	}

	inject_styles() {
		if (!document.getElementById("cbt-court-board-css")) {
			const style = document.createElement("style");
			style.id = "cbt-court-board-css";
			style.textContent = CBT_BOARD_STYLES;
			document.head.appendChild(style);
		}
	}

	build_skeleton() {
		this.$root = $(`
			<div class="cbt-board">
				<div class="cbt-board-main">
					<div class="cbt-board-topbar">
						<div class="cbt-date-nav">
							<button class="btn btn-default btn-sm" data-action="prev-day" title="${__("Previous day")}">‹</button>
							<button class="btn btn-default btn-sm" data-action="today">${__("Today")}</button>
							<button class="btn btn-default btn-sm" data-action="next-day" title="${__("Next day")}">›</button>
							<span class="cbt-board-date-label"></span>
						</div>
						<button class="cbt-noshow-chip" data-action="no-shows"
							data-testid="no-show-chip" style="display:none"></button>
						<!-- The phone's way to the money. On a wide screen the
						     Pending Payments panel sits beside the grid; below
						     700px it is BELOW it, and it was measured starting
						     at y=1370 — two screens down, reachable only by
						     scrolling past every hour of the day. The chip is
						     hidden by CSS on any screen wide enough not to
						     need it. -->
						<button class="cbt-jump-chip" data-action="pending-jump"
							data-testid="pending-jump" style="display:none"></button>
						<div class="cbt-legend"></div>
						<span class="cbt-board-updated"></span>
					</div>
					<div class="cbt-board-canvas"></div>
					<!-- Backlog B46: the cart's own bar, the desk twin of the
					     portal's #cbt-checkoutbar. Sticky at the foot of the
					     board so "Book" is always one thumb away, and hidden
					     until something is selected. It carries NO money on
					     purpose: the price is the dialog's, in its footer,
					     where B42 put it — a second figure here could differ
					     from it the moment a member or a discount is named. -->
					<div class="cbt-cart-bar" data-testid="cart-bar" style="display:none">
						<span class="cbt-cart-count" data-testid="cart-count"></span>
						<span class="cbt-cart-where" data-testid="cart-where"></span>
						<button class="btn btn-default btn-sm" data-action="cart-clear">${__(
							"Clear"
						)}</button>
						<button class="btn btn-primary btn-sm" data-action="cart-book"
							data-testid="cart-book">${__("Book")}</button>
					</div>
				</div>
				<div class="cbt-pending-panel">
					<div class="cbt-panel-head">
						<span>${__("Pending Payments")}</span>
						<span class="cbt-panel-count" style="display:none"></span>
					</div>
					<div class="cbt-panel-body"></div>
				</div>
			</div>
		`).appendTo(this.page.main);
		// The board's own hook on frappe's page CONTAINER, so the phone rules
		// can reach the Branch/Date controls in .page-form — which live outside
		// .cbt-board — without a bare `.page-form` selector that would reach
		// every other app installed on this bench.
		$(this.wrapper).addClass("cbt-board-page");
		this.$canvas = this.$root.find(".cbt-board-canvas");
		this.$panel_body = this.$root.find(".cbt-panel-body");
		this.$panel_count = this.$root.find(".cbt-panel-count");
		this.$date_label = this.$root.find(".cbt-board-date-label");
		this.$updated = this.$root.find(".cbt-board-updated");
		this.$noshow_chip = this.$root.find(".cbt-noshow-chip");
		this.$jump_chip = this.$root.find(".cbt-jump-chip");
		this.$cart_bar = this.$root.find(".cbt-cart-bar");
		this.render_legend();
	}

	render_legend() {
		const entries = [
			[__("Available"), "var(--card-bg); border:1px solid var(--border-color)"],
			[__("Reserved"), "var(--bg-yellow)"],
			[__("Confirmed"), "var(--bg-green)"],
			[__("Extended"), "var(--bg-blue)"],
			[__("Blocked"), "var(--bg-red)"],
			[__("Past"), "var(--bg-gray)"],
			// Section-18 (B7): the running hour is a NEW look on every board, so
			// it is explained where staff already come to read the colour key.
			// Dashed border, no fill — it matches the chip.
			[
				__("In progress"),
				// Section-23: --cbt-primary, so this swatch and the chip's own
				// dashed border can never drift apart.
				"var(--card-bg); border:1px dashed var(--cbt-primary)",
			],
		];
		this.$root.find(".cbt-legend").html(
			entries
				.map(
					([label, bg]) =>
						`<span class="cbt-legend-item">
							<span class="cbt-legend-dot" style="background:${bg}"></span>${label}
						</span>`
				)
				.join("")
		);
	}

	setup_toolbar() {
		this.branch_field = this.page.add_field({
			fieldtype: "Link",
			fieldname: "branch",
			label: __("Branch"),
			options: "CBT Branch",
			get_query: () => ({ filters: { is_active: 1 } }),
			change: () => {
				// USER changes only — programmatic sets are guarded, because
				// their change event fires inside set_value's async chain and
				// can land AFTER a later caller re-pointed the board (the
				// bootstrap-vs-caller hijack).
				if (this._sync_depth) return;
				const value = this.branch_field.get_value();
				if (value && value !== this.branch) {
					this.branch = value;
					localStorage.setItem("cbt_court_board_branch", value);
					// B46: a cart is ONE branch — api/portal._cart_courts
					// refuses a mixed one outright ("a cart books one branch at
					// a time"), so changing branch empties it rather than
					// carrying picks the server would then reject. The DATE is
					// the opposite case and is kept, exactly as the portal
					// keeps it: booking Saturday and Sunday together is the
					// whole point.
					this.cart_clear();
					this.load();
				}
			},
		});
		this.date_field = this.page.add_field({
			fieldtype: "Date",
			fieldname: "date",
			label: __("Date"),
			default: this.date,
			change: () => {
				if (this._sync_depth) return;
				const value = this.date_field.get_value();
				if (value && value !== this.date) {
					this.date = value;
					this.load();
				}
			},
		});
		this._set_field_guarded(this.date_field, this.date);

		this.page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
		this.page.add_inner_button(__("Block Slots"), () => this.open_block_dialog());
	}

	bind_events() {
		this.$root.on("click", "[data-action='prev-day']", () => this.shift_date(-1));
		this.$root.on("click", "[data-action='next-day']", () => this.shift_date(1));
		this.$root.on("click", "[data-action='today']", () =>
			this.set_date(frappe.datetime.get_today())
		);
		this.$root.on("click", "[data-action='no-shows']", () =>
			this.open_noshow_dialog()
		);
		// Scroll the PANEL into view, never the page to a coordinate: the desk
		// shell is frappe's and its scrolling container is not ours to assume.
		this.$root.on("click", "[data-action='pending-jump']", () => {
			const panel = this.$root.find(".cbt-pending-panel")[0];
			if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
		});

		// B33: bound on the CANVAS, not the nodes, so it survives every re-render.
		this.$canvas.on("click", ".cbt-sketch-court", (e) =>
			this.focus_court($(e.currentTarget).attr("data-court"))
		);
		this.$canvas.on("keydown", ".cbt-sketch-court", (e) => {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				this.focus_court($(e.currentTarget).attr("data-court"));
			}
		});

		// B46: the cart bar. Bound on the ROOT, not the nodes, so it survives
		// every re-render — the same discipline the canvas handlers use.
		this.$root.on("click", "[data-action='cart-clear']", () => {
			this.cart_clear();
		});
		this.$root.on("click", "[data-action='cart-book']", () => {
			this.open_cart_book();
		});

		this.$canvas.on("click", ".cbt-slot", (e) => {
			const $slot = $(e.currentTarget);
			const status = $slot.attr("data-status");
			if (status === "available") {
				// Backlog B46: a tap SELECTS, exactly as it does on the
				// customer's own grid — it no longer opens the dialog. That is
				// one extra tap on a single-slot sale and it is the trade the
				// row exists to make: the desk can now express what the website
				// has been able to express since B35 (many courts, many dates,
				// one payment), and it expresses it with the SAME gesture the
				// customer uses, rather than inventing a second one.
				this.cart_toggle($slot.attr("data-court"), $slot.attr("data-start"));
			} else if (status === "blocked") {
				frappe.msgprint({
					title: __("Blocked"),
					indicator: "red",
					message: __("This slot is blocked: {0}", [
						frappe.utils.escape_html($slot.attr("data-reason") || __("Other")),
					]),
				});
			} else if (status === "booked") {
				const booking = $slot.attr("data-booking");
				const booking_status = $slot.attr("data-booking-status");
				if (booking_status === "Reserved") {
					this.open_verification_dialog(booking);
				} else {
					this.open_details_dialog(booking);
				}
			}
		});

		this.$panel_body.on("click", "[data-panel-action]", (e) => {
			e.stopPropagation();
			const $btn = $(e.currentTarget);
			const booking = $btn.attr("data-booking");
			const action = $btn.attr("data-panel-action");
			if (action === "accept") {
				this.accept_booking(booking);
			} else {
				// Reject needs its reason; Open is the same dialog — both land
				// on the verification dialog (reason Select lives there).
				this.open_verification_dialog(booking);
			}
		});
		this.$panel_body.on("click", ".cbt-pending-thumb", (e) => {
			window.open($(e.currentTarget).attr("data-url"), "_blank");
		});
	}

	start_timers() {
		// 30s data refresh — suspended while a dialog is open (modal state must
		// never be yanked out from under the user mid-action).
		this.refresh_timer = setInterval(() => {
			if (!this.branch) return;
			if (!$(this.wrapper).is(":visible")) return;
			if (document.querySelector(".modal.show")) return;
			this.load({ quiet: true });
		}, 30000);
		// 1s countdown ticker — pure DOM, no network.
		this.tick_timer = setInterval(() => this.tick_countdowns(), 1000);
	}

	bootstrap_branch() {
		// Branch list is permission-scoped server-side (tenancy hooks): staff
		// see only their company's branches. Exactly one visible active branch
		// ⇒ auto-select and lock the selector (single-branch staff UX).
		frappe.db
			.get_list("CBT Branch", {
				filters: { is_active: 1 },
				fields: ["name"],
				limit: 500,
			})
			.then((rows) => {
				const names = (rows || []).map((r) => r.name);
				const remembered = localStorage.getItem("cbt_court_board_branch");
				let pick = null;
				if (names.length === 1) {
					pick = names[0];
					this.branch_field.$input.prop("disabled", true);
				} else if (remembered && names.includes(remembered)) {
					pick = remembered;
				} else if (names.length) {
					pick = names[0];
				}
				if (pick && !this.branch) {
					// A caller (or the change handler) may have already pointed
					// the board somewhere — bootstrap never overrides it.
					this.show(pick);
				} else if (!pick) {
					this.render_empty(
						"🏸",
						__("No active branches are visible to you"),
						__("Ask your administrator for access to a branch.")
					);
				}
				this._bootstrapped = true;
			});
	}

	_set_field_guarded(field, value) {
		// Each set_value chain holds ITS OWN increment until it settles (a
		// boolean would be released by the first chain while a slower one is
		// still in flight — exactly the hijack this guard exists to kill).
		// Released on both resolve and reject: a failed Link validation must
		// never permanently dead the change handlers.
		this._sync_depth = (this._sync_depth || 0) + 1;
		const release = () => {
			this._sync_depth--;
		};
		return Promise.resolve(field.set_value(value)).then(release, release);
	}

	show(branch, date) {
		// Public entry: point the board at branch × date and load once.
		// (Bootstrap and the E2E harness both use this.)
		this.branch = branch;
		if (date) this.date = date;
		const sets = [
			this._set_field_guarded(this.branch_field, this.branch),
			this._set_field_guarded(this.date_field, this.date),
		];
		// A stale overlapping chain can still overwrite the visible INPUT
		// (the load hijack itself is dead via the guard) — re-assert the
		// display once every pending chain has settled.
		Promise.all(sets).then(() => {
			if (this.branch_field.get_value() !== this.branch) {
				this._set_field_guarded(this.branch_field, this.branch);
			}
		});
		return this.load();
	}

	shift_date(days) {
		this.set_date(frappe.datetime.add_days(this.date, days));
	}

	set_date(date) {
		this.date = date;
		this._set_field_guarded(this.date_field, date);
		this.load();
	}

	// ------------------------------------------------------------------
	// The cart (Backlog B46)
	//
	// Deliberately the PORTAL's model, key for key: the client tracks SLOTS and
	// the server merges them into bookings. Anything cleverer here would be a
	// second opinion about what "one booking" means, and the two faces would
	// start disagreeing about it the first time a rule changed.
	// ------------------------------------------------------------------

	cart_key(court, date) {
		return `${court}|${date}`;
	}

	cart_has(court, date, start) {
		return (this.cart[this.cart_key(court, date)] || []).indexOf(start) !== -1;
	}

	cart_toggle(court, start, date) {
		date = date || this.date;
		const key = this.cart_key(court, date);
		const times = (this.cart[key] || []).slice();
		const at = times.indexOf(start);
		if (at === -1) times.push(start);
		else times.splice(at, 1);
		if (times.length) this.cart[key] = times;
		else delete this.cart[key];
		this.paint_cart();
	}

	cart_clear() {
		this.cart = {};
		this.paint_cart();
	}

	cart_count() {
		return Object.keys(this.cart).reduce(
			(total, key) => total + this.cart[key].length,
			0
		);
	}

	cart_dates() {
		return Array.from(
			new Set(Object.keys(this.cart).map((key) => key.split("|")[1]))
		).sort();
	}

	/** One entry per SLOT, stable order — the shape both cart endpoints take. */
	cart_items() {
		const items = [];
		Object.keys(this.cart)
			.sort()
			.forEach((key) => {
				const [court, date] = key.split("|");
				this.cart[key]
					.slice()
					.sort()
					.forEach((start) => {
						items.push({
							court: court,
							booking_date: date,
							start_time: start,
							number_of_slots: 1,
						});
					});
			});
		return items;
	}

	/**
	 * Drop picks that have been sold since they were picked — for the DATE on
	 * screen only; create_desk_cart's own _reject_unavailable_runs catches the
	 * rest and refuses the whole cart rather than booking part of it.
	 *
	 * Returns how many were dropped, so the board can SAY so: a slot silently
	 * vanishing from a cart is how an operator ends up promising a court that
	 * was never held.
	 */
	prune_cart() {
		if (!this.board) return 0;
		let dropped = 0;
		for (const court of this.board.courts || []) {
			const key = this.cart_key(court.court, this.board.date);
			const times = this.cart[key];
			if (!times) continue;
			const live = times.filter((start) =>
				(court.slots || []).some(
					(slot) => slot.start_time === start && slot.status === "available"
				)
			);
			dropped += times.length - live.length;
			if (live.length) this.cart[key] = live;
			else delete this.cart[key];
		}
		return dropped;
	}

	paint_cart() {
		// aria-pressed, not a class alone: the cell is a real toggle now, and a
		// screen reader has to be able to say which hours are in the basket.
		this.$canvas.find(".cbt-slot[data-status='available']").each((_index, node) => {
			const picked = this.cart_has(
				node.dataset.court,
				this.date,
				node.dataset.start
			);
			node.setAttribute("aria-pressed", String(picked));
			$(node).toggleClass("cbt-slot--picked", picked);
		});
		this.render_cart_bar();
	}

	render_cart_bar() {
		const count = this.cart_count();
		this.$cart_bar.toggle(count > 0);
		if (!count) return;
		this.$cart_bar
			.find(".cbt-cart-count")
			.text(count === 1 ? __("1 slot selected") : __("{0} slots selected", [count]));
		// The dates, because the cart SURVIVES a date change and the operator
		// may be looking at a day none of the picks are on.
		const dates = this.cart_dates();
		this.$cart_bar
			.find(".cbt-cart-where")
			.text(
				dates.length > 1
					? __("across {0} days", [dates.length])
					: dates[0] === this.date
					? ""
					: moment(dates[0], "YYYY-MM-DD").format("ddd, D MMM")
			);
	}

	// ------------------------------------------------------------------
	// Data
	// ------------------------------------------------------------------

	load(opts = {}) {
		if (!this.branch) return Promise.resolve();
		// Sequence token: a newer load (fast branch switch, auto-refresh racing
		// a manual change) invalidates every in-flight response of this one —
		// stale payloads must never win the render.
		const seq = (this._load_seq = (this._load_seq || 0) + 1);
		if (!opts.quiet) this.$updated.text(__("Loading…"));
		const board_call = frappe.call({
			method: "court_booking_tech.api.board.get_board_data",
			type: "GET",
			args: { branch: this.branch, date: this.date },
		});
		return board_call.then((r) => {
			if (seq !== this._load_seq) return;
			this.board = r.message;
			this.clock_offset =
				frappe.datetime.str_to_obj(this.board.server_now).getTime() - Date.now();
			this.render_board();
			return frappe
				.call({
					method: "court_booking_tech.api.board.get_pending_payments",
					type: "GET",
					args: { company: this.board.company },
				})
				.then((p) => {
					if (seq !== this._load_seq) return;
					this.pending = p.message;
					this.render_panel();
					// Section-18 (Backlog B9): the SERVER's clock, like every
					// countdown beside it. This stamp is what staff read to
					// decide whether the grid is stale, so a device with a wrong
					// clock (or a tenant abroad) must not be able to make it
					// contradict the deadlines next to it — and under
					// section-17's pinned test clock the old version disagreed
					// visibly, beside a chip whose whole meaning is "this hour is
					// happening now".
					this.$updated.text(
						__("Updated {0}", [
							moment(this.server_now_ms()).format("HH:mm:ss"),
						])
					);
				});
		});
	}

	server_now_ms() {
		return Date.now() + this.clock_offset;
	}

	is_running_slot(slot) {
		// Section-18 (B7): is this slot happening RIGHT NOW? `start <= now < end`,
		// the same bounds slots.py uses to decide it is not yet past.
		// frappe.datetime.str_to_obj on both edges, never `new Date(...)`: it is
		// how this class parses `server_now` to build clock_offset, so the two
		// sides of the comparison are parsed identically and the browser's
		// timezone drops out.
		if (!this.board) return false;
		const at = (time) =>
			frappe.datetime.str_to_obj(`${this.board.date} ${time}`).getTime();
		const now = this.server_now_ms();
		return at(slot.start_time) <= now && now < at(slot.end_time);
	}

	// ------------------------------------------------------------------
	// Rendering
	// ------------------------------------------------------------------

	render_empty(icon, title, hint) {
		this.$canvas.html(`
			<div class="cbt-board-empty">
				<div class="cbt-empty-icon">${icon}</div>
				<div class="cbt-empty-title">${title}</div>
				<div class="text-muted" style="font-size:var(--text-sm)">${hint || ""}</div>
			</div>
		`);
	}

	render_board() {
		const board = this.board;
		this.$date_label.text(
			moment(this.date, "YYYY-MM-DD").format("ddd, D MMM YYYY")
		);
		this.render_noshow_chip();
		if (!board.courts.length) {
			this.render_empty("🏟️", __("No active courts on this branch"), "");
			this.paint_cart();
			return;
		}
		const has_slots = board.courts.some((c) => c.slots.length);
		if (!has_slots) {
			this.render_empty(
				"🌙",
				__("{0} is closed on this day", [
					frappe.utils.escape_html(board.branch_name),
				]),
				__("Pick another date, or check the branch business hours.")
			);
			this.paint_cart();
			return;
		}

		this.$canvas.empty();
		const $layout = this.render_court_layout();
		if ($layout) this.$canvas.append($layout);
		this.$canvas.append(this.render_matrix());
		// B46: a slot sold while it sat in the cart is dropped, and SAID — a
		// pick that vanishes silently is how an operator promises a court that
		// was never held.
		const lost = this.prune_cart();
		if (lost) {
			frappe.show_alert({
				message:
					lost === 1
						? __("One selected slot was just taken — it has been removed.")
						: __("{0} selected slots were just taken — they have been removed.", [
								lost,
						  ]),
				indicator: "orange",
			});
		}
		this.paint_cart();
		this.tick_countdowns();
	}

	render_matrix() {
		// Backlog B33. ONE row per grid slot, ONE column per court, so "what is
		// free at 3pm?" reads across instead of down N cards. The CELL is the
		// unchanged render_slot output, which is why ten E2E files keep their
		// selectors — see docs/sections/section-7.md.
		const board = this.board;
		const esc = frappe.utils.escape_html;
		const $wrap = $('<div class="cbt-matrix-wrap" data-testid="board-matrix"></div>');
		const $table = $('<table class="cbt-matrix"></table>');

		const $head = $("<thead></thead>");
		const $head_row = $("<tr></tr>").append(
			`<th scope="col" class="cbt-timecell cbt-matrix-corner">${__("Time")}</th>`
		);
		for (const court of board.courts) {
			$head_row.append(`
				<th scope="col" class="cbt-colhead" data-court="${esc(court.court)}" title="${esc(court.court_type || "")}">
					<p class="cbt-colhead-name">${esc(court.court_name)}</p>
				</th>
			`);
		}
		$table.append($head.append($head_row));

		const $body = $("<tbody></tbody>");
		// One grid per BRANCH (slots._availability), so the first court's rows
		// are every court's rows — the same premise the portal grid is built on.
		(board.courts[0].slots || []).forEach((row, index) => {
			const $line = $("<tr></tr>")
				.attr("data-start", row.start_time)
				.attr("data-end", row.end_time);
			// Backlog B45: the SAME string the customer's grid renders, from the
			// same function (public/js/cbt_time_format.js). Until 2026-09-05 this
			// printed 06:00 - 07:00 while the phone in the customer's hand said
			// 6 AM - 7 AM, and staff read the machine's version out loud.
			$line.append(
				`<th scope="row" class="cbt-timecell">${cbt.fmt.timeRange(
					row.start_time,
					row.end_time
				)}</th>`
			);
			for (const court of board.courts) {
				const $cell = $('<td class="cbt-cell"></td>').attr("data-court", court.court);
				const slot = (court.slots || [])[index];
				// An empty cell rather than a skipped one: a short column must
				// never shift the slots below it into the wrong hour.
				if (slot) $cell.append(this.render_slot(court, slot));
				$line.append($cell);
			}
			$body.append($line);
		});
		$table.append($body);
		return $wrap.append($table);
	}

	render_court_layout() {
		// The layout is now a SKETCH, not the working grid: tapping a court
		// focuses its column. Collapsed by default (user ruling 2026-09-05).
		const board = this.board;
		const layout = board.layout || {};
		const placed = new Set(
			(layout.cells || []).map((cell) => cell.court).filter(Boolean)
		);
		if (
			!layout.rows ||
			!layout.columns ||
			!(layout.cells || []).length ||
			!board.courts.some((c) => placed.has(c.court))
		) {
			return null;
		}
		const esc = frappe.utils.escape_html;
		const names = {};
		for (const court of board.courts) names[court.court] = court.court_name;
		const cell_map = {};
		for (const cell of layout.cells || []) {
			cell_map[`${cell.row_index}:${cell.col_index}`] = cell.court;
		}

		const $grid = $(
			`<div class="cbt-sketch-grid" style="grid-template-columns:repeat(${layout.columns}, minmax(0, 1fr))"></div>`
		);
		for (let row = 1; row <= layout.rows; row++) {
			for (let col = 1; col <= layout.columns; col++) {
				const court = cell_map[`${row}:${col}`];
				if (court && names[court]) {
					$grid.append(
						`<div class="cbt-sketch-court" data-court="${esc(court)}"
							role="button" tabindex="0">${esc(names[court])}</div>`
					);
				} else {
					$grid.append('<div class="cbt-sketch-gap"></div>');
				}
			}
		}
		return $(`
			<details class="cbt-layout-details" data-testid="court-layout">
				<summary>${__("Court Layout")} — ${__("{0} courts on the floor", [
					placed.size,
				])}</summary>
			</details>
		`).append($grid);
	}

	render_noshow_chip() {
		// A released booking holds no slot, so it appears NOWHERE on the grid.
		// Without this chip a no-show would simply vanish from the desk's view
		// the instant it happened — and with it the only route back.
		const rows = (this.board && this.board.no_shows) || [];
		this.$noshow_chip
			.toggle(rows.length > 0)
			.text(__("No-shows: {0}", [rows.length]));
	}

	open_noshow_dialog() {
		const rows = (this.board && this.board.no_shows) || [];
		const esc = frappe.utils.escape_html;
		const dialog = new frappe.ui.Dialog({
			title: __("Released no-shows — {0}", [
				moment(this.date, "YYYY-MM-DD").format("ddd, D MMM YYYY"),
			]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "rows",
					options: `
						<div class="text-muted small" style="margin-bottom:.5rem">
							${__(
								"Nobody checked in, so these slots went back on sale. The payment stands — the billing statement is still Paid & Verified. Undo puts a booking back on the board if the slot is still free."
							)}
						</div>
						${rows
							.map(
								(row) => `
							<div class="cbt-noshow-row" data-booking="${esc(row.name)}">
								<div class="cbt-noshow-who">
									<div><b>${esc(row.customer_name || row.customer || row.name)}</b></div>
									<div class="text-muted" style="font-size:var(--text-xs)">
										${esc(row.court_name)} · ${esc(row.name)}</div>
								</div>
								<span class="cbt-noshow-when">${cbt.fmt.timeRange(
									row.start_time,
									row.end_time
								)}</span>
								<button class="btn btn-default btn-xs" data-action="undo"
									data-booking="${esc(row.name)}">${__("Undo")}</button>
							</div>`
							)
							.join("")}
						<div style="margin-top:.75rem">
							<a href="#" data-action="list-view">${__("Open the full list")}</a>
						</div>
					`,
				},
			],
		});
		const $wrap = dialog.fields_dict.rows.$wrapper;
		$wrap.find("[data-action='undo']").on("click", (e) => {
			const booking = $(e.currentTarget).attr("data-booking");
			frappe
				.call({
					method: "court_booking_tech.api.bookings.undo_no_show",
					args: { name: booking },
					freeze: true,
				})
				.then(() => {
					dialog.hide();
					frappe.show_alert({
						message: __("{0} is back on the board", [booking]),
						indicator: "green",
					});
					this.load();
				});
		});
		$wrap.find("[data-action='list-view']").on("click", (e) => {
			e.preventDefault();
			dialog.hide();
			frappe.set_route("List", "CBT Court Booking", {
				booking_status: "No Show",
				booking_date: this.date,
				branch: this.branch,
			});
		});
		dialog.show();
	}

	focus_court(court) {
		// B33: a court is a COLUMN now, so a sketch tap highlights the column
		// and scrolls the WRAPPER — never the page (B31's allowlist).
		const $heads = this.$canvas.find("th.cbt-colhead[data-court]");
		const $target = $heads.filter(`[data-court="${court}"]`);
		if (!$target.length) return;
		this.$canvas
			.find("th.cbt-colhead, td.cbt-cell")
			.each((_index, node) =>
				$(node).toggleClass("cbt-col--focus", node.dataset.court === court)
			);
		this.$canvas
			.find(".cbt-sketch-court")
			.each((_index, node) =>
				$(node).toggleClass("cbt-sketch-court--on", node.dataset.court === court)
			);
		$target[0].scrollIntoView({
			behavior: "smooth",
			block: "nearest",
			inline: "nearest",
		});
	}

	render_slot(court, slot) {
		const time_label = cbt.fmt.timeRange(slot.start_time, slot.end_time);
		// B33 (user ruling, verbatim: "always show amount and status of that time
		// slot"): the MONEY is on the face of every cell, not only on hover. A
		// court with rate rules charges differently by hour, so the column header's
		// "from ₱400" is not this hour's price.
		// B45: the COMPACT form the portal's cells have used since B33 —
		// "₱ 400.00" fills a 124px phone cell on its own.
		const rate = slot.rate == null ? "—" : cbt.fmt.moneyShort(slot.rate);
		const $el = $(`
			<div class="cbt-slot" data-status="${slot.status}"
				data-court="${frappe.utils.escape_html(court.court)}"
				data-start="${slot.start_time}" data-end="${slot.end_time}">
				<span class="cbt-slot-time">${time_label}</span>
				<span class="cbt-slot-rate" data-testid="slot-rate">${rate}</span>
				<span class="cbt-slot-body"></span>
			</div>
		`);
		const $body = $el.find(".cbt-slot-body");

		if (slot.status === "available") {
			// Section-18 (B7): an available slot that has STARTED is its own
			// state. Both edges come from the SERVER — the payload's date and the
			// clock offset built from its `server_now` — so the browser's own
			// clock and timezone cancel out of the comparison, and the class can
			// never disagree with the `status` the server computed from the same
			// instant (S7 doctrine: never the client clock).
			const running = this.is_running_slot(slot);
			$el.toggleClass("cbt-slot-running", running);
			// B46: the cell is a TOGGLE now, so it says so to a screen reader as
			// well as to the eye. paint_cart keeps this in step after every
			// render — set here too so a freshly rendered cell is never
			// pressed-less between the two.
			$el.attr("role", "button").attr("aria-pressed", "false");
			$body.append(
				`<span class="cbt-slot-hint">+ ${
					running ? __("Select (in progress)") : __("Select")
				}</span>`
			);
		} else if (slot.status === "blocked") {
			$el.attr("data-reason", slot.reason || "");
			$body.append(
				`<span class="cbt-slot-who">${frappe.utils.escape_html(
					__(slot.reason || "Blocked")
				)}</span>`
			);
		} else if (slot.status === "booked") {
			$el.attr("data-booking", slot.booking);
			$el.attr("data-booking-status", slot.booking_status);
			$el.addClass(SLOT_STATUS_CLASS[slot.booking_status] || "");
			$body.append(
				`<span class="cbt-slot-who">${frappe.utils.escape_html(
					slot.customer_name || ""
				)}</span>`
			);
			if (slot.booking_status === "Reserved") {
				const deadline = slot.verification_deadline_at || slot.reservation_expires_at;
				if (slot.pending_proof_count) {
					$body.append(
						`<span class="cbt-proof-badge" title="${__("Proofs awaiting review")}">📎 ${
							slot.pending_proof_count
						}</span>`
					);
				}
				if (deadline) {
					$body.append(
						`<span class="cbt-count" data-deadline="${deadline}"
							title="${slot.verification_deadline_at ? __("Verification deadline") : __("Reservation expires")}"></span>`
					);
				}
			} else if (slot.checked_in_at) {
				// Section-16: the ✓ now means ATTENDANCE, not payment. It used
				// to sit on every Confirmed slot, where it merely repeated what
				// the green chip and the legend already said — so the glyph was
				// free to take the meaning the desk actually needs at a glance:
				// who is on court.
				$body.append(
					`<span class="cbt-checkin" data-testid="checked-in"
						title="${__("Checked in {0}", [
							frappe.datetime.str_to_user(slot.checked_in_at),
						])}">✓</span>`
				);
			} else if (slot.release_at) {
				// Unattended and already running: this is the slot about to go
				// back on sale, and it is the only one staff can still save.
				$body.append(
					`<span class="cbt-count cbt-count-release"
						data-testid="release-countdown" data-deadline="${slot.release_at}"
						title="${__("Released for walk-ins if nobody checks in")}"></span>`
				);
			}
		}
		return $el;
	}

	render_panel() {
		const items = (this.pending && this.pending.items) || [];
		this.$panel_count.toggle(items.length > 0).text(items.length);
		// The phone's shortcut to the panel. `display` is set here and the media
		// query decides whether it is ever painted — a chip that duplicated the
		// panel already on screen would be clutter on a desktop.
		this.$jump_chip
			.css("display", items.length ? "" : "none")
			// No peso sign: "₱ 4 awaiting payment" reads as FOUR PESOS, which is
			// the one thing this chip must never say on a money surface.
			.text(__("{0} awaiting payment", [items.length]));
		if (!items.length) {
			this.$panel_body.html(
				`<div class="cbt-panel-empty">✅ ${__("Nothing awaiting verification")}</div>`
			);
			return;
		}
		this.$panel_body.empty();
		for (const item of items) {
			this.$panel_body.append(this.render_pending_item(item));
		}
		this.tick_countdowns();
	}

	render_pending_item(item) {
		const esc = frappe.utils.escape_html;
		const proof = item.latest_proof;
		let proof_html;
		if (!proof) {
			proof_html = `<span class="cbt-proof-badge">${__("No proof yet")}</span>`;
		} else if (proof.is_pdf) {
			proof_html = `<span class="cbt-proof-badge">📄 ${__("PDF proof")}</span>`;
		} else {
			proof_html = `<img class="cbt-pending-thumb" src="${esc(proof.file_url)}"
				data-url="${esc(proof.file_url)}" alt="${__("Payment proof")}">`;
		}
		const kind = item.verification_deadline_at
			? __("verify by")
			: __("expires");
		const $item = $(`
			<div class="cbt-pending-item" data-booking="${esc(item.name)}">
				<div class="cbt-pending-top">
					<span class="cbt-pending-cust">${esc(item.customer_name || item.customer)}</span>
					<span class="cbt-pending-ref">${esc(item.name)}</span>
				</div>
				<div class="cbt-pending-where">
					${esc(item.court_name)} · ${moment(item.booking_date, "YYYY-MM-DD").format("D MMM")}
					${cbt.fmt.timeRange(item.start_time, item.end_time)} · ${esc(item.branch_name)}
				</div>
				<div class="cbt-pending-row">
					<span class="cbt-pending-amount">${format_currency(item.total_amount, "PHP")}</span>
					${proof_html}
					${
						item.effective_deadline
							? `<span class="cbt-count" data-deadline="${item.effective_deadline}" title="${kind}"></span>`
							: ""
					}
				</div>
				<div class="cbt-pending-actions">
					<button class="btn btn-success btn-xs" data-panel-action="accept"
						data-booking="${esc(item.name)}">${__("Accept")}</button>
					${
						item.pending_proof_count
							? `<button class="btn btn-danger btn-xs" data-panel-action="reject"
								data-booking="${esc(item.name)}">${__("Reject")}</button>`
							: ""
					}
					<button class="btn btn-default btn-xs" data-panel-action="open"
						data-booking="${esc(item.name)}">${__("Open")}</button>
				</div>
			</div>
		`);
		return $item;
	}

	tick_countdowns() {
		const now = this.server_now_ms();
		this.$root.find(".cbt-count").each((_, el) => {
			const $el = $(el);
			const deadline = frappe.datetime.str_to_obj($el.attr("data-deadline")).getTime();
			const remaining = deadline - now;
			$el.toggleClass("cbt-count-late", remaining < 5 * 60 * 1000);
			$el.text(this.format_remaining(remaining));
		});
	}

	format_remaining(ms) {
		if (ms <= 0) return __("due");
		const total_s = Math.floor(ms / 1000);
		const h = Math.floor(total_s / 3600);
		const m = Math.floor((total_s % 3600) / 60);
		const s = total_s % 60;
		if (h >= 48) return __("{0}d", [Math.floor(h / 24)]);
		if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
		return `${m}:${String(s).padStart(2, "0")}`;
	}

	// ------------------------------------------------------------------
	// Actions
	// ------------------------------------------------------------------

	open_cart_book() {
		const board = this.board;
		// Backlog B46. This was `open_quick_book(slot_data)` — ONE court, ONE
		// start, and an INTEGER for "how many consecutive slots" — while the
		// customer's own website had held a multi-court, multi-date,
		// non-contiguous cart since B35. It now books the SET the operator
		// selected on the board, through the same server model the portal uses.
		if (!this.cart_count()) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Quick Book — {0}", [board.branch_name]),
			fields: [
				{
					// Section-14: the static Rate line is GONE. It rendered the
					// court's BASE rate, which on a court with rate rules is a
					// price the booking may not be charged at — and a stale
					// number at the top of the dialog wins the argument against
					// the live one at the bottom. All money now lives in the
					// server-quoted estimate block below.
					//
					// B46: what this shows is the SERVER's view of the cart —
					// its RUNS, not the operator's taps. Two adjacent hours on
					// one court are ONE booking and one line, and that merge is
					// _normalise_cart's rule, not a second opinion rendered
					// here. update_estimate fills it on every quote.
					fieldtype: "HTML",
					fieldname: "summary",
					options: `<div class="cbt-cart-lines" data-testid="cart-lines"></div>`,
				},
				// Backlog B42: three named groups — who / how long / how they
				// pay — instead of thirteen controls in one column. Section and
				// Column Breaks only: every real control keeps its
				// data-fieldname and data-fieldtype, which is the contract
				// helpers/gestures.py dispatches on.
				{ fieldtype: "Section Break", label: __("Who is playing") },
				{
					// The toggle LEADS the group: it is the fork the whole
					// section depends on, and reading order put it after the
					// field it produces in the first pass.
					// Section-13 (Backlog B1): a stranger paying cash books
					// without an account. Toggling swaps the Link for free
					// text — a cash walk-in will often not give an email, and
					// minting a throwaway User for them was the old workaround.
					fieldtype: "Check",
					fieldname: "walk_in",
					label: __("Walk-in (no account)"),
					default: 0,
					change: () => sync_membership(),
				},
				{
					fieldtype: "Link",
					fieldname: "customer",
					label: __("Customer"),
					options: "User",
					depends_on: "eval:!doc.walk_in",
					mandatory_depends_on: "eval:!doc.walk_in",
					get_query: () => ({
						query: "court_booking_tech.api.board.customer_query",
					}),
					change: () => sync_membership(),
				},
				{
					fieldtype: "Data",
					fieldname: "customer_name",
					label: __("Walk-in Name"),
					depends_on: "eval:doc.walk_in",
					mandatory_depends_on: "eval:doc.walk_in",
					description: __("Printed on the billing statement."),
				},
				// B42: the hint belongs to the CUSTOMER, which is what it
				// describes. It used to sit orphaned between Discount and
				// Payment Method, where it read as a label for the Select.
				{ fieldtype: "HTML", fieldname: "member_hint" },
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Data",
					fieldname: "customer_phone",
					label: __("Phone (optional)"),
					depends_on: "eval:doc.walk_in",
				},
				// Backlog B46: the "How long" group and its `number_of_slots`
				// Int are GONE. A duration typed into a box could only ever
				// express "N consecutive hours on the one court you clicked",
				// which is the whole gap this row closes — the answer is now on
				// the board, in the cells the operator actually selected, and
				// the summary above states what that came to.
				//
				// ⚠ `number_of_slots` is still a real field on CBT Court
				// Booking and still a parameter of api.bookings.create_booking
				// (seeds, scripts and the reschedule dialog all pass it). What
				// disappeared is the DESK's way of typing it.
				{ fieldtype: "Section Break", label: __("Price") },
				{
					fieldtype: "Percent",
					fieldname: "discount_percent",
					label: __("Discount (%)"),
					default: 0,
					// ⚠ NO `precision` here. Measured 2026-09-05: df.precision
					// reaches the value get_value() RETURNS, not just the one it
					// renders — typing 12.345 into a precision-2 Percent posts
					// 12.34. On the money surface this row exists to make
					// honest, that would be the defect.
					// Section-18 (B4): a RE-QUOTE, not a repaint — the discount is
					// applied server-side now, so the screen cannot answer this
					// on its own.
					change: () => refresh_quote(),
				},
				{ fieldtype: "Section Break", label: __("How they pay") },
				{
					fieldtype: "Select",
					fieldname: "payment_method",
					label: __("Payment Method"),
					options: ["Cash", "Fund Transfer", "Free"].join("\n"),
					default: "Cash",
					reqd: 1,
					description: __("Fund Transfer holds the slot until payment is verified."),
					// Backlog B27: Free drops the platform fee — re-quote. Backlog
					// B29: the method decides which channels may take the money.
					change: () => {
						sync_channels();
						refresh_quote();
					},
				},
				{ fieldtype: "Column Break" },
				{
					// Backlog B29: WHERE the money goes — the drawer, the e-wallet or
					// the bank. Options are the company's ENABLED channels of the
					// method's kind, fetched when the method changes; Free has none.
					fieldtype: "Select",
					fieldname: "payment_channel",
					label: __("Paid via"),
					options: [],
					depends_on: "eval:doc.payment_method!=='Free'",
				},
				{
					// In the SAME section as the method and channel, not a new
					// one: an extra Section Break whose only visible control was
					// Notes reserved a column's vertical padding and read as a
					// hole in the middle of the form.
					// Backlog B39: shown only once the quote says this customer
					// holds a balance here (update_estimate toggles `hidden`).
					fieldtype: "Check",
					fieldname: "apply_credit",
					label: __("Use store credit"),
					default: 0,
					hidden: 1,
					change: () => update_estimate(),
				},
				{ fieldtype: "Small Text", fieldname: "notes", label: __("Notes") },
				{
					// B42: this node is MOVED into the dialog footer after show()
					// — see anchor_estimate(). It stays a field so that
					// fields_dict.estimate, and every data-testid inside it,
					// keep working exactly as before.
					fieldtype: "HTML",
					fieldname: "estimate",
				},
			],
			primary_action_label: __("Book"),
			primary_action: async (values) => {
				// Section-18 (B4). WYSIWYG money is a promise to the person
				// standing at the desk: never charge a number the screen has not
				// caught up to. Blurring the discount field in order to click Book
				// fires its change handler, and since B4 the total comes from a
				// SERVER round trip instead of a synchronous JS repaint — so
				// settle the in-flight quote before booking.
				//
				// This is about the DISPLAY being honest, not about the amount
				// sent: `values` comes from get_values(), which reads the control
				// itself rather than the last change event, so the figure posted
				// below was always what staff typed. refresh_quote never rejects
				// (it catches internally), and `await undefined` is a no-op before
				// the first quote, so no path can strand the button. frappe
				// ignores a primary_action's return value, so async is free here.
				await dialog._quote_promise;
				frappe
					.call({
						method: "court_booking_tech.api.bookings.create_desk_cart",
						args: {
							// B46: the SET, as slots. The server merges them into
							// runs (_normalise_cart) — one payment, one group,
							// ONE billing document, exactly as the website has
							// done since B35.
							items: JSON.stringify(this.cart_items()),
							// Section-13: EXACTLY ONE identity reaches the
							// server. Sending both is a contradiction the API
							// refuses, so the toggle decides — never send the
							// stale other half left in the hidden field.
							customer: values.walk_in ? null : values.customer,
							customer_name: values.walk_in ? values.customer_name : null,
							customer_phone: values.walk_in
								? values.customer_phone
								: null,
							payment_method: values.payment_method,
							// B29: Free carries no channel; otherwise the one on
							// screen (empty = the server picks the kind's default).
							payment_channel:
								values.payment_method === "Free"
									? null
									: values.payment_channel || null,
							// WYSIWYG money: send exactly what the staff can
							// see. An untouched Percent field reads as 0, which
							// is indistinguishable from a deliberate 0 — so the
							// screen, not a hidden re-derivation, decides the
							// price, and the hint below makes a failed
							// membership lookup visible rather than silent.
							discount_percent: flt(values.discount_percent) || 0,
							// B39: spend the customer's store credit at this company.
							apply_credit: values.apply_credit ? 1 : 0,
							notes: values.notes,
						},
					})
					.then((r) => {
						// BEFORE hide(): the Customer Link's in-flight
						// validate-and-fetch can land the moment the modal
						// starts fading, and its change handler must find the
						// dialog already spent — see dialog_is_spent().
						dialog._booked = true;
						dialog.hide();
						const out = r.message;
						const status = out.booking_status;
						const first = (out.bookings || [])[0] || {};
						frappe.show_alert({
							message:
								out.count === 1
									? __("{0} — {1}", [first.name, __(status)])
									: __("{0} bookings — {1}, one payment", [
											out.count,
											__(status),
									  ]),
							indicator: status === "Confirmed" ? "green" : "orange",
						});
						// The cart is SPENT. Clearing before the reload stops a
						// booked slot flashing back as "selected" for the width
						// of one render.
						this.cart_clear();
						this.load();
					});
			},
		});
		// Section-14 made the SUBTOTAL server-computed: a court can price 18:00
		// differently from 17:00, so `court.hourly_rate × hours` is no longer a
		// price anyone is charged, and staff must see what the customer will
		// actually pay before they say it out loud (WYSIWYG money, S11).
		//
		// Section-18 (Backlog B4) finished the job. The DISCOUNT used to be
		// multiplied HERE, against that server subtotal, because get_quote
		// derived its discount from the SESSION user (the staff member) rather
		// than the customer being booked — one money area with two authorities.
		// get_quote now takes staff-gated `customer` + `discount_percent`, so
		// every figure on screen is the server's and there is nothing left to
		// re-derive. The block says "Total", not "Estimated total".
		//
		// The membership pre-fill flow is UNCHANGED: the Percent field is still
		// the WYSIWYG source of truth staff see and edit, and it is still what
		// gets posted. The server just does all the arithmetic now.
		let quote = null;
		let quote_failed = false;

		// Backlog B42. The money block LEAVES the field flow and lives in the
		// dialog footer, above the Book button.
		//
		// Why, measured: the dialog's .modal-body had max-height:none and
		// overflow-y:visible, so at one Cash slot it filled 959 of a 960px
		// screen and anything that grew the form — walk-in, Fund Transfer's
		// channel row, a rate breakdown — pushed the total off the bottom. The
		// operator was clicking Book without being able to see the price. The
		// stylesheet now scrolls the body; this puts the one figure that must
		// never scroll away outside the scrolling region.
		//
		// The NODE is moved, not re-created, so fields_dict.estimate and every
		// data-testid inside it are byte-identical for the fifteen E2E selector
		// sites that read them. Idempotent, and called at the TOP of
		// update_estimate — including its early-return paths — because frappe
		// re-renders sections on set_value / set_df_property and this must
		// survive that. Measured in a live dialog: set_df_property twice,
		// set_value and a full refresh() all leave the node where it was put.
		const anchor_estimate = () => {
			const node = dialog.fields_dict.estimate.$wrapper[0];
			const footer = dialog.$wrapper[0].querySelector(".modal-footer");
			if (node && footer && node.parentNode !== footer) footer.prepend(node);
		};

		// ⚠ THE DIALOG OUTLIVES ITSELF. Its async handlers keep landing after it
		// is hidden — measured 2026-09-05: a Customer Link's
		// `validate_link_and_fetch` resolves AFTER the booking, fires `change`,
		// and used to re-quote a cart that had just been emptied. The server
		// answered "Your cart is empty." and frappe put that raw error in the
		// operator's face after EVERY successful booking, over a board they
		// then could not click.
		//
		// So every async entry point checks this first. `_quote_settled` is set
		// TRUE on the way out, because it is the E2E's honest gate and a dialog
		// that will never quote again has, in the only sense that matters,
		// settled.
		const dialog_is_spent = () => {
			if (dialog._booked || !this.cart_count()) {
				dialog._quote_settled = true;
				return true;
			}
			return false;
		};

		const refresh_quote = () => {
			if (dialog_is_spent()) return Promise.resolve();
			const walk_in = cint(dialog.get_value("walk_in"));
			const picked = dialog.get_value("customer");
			const args = {
				// B46: the whole CART, priced in one request — the fee ordinal
				// advances inside the basket and the VAT footer is computed once
				// on the summed court share, which per-item quoting cannot do.
				items: JSON.stringify(this.cart_items()),
				// What staff can SEE decides the price. An untouched Percent
				// field reads 0, which is a real value the server honours as an
				// override (S11 as-built 5) — so this is always sent, and it is
				// why `customer` below never decides the money from this caller.
				discount_percent: flt(dialog.get_value("discount_percent")) || 0,
				// Backlog B27: a Free booking carries no platform fee, so the
				// quote must know the method or it would show a fee the server
				// then never charges.
				payment_method: dialog.get_value("payment_method") || "Cash",
			};
			// OMITTED rather than nulled in walk-in mode: a walk-in has no
			// account, so there is no membership to resolve, and sending an empty
			// value would still arm the staff gate for no reason.
			if (!walk_in && picked) args.customer = picked;

			// Settle flag + sequence token, the cbt_reschedule.js idiom. The
			// token stops a slow earlier response repainting over a newer one
			// (the board's own load() uses the same guard); the boolean is the
			// honest E2E gate, because an E2E that waited for "a get_quote
			// response" would hang whenever set_value lands on the value the
			// field already holds and fires no change event.
			dialog._quote_settled = false;
			const seq = (dialog._quote_seq = (dialog._quote_seq || 0) + 1);
			dialog._quote_promise = frappe
				.xcall(
					"court_booking_tech.api.board.get_desk_cart_quote",
					args,
					"GET" // xcall defaults to POST (S10 lesson 17a)
				)
				.then((res) => {
					if (seq !== dialog._quote_seq) return;
					quote = res;
					quote_failed = false;
					update_estimate();
					dialog._quote_settled = true;
				})
				.catch((err) => {
					if (seq !== dialog._quote_seq) return;
					// A run that overflows closing time is refused by the quote
					// AND by the insert — say so instead of showing a number
					// the server would never honour.
					console.error("CBT quote failed", err);
					quote = null;
					quote_failed = true;
					update_estimate();
					dialog._quote_settled = true;
				});
			return dialog._quote_promise;
		};

		const update_estimate = () => {
			// FIRST, before any early return: the node must be in the footer on
			// every path, including the two that render and stop below.
			anchor_estimate();
			const $wrapper = dialog.fields_dict.estimate.$wrapper;
			if (quote_failed) {
				$wrapper.html(
					`<div class="cbt-quote cbt-quote-fail" data-testid="quote-total">
						${__("Could not price this selection — it may run past closing time.")}
					</div>`
				);
				return;
			}
			if (!quote) {
				$wrapper.html(
					`<div class="cbt-quote"><div class="cbt-quote-line">
						<span>${__("Pricing…")}</span><span></span>
					</div></div>`
				);
				return;
			}
			const esc = frappe.utils.escape_html;
			// Section-18 (B4): every figure below is the SERVER's, computed with
			// the single-rounding formula the booking controller shares — so the
			// number on screen is the number that gets booked, by construction
			// rather than by two implementations agreeing.
			const disc = flt(quote.discount_percent);
			const total = flt(quote.total_amount);
			// Backlog B27: the platform's add-on, its OWN line (user ruling:
			// "show the booking fee so we are transparent"). 0 for every
			// tenant not billed Per Booking, so the line simply does not render.
			const fee = flt(quote.platform_fee);
			// Backlog B49: a continued session pays ONE fee — the fee line shows
			// the as-if figure and the discount line what the session saved.
			const waived = flt(quote.continuous_discount);
			// Backlog B39: store credit is a PAYMENT, so it is shown UNDER the
			// total as what the customer still owes — it never moves the total.
			const credit = flt(quote.credit_available);
			dialog.set_df_property("apply_credit", "hidden", credit <= 0);
			if (credit <= 0 && cint(dialog.get_value("apply_credit"))) {
				dialog.set_value("apply_credit", 0);
			}
			// B46: the SERVER's plan, never min(balance, total). take_credit
			// spends ONE credit document per BOOKING, so a cart of N rows can
			// settle LESS than the balance covers — see credits.plan_spend.
			const spend = cint(dialog.get_value("apply_credit"))
				? flt(quote.credit_spend)
				: 0;
			// B42: one line-item shape for every row, so the figures form a
			// column instead of ragged sentences. The testids are unchanged.
			const line = (label, value, testid) =>
				`<div class="cbt-quote-line"${testid ? ` data-testid="${testid}"` : ""}>
					<span>${label}</span><span>${value}</span>
				</div>`;
			const credit_line = credit
				? line(
						__("Store credit"),
						format_currency(credit, "PHP"),
						"credit-available"
				  ) +
				  (spend
						? `<div class="cbt-quote-due" data-testid="credit-due">
								<span class="cbt-quote-label">${__("Due now")}</span>
								<span class="cbt-quote-amount">${format_currency(
									total - spend,
									"PHP"
								)}</span>
							</div>`
						: "")
				: "";

			// B46: THE CART, as the SERVER merged it — one block per BOOKING,
			// not one per tap. Two adjacent hours on a court are one run, one
			// fee and one line, and that arithmetic is _normalise_cart's, so
			// nothing here re-derives it. This lives in the dialog BODY (which
			// scrolls) rather than the footer, because a twelve-court tournament
			// would otherwise push the total off the screen again — the exact
			// defect B42 measured and fixed.
			const items = quote.items || [];
			dialog.fields_dict.summary.$wrapper.find(".cbt-cart-lines").html(
				items
					.map((item) => {
						const segments = item.segments || [];
						const detail =
							segments.length > 1
								? segments
										.map(
											(segment) =>
												`<div class="cbt-cart-seg" data-testid="rate-breakdown">
													${cbt.fmt.timeRange(segment.start_time, segment.end_time)}${
													segment.label
														? " · " + esc(segment.label)
														: ""
												} · ${format_currency(segment.hourly_rate, "PHP")}/hr</div>`
										)
										.join("")
								: `<div class="cbt-cart-seg">${format_currency(
										item.hourly_rate,
										"PHP"
								  )}/hr × ${item.duration_hours}</div>`;
						return `<div class="cbt-cart-line" data-testid="cart-line"
								data-court="${esc(item.court)}" data-date="${esc(item.booking_date)}"
								data-start="${esc(item.start_time)}" data-end="${esc(item.end_time)}">
								<div class="cbt-cart-line-main">
									<div class="cbt-cart-line-where">${esc(item.court_name)}</div>
									<div class="cbt-cart-line-when">${moment(
										item.booking_date,
										"YYYY-MM-DD"
									).format("ddd, D MMM")} · ${cbt.fmt.timeRange(
							item.start_time,
							item.end_time
						)}</div>
									${detail}
								</div>
								<div class="cbt-cart-line-amount">${format_currency(
									item.total_amount,
									"PHP"
								)}</div>
								<button class="btn btn-xs btn-default cbt-cart-line-drop"
									data-action="drop-run" title="${__("Remove")}">✕</button>
							</div>`;
					})
					.join("")
			);

			$wrapper.html(
				`<div class="cbt-quote${spend ? " cbt-quote--paid" : ""}">
					${
						items.length > 1
							? line(
									__("Bookings"),
									// The COUNT, not the subtotal: with no
									// discount and no fee the subtotal IS the
									// total, and printing it twice makes the one
									// figure that matters look like two. The
									// portal's checkout says it the same way.
									items.length +
										(cint(quote.booking_fee_count)
											? " · " +
											  __("{0} booking fee(s)", [
													quote.booking_fee_count,
											  ])
											: ""),
									"cart-summary"
							  )
							: ""
					}
					${
						disc
							? line(
									__("Less {0}%", [disc]),
									"-" + format_currency(flt(quote.discount_amount), "PHP")
							  )
							: ""
					}
					${
						fee + waived
							? line(
									__("Booking fee"),
									"+" + format_currency(fee + waived, "PHP"),
									"booking-fee"
							  )
							: ""
					}
					${
						waived
							? line(
									__("Continuous booking discount"),
									"-" + format_currency(waived, "PHP"),
									"continuous-discount"
							  )
							: ""
					}
					<div class="cbt-quote-total" data-testid="quote-total">
						<span class="cbt-quote-label">${__("Total")}</span>
						<span class="cbt-quote-amount">${format_currency(total, "PHP")}</span>
					</div>
					${credit_line}
				</div>`
			);
		};

		// Membership lookup. The RESULT is shown, not just applied: a silent
		// number appearing in a money field is exactly how a failed lookup
		// becomes an invisibly mispriced booking.
		const sync_membership = () => {
			if (dialog_is_spent()) return;
			const customer = dialog.get_value("customer");
			const hint = dialog.fields_dict.member_hint.$wrapper;
			if (cint(dialog.get_value("walk_in"))) {
				// Section-13. A walk-in has no account, so no membership can
				// apply — SAY so rather than leaving the money area blank, and
				// force the discount back to 0 in case a member was picked
				// before the toggle was flipped (WYSIWYG money, S11 as-built 5).
				// Rendering the hint is also load-bearing for the E2E helpers,
				// which gate the dialog submit on this node existing.
				dialog.set_value("discount_percent", 0);
				hint.html(
					`<div class="text-muted small" data-testid="member-hint">${__(
						"Walk-in — no membership."
					)}</div>`
				);
				// Section-18 (B4): re-QUOTE, and explicitly rather than relying
				// on set_value's change event — set_value fires no change when
				// the field already holds the value (0 -> 0 is the common case
				// here), which would leave a stale price on screen. Toggling
				// walk-in also changes whether `customer` is sent at all. The
				// sequence token makes the resulting double-fire harmless.
				refresh_quote();
				return;
			}
			if (!customer) {
				hint.empty();
				return;
			}
			frappe
				.xcall(
					"court_booking_tech.membership.get_member_discount_for",
					{ company: board.company, customer: customer },
					"GET" // xcall defaults to POST (S10 lesson 17a)
				)
				.then((res) => {
					if (res.has_membership) {
						dialog.set_value("discount_percent", res.discount_percent);
						hint.html(
							`<div class="text-muted small" data-testid="member-hint">
								<span class="indicator green">${__("{0} member", [res.tier])}</span>
								${__("— {0}% applied", [res.discount_percent])}
							</div>`
						);
					} else {
						dialog.set_value("discount_percent", 0);
						hint.html(
							`<div class="text-muted small" data-testid="member-hint">${__(
								"No membership at this company."
							)}</div>`
						);
					}
					// Section-18 (B4): the discount the server must price with
					// has just landed in the field, so re-quote. Explicit for the
					// same reason as the walk-in branch above: picking the same
					// member twice writes an unchanged value and fires no change.
					refresh_quote();
				})
				.catch((err) => {
					console.error("CBT membership lookup failed", err);
					hint.html(
						`<div class="text-danger small" data-testid="member-hint">${__(
							"Membership lookup failed — check the discount before booking."
						)}</div>`
					);
				});
		};

		// Backlog B29: the channel list for the CURRENT method — refetched on
		// every method change (Cash → the cash drawers, Fund Transfer → GCash and
		// the banks, Free → nothing). `_channels_settled` is the E2E gate, the
		// `_quote_settled` idiom: false while a fetch is in flight, true once the
		// options have rendered — set on the failure path too, so a dead endpoint
		// fails an assertion instead of burning the timeout.
		const sync_channels = () => {
			if (dialog._booked) {
				dialog._channels_settled = true;
				return;
			}
			const method = dialog.get_value("payment_method") || "Cash";
			const control = dialog.fields_dict.payment_channel;
			dialog._channels_settled = false;
			const seq = (dialog._channels_seq = (dialog._channels_seq || 0) + 1);
			if (method === "Free") {
				control.df.options = [];
				control.refresh();
				dialog.set_value("payment_channel", "");
				dialog._channels_settled = true;
				return;
			}
			frappe
				.xcall(
					"court_booking_tech.api.channels.list_company_channels",
					{ company: board.company, payment_method: method },
					"GET"
				)
				.then((rows) => {
					if (seq !== dialog._channels_seq) return;
					control.df.options = (rows || []).map((r) => ({ value: r.name, label: r.label }));
					control.refresh();
					dialog.set_value("payment_channel", rows && rows.length ? rows[0].name : "");
					dialog._channels_settled = true;
				})
				.catch((err) => {
					if (seq !== dialog._channels_seq) return;
					console.error("CBT channel list failed", err);
					control.df.options = [];
					control.refresh();
					dialog._channels_settled = true;
				});
		};

		// B46: take a booking back OUT of the cart without closing the dialog —
		// the operator misheard a court, or the customer changed their mind
		// while the total was being read out. Delegated on the wrapper because
		// update_estimate rebuilds these rows on every quote.
		//
		// A run covers a CONTIGUOUS window, so removing it means dropping every
		// picked start inside [start, end) on that court and date. String
		// comparison is safe and is what the whole app uses for grid times:
		// they are zero-padded "HH:MM:SS", and a midnight end is "24:00:00",
		// which sorts ABOVE every real start exactly as it should.
		dialog.$wrapper.on("click", "[data-action='drop-run']", (event) => {
			const row = $(event.currentTarget).closest(".cbt-cart-line");
			const court = row.attr("data-court");
			const date = row.attr("data-date");
			const key = this.cart_key(court, date);
			const start = row.attr("data-start");
			const end = row.attr("data-end");
			const kept = (this.cart[key] || []).filter(
				(time) => !(time >= start && time < end)
			);
			if (kept.length) this.cart[key] = kept;
			else delete this.cart[key];
			this.paint_cart();
			if (!this.cart_count()) {
				// An empty cart has nothing to book and nothing to price —
				// closing is the honest end, not a dialog quoting ₱0.
				dialog.hide();
				return;
			}
			refresh_quote();
		});

		// B42: the scoping hook for this dialog's own rules. Nothing else on the
		// desk may pick them up.
		dialog.$wrapper.addClass("cbt-qb");
		// Call ORDER below is unchanged on purpose — the E2E gates
		// (_quote_settled, _channels_settled) are armed before the modal is
		// shown, and anchor_estimate is a guarded no-op if the footer is not
		// built yet, because update_estimate re-anchors on every quote anyway.
		anchor_estimate();
		update_estimate();
		refresh_quote();
		sync_channels();
		dialog.show();
	}

	accept_booking(booking) {
		frappe.confirm(
			__(
				"Confirm {0}? This marks the payment as verified and accepts any pending proofs.",
				[booking]
			),
			() => {
				frappe
					.call({
						method: "court_booking_tech.api.bookings.confirm_booking",
						args: { name: booking },
					})
					.then(() => {
						frappe.show_alert({
							message: __("{0} confirmed", [booking]),
							indicator: "green",
						});
						this.load();
					});
			}
		);
	}

	rate_rows(detail) {
		// Section-14: how the Amount above was arrived at, when it was not one
		// flat rate. The desk fields the "why is this ₱550 and not ₱400?"
		// question at the counter, so the answer belongs on the same screen —
		// and `detail.hourly_rate` cannot BE that answer: on a segmented
		// booking it is the blended average, a rate on no rule and on no line
		// of the statement.
		const segments = detail.rate_segments || [];
		if (segments.length < 2) return "";
		const esc = frappe.utils.escape_html;
		const lines = segments
			.map(
				(segment) =>
					`<div data-testid="rate-breakdown">${cbt.fmt.timeRange(
						segment.start_time,
						segment.end_time
					)}${segment.label ? " · " + esc(segment.label) : ""} ·
					${format_currency(segment.hourly_rate, "PHP")}/hr ×
					${segment.hours}: ${format_currency(segment.amount, "PHP")}</div>`
			)
			.join("");
		return `<dt>${__("Rate breakdown")}</dt><dd>${lines}</dd>`;
	}

	reschedule_rows(detail) {
		// Section-15: a moved booking explains itself. Without these a staff
		// member opening the cancelled half sees a bare "Cancelled" row with no
		// story, which reads as a mistake rather than a move.
		const esc = frappe.utils.escape_html;
		let html = "";
		if (detail.rescheduled_from) {
			html += `<dt>${__("Moved from")}</dt>
				<dd data-testid="rescheduled-from">${esc(detail.rescheduled_from)}</dd>`;
		}
		if (detail.rescheduled_to) {
			html += `<dt>${__("Moved to")}</dt>
				<dd data-testid="rescheduled-to">${esc(detail.rescheduled_to)}</dd>`;
		}
		return html;
	}

	reschedule_button(detail) {
		// Rendered exactly where api/bookings.reschedule_booking accepts. The
		// test is inlined rather than calling cbt.reschedule.can_move because
		// the dialog asset is loaded on DEMAND — it does not exist yet when
		// this HTML is built.
		const movable =
			["Reserved", "Confirmed"].includes(detail.booking_status) &&
			!detail.extended_from;
		return movable
			? `<button class="btn btn-default btn-sm" data-action="reschedule">
					🗓️ ${__("Reschedule…")}</button>`
			: "";
	}

	open_reschedule(booking) {
		// Loaded on demand (the cbt_branch.js Leaflet pattern) — the dialog has
		// no business in every desk page load.
		frappe.require("/assets/court_booking_tech/js/cbt_reschedule.js", () => {
			cbt.reschedule.open(booking, { on_done: () => this.load() });
		});
	}

	open_verification_dialog(booking) {
		frappe
			.call({
				method: "court_booking_tech.api.board.get_booking_detail",
				type: "GET",
				args: { booking: booking },
			})
			.then((r) => this.show_verification_dialog(r.message));
	}

	show_verification_dialog(detail) {
		const esc = frappe.utils.escape_html;
		const deadline = detail.verification_deadline_at || detail.reservation_expires_at;
		const clock_label = detail.verification_deadline_at
			? __("Verification deadline")
			: __("Reservation expires");

		const proofs_html = detail.proofs.length
			? detail.proofs.map((p) => this.render_proof_card(p)).join("")
			: `<div class="text-muted">${__("No proof uploaded yet.")}</div>`;

		// B29: what the newest pending receipt CLAIMS leads the pre-selection —
		// staff are looking at that receipt when they decide.
		const claimed = detail.proofs
			.filter((p) => p.status === "Pending" && p.payment_channel)
			.slice(-1)[0];
		const channel_default =
			(claimed && claimed.payment_channel) || detail.payment_channel || "";
		const channel_options = (detail.payment_channels || []).map((c) => ({
			value: c.name,
			label: c.label,
		}));
		if (
			detail.payment_channel &&
			!channel_options.some((o) => o.value === detail.payment_channel)
		) {
			channel_options.push({
				value: detail.payment_channel,
				label: `${detail.payment_channel_label || detail.payment_channel} (${__("disabled")})`,
			});
		}

		// B40: same "last pending receipt leads" rule the channel above uses.
		const referenced = detail.proofs
			.filter((p) => p.status === "Pending" && p.reference_no)
			.slice(-1)[0];
		const reference_default =
			(referenced && referenced.reference_no) ||
			(detail.proofs.filter((p) => p.reference_no).slice(-1)[0] || {}).reference_no ||
			"";

		const dialog = new frappe.ui.Dialog({
			title: __("Verify Payment — {0}", [detail.name]),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "detail",
					options: `
						<dl class="cbt-detail-grid">
							<dt>${__("Customer")}</dt><dd data-testid="detail-customer">${esc(
								detail.customer_name || detail.customer
							)}${
								detail.customer
									? ""
									: ` <span class="indicator gray">${__("walk-in")}</span>`
							}</dd>
							${
								// A walk-in's phone matters MOST on an unpaid hold:
								// it is the only way to chase the transfer.
								detail.customer_phone
									? `<dt>${__("Phone")}</dt><dd>${esc(detail.customer_phone)}</dd>`
									: ""
							}
							<dt>${__("Court")}</dt><dd>${esc(detail.court_name)} ·
								${moment(detail.booking_date, "YYYY-MM-DD").format("ddd, D MMM YYYY")}
								${cbt.fmt.timeRange(detail.start_time, detail.end_time)}</dd>
							<dt>${__("Amount")}</dt><dd>${format_currency(detail.total_amount, "PHP")}
								· ${esc(detail.payment_method || "")}${
									detail.payment_channel_label
										? ` · <span data-testid="detail-channel">${__("via")} ${esc(
												detail.payment_channel_label
										  )}</span>`
										: ""
								}</dd>
							${
								// B39: a booking settled with store credit says so.
								flt(detail.credit_applied)
									? `<dt>${__("Paid by credit")}</dt>
										<dd data-testid="detail-credit">${format_currency(
											detail.credit_applied,
											"PHP"
										)}</dd>`
									: ""
							}
							${this.rate_rows(detail)}
							${this.reschedule_rows(detail)}
								<dt>${esc(clock_label)}</dt>
							<dd>${
								deadline
									? `${frappe.datetime.str_to_user(deadline)}
										<span class="cbt-count" data-deadline="${deadline}"></span>`
									: "—"
							}</dd>
							${
								detail.rejection_count
									? `<dt>${__("Rejections")}</dt><dd>${detail.rejection_count}
										· ${__("a second rejection expires the booking")}</dd>`
									: ""
							}
						</dl>
						<div class="cbt-proof-list">${proofs_html}</div>
							<div style="margin-top:8px">${this.reschedule_button(detail)}</div>
						<div style="margin-top:8px">
							<button class="btn btn-default btn-sm" data-action="staff-upload">
								📤 ${__("Upload proof for customer")}
							</button>
							<input type="file" accept=".jpg,.jpeg,.png,.pdf" style="display:none"
								class="cbt-staff-proof-file">
						</div>
					`,
				},
				{
					// Backlog B29 (user ruling: "customer pick, staff can edit based
					// on the uploaded"). Pre-selected from the newest pending proof's
					// claim, else the booking's own channel; the options are the
					// company's enabled transfer channels, plus the current one if
					// it has since been disabled (history keeps its channel).
					fieldtype: "Select",
					fieldname: "payment_channel",
					label: __("Received via"),
					options: channel_options,
					default: channel_default,
					description: __(
						"The channel the money actually arrived through — change it if the receipt says otherwise."
					),
				},
				{
					// Backlog B40 (user ruling: optional, warn on a duplicate, never block).
					fieldtype: "Data",
					fieldname: "reference_no",
					label: __("Reference No"),
					default: reference_default,
					description: __(
						"The bank / GCash transaction reference on the receipt — optional, and yours to correct."
					),
					onchange: () => this.check_reference(dialog, detail),
				},
				{
					fieldtype: "HTML",
					fieldname: "reference_warning",
					options: "",
				},
				{
					fieldtype: "Select",
					fieldname: "rejection_reason",
					label: __("Rejection Reason"),
					options: [""].concat(REJECT_REASONS).join("\n"),
					description: __(
						'"Invalid / suspected fake" expires the booking immediately; other reasons grant ONE re-upload window. A second rejection of any kind expires it.'
					),
				},
			],
			primary_action_label: __("Accept & Confirm"),
			primary_action: () => {
				const method = detail.proofs.some((p) => p.status === "Pending")
					? "court_booking_tech.api.proofs.accept_proofs"
					: "court_booking_tech.api.bookings.confirm_booking";
				const payment_channel = dialog.get_value("payment_channel") || null;
				const args =
					method.indexOf("accept_proofs") > -1
						? { booking: detail.name, payment_channel }
						: { name: detail.name, payment_channel };
				// B40: the reference is written FIRST and awaited — a thrown accept
				// would otherwise discard what staff just typed. Two requests, not
				// one transaction: a saved reference on a booking that failed to
				// confirm is the ruling's "never block" working as intended.
				dialog.disable_primary_action();
				this.save_reference(dialog, detail)
					.then(() => frappe.call({ method, args }))
					.then(() => {
						dialog.hide();
						frappe.show_alert({
							message: __("{0} confirmed — payment verified", [detail.name]),
							indicator: "green",
						});
						this.load();
					})
					.catch(() => dialog.enable_primary_action());
			},
			secondary_action_label: __("Reject"),
			secondary_action: () => {
				const reason = dialog.get_value("rejection_reason");
				if (!reason) {
					frappe.msgprint(__("Pick a rejection reason first."));
					return;
				}
				frappe
					.call({
						method: "court_booking_tech.api.proofs.reject_proofs",
						args: { booking: detail.name, reason: reason },
					})
					.then((r) => {
						dialog.hide();
						const out = r.message;
						if (out.outcome === "Regrace") {
							frappe.show_alert({
								message: __("Proofs rejected — customer may re-upload until {0}", [
									frappe.datetime.str_to_user(out.reservation_expires_at),
								]),
								indicator: "orange",
							});
						} else {
							frappe.show_alert({
								message: __("Proofs rejected — booking expired"),
								indicator: "red",
							});
						}
						this.load();
					});
			},
		});

		const $wrap = dialog.fields_dict.detail.$wrapper;
		$wrap.find(".cbt-proof-thumb").on("click", (e) => {
			window.open($(e.currentTarget).attr("data-url"), "_blank");
		});
		$wrap.find("[data-action='reschedule']").on("click", () => {
			dialog.hide();
			this.open_reschedule(detail.name);
		});
		$wrap.find("[data-action='staff-upload']").on("click", () => {
			$wrap.find(".cbt-staff-proof-file").trigger("click");
		});
		$wrap.find(".cbt-staff-proof-file").on("change", (e) => {
			const file = e.target.files[0];
			if (!file) return;
			// B40: the desk's own upload path used to post a blank reference by
			// construction — it now carries whatever staff typed above it.
			this.upload_staff_proof(
				detail.name,
				file,
				dialog.get_value("reference_no")
			).then(() => {
				dialog.hide();
				frappe.show_alert({
					message: __("Proof uploaded"),
					indicator: "green",
				});
				this.load();
				this.open_verification_dialog(detail.name);
			});
		});

		dialog.show();
		this.tick_countdowns();
	}

	check_reference(dialog, detail) {
		const $wrap = dialog.fields_dict.reference_warning.$wrapper;
		const reference_no = (dialog.get_value("reference_no") || "").trim();
		if (!reference_no) {
			$wrap.empty();
			return Promise.resolve([]);
		}
		return frappe
			.call({
				method: "court_booking_tech.api.proofs.find_reference_duplicates",
				type: "GET",
				args: { booking: detail.name, reference_no: reference_no },
			})
			.then((r) => {
				this.render_reference_warning($wrap, r.message || []);
				return r.message || [];
			});
	}

	render_reference_warning($wrap, matches) {
		if (!matches.length) {
			$wrap.empty();
			return;
		}
		const esc = frappe.utils.escape_html;
		const rows = matches
			.map((m) => {
				const where = m.booking
					? `${esc(m.booking)} · ${esc(m.court_name || "")} ${
							m.booking_date
								? frappe.datetime.str_to_user(m.booking_date)
								: ""
					  } · ${format_currency(m.total_amount, "PHP")} · ${__(
							m.booking_status || ""
					  )}`
					: `${esc(m.open_play_session || "")} · ${__("open play")}`;
				return `<li>${where}</li>`;
			})
			.join("");
		$wrap.html(`
			<div class="alert alert-warning cbt-reference-warning"
				data-testid="reference-duplicate-warning">
				<strong>⚠ ${__("This reference is already on record here")}</strong>
				<ul>${rows}</ul>
				<div class="text-muted">${__(
					"You can still accept — check the receipt is not being reused."
				)}</div>
			</div>
		`);
	}

	save_reference(dialog, detail) {
		const typed = (dialog.get_value("reference_no") || "").trim();
		const stored = (
			(detail.proofs.filter((p) => p.reference_no).slice(-1)[0] || {})
				.reference_no || ""
		).trim();
		if (typed === stored) {
			return Promise.resolve(null);
		}
		return frappe.call({
			method: "court_booking_tech.api.proofs.set_proof_reference",
			args: { booking: detail.name, reference_no: typed },
		});
	}

	render_proof_card(proof) {
		const esc = frappe.utils.escape_html;
		const badge_color = { Pending: "orange", Accepted: "green", Rejected: "red" }[
			proof.status
		];
		const preview = proof.is_pdf
			? `<a class="cbt-proof-pdf" href="${esc(proof.file)}" target="_blank">PDF</a>`
			: `<img class="cbt-proof-thumb" src="${esc(proof.file)}"
				data-url="${esc(proof.file)}" alt="${__("Payment proof")}">`;
		return `
			<div class="cbt-proof-card" data-proof="${esc(proof.name)}">
				${preview}
				<div class="cbt-proof-meta">
					<span class="indicator-pill ${badge_color}">${__(proof.status)}</span>
					<span class="text-muted">· ${__(proof.source)}</span>
					${
						proof.reference_no
							? `<div class="ellipsis">${__("Ref")}: <b>${esc(proof.reference_no)}</b></div>`
							: ""
					}
					${
						proof.payment_channel_label
							? `<div class="ellipsis" data-testid="proof-channel">${__("via")} ${esc(
									proof.payment_channel_label
							  )}</div>`
							: ""
					}
					${proof.remarks ? `<div class="ellipsis text-muted">${esc(proof.remarks)}</div>` : ""}
					<div class="text-muted" style="font-size:var(--text-xs)">
						${frappe.datetime.str_to_user(proof.uploaded_at || "")}
						${
							proof.status === "Rejected" && proof.rejection_reason
								? ` · ${esc(proof.rejection_reason)}`
								: ""
						}
					</div>
				</div>
			</div>
		`;
	}

	upload_staff_proof(booking, file, reference_no) {
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("booking", booking);
		if ((reference_no || "").trim()) {
			form.append("reference_no", reference_no.trim());
		}
		return fetch(
			"/api/method/court_booking_tech.api.proofs.upload_proof",
			{
				method: "POST",
				headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
				body: form,
			}
		).then((resp) => {
			if (!resp.ok) {
				return resp.text().then((body) => {
					let message = __("Upload failed");
					try {
						const parsed = JSON.parse(body);
						const server_messages = JSON.parse(parsed._server_messages || "[]");
						if (server_messages.length) {
							message = JSON.parse(server_messages[0]).message;
						}
					} catch (e) {
						/* keep the generic message */
					}
					frappe.msgprint({ title: __("Upload failed"), indicator: "red", message });
					throw new Error(message);
				});
			}
			return resp.json();
		});
	}

	open_details_dialog(booking) {
		frappe
			.call({
				method: "court_booking_tech.api.board.get_booking_detail",
				type: "GET",
				args: { booking: booking },
			})
			.then((r) => this.show_details_dialog(r.message));
	}

	open_ban_dialog(detail) {
		// A ban is narrow ON PURPOSE (section-11): it stops this customer from
		// booking THIS company ONLINE. The dialog says so, because a staff
		// member who thinks they are barring someone from the building will
		// use it wrong and then distrust it.
		const dialog = new frappe.ui.Dialog({
			title: __("Ban {0}", [detail.customer_name || detail.customer]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "explainer",
					options: `<div class="text-muted small" style="margin-bottom:.5rem">
						${__(
							"They will no longer be able to book this company online. Your staff can still book them at the desk, they can still join open play, and their existing bookings are untouched. Other facilities are unaffected."
						)}
					</div>`,
				},
				{
					fieldtype: "Small Text",
					fieldname: "reason",
					label: __("Reason"),
					reqd: 1,
					description: __("Kept for audit — visible to your team, never to the customer."),
				},
			],
			primary_action_label: __("Ban Customer"),
			primary_action: (values) => {
				frappe
					.call({
						method: "court_booking_tech.api.bans.ban_customer",
						args: {
							company: detail.company,
							customer: detail.customer,
							reason: values.reason,
						},
					})
					.then((r) => {
						dialog.hide();
						const already = r.message && r.message.already_banned;
						frappe.show_alert({
							message: already
								? __("Already banned ({0})", [r.message.ban])
								: __("{0} banned", [detail.customer_name || detail.customer]),
							indicator: already ? "orange" : "red",
						});
					});
			},
		});
		dialog.show();
	}

	show_details_dialog(detail) {
		const esc = frappe.utils.escape_html;
		const can_extend = detail.booking_status === "Confirmed";
		// Rendered exactly where api/bookings.check_in accepts, and hidden once
		// it has happened — a button that silently no-ops is how staff stop
		// trusting the one they need.
		const can_check_in =
			["Confirmed", "Extended"].includes(detail.booking_status) &&
			!detail.checked_in_at;
		const dialog = new frappe.ui.Dialog({
			title: __("{0} — {1}", [detail.name, __(detail.booking_status)]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "detail",
					options: `
						<dl class="cbt-detail-grid">
							<dt>${__("Customer")}</dt><dd data-testid="detail-customer">${esc(
								detail.customer_name || detail.customer
							)}${
								detail.customer
									? ""
									: ` <span class="indicator gray">${__("walk-in")}</span>`
							}</dd>
							${
								detail.customer_phone
									? `<dt>${__("Phone")}</dt><dd>${esc(detail.customer_phone)}</dd>`
									: ""
							}
							<dt>${__("Court")}</dt><dd>${esc(detail.court_name)} ·
								${moment(detail.booking_date, "YYYY-MM-DD").format("ddd, D MMM YYYY")}
								${cbt.fmt.timeRange(detail.start_time, detail.end_time)}</dd>
							<dt>${__("Amount")}</dt><dd>${format_currency(detail.total_amount, "PHP")}
								· ${esc(detail.payment_method || "")}${
									detail.payment_channel_label
										? ` · <span data-testid="detail-channel">${__("via")} ${esc(
												detail.payment_channel_label
										  )}</span>`
										: ""
								}</dd>
							${
								// B39: a booking settled with store credit says so.
								flt(detail.credit_applied)
									? `<dt>${__("Paid by credit")}</dt>
										<dd data-testid="detail-credit">${format_currency(
											detail.credit_applied,
											"PHP"
										)}</dd>`
									: ""
							}
							${this.rate_rows(detail)}
							${
								detail.invoice_status
									? `<dt>${__("Billing")}</dt><dd>${esc(detail.invoice_status)}</dd>`
									: ""
							}
							${
								detail.extended_from
									? `<dt>${__("Extends")}</dt><dd>${esc(detail.extended_from)}</dd>`
									: ""
							}
							${this.reschedule_rows(detail)}
							${
								detail.checked_in_at
									? `<dt>${__("Checked in")}</dt>
										<dd data-testid="detail-checked-in">${frappe.datetime.str_to_user(
											detail.checked_in_at
										)}${
											detail.checked_in_by
												? ` · ${esc(detail.checked_in_by)}`
												: ""
									  }</dd>`
									: ""
							}
						</dl>
						<div style="display:flex; gap:6px; flex-wrap:wrap">
							${
								// Section-16: one click, and it is the only thing
								// standing between a paid booking and automatic
								// release — so it goes FIRST, before the reads.
								can_check_in
									? `<button class="btn btn-primary btn-sm" data-action="check-in">
											✓ ${__("Check in")}</button>`
									: ""
							}
							${
								detail.booking_status === "No Show"
									? `<button class="btn btn-default btn-sm" data-action="undo-no-show">
											${__("Undo no-show")}</button>`
									: ""
							}
							${
								detail.billing_doc
									? `<button class="btn btn-default btn-sm" data-action="print">
										🧾 ${__("Billing Statement")}</button>`
									: ""
							}
							<button class="btn btn-default btn-sm" data-action="open-form">
								${__("Open Booking")}</button>
							${
								// Section-13: a ban is per-ACCOUNT (S11). A walk-in
								// has none, so the action is not merely useless
								// here — it would name the wrong thing.
								detail.customer
									? `<button class="btn btn-default btn-sm" data-action="ban-customer">
											${__("Ban Customer…")}</button>`
									: ""
							}
							${this.reschedule_button(detail)}
							${
								// Section-26: the SERVER decided. A paid booking's
								// cancel is a refund — Company Admin only — and a
								// seat that may not do it reads the sentence where
								// the button would be, instead of a 403 after the
								// click.
								detail.can_cancel
									? `<button class="btn btn-danger btn-sm" data-action="cancel-booking">
											${detail.is_refund ? __("Cancel & Refund…") : __("Cancel Booking")}</button>`
									: `<span class="text-muted" data-testid="cancel-refusal"
											style="align-self:center">${esc(detail.cancel_refusal || "")}</span>`
							}
						</div>
					`,
				},
			],
			primary_action_label: can_extend ? __("Extend Session") : undefined,
			primary_action: can_extend
				? () => {
						dialog.hide();
						this.open_extend_dialog(detail);
				  }
				: undefined,
		});

		const $wrap = dialog.fields_dict.detail.$wrapper;
		$wrap.find("[data-action='print']").on("click", () => {
			// Same printview URL pattern as the booking form's button (S6).
			const url =
				"/printview?doctype=" +
				encodeURIComponent("CBT Booking Invoice") +
				"&name=" +
				encodeURIComponent(detail.billing_doc) +
				"&format=" +
				encodeURIComponent("CBT Billing Statement") +
				"&no_letterhead=1";
			window.open(url, "_blank");
		});
		$wrap.find("[data-action='reschedule']").on("click", () => {
			dialog.hide();
			this.open_reschedule(detail.name);
		});
		$wrap.find("[data-action='open-form']").on("click", () => {
			dialog.hide();
			frappe.set_route("Form", "CBT Court Booking", detail.name);
		});
		$wrap.find("[data-action='ban-customer']").on("click", () => {
			dialog.hide();
			this.open_ban_dialog(detail);
		});
		$wrap.find("[data-action='check-in']").on("click", () => {
			frappe
				.call({
					method: "court_booking_tech.api.bookings.check_in",
					args: { name: detail.name },
					freeze: true,
				})
				.then(() => {
					dialog.hide();
					frappe.show_alert({
						message: __("{0} checked in", [detail.name]),
						indicator: "green",
					});
					this.load();
				});
		});
		$wrap.find("[data-action='undo-no-show']").on("click", () => {
			frappe
				.call({
					method: "court_booking_tech.api.bookings.undo_no_show",
					args: { name: detail.name },
					freeze: true,
				})
				.then(() => {
					dialog.hide();
					frappe.show_alert({
						message: __("{0} is back on the board", [detail.name]),
						indicator: "green",
					});
					this.load();
				});
		});
		$wrap.find("[data-action='cancel-booking']").on("click", () => {
			const cancel = (reason, issue_credit, cause) =>
				frappe
					.call({
						method: "court_booking_tech.api.bookings.cancel_booking",
						args: {
							name: detail.name,
							reason: reason || undefined,
							issue_credit: issue_credit ? 1 : 0,
							cause: cause || undefined,
						},
					})
					.then(() => {
						dialog.hide();
						frappe.show_alert({
							message: issue_credit
								? __("{0} cancelled — store credit issued", [detail.name])
								: reason
								? __("{0} cancelled and refunded", [detail.name])
								: __("{0} cancelled", [detail.name]),
							indicator: "orange",
						});
						this.load();
					});
			if (detail.is_refund) {
				// Section-26: money OUT. The admin types WHY — it lands on the
				// billing document, the ledger's reversal line and the print.
				frappe.prompt(
					[
						{
							fieldtype: "HTML",
							options: `<p>${__(
								detail.booking_status === "No Show"
									? "{0} was already released, so no slot is freed — its payment is written off and its billing statement stops counting as revenue."
									: "{0} is PAID. Cancelling it is a refund: the slot becomes available again and its payment is reversed in the books.",
								[detail.name]
							)}</p>`,
						},
						// B53: the CAUSE decides whether cash may leave at all. The
						// server re-checks it — these options are only what this
						// seat may pick, never the authority.
						...((detail.cause_options || []).length > 1
							? [
									{
										fieldtype: "Select",
										fieldname: "cause",
										label: __("Cause"),
										options: detail.cause_options.join("\n"),
										default: detail.cause_options[0],
										reqd: 1,
									},
							  ]
							: []),
						...(detail.cash_refund_note
							? [
									{
										fieldtype: "HTML",
										options: `<p class="text-muted" data-testid="cash-refund-note">${frappe.utils.escape_html(
											detail.cash_refund_note
										)}</p>`,
									},
							  ]
							: []),
						{
							fieldtype: "Small Text",
							fieldname: "reason",
							label: __("Reason for the refund"),
							reqd: 1,
						},
						// Backlog B39: a walk-in has no account to hold credit, so
						// the choice is only offered where it can be honoured.
						...(detail.customer
							? [
									{
										fieldtype: "Check",
										fieldname: "issue_credit",
										label: __("Issue store credit instead of cash"),
										default: 0,
										description: __(
											"Spendable at this facility only. The books then show a reversal and a liability, not cash leaving the drawer."
										),
									},
							  ]
							: []),
					],
					(values) => cancel(values.reason, values.issue_credit, values.cause),
					__("Cancel & refund {0}", [detail.name]),
					__("Cancel & Refund")
				);
				return;
			}
			frappe.confirm(
				__("Cancel {0}? The slot becomes available again.", [detail.name]),
				() => cancel(null, 0)
			);
		});
		dialog.show();
	}

	open_extend_dialog(detail) {
		const dialog = new frappe.ui.Dialog({
			title: __("Extend {0}", [detail.name]),
			fields: [
				{
					fieldtype: "Int",
					fieldname: "slots",
					label: __("Additional Slots"),
					default: 1,
					reqd: 1,
					description: __("The extension starts on the next grid slot after {0}.", [
						cbt.fmt.timeShort(detail.end_time),
					]),
				},
				{
					fieldtype: "Select",
					fieldname: "payment_method",
					label: __("Payment Method"),
					options: ["Cash", "Fund Transfer", "Free"].join("\n"),
					default: "Cash",
					reqd: 1,
					change: () => sync_channels(),
				},
				{
					// B29: an extension is its own payment, so it names its own
					// channel — same picker as the quick-book dialog.
					fieldtype: "Select",
					fieldname: "payment_channel",
					label: __("Paid via"),
					options: [],
					depends_on: "eval:doc.payment_method!=='Free'",
				},
			],
			primary_action_label: __("Extend"),
			primary_action: (values) => {
				frappe
					.call({
						method: "court_booking_tech.api.bookings.extend_booking",
						args: {
							name: detail.name,
							slots: values.slots,
							payment_method: values.payment_method,
							payment_channel:
								values.payment_method === "Free"
									? null
									: values.payment_channel || null,
						},
					})
					.then((r) => {
						dialog.hide();
						frappe.show_alert({
							message: __("Extended — new booking {0}", [r.message]),
							indicator: "green",
						});
						this.load();
					});
			},
		});
		const sync_channels = () =>
			window.cbt_sync_channel_select(
				dialog,
				detail.company,
				dialog.get_value("payment_method")
			);
		sync_channels();
		dialog.show();
	}

	open_block_dialog() {
		if (!this.branch) {
			frappe.msgprint(__("Pick a branch first."));
			return;
		}
		// Section-21 (Backlog B12): From/To offer the branch's REAL grid times
		// instead of asking staff to type a clock value into a slider widget.
		// Nothing is lost by restricting them to grid boundaries — a block acts on
		// whole slots via interval overlap (cbt_slot_block.py::_warn_confirmed_
		// overlaps, slots._overlaps), so a window that starts mid-slot blocks
		// exactly the same chips as one that starts on the boundary. What IS
		// gained: `_validate_window`'s "End Time must be after Start Time" throw
		// becomes unreachable from this dialog, because To only ever offers ends
		// after the chosen From.
		//
		// The blank first option is load-bearing, not cosmetic — see
		// set_time_options for what ControlSelect does without it.
		const BLANK = { value: "", label: __("Select a time") };
		const CLOSED_MESSAGE = __(
			"No bookable slots on that date — the branch is closed, or it has no active courts."
		);
		let grid_starts = [];
		let grid_ends = [];

		const dialog = new frappe.ui.Dialog({
			title: __("Block Slots — {0}", [
				this.board ? this.board.branch_name : this.branch,
			]),
			fields: [
				{
					fieldtype: "Link",
					fieldname: "court",
					label: __("Court"),
					options: "CBT Court",
					description: __("Leave empty to close the WHOLE branch for this window."),
					get_query: () => ({ filters: { branch: this.branch } }),
				},
				{
					fieldtype: "Date",
					fieldname: "block_date",
					label: __("Date"),
					default: this.date,
					reqd: 1,
					// The grid is per DATE, and staff may block a day other than
					// the one the board is showing.
					change: () => reload_grid(),
				},
				{
					fieldtype: "Select",
					fieldname: "start_time",
					label: __("From"),
					options: [BLANK],
					reqd: 1,
					change: () => apply_end_options(),
				},
				{
					fieldtype: "Select",
					fieldname: "end_time",
					label: __("To"),
					options: [BLANK],
					reqd: 1,
				},
				{
					fieldtype: "Select",
					fieldname: "reason",
					label: __("Reason"),
					options: BLOCK_REASONS.join("\n"),
					default: "Maintenance",
					reqd: 1,
				},
				{ fieldtype: "Small Text", fieldname: "notes", label: __("Notes") },
			],
			primary_action_label: __("Block"),
			primary_action: (values) => {
				frappe
					.call({
						method: "court_booking_tech.api.bookings.create_block",
						args: {
							branch: this.branch,
							block_date: values.block_date,
							start_time: values.start_time,
							end_time: values.end_time,
							reason: values.reason,
							court: values.court || null,
							notes: values.notes,
						},
					})
					.then(() => {
						dialog.hide();
						frappe.show_alert({
							message: values.court
								? __("Court blocked")
								: __("Branch blocked for that window"),
							indicator: "red",
						});
						this.load();
					});
			},
		});

		// --- grid times ------------------------------------------------------

		const option = (value) => ({ value: value, label: cbt.time_label(value) });

		/**
		 * Fetch the branch's slot grid for the dialog's CURRENT date and rebuild
		 * both dropdowns.
		 *
		 * `_grid_settled` is the dialog's own gate (the `_quote_settled` idiom
		 * from cbt_reschedule.js): false while a fetch is in flight, true once its
		 * options have rendered. It is set on the FAILURE path too — an E2E gate
		 * that only settles on success waits out its full timeout on a dead
		 * network instead of failing where the fault is.
		 */
		const reload_grid = () => {
			const date = dialog.get_value("block_date");
			if (!date) return Promise.resolve();
			// Sequence token, same discipline as the board's own load(): a fast
			// second date change must never let the first response win the render.
			const seq = (dialog._grid_seq = (dialog._grid_seq || 0) + 1);
			dialog._grid_settled = false;
			return frappe
				.xcall(
					"court_booking_tech.slots.get_availability",
					{ branch: this.branch, date: date },
					"GET" // xcall defaults to POST (S10 lesson 17a)
				)
				.then((data) => {
					if (seq !== dialog._grid_seq) return;
					// The grid is BRANCH-level (slots.get_slot_grid takes a branch,
					// not a court), so every court row carries the same slots.
					// Union + dedupe anyway rather than reading courts[0]: a branch
					// whose first court is somehow slotless must not blank the
					// dialog for all the others.
					const starts = new Set();
					const ends = new Set();
					for (const court of data.courts || []) {
						for (const slot of court.slots || []) {
							starts.add(slot.start_time);
							ends.add(slot.end_time);
						}
					}
					// Zero-padded "HH:MM:SS" sorts and compares correctly as a
					// string — which is also what makes the To filter below a
					// one-liner.
					grid_starts = Array.from(starts).sort();
					grid_ends = Array.from(ends).sort();
					return set_time_options().then(() => {
						dialog._grid_settled = true;
					});
				})
				.catch((error) => {
					console.error("CBT slot grid lookup failed", error);
					if (seq !== dialog._grid_seq) return;
					grid_starts = [];
					grid_ends = [];
					return set_time_options().then(() => {
						dialog._grid_settled = true;
					});
				});
		};

		/**
		 * Rebuild the From options, then re-derive To.
		 *
		 * THE `set_value` AT THE END IS NOT OPTIONAL. ControlSelect.add_options
		 * forces `selectedIndex = 0` (select.js:133), and set_formatted_input then
		 * writes whatever the DOM ended up holding straight back INTO the model
		 * (select.js:55-59). So an options change that drops the chosen value
		 * silently rewrites it to the first option — no change event, no visible
		 * cue, and a block created for an hour nobody picked. Every options change
		 * in this dialog is therefore followed by an explicit value assertion, and
		 * `previous` is read BEFORE any set_df_property for the same reason.
		 */
		const set_time_options = () => {
			const previous = dialog.get_value("start_time");
			dialog.set_df_property(
				"start_time",
				"description",
				grid_starts.length ? "" : CLOSED_MESSAGE
			);
			dialog.set_df_property(
				"start_time",
				"options",
				[BLANK].concat(grid_starts.map(option))
			);
			return Promise.resolve(
				dialog.set_value("start_time", grid_starts.includes(previous) ? previous : "")
			).then(() => apply_end_options());
		};

		/**
		 * To offers only the ends AFTER the chosen From, so every window this
		 * dialog can express is one the controller accepts.
		 *
		 * Returned (not fired-and-forgotten) from From's `change`, so that
		 * `set_value('start_time', …)` resolves only once To has settled —
		 * frappe threads a change handler's return value through run_serially
		 * (base_control.js:239-245). That is what makes "set From, then set To"
		 * deterministic for the E2E files.
		 */
		const apply_end_options = () => {
			const start = dialog.get_value("start_time");
			const ends = start ? grid_ends.filter((end) => end > start) : grid_ends;
			const previous = dialog.get_value("end_time");
			dialog.set_df_property("end_time", "options", [BLANK].concat(ends.map(option)));
			return Promise.resolve(
				dialog.set_value("end_time", ends.includes(previous) ? previous : "")
			);
		};

		dialog._grid_settled = false;
		reload_grid();
		dialog.show();
	}
}
