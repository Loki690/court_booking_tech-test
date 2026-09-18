# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from court_booking_tech.tenancy import PLATFORM_ROLES

ROLE_MAP = {
	"Company Admin": "CBT Company Admin",
	"Company Staff": "CBT Company Staff",
}
MANAGED_ROLES = set(ROLE_MAP.values())


class CBTCompanyUser(Document):
	def validate(self):
		# Leak vector 2: a binding with a blank company link would be visible
		# to every tenant under User Permissions — belt and suspenders on reqd.
		if not self.company:
			frappe.throw(_("Company is required on a company user binding."))
		if self.user in ("Administrator", "Guest"):
			frappe.throw(_("Administrator and Guest cannot be bound to a company."))
		if PLATFORM_ROLES & set(frappe.get_roles(self.user)):
			frappe.throw(
				_(
					"{0} holds a platform role (System Manager / CBT Platform Admin) and cannot "
					"be bound to a company — the binding's User Permission would clamp their "
					"platform-wide visibility to one tenant."
				).format(self.user)
			)

	def on_update(self):
		self.sync_user_access()

	def on_trash(self):
		self.remove_user_access()

	def sync_user_access(self):
		"""Idempotent: exactly the mapped CBT role + exactly one User Permission
		(allow=CBT Company, for_value=this company) on the bound user."""
		target_role = ROLE_MAP[self.company_role]
		user = frappe.get_doc("User", self.user)
		current_roles = {r.role for r in user.roles}

		stale_roles = (MANAGED_ROLES - {target_role}) & current_roles
		if stale_roles or target_role not in current_roles:
			user.set("roles", [r for r in user.roles if r.role not in stale_roles])
			if target_role not in current_roles:
				user.append("roles", {"role": target_role})
			user.save(ignore_permissions=True)

		has_target_permission = False
		for perm in frappe.get_all(
			"User Permission",
			filters={"user": self.user, "allow": "CBT Company"},
			fields=["name", "for_value"],
		):
			if perm.for_value == self.company:
				has_target_permission = True
			else:
				frappe.delete_doc("User Permission", perm.name, ignore_permissions=True)

		if not has_target_permission:
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": self.user,
					"allow": "CBT Company",
					"for_value": self.company,
					"apply_to_all_doctypes": 1,
				}
			).insert(ignore_permissions=True)

	def remove_user_access(self):
		"""Idempotent teardown of everything sync_user_access grants."""
		if frappe.db.exists("User", self.user):
			user = frappe.get_doc("User", self.user)
			kept = [r for r in user.roles if r.role not in MANAGED_ROLES]
			if len(kept) != len(user.roles):
				user.set("roles", kept)
				user.save(ignore_permissions=True)

		for perm_name in frappe.get_all(
			"User Permission",
			filters={"user": self.user, "allow": "CBT Company"},
			pluck="name",
		):
			frappe.delete_doc("User Permission", perm_name, ignore_permissions=True)
