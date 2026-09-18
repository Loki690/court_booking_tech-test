"""
Court Booking Tech — THE CSS-IN-JS TRAP, made falsifiable (2026-09-05)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_board_assets

Both board pages carry their stylesheet in an UNTAGGED TEMPLATE LITERAL
(`CBT_BOARD_STYLES`, `CBT_OP_STYLES`). One stray backtick or dollar-brace ends
that string early and the board loads with NO STYLESHEET AT ALL — and the E2E
suite still passes, because it selects on classes.

The app's own comments have warned about this since section-23. It bit anyway,
on 2026-09-05, while writing the phone rules for Backlog B46: a backtick typed
inside a CSS *comment* to quote a selector ended the literal. `node --check`
reported the file VALID. What actually happened on the desk is worse than an
unstyled board — frappe evaluates a Page's script with `new Function(text)`,
which parses it as a function body, and it threw

    SyntaxError: Invalid left-hand side in assignment

so the page rendered nothing at all.

THE GUARD. Each file ends its style block with a sentinel comment. This test
reads the text between the file's FIRST TWO backticks — which is the literal, if
and only if nothing inside it closed the string early — and fails when the
sentinel is not there. A stray backtick moves the second delimiter earlier, the
sentinel falls outside, and this goes red at the cause.

It is a FILE test on purpose: no site, no browser, no seed. It runs in
milliseconds and it is the only thing between a one-character typo and a desk
screen that silently loses every colour it has.
"""

from pathlib import Path

from frappe.tests.utils import FrappeTestCase

APP_ROOT = Path(__file__).resolve().parent.parent

SENTINEL = "CBT_STYLE_BLOCK_END"

STYLE_FILES = {
	"CBT_BOARD_STYLES": APP_ROOT
	/ "court_booking_tech"
	/ "page"
	/ "cbt_court_board"
	/ "cbt_court_board.js",
	"CBT_OP_STYLES": APP_ROOT
	/ "court_booking_tech"
	/ "page"
	/ "cbt_open_play_board"
	/ "cbt_open_play_board.js",
}

# A selector that must survive, per file — belt and braces beside the sentinel:
# it names something the board would visibly lose, so a reader of a failure
# knows what broke rather than only that something did.
LIVE_RULES = {
	"CBT_BOARD_STYLES": [".cbt-matrix", ".cbt-slot", ".cbt-cart-bar", ".cbt-quote-total"],
	"CBT_OP_STYLES": [".cbt-op-court-card", ".cbt-op-queue-row", ".cbt-op-tv"],
}


def _style_block(source: str) -> str:
	"""The text between the file's first two backticks.

	That IS the stylesheet when nothing inside it closed the string early, and
	it is deliberately naive: the point is to reproduce what the JS parser sees,
	not to be clever about it.
	"""
	first = source.index("`")
	second = source.index("`", first + 1)
	return source[first + 1 : second]


class TestBoardStyleBlocks(FrappeTestCase):
	def test_every_style_block_reaches_its_sentinel(self):
		"""If this fails, a backtick or a dollar-brace ended the literal early
		and that board has NO stylesheet — look for one in a CSS comment."""
		for name, path in STYLE_FILES.items():
			with self.subTest(block=name):
				self.assertTrue(path.exists(), f"{path} is missing")
				block = _style_block(path.read_text(encoding="utf-8"))
				# assertTrue, not assertIn: assertIn prints the whole CONTAINER
				# on failure, and the container here is a 20 KB stylesheet — the
				# one line that says what to do would scroll away.
				self.assertTrue(
					SENTINEL in block,
					f"{name} ends before its {SENTINEL} sentinel — a backtick or "
					f"a dollar-brace inside {path.name} closed the template "
					"literal early, so that board now loads with NO stylesheet. "
					f"The block stops after {len(block)} chars, ending: "
					f"...{block[-90:]!r}",
				)

	def test_no_dollar_brace_inside_a_style_block(self):
		"""`${` would not merely truncate — it would INTERPOLATE, so the board
		would either throw on an undefined name or paste a value into the CSS."""
		for name, path in STYLE_FILES.items():
			with self.subTest(block=name):
				block = _style_block(path.read_text(encoding="utf-8"))
				self.assertNotIn(
					"${",
					block,
					f"{name} contains a dollar-brace: the stylesheet is being "
					"interpolated, which it must never be.",
				)

	def test_the_rules_the_boards_cannot_live_without_are_still_there(self):
		"""Names a failure rather than only detecting one. Each of these is a
		rule whose absence is visible on screen and invisible to the suite."""
		for name, path in STYLE_FILES.items():
			block = _style_block(path.read_text(encoding="utf-8"))
			for selector in LIVE_RULES[name]:
				with self.subTest(block=name, selector=selector):
					self.assertIn(
						selector,
						block,
						f"{selector} is no longer inside {name} — either it was "
						"deleted, or the literal is truncated above it.",
					)

	def test_the_sentinel_is_the_LAST_thing_in_the_block(self):
		"""The sentinel only proves the literal reached it. Requiring it to be
		last is what makes a backtick typed BELOW it fail too."""
		for name, path in STYLE_FILES.items():
			with self.subTest(block=name):
				block = _style_block(path.read_text(encoding="utf-8"))
				tail = block[block.rindex(SENTINEL) :]
				# Only the rest of that comment and whitespace may follow.
				self.assertNotIn(
					"{",
					tail,
					f"{name} has CSS after its {SENTINEL} sentinel — move the "
					"sentinel back to the end of the block.",
				)
