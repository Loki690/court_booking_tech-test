# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CBTBookingFeeTier(Document):
	# Validated as a SET by the parent (court_booking_tech.platform_fees
	# .validate_tiers): contiguity, order and the open-ended last tier are
	# properties of the whole table, not of one row.
	pass
