# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
Backlog B29 (2026-08-27): give every existing company its default Cash + GCash
channels, the platform its own pair, and stamp the kind's default channel on
every payment row that pre-dates channels — so the by-channel reports and the
Tenant Ledger have no "unallocated" history on a site that upgrades.

Idempotent (payment_channels.ensure_all): a company that already has ANY
channel is left alone (a deliberately disabled GCash is not re-created), and
only rows with an empty channel are touched.
"""

from court_booking_tech.payment_channels import ensure_all


def execute():
	ensure_all()
