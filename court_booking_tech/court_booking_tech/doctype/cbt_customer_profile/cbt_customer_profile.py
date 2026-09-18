# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""Cross-company customer asset (PLAN §4): no company field, no tenancy
hooks, and NO customer DocPerms — the portal reads/writes it exclusively
through api/profile.py (leak vector 4)."""

from frappe.model.document import Document


class CBTCustomerProfile(Document):
	pass
