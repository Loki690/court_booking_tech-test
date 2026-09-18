"""
Court Booking Tech — what STAFF read on screen carries no internal references

Run:  bench --site dev.localhost run-tests --module court_booking_tech.tests.test_ui_text

WHY THIS EXISTS. The user opened a court, set it to continuous fee, and the help
text under the checkbox began *"Backlog B49:"*. A backlog ID, a section number or
a PLAN reference means nothing to someone running a court — it is internal
bookkeeping leaking onto a form a customer's booking depends on. Measured
2026-09-11: **43 of 211** CBT field descriptions leaked one.

It is the same defect class as an HTML comment shipping to the browser, and it
has the same property: **nothing can catch it except a rule like this one**, and
it comes back the moment someone writes a new field while thinking in row IDs.

⚠ This reads the MIGRATED meta, not the JSON on disk — what a person actually
sees. A description fixed in the file but not migrated still fails here, which is
correct: the form is what matters.
"""

import re
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

# Each pattern is something a court operator has no way to interpret.
INTERNAL_REFS = (
	(re.compile(r"\bBacklog\b", re.I), "the word 'Backlog'"),
	(re.compile(r"\bB\d{1,3}\b"), "a backlog id like B49"),
	(re.compile(r"\bsection-\d+", re.I), "a section reference"),
	(re.compile(r"\bPLAN\s*§", re.I), "a PLAN reference"),
	(re.compile(r"\bS\d+\s+as-built\b", re.I), "an as-built reference"),
)


def _cbt_doctypes():
	return frappe.get_all(
		"DocType",
		filters={"module": "Court Booking Tech", "istable": ("in", (0, 1))},
		pluck="name",
	)


def _offences(attr):
	found = []
	for doctype in _cbt_doctypes():
		for field in frappe.get_meta(doctype).fields:
			text = (getattr(field, attr, None) or "").strip()
			if not text:
				continue
			for pattern, what in INTERNAL_REFS:
				if pattern.search(text):
					found.append(f"{doctype}.{field.fieldname} ({attr}) contains {what}: {text[:110]}")
					break
	return found


class TestNoInternalRefsOnScreen(FrappeTestCase):
	def test_no_field_description_leaks_an_internal_reference(self):
		offences = _offences("description")
		self.assertEqual(
			offences,
			[],
			"Field help text is read by STAFF and means nothing to them if it cites our "
			"bookkeeping. Move the reference into docs/sections and say what the field "
			"DOES:\n  " + "\n  ".join(offences),
		)

	def test_no_field_label_leaks_an_internal_reference(self):
		offences = _offences("label")
		self.assertEqual(
			offences, [], "Field labels leaking an internal reference:\n  " + "\n  ".join(offences)
		)

	def test_the_guard_can_actually_fail(self):
		"""A positive control: the patterns must match a real offender."""
		sample = "Backlog B49: a session that continues onto this court (section-24)."
		matched = [what for pattern, what in INTERNAL_REFS if pattern.search(sample)]
		self.assertGreaterEqual(len(matched), 2, f"patterns did not fire on {sample!r}")
		self.assertFalse(
			[
				what
				for pattern, what in INTERNAL_REFS
				if pattern.search("A session that continues onto this court. Off by default.")
			],
			"the patterns fire on clean text — they would block honest descriptions",
		)


if __name__ == "__main__":
	unittest.main()
