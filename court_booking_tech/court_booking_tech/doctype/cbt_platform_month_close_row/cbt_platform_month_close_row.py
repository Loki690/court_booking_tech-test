# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""One tenant's frozen figures inside a CBT Platform Month Close (Backlog
B21(b)). Every column mirrors a CBT Platform Revenue report column; the parent
copies them at close time and refuses edits afterwards."""

from frappe.model.document import Document


class CBTPlatformMonthCloseRow(Document):
	pass
