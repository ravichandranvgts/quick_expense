app_name = "quick_expense"
app_title = "Quick Expense"
app_publisher = "VGTS"
app_description = "One-screen GST expense entry for ERPNext with India Compliance"
app_email = "256799630+ravichandranvgts@users.noreply.github.com"
app_license = "mit"

required_apps = ["frappe", "erpnext", "india_compliance"]

add_to_apps_screen = [
	{
		"name": "quick_expense",
		"logo": "/assets/erpnext/images/erpnext-logo.svg",
		"title": "Quick Expense",
		"route": "/app/quick-expense",
	}
]

app_include_css = "/assets/quick_expense/css/quick_expense.css"

doctype_js = {
	"GSTR 3B Report": "public/js/override_banner.js",
	"Purchase Reconciliation Tool": "public/js/override_banner.js",
	"Journal Entry": "public/js/journal_entry.js",
}
doctype_list_js = {"Journal Entry": "public/js/journal_entry_list.js"}

after_install = "quick_expense.services.setup_service.after_install"
after_migrate = "quick_expense.services.setup_service.after_migrate"
setup_wizard_complete = "quick_expense.services.setup_service.setup_all"

# India Compliance report overrides (Journal Entry mode) are applied per process
before_request = ["quick_expense.overrides.ic_reports.apply_patches"]
before_job = ["quick_expense.overrides.ic_reports.apply_patches"]

permission_query_conditions = {
	"Journal Entry": "quick_expense.services.journal_entry_service.get_permission_query_conditions",
}
has_permission = {
	"Journal Entry": "quick_expense.services.journal_entry_service.has_permission",
}

doc_events = {
	"Journal Entry": {
		"onload": "quick_expense.overrides.doc_events.journal_entry_onload",
		"before_validate": "quick_expense.overrides.doc_events.journal_entry_before_validate",
		"validate": "quick_expense.overrides.doc_events.journal_entry_validate",
		"on_update": "quick_expense.overrides.doc_events.journal_entry_on_update",
		"before_submit": "quick_expense.overrides.doc_events.journal_entry_before_submit",
		"on_submit": "quick_expense.overrides.doc_events.journal_entry_on_submit",
		"before_cancel": "quick_expense.overrides.doc_events.journal_entry_before_cancel",
		"on_cancel": "quick_expense.overrides.doc_events.journal_entry_on_cancel",
		"on_trash": "quick_expense.overrides.doc_events.journal_entry_on_trash",
	},
	"Company": {
		"on_update": "quick_expense.overrides.doc_events.company_on_update",
	},
}

scheduler_events = {
	"daily": [
		"quick_expense.services.approval_service.escalate_pending",
		"quick_expense.overrides.compatibility_guard.daily",
	],
	"monthly": ["quick_expense.services.notification_service.send_2b_follow_up"],
}

before_tests = "quick_expense.tests.setup.before_tests"

export_python_type_annotations = True
