# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTBranchCourtCell(Document):
	# All floor-plan validation (indices within dims, duplicate/foreign court)
	# lives in the parent CBT Branch controller — rows are meaningless alone.
	pass
