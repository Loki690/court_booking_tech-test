"""
Court Booking Tech — the suite must run every test it defines (Backlog B54)

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_suite_collection

WHY THIS MODULE EXISTS. B54 was filed on the reading "`--module ...test_reports` runs 3 tests of
~43 and prints OK". Measured 2026-09-10, that is FALSE — all 45 run. `TestRunner.iterRun`
(frappe/testing/runner.py) yields ONE SUITE PER CATEGORY and the caller runs each separately, so a
module run prints one `Ran N tests … OK` block PER CATEGORY. `test_reports` prints `Ran 42` then
`Ran 3`; `tail` shows only the second. `scripts/run_all_tests.sh` already sums the blocks and says
so in a comment — the knowledge existed and was not connected.

HOW A TEST LANDS IN A CATEGORY (frappe/testing/discovery.py::_add_module_tests). `IntegrationTestCase`
and `UnitTestCase` are matched as ISINSTANCE patterns, so they hit anywhere in the MRO. Everything
else falls to `unspecified-category`, which is then upgraded to `old-frappe-test-class-category`
only if a DIRECT base is literally NAMED `FrappeTestCase`. That name match is why `test_reports`
splits: `TestGetMyCompany(FrappeTestCase)` is upgraded and its six siblings on `ReportTestCase`
are not. The split is harmless. Only the reading was wrong.

WHAT WOULD ACTUALLY BE DANGEROUS, and what these tests pin:

  1. A category being FILTERED OUT. `_add_module_tests` drops any test whose category is absent
     from `cfg.selected_categories`. That list defaults empty AND `TestParameters` — the dataclass
     the `bench run-tests` CLI populates — has no `selected_categories` field at all, so today the
     CLI cannot arm it. If a future frappe ships a non-empty default, 529 of this app's tests leave
     every per-module run in silence.
  2. A MODULE FAILING TO IMPORT. `tests/run_all.py` used to swallow that into a printed `SKIP` and
     the lane still printed OK. It now re-raises (B54), and test 3 here is the independent check.

These tests touch no DocType and write nothing, so they subclass `UnitTestCase` rather than the
deprecated `FrappeTestCase` — which also keeps this module out of the very bucket frappe warns is
going away in v17.
"""

import importlib
import io
import pkgutil
import unittest
import warnings

from frappe.testing.config import TestConfig
from frappe.testing.discovery import discover_module_tests
from frappe.testing.runner import TestRunner
from frappe.tests import UnitTestCase

APP = "court_booking_tech"


def _module_names():
	"""Every `test_*` module under tests/, from pkgutil — never a hand-rolled list."""
	import court_booking_tech.tests as pkg

	for info in pkgutil.iter_modules(pkg.__path__):
		if info.name.startswith("test_"):
			yield f"{pkg.__name__}.{info.name}"


def _frappe_categories(module_name, selected=None):
	"""What frappe's discovery would RUN for one module, per category.

	Never calls iterRun(), so no preparation hook fires and nothing touches the database.
	"""
	runner = TestRunner(stream=io.StringIO(), cfg=TestConfig(selected_categories=list(selected or [])))
	discover_module_tests(module_name, runner, APP)
	return {name: suite.countTestCases() for name, suite in runner.per_app_categories[APP].items()}


def _import_all():
	"""Import every test module, REPORTING failures instead of raising them.

	A propagated ImportError here is unreadable: frappe's runner prints tb_locals, and the
	locals hold whole TestSuite reprs (measured: 360 KB for one bad import).
	"""
	modules, broken = {}, []
	for name in _module_names():
		try:
			modules[name] = importlib.import_module(name)
		except Exception as e:
			broken.append(f"{name}: {type(e).__name__}: {e}")
	return modules, broken


class TestSuiteCollection(UnitTestCase):
	def test_every_module_runs_every_test_it_defines(self):
		"""Whatever unittest can load, frappe must actually run — in however many blocks."""
		losses = []
		with warnings.catch_warnings():
			# Collecting every module trips ~440 FrappeTestCase deprecation warnings.
			warnings.simplefilter("ignore")
			modules, _broken = _import_all()  # a broken import is test 3's to report
			for name, module in modules.items():
				defined = unittest.TestLoader().loadTestsFromModule(module).countTestCases()
				categories = _frappe_categories(name)
				runs = sum(categories.values())
				if runs != defined:
					losses.append(f"{name}: defines {defined}, frappe runs {runs} {categories}")

		self.assertEqual(
			losses,
			[],
			"frappe's discovery is dropping tests. Each line is 'module: defines N, runs M'. "
			"The drop is one of the three `continue`s in "
			"frappe/testing/discovery.py::_add_module_tests:\n  " + "\n  ".join(losses),
		)

	def test_the_category_filter_is_off_by_default_and_bites_when_armed(self):
		"""A positive control: prove the comparison above can fail, not just that it passes."""
		default = TestConfig()
		self.assertFalse(default.selected_categories, "frappe now filters categories by default")
		self.assertFalse(default.tests, "frappe now filters test names by default")
		self.assertIsNone(default.case, "frappe now filters to a single case by default")

		module = f"{APP}.tests.test_reports"
		with warnings.catch_warnings():
			warnings.simplefilter("ignore")
			unfiltered = sum(_frappe_categories(module).values())
			filtered = sum(_frappe_categories(module, ["old-frappe-test-class-category"]).values())

		# Deliberately no hard-coded 42/3: a new report test must not turn this red.
		self.assertGreater(filtered, 0, "the armed category matched nothing — the control is dead")
		self.assertLess(
			filtered,
			unfiltered,
			"arming selected_categories dropped nothing, so the guard above proves nothing",
		)

	def test_no_test_module_is_silently_dropped(self):
		"""An import failure raises HERE, where it is a red test with a name.

		`tests/run_all.py` re-raises for the same reason (B54): before that it printed
		`SKIP <module>` and the lane still reported OK with that module's tests absent.
		"""
		with warnings.catch_warnings():
			warnings.simplefilter("ignore")
			modules, broken = _import_all()
			empty = [n for n in modules if sum(_frappe_categories(n).values()) == 0]

		self.assertEqual(broken, [], "test modules that FAIL TO IMPORT:\n  " + "\n  ".join(broken))
		self.assertEqual(empty, [], f"test modules that collect nothing at all: {empty}")

	def test_the_full_lane_sees_every_module_this_guard_checks(self):
		"""The lane runs `--module ...tests.run_all`, so run_all's own discovery is the contract."""
		from court_booking_tech.tests import run_all

		with warnings.catch_warnings():
			warnings.simplefilter("ignore")
			lane, lane_error = 0, None
			try:
				lane = run_all.load_tests(unittest.TestLoader(), None, None).countTestCases()
			except Exception as e:
				lane_error = f"{type(e).__name__}: {e}"
			modules, _broken = _import_all()
			guarded = sum(
				unittest.TestLoader().loadTestsFromModule(m).countTestCases() for m in modules.values()
			)

		# Raised OUTSIDE the except so the chained traceback (and its TestSuite locals) stays out.
		self.assertIsNone(lane_error, f"run_all could not build the lane suite: {lane_error}")
		self.assertEqual(
			lane,
			guarded,
			f"run_all collects {lane} but this guard only checks {guarded}. run_all also walks "
			"doctype/*/ and seeds/ — if a test module appeared there, extend _module_names().",
		)
