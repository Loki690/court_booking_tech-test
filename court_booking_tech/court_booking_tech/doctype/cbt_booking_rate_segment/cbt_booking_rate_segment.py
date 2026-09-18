# Copyright (c) 2026, BYTEUNITY SOLUTIONS & SERVICES CORP. and contributors
# For license information, please see license.txt
"""
CBT Booking Rate Segment (section-14) — the booking's OWN pricing snapshot.

One row per priced stretch of the booking (contiguous slots at the same rate
are merged). Written by the booking controller from
court_booking_tech.pricing.build_rate_segments and never edited by hand: the
whole table is read-only on the form, and a staff rate override CLEARS it
rather than editing it.

It exists because rate rules are mutable and invoice amounts re-derive from the
BOOKING on every sync (S6) — so a booking that did not carry its own segments
would silently re-price itself the day someone edited the court.
"""

from frappe.model.document import Document


class CBTBookingRateSegment(Document):
	pass
