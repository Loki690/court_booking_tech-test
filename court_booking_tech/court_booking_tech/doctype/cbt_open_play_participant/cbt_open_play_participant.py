# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTOpenPlayParticipant(Document):
	# Engine-owned (api/open_play.py): fees, payment status and the billing
	# link are written under the session's row lock. Rows are mutated IN PLACE
	# — invoices and proofs point at these row names.
	pass
