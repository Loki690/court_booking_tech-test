# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTOpenPlayQueue(Document):
	# Engine-owned (api/open_play.py). queue_position is meaningful for WAITING
	# rows only (1..n, no gaps); everyone else carries 0.
	pass
