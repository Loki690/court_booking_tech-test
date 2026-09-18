# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Court Rate Rule (section-14) — a peak/off-peak window on CBT Court.

Deliberately behaviour-free. A child row cannot see its siblings, and every
meaningful check here is a CROSS-ROW one (no same-tier overlap), so validation
lives on the parent in cbt_court.py::_validate_rate_rules. Resolution lives in
court_booking_tech.pricing — the one seam quote, controller and board share.
"""

from frappe.model.document import Document


class CBTCourtRateRule(Document):
	pass
