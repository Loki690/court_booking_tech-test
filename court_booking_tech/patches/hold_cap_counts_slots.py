# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Backlog B35 (2026-09-04, user ruling): `max_active_proof_holds_per_customer`
stopped counting BOOKINGS and started counting SLOTS, and its default rose
5 -> 8.

A JSON `default` only ever fills a Singles row that does not exist yet. Every
site already installed has the old default MATERIALISED in `tabSingles` as 5 —
so without this patch the unit change alone would tighten them from "5 bookings"
to "5 slots", which is strictly less than they have today and would refuse a
four-hour booking plus anything else.

Deliberately narrow: it moves the value ONLY when it is exactly 5, the number
this app shipped. A platform admin who has tuned the knob to anything else has
made a decision, and a migration that overwrites a tuned figure is worse than
one that does nothing. Idempotent — after the first run the value is 8 and the
guard stops matching.
"""

import frappe
from frappe.utils import cint

OLD_DEFAULT = 5
NEW_DEFAULT = 8
FIELD = "max_active_proof_holds_per_customer"


def execute():
	current = frappe.db.get_single_value("CBT Platform Settings", FIELD)
	# None = never written (a fresh install reads the JSON default and needs
	# nothing); anything other than the old default is a deliberate choice.
	if current is None or cint(current) != OLD_DEFAULT:
		return
	frappe.db.set_single_value("CBT Platform Settings", FIELD, NEW_DEFAULT)
