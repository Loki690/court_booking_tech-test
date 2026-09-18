# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTOpenPlayCourt(Document):
	# Court membership rules (belongs to the session's branch, active, no
	# duplicates) live in the parent CBT Open Play Session controller — a row
	# is meaningless on its own.
	pass
