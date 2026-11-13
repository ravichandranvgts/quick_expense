"""Install-time setup: roles, Journal Entry custom fields and permissions, workspace.

Everything here is idempotent: it only adds what is missing and never
overwrites a value a user has edited. Per-company defaults (Company GSTIN, cost
center, round off account) are read from the Company itself.
"""

import json

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import nowdate

from quick_expense.constants import APP_ROLES, JE_VOUCHER_TYPE
from quick_expense.overrides.custom_fields import CUSTOM_FIELDS

SETTINGS = "Quick Expense Settings"


def after_install():
	setup_all()


def after_migrate():
	setup_all()


def setup_wizard_complete(wizard_answers=None):
	# Frappe passes the wizard's answers; setup_all() doesn't need them. Without this
	# wrapper the wizard failed on any site where Quick Expense was installed first,
	# which is how Frappe Cloud creates sites.
	setup_all()


def setup_all():
	create_roles()
	create_custom_fields(CUSTOM_FIELDS, update=True)
	make_property_setters()
	grant_journal_entry_permissions()
	set_settings_defaults()
	create_workspace_and_cards()
	frappe.clear_cache(doctype=SETTINGS)


# ----------------------------------------------------------------------------
# global setup


def create_roles():
	for role in APP_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def make_property_setters():
	options = (frappe.get_meta("Journal Entry").get_field("voucher_type").options or "").split("\n")
	if JE_VOUCHER_TYPE not in options:
		options.append(JE_VOUCHER_TYPE)
		make_property_setter(
			"Journal Entry",
			"voucher_type",
			"options",
			"\n".join(options),
			"Text",
			validate_fields_for_doctype=False,
		)


# Journal Entry rights for the Quick Expense roles; the permission hooks in
# journal_entry_service restrict them to Quick Expense bills only. The Approver
# posts (submits) the bill; a Frappe Workflow on Journal Entry can add more steps.
JE_PERMISSIONS = {
	"Quick Expense User": dict(read=1, write=1, create=1, delete=1, print=1, email=1, report=1),
	"Quick Expense Approver": dict(
		read=1, write=1, create=1, delete=1, submit=1, cancel=1, amend=1, print=1, email=1, report=1
	),
}


def grant_journal_entry_permissions():
	from frappe.permissions import add_permission, update_permission_property

	for role, rights in JE_PERMISSIONS.items():
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.exists("Custom DocPerm", {"parent": "Journal Entry", "role": role, "permlevel": 0}):
			add_permission("Journal Entry", role, 0)
		for right, value in rights.items():
			update_permission_property("Journal Entry", role, 0, right, value, validate=False)

		# India Compliance's Journal Entry form looks up the company's GSTINs on load and
		# needs read on Company. Without it the lookup fails, Frappe skips the form's later
		# onload handlers, and a Quick Expense User's new bill never became Quick Expense.
		if not frappe.db.exists("Custom DocPerm", {"parent": "Company", "role": role, "permlevel": 0}):
			add_permission("Company", role, 0)
		update_permission_property("Company", role, 0, "read", 1, validate=False)


def set_settings_defaults():
	settings = frappe.get_single(SETTINGS)
	meta = frappe.get_meta(SETTINGS)
	changed = False
	for field in meta.fields:
		if (
			field.default is not None
			and field.fieldtype not in ("Table", "Section Break", "Tab Break")
			and settings.get(field.fieldname) in (None, "")
		):
			settings.set(field.fieldname, field.default if field.default != "Today" else nowdate())
			changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.flags.from_setup = True
		settings.save()


# ----------------------------------------------------------------------------
# workspace


QE = ["voucher_type", "=", "Quick Expense"]
THIS_MONTH = ["posting_date", "Timespan", "this month"]
NUMBER_CARDS = [
	("Quick Expense This Month", "Sum", "qe_grand_total", [QE, ["docstatus", "=", 1], THIS_MONTH]),
	("Quick Expense Eligible ITC", "Sum", "qe_total_eligible_itc", [QE, ["docstatus", "=", 1], THIS_MONTH]),
	(
		"Quick Expense Ineligible GST",
		"Sum",
		"qe_total_ineligible_gst",
		[QE, ["docstatus", "=", 1], THIS_MONTH],
	),
	("Quick Expense Drafts", "Count", None, [QE, ["docstatus", "=", 0]]),
	(
		"Quick Expense Pending 2B",
		"Count",
		None,
		[QE, ["docstatus", "=", 1], ["reconciliation_status", "in", ["Unreconciled", "Match Found"]]],
	),
]

# India Compliance's own GST reports and tools; Quick Expense adds no reports of its own
IC_SHORTCUTS = [
	("Purchase Reconciliation Tool (2A / 2B)", "DocType", "Purchase Reconciliation Tool", None),
	("GSTR-3B Report", "DocType", "GSTR 3B Report", "List"),
	("GST Purchase Register", "Report", "GST Purchase Register", None),
	("GSTR-3B Details", "Report", "GSTR-3B Details", None),
	("Journal Entries", "DocType", "Journal Entry", "List"),
]
# DocType shortcuts, not URL ones: Frappe opens a URL shortcut in a new browser tab.
# A plain {field: value} stats_filter becomes route_options, which filters the list
# and pre-fills a new form, so both open in the same tab as Quick Expense.
QE_ROUTE_FILTER = json.dumps({"voucher_type": JE_VOUCHER_TYPE})
QE_SHORTCUTS = {
	"Quick Expense Bills": "List",
	"New Quick Expense Bill": "New",
}


def fix_url_shortcuts():
	"""Turn the old URL shortcuts of an existing workspace into same-tab DocType shortcuts."""
	for label, doc_view in QE_SHORTCUTS.items():
		frappe.db.set_value(
			"Workspace Shortcut",
			{"parent": "Quick Expense", "parenttype": "Workspace", "label": label, "type": "URL"},
			{
				"type": "DocType",
				"link_to": "Journal Entry",
				"doc_view": doc_view,
				"url": None,
				"stats_filter": QE_ROUTE_FILTER,
			},
		)


def fix_number_card_labels():
	"""The workspace finds each card's data by its row label, not by the card name. The
	Drafts card replaced "Pending Approval" under a new name, but upgraded workspaces kept
	the old row label, so the Drafts card drew as an empty box."""
	for label, *_rest in NUMBER_CARDS:
		frappe.db.set_value(
			"Workspace Number Card",
			{"parent": "Quick Expense", "parenttype": "Workspace", "number_card_name": label},
			"label",
			label,
		)
		frappe.db.set_value("Number Card", label, "label", label)


def create_workspace_and_cards():
	doctype = "Journal Entry"
	for label, function, field, filters in NUMBER_CARDS:
		if frappe.db.exists("Number Card", label):
			if frappe.db.get_value("Number Card", label, "document_type") == doctype:
				continue
			frappe.delete_doc("Number Card", label, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Number Card",
				"name": label,
				"label": label,
				"type": "Document Type",
				"document_type": doctype,
				"function": function,
				"aggregate_function_based_on": field,
				"filters_json": json.dumps([[doctype, f, op, value, False] for f, op, value in filters]),
				"dynamic_filters_json": json.dumps(
					[[doctype, "company", "=", 'frappe.defaults.get_user_default("Company")']]
				),
				"is_public": 1,
				"is_standard": 0,
				"module": "Quick Expense",
				"show_percentage_stats": 0,
			}
		).insert(ignore_permissions=True)

	if frappe.db.exists("Workspace", "Quick Expense"):
		fix_url_shortcuts()
		fix_number_card_labels()
		return

	blocks = [
		{
			"id": "qe-h",
			"type": "header",
			"data": {"text": '<span class="h4"><b>Quick Expense</b></span>', "col": 12},
		}
	]
	for label, *_rest in NUMBER_CARDS:
		blocks.append(
			{"id": f"nc-{len(blocks)}", "type": "number_card", "data": {"number_card_name": label, "col": 4}}
		)
	shortcuts = [
		("Quick Expense Bills", "DocType", "Journal Entry", "List"),
		("New Quick Expense Bill", "DocType", "Journal Entry", "New"),
		("Settings", "DocType", "Quick Expense Settings", None),
		*IC_SHORTCUTS,
	]
	for label, *_rest in shortcuts:
		blocks.append(
			{"id": f"sc-{len(blocks)}", "type": "shortcut", "data": {"shortcut_name": label, "col": 3}}
		)

	workspace = frappe.get_doc(
		{
			"doctype": "Workspace",
			"name": "Quick Expense",
			"label": "Quick Expense",
			"title": "Quick Expense",
			"module": "Quick Expense",
			"public": 1,
			"icon": "expenses",
			"content": json.dumps(blocks),
			"number_cards": [{"number_card_name": label, "label": label} for label, *_rest in NUMBER_CARDS],
			"shortcuts": [
				{
					"label": label,
					"type": shortcut_type,
					"link_to": link_to,
					"doc_view": doc_view or "",
					"stats_filter": QE_ROUTE_FILTER if label in QE_SHORTCUTS else None,
					"report_ref_doctype": "Purchase Invoice" if shortcut_type == "Report" else None,
				}
				for label, shortcut_type, link_to, doc_view in shortcuts
			],
		}
	)
	workspace.flags.ignore_links = True
	workspace.insert(ignore_permissions=True)
