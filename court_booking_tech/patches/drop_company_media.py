# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt

import frappe

DOCTYPE = "CBT Company Media"


def execute():
	"""Retire CBT Company Media (section-29).

	Removing the folder alone leaves the DocType row and its table on every
	bench that already migrated, so the list view keeps working and the desk
	keeps offering a form nothing renders.
	"""
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	# Attachments belong to rows that are about to stop existing. The FILES
	# themselves stay: a photo carried into the new gallery shares the blob.
	frappe.db.sql(
		"""update `tabFile`
		   set attached_to_doctype = null, attached_to_name = null,
		       attached_to_field = null
		   where attached_to_doctype = %s""",
		DOCTYPE,
	)
	frappe.delete_doc("DocType", DOCTYPE, force=True, ignore_missing=True)
	frappe.db.sql_ddl("drop table if exists `tabCBT Company Media`")
