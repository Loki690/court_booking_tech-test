app_name = "court_booking_tech"
app_title = "Court Booking Tech"
app_publisher = "BYTEUNITY SOLUTIONS & SERVICES CORP."
app_description = "Multi-tenant SaaS court booking platform for many facility companies"
app_email = "byteunitycorp@gmail.com"
app_license = "mit"

# Fixtures
# --------
# CBT roles ship as fixtures. The filter is an explicit in-list so a future
# `bench export-fixtures` can never vacuum foreign (frappe/erpnext/hrms) roles
# into this app's fixtures.
fixtures = [
	{
		"dt": "Role",
		"filters": [
			[
				"role_name",
				"in",
				[
					"CBT Platform Admin",
					"CBT Company Admin",
					"CBT Company Staff",
					"CBT Customer",
				],
			]
		],
	},
	# Section-9 transactional mail (section-19 adds the third, Backlog B22 the
	# fourth). Shipped as fixtures (not hard-coded strings) so the wording
	# lives in ONE editable JSON — but that JSON is force-imported on every
	# migrate (sync_fixtures → import_doc → import_file_by_path(force=True)),
	# so a desk edit does NOT survive; reword in the fixture file (2026-08-27).
	# Explicit in-list filter, never a bare "Email Template": a later
	# export-fixtures must not vacuum every template on the site into this app
	# (the S1 as-built 5 rule for roles).
	{
		"dt": "Email Template",
		"filters": [
			[
				"name",
				"in",
				[
					"CBT Proof Rejected",
					"CBT Booking Confirmed",
					"CBT Booking Rescheduled",
					"CBT Account Already Exists",
				],
			]
		],
	},
	# Section-23 (Backlog B14): the CBT Hub's number-card row. Same explicit
	# in-list filter as above, and for a sharper reason here — fixture install
	# goes through `import_doc`, which DELETES any same-named doc before
	# inserting. That is why every name carries the "CBT " prefix: a card named
	# plain "Memberships" would silently destroy another app's (or a user's)
	# card of that name on every migrate, and a bare {"dt": "Number Card"}
	# filter would then vacuum foreign cards back into this app.
	#
	# The user-facing `label` stays unprefixed — it is what the Hub renders,
	# and it is the string the Workspace's `number_cards` child rows match on
	# (frappe/.../blocks/block.js matches the content block's number_card_name
	# against the CHILD ROW'S LABEL, not against the doc name).
	#
	# `module` is deliberately EMPTY and `is_standard` 0: Number Card's own
	# list-permission hook ANDs "module in allowed_modules OR module is null",
	# so naming a module risks the cards vanishing for exactly the audience
	# they are for — a CBT Company Admin who is not a System Manager.
	{
		"dt": "Number Card",
		"filters": [
			[
				"name",
				"in",
				[
					"CBT Active Bookings",
					"CBT Pending Proofs",
					"CBT Memberships",
				],
			]
		],
	},
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "court_booking_tech",
# 		"logo": "/assets/court_booking_tech/logo.png",
# 		"title": "Court Booking Tech",
# 		"route": "/court_booking_tech",
# 		"has_permission": "court_booking_tech.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------
# Section-21 (Backlog B12): replace frappe's three-slider Air-Datepicker Time
# widget with the browser's native <input type="time">, for every Time field on
# the site. THE FIRST app_include_* hook in this app — keep the file lean, it
# loads on every desk page.
#
# This is a DESK hook, not an app-wide one (www/desk.py:38 -> desk.html): the
# customer portal is served by the website layer and is untouched, which is fine
# because it never asks for a time. It IS site-wide across APPS, though — every
# other app installed here gets the control too, which was verified harmless
# before shipping (neither ph_payroll's nor fleet_mgt's E2E suites touch a Time
# widget or a datepicker).
#
# Section-25 (Backlog B29) adds the second file: the payment-channel Select
# filler that four dialogs on three different pages share. A page script's
# `window.` global only exists on its own page — the open play board's Add
# Players dialog threw on the court board's copy — so it is desk-wide by hook.
# ?v=<version>-<content hash> on every include (2026-09-04): returning desk
# staff kept stale CSS/JS after a deploy. Hooks are redis-cached — a deploy
# must `bench clear-cache` (migrate does) or the OLD stamp keeps serving.
from court_booking_tech.assets_version import cbt_asset_url

#
# Backlog B45 adds the third, and it is the only one the PORTAL loads too
# (templates/includes/cbt_portal_head.html): cbt_time_format.js is the app's one
# client-side time language, so the desk board and the customer's grid cannot
# say different things about the same hour. It is listed FIRST because
# cbt_time_control.js reads it — but that file resolves `cbt.fmt` at CALL time,
# so this order is documentation, not a dependency.

app_include_js = [
	cbt_asset_url("/assets/court_booking_tech/js/cbt_time_format.js"),
	cbt_asset_url("/assets/court_booking_tech/js/cbt_time_control.js"),
	cbt_asset_url("/assets/court_booking_tech/js/cbt_payment_channels.js"),
]

# Section-23 (Backlog B14): the design-token layer. Same DESK-only reach and
# same site-wide-across-apps caveat as the line above, which is exactly why
# that file declares CUSTOM PROPERTIES AND NOTHING ELSE — no element selectors,
# no frappe-variable overrides, no component rules. The two board pages and the
# portal stylesheet are the consumers; the portal loads it a second time from
# templates/includes/cbt_portal_head.html, because app_include_css never
# reaches the website layer.
app_include_css = cbt_asset_url("/assets/court_booking_tech/css/cbt_theme.css")

# include js, css files in header of web template
# web_include_css = "/assets/court_booking_tech/css/court_booking_tech.css"
# web_include_js = "/assets/court_booking_tech/js/court_booking_tech.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "court_booking_tech/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "court_booking_tech/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# Portal templates stamp their asset links and print the app version through
# these (templates/includes/cbt_portal_head.html + cbt_portal_foot.html).
jinja = {
	"methods": [
		"court_booking_tech.assets_version.cbt_asset_url",
		"court_booking_tech.assets_version.cbt_asset_version",
		"court_booking_tech.assets_version.cbt_app_version",
	],
}

# Installation
# ------------

# before_install = "court_booking_tech.install.before_install"
# after_install = "court_booking_tech.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "court_booking_tech.uninstall.before_uninstall"
# after_uninstall = "court_booking_tech.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "court_booking_tech.utils.before_app_install"
# after_app_install = "court_booking_tech.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "court_booking_tech.utils.before_app_uninstall"
# after_app_uninstall = "court_booking_tech.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "court_booking_tech.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "court_booking_tech.notifications.get_notification_config"

# Permissions
# -----------
# Tenant scoping, defense-in-depth layer 2 (layer 1 = User Permissions synced
# by CBT Company User; layer 3 = tenancy.require_company_access in every
# whitelisted API). All functions live in the single tenancy choke point.

permission_query_conditions = {
	"CBT Company": "court_booking_tech.tenancy.company_query",
	"CBT Company User": "court_booking_tech.tenancy.company_user_query",
	"CBT Branch": "court_booking_tech.tenancy.branch_query",
	"CBT Court": "court_booking_tech.tenancy.court_query",
	"CBT Court Booking": "court_booking_tech.tenancy.booking_query",
	"CBT Slot Block": "court_booking_tech.tenancy.slot_block_query",
	"CBT Payment Proof": "court_booking_tech.tenancy.payment_proof_query",
	"CBT Booking Invoice": "court_booking_tech.tenancy.booking_invoice_query",
	"CBT Open Play Session": "court_booking_tech.tenancy.open_play_session_query",
	"CBT Membership": "court_booking_tech.tenancy.membership_query",
	"CBT Customer Ban": "court_booking_tech.tenancy.customer_ban_query",
	"CBT Platform Statement": "court_booking_tech.tenancy.platform_statement_query",
	"CBT Payment Channel": "court_booking_tech.tenancy.payment_channel_query",
	"CBT Customer Credit": "court_booking_tech.tenancy.customer_credit_query",
	# Backlog B38: the ONE non-CBT doctype here. frappe's standard_queries maps
	# User -> user_query, which is a frappe.get_list, so this reaches every User
	# Link on the desk as well as /app/user (section-13 addendum).
	"User": "court_booking_tech.tenancy.user_query",
}

has_permission = {
	"CBT Company": "court_booking_tech.tenancy.company_has_permission",
	"CBT Company User": "court_booking_tech.tenancy.company_user_has_permission",
	"CBT Branch": "court_booking_tech.tenancy.branch_has_permission",
	"CBT Court": "court_booking_tech.tenancy.court_has_permission",
	"CBT Court Booking": "court_booking_tech.tenancy.booking_has_permission",
	"CBT Slot Block": "court_booking_tech.tenancy.slot_block_has_permission",
	"CBT Payment Proof": "court_booking_tech.tenancy.payment_proof_has_permission",
	"CBT Booking Invoice": "court_booking_tech.tenancy.booking_invoice_has_permission",
	"CBT Open Play Session": "court_booking_tech.tenancy.open_play_session_has_permission",
	"CBT Membership": "court_booking_tech.tenancy.membership_has_permission",
	"CBT Customer Ban": "court_booking_tech.tenancy.customer_ban_has_permission",
	"CBT Platform Statement": "court_booking_tech.tenancy.platform_statement_has_permission",
	"CBT Payment Channel": "court_booking_tech.tenancy.payment_channel_has_permission",
	"CBT Customer Credit": "court_booking_tech.tenancy.customer_credit_has_permission",
	"User": "court_booking_tech.tenancy.user_has_permission",
}

# Document Events
# ---------------
# Billing engine (section-6, PLAN §6): every booking gets its invoice at
# creation; every doc.save re-derives the invoice's status/amounts. The
# frappe.db.set_value status flips (expiry sweep, proof rejection, dead-hold
# upload) call billing.sync_invoice_for_booking explicitly — set_value fires
# no doc_events.

doc_events = {
	"CBT Court Booking": {
		"after_insert": "court_booking_tech.billing.on_booking_after_insert",
		"on_update": [
			"court_booking_tech.billing.on_booking_update",
			# Section-9: the booking-confirmed mail rides the SAME seam as the
			# invoice flip, so "money verified" can never notify without also
			# updating the document (and vice versa).
			"court_booking_tech.notifications.on_booking_update",
		],
	},
	# Belt-and-suspenders default role for portal users (PLAN §7, section-8):
	# Portal Settings default_role covers stock signup; this covers social
	# login first-visits and admin/API-created Website Users.
	"User": {
		"after_insert": "court_booking_tech.customer.on_user_after_insert",
	},
}

# Portal signup (section-8, PLAN §2 D9)
# -------------------------------------
# The ONLY leak-proof seam around stock sign_up: covers api v1, api v2 AND
# `cmd=` form posts. The wrapper delegates to core by direct import.
override_whitelisted_methods = {
	"frappe.core.doctype.user.user.sign_up": "court_booking_tech.api.signup.sign_up",
}

# Login-page signup panel -> link-card to /signup (NO <form> — frappe's
# login.js hard-binds .form-signup submit and would post token-less).
signup_form_template = "court_booking_tech/templates/signup_link_card.html"

# Portal routes (section-9)
# -------------------------
# The solo `court_booking` app ALREADY ships www/book.html and
# www/my-bookings.html, and D3 has both apps coexisting on one site — the
# winner would otherwise be decided by app order in apps.txt. Route rules are
# resolved BEFORE any www file (website/path_resolver.resolve_path ->
# resolve_from_map), so mapping the public URLs onto CBT-prefixed page files
# keeps PLAN §5's printed deep link (/book?c=&b=) working AND makes resolution
# deterministic. Same lesson as the "CBT Hub" workspace / cbt-court-board page.
website_route_rules = [
	{"from_route": "/book", "to_route": "cbt-book"},
	{"from_route": "/my-bookings", "to_route": "cbt-my-bookings"},
	{"from_route": "/my-bookings/<name>", "to_route": "cbt-booking-detail"},
	{"from_route": "/billing-statement", "to_route": "cbt-billing-statement"},
]

# The marketplace is the site's front door FOR SHOPPERS (PLAN §7, D5) — but a
# global `home_page` hook (or Website Settings.home_page) would also redirect
# desk users to /find-court after login (get_home_page short-circuits the
# "me"->"desk" remap once any hook/setting resolves). This per-user hook returns
# find-court only for guests and portal customers, and None for System Users so
# frappe's own fallback still lands staff on the desk. (Regression caught by the
# E2E admin-login fixture — do NOT reintroduce a global home_page here.)
get_website_user_home_page = "court_booking_tech.customer.website_user_home_page"

standard_portal_menu_items = [
	{"title": "Find a Court", "route": "/find-court", "role": ""},
	{"title": "My Bookings", "route": "/my-bookings", "role": "CBT Customer"},
	{"title": "My Profile", "route": "/my-profile", "role": "CBT Customer"},
]

# Scheduled Tasks
# ---------------
# Base expiry clock (PLAN §5): per-minute sweep. Tests NEVER wait on cron —
# they call tasks.expire_reservations() directly (monkeypatched clock), and
# E2E uses the allow_tests-gated tasks.run_expiry_sweep() trigger.

scheduler_events = {
	"cron": {
		"* * * * *": [
			"court_booking_tech.tasks.expire_reservations",
		],
	},
}

# Testing
# -------

# before_tests = "court_booking_tech.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "court_booking_tech.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "court_booking_tech.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "court_booking_tech.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["court_booking_tech.utils.before_request"]
# after_request = ["court_booking_tech.utils.after_request"]

# Job Events
# ----------
# before_job = ["court_booking_tech.utils.before_job"]
# after_job = ["court_booking_tech.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"court_booking_tech.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

