"""
Court Booking Tech — Run All Tests (single invocation)

Usage from inside the frappe container:
    cd /workspace/frappe_docker/development/frappe-bench
    bench --site dev.localhost run-tests --module court_booking_tech.tests.run_all

Suite-level runs go through the logging harness on the host instead:
    bash court_booking_tech/scripts/run_all_tests.sh   (docs repo)

This auto-discovers every test_*.py under:
  - court_booking_tech/tests/                          (unit + integration)
  - court_booking_tech/court_booking_tech/doctype/*/   (doctype tests)
  - court_booking_tech/seeds/                          (seed smoke)

New tests are picked up automatically — no manual registration needed.
"""

import importlib
import os
import unittest


def load_tests(loader, tests, pattern):
	"""Called by unittest to discover tests in this module."""
	suite = unittest.TestSuite()
	total = 0

	def add_dir(dirpath, module_prefix):
		nonlocal total
		if not os.path.isdir(dirpath):
			return
		for filename in sorted(os.listdir(dirpath)):
			if not filename.startswith("test_") or not filename.endswith(".py"):
				continue
			module_name = f"{module_prefix}.{filename[:-3]}"
			try:
				mod = importlib.import_module(module_name)
			except Exception as e:
				# A module that cannot import is a FAILURE, never a skip (B54): the old
				# `SKIP` print left the lane green with that module's tests absent.
				raise ImportError(f"{module_name} failed to import: {e}") from e
			mod_suite = loader.loadTestsFromModule(mod)
			count = mod_suite.countTestCases()
			if count > 0:
				suite.addTests(mod_suite)
				total += count

	tests_dir = os.path.dirname(__file__)
	pkg_dir = os.path.normpath(os.path.join(tests_dir, os.pardir))

	# 1. tests/ directory
	add_dir(tests_dir, "court_booking_tech.tests")

	# 2. court_booking_tech/doctype/*/test_*.py (doctype tests, module dir)
	doctype_dir = os.path.join(pkg_dir, "court_booking_tech", "doctype")
	if os.path.isdir(doctype_dir):
		for dt_folder in sorted(os.listdir(doctype_dir)):
			add_dir(
				os.path.join(doctype_dir, dt_folder),
				f"court_booking_tech.court_booking_tech.doctype.{dt_folder}",
			)

	# 3. seeds/test_*.py (seed smoke tests)
	add_dir(os.path.join(pkg_dir, "seeds"), "court_booking_tech.seeds")

	print(f"  Court Booking Tech: {total} tests discovered")
	return suite
