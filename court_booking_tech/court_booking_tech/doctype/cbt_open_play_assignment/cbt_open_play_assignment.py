# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTOpenPlayAssignment(Document):
	# Engine-owned (api/open_play.py). ends_at is a SERVER timestamp — the
	# board renders countdowns from a clock offset, never its own clock.
	pass
