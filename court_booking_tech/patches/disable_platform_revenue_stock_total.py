# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Section-11: turn OFF the stock total row on CBT Platform Revenue.

frappe's standard-file importer treats `add_total_row` as a USER preference and
deliberately preserves the existing DB value on every import
(`frappe.modules.import_file.ignore_values["Report"]`). The value in the
report's JSON therefore only ever applies to a FRESH install — an existing site
keeps whatever it was created with, no matter how many times it migrates.

The report builds its own total (see cbt_platform_revenue._total_row) because
the stock one sums EVERY numeric column, including the commission RATE: three
tenants rendered a platform-wide "3.333%" that is not a real number anywhere,
and the two totals together double-counted the money columns.

Idempotent, and safe on a site where it is already off.
"""

import frappe

REPORT = "CBT Platform Revenue"


def execute():
	if not frappe.db.exists("Report", REPORT):
		return
	if not frappe.db.get_value("Report", REPORT, "add_total_row"):
		return
	frappe.db.set_value("Report", REPORT, "add_total_row", 0)
