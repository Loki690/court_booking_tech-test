# Court Booking Tech for Frappe

Multi-tenant SaaS court booking platform: byteunitycorp hosts the system and onboards many
facility companies (subscription or percentage cut). Each company gets a reservation/booking
system, open play management, and a print-only billing document in its own name (VAT-INC 12% or
NON-VAT); customers get one account to find and book courts anywhere, branches ordered
nearest-first. Standalone Frappe app — no ERPNext dependency.

## Installation

```bash
bench get-app https://github.com/byteunitycorp/court_booking_tech.git --branch version-16
bench --site [your-site] install-app court_booking_tech
bench --site [your-site] migrate
```

## Seed Data (Staging Only)

Not yet implemented. Planned: `bench --site [your-site] execute court_booking_tech.seeds.seed_test_data.seed_all`
— idempotent demo tenants/branches/courts/bookings, never for production.

## Features

**Status: planning.** Nothing is implemented yet. The full design and phased roadmap live in the
docs repo: `frappe-bench/court_booking_tech/docs/PLAN.md`.

## Testing

```bash
bench --site [your-site] run-tests --app court_booking_tech
```

## License

MIT
