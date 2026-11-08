"""Install-time and new-company setup (prompt section 4.3).

Everything here is idempotent: it only adds what is missing and never
overwrites a value a user has edited. Gaps it cannot fill safely (for example a
GST rate with no India Compliance Item Tax Template) are reported by the Setup
Check instead of being guessed.
"""

import json

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint, flt, nowdate

from quick_expense.constants import (
	APP_ROLES,
	DEFAULT_APPROVAL_RULES,
	JE_VOUCHER_TYPE,
	ROLE_ACCOUNTS_MANAGER,
	TAXABLE,
)
from quick_expense.overrides.custom_fields import CUSTOM_FIELDS

SETTINGS = "Quick Expense Settings"


def after_install():
	setup_all()


def after_migrate():
	setup_all()
	from quick_expense.overrides.compatibility_guard import check_compatibility

	check_compatibility()


def setup_all():
	create_roles()
	create_custom_fields(CUSTOM_FIELDS, update=True)
	make_property_setters()
	grant_journal_entry_permissions()
	set_settings_defaults()
	for company in frappe.get_all("Company", filters={"country": "India"}, pluck="name", order_by="creation"):
		setup_company(company)
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
# journal_entry_service restrict them to Quick Expense bills only
JE_PERMISSIONS = {
	"Quick Expense User": dict(read=1, write=1, create=1, delete=1, print=1, email=1, report=1),
	"Quick Expense Approver": dict(read=1, write=1, create=1, delete=1, print=1, email=1, report=1),
	"Finance Manager": dict(read=1, write=1, print=1, email=1, report=1),
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
# per company


def setup_company(company: str):
	from quick_expense.services.gstin_service import get_company_gstins

	settings = frappe.get_single(SETTINGS)
	company_doc = frappe.get_cached_doc("Company", company)

	if not any(row.company == company for row in settings.company_settings):
		gstins = get_company_gstins(company)
		settings.append(
			"company_settings",
			{
				"company": company,
				"default_company_gstin": gstins[0].gstin if gstins else "",
				"default_cost_center": company_doc.cost_center,
				"round_off_account": company_doc.round_off_account,
			},
		)
	for row in settings.company_settings:
		if row.company == company:
			row.reverse_charge_accounts = describe_reverse_charge_accounts(company)
			if not row.default_company_gstin:
				gstins = get_company_gstins(company)
				row.default_company_gstin = gstins[0].gstin if gstins else ""

	if not any(row.company == company for row in settings.allowed_accounts):
		for account in (company_doc.default_cash_account, company_doc.default_bank_account):
			if account and not frappe.get_cached_value("Account", account, "is_group"):
				mode = (
					"Cash" if frappe.get_cached_value("Account", account, "account_type") == "Cash" else None
				)
				settings.append(
					"allowed_accounts",
					{
						"company": company,
						"account": account,
						"mode_of_payment": mode
						if mode and frappe.db.exists("Mode of Payment", mode)
						else None,
					},
				)

	if not any(row.company == company for row in settings.approval_rules):
		for rule in DEFAULT_APPROVAL_RULES:
			row = {
				"company": company,
				"from_amount": rule["from_amount"],
				"to_amount": rule["to_amount"],
				"approval_levels_required": str(rule["levels"]),
			}
			for level, role in enumerate(rule["roles"], start=1):
				row[f"level_{level}_role"] = role
			settings.append("approval_rules", row)

	settings.flags.ignore_permissions = True
	settings.flags.from_setup = True
	settings.save()


def describe_reverse_charge_accounts(company) -> str:
	from india_compliance.gst_india.utils import get_gst_accounts_by_type

	accounts = get_gst_accounts_by_type(company, "Purchase Reverse Charge", throw=False)
	return "\n".join(
		f"{key.replace('_account', '').upper()}: {value}" for key, value in accounts.items() if value
	)


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
	("Quick Expense Pending Approval", "Count", None, [QE, ["qe_approval_status", "=", "Pending Approval"]]),
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
SHORTCUT_URLS = {
	"Quick Expense Bills": "/app/journal-entry?voucher_type=Quick%20Expense",
	"New Quick Expense Bill": "/app/journal-entry/new?voucher_type=Quick%20Expense",
}


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
		("Quick Expense Bills", "URL", None, None),
		("New Quick Expense Bill", "URL", None, None),
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
					"url": SHORTCUT_URLS.get(label),
					"report_ref_doctype": "Purchase Invoice" if shortcut_type == "Report" else None,
				}
				for label, shortcut_type, link_to, doc_view in shortcuts
			],
		}
	)
	workspace.flags.ignore_links = True
	workspace.insert(ignore_permissions=True)


# ----------------------------------------------------------------------------
# Setup Check


@frappe.whitelist()
def run_setup_check():
	frappe.only_for(("Accounts Manager", "System Manager"))
	issues = get_setup_issues()
	frappe.db.set_single_value(SETTINGS, "setup_check_result", json.dumps(issues))
	return issues


def get_setup_issues() -> list[dict]:
	from india_compliance.gst_india.utils import get_gst_accounts_by_type, is_api_enabled

	from quick_expense.services.approval_service import get_users_for_step
	from quick_expense.services.gstin_service import get_company_gstins
	from quick_expense.services.tax_engine import get_template_for_rate

	settings = frappe.get_single(SETTINGS)
	issues = []

	def add(company, gstin, area, message, link=None):
		issues.append(
			{"company": company, "gstin": gstin or "", "area": area, "message": message, "link": link}
		)

	if not is_api_enabled():
		add(
			"",
			"",
			_("GSTIN API"),
			_(
				"India Compliance API is not enabled. Supplier names will have to be typed manually and those entries need approval."
			),
			"/app/gst-settings",
		)
	if cint(settings.overrides_disabled):
		add(
			"",
			"",
			_("Journal Entry mode"),
			settings.overrides_disabled_reason
			or _("Report overrides are switched off by the compatibility guard."),
		)

	for company in frappe.get_all("Company", filters={"country": "India"}, pluck="name"):
		gstins = get_company_gstins(company)
		if not gstins:
			add(
				company,
				"",
				_("Company GSTIN"),
				_("No GST address with a GSTIN is linked to the company."),
				"/app/address/new",
			)
		if not any(row.company == company for row in settings.company_settings):
			add(
				company,
				"",
				_("Company Settings"),
				_("No Company Settings row. Save the company again or run migrate."),
			)
		if not any(row.company == company for row in settings.allowed_accounts):
			add(
				company,
				"",
				_("Cash and Bank"),
				_("No Allowed Cash or Bank account."),
				"/app/quick-expense-settings",
			)
		for account_type in ("Input", "Purchase Reverse Charge"):
			accounts = get_gst_accounts_by_type(company, account_type, throw=False)
			if not accounts or not (
				accounts.get("cgst_account") and accounts.get("sgst_account") and accounts.get("igst_account")
			):
				add(
					company,
					"",
					_("GST Accounts"),
					_("{0} CGST / SGST / IGST accounts are not set in GST Settings.").format(account_type),
					"/app/gst-settings",
				)
		if not (frappe.get_cached_value("Company", company, "round_off_account")):
			add(
				company, "", _("Round Off"), _("Company has no Round Off Account."), f"/app/company/{company}"
			)

		for treatment, rate in (
			(TAXABLE, 5),
			(TAXABLE, 18),
			("Exempted", 0),
			("Nil-Rated", 0),
			("Non-GST", 0),
		):
			if not get_template_for_rate(company, rate, treatment):
				add(
					company,
					"",
					_("Item Tax Template"),
					_("No Item Tax Template for {0}.").format(
						_("GST {0}%").format(rate) if treatment == TAXABLE else treatment
					),
					"/app/item-tax-template/new",
				)

		rules = [r for r in settings.approval_rules if r.company == company]
		if cint(settings.approval_required) and not rules:
			add(company, "", _("Approval"), _("No approval rules."))
		probe = frappe.new_doc("Journal Entry")
		probe.update({"company": company, "owner": "Administrator", "voucher_type": JE_VOUCHER_TYPE})
		steps = {
			(r.get(f"level_{level}_role") or "", r.get(f"level_{level}_user") or "")
			for r in rules
			for level in range(1, cint(r.approval_levels_required) + 1)
		}
		steps.add((ROLE_ACCOUNTS_MANAGER, ""))
		for gstin_row in gstins or [frappe._dict(gstin="")]:
			probe.company_gstin = gstin_row.gstin
			for role, user in sorted(steps):
				if not get_users_for_step(probe, {"role": role, "user": user}):
					add(
						company,
						gstin_row.gstin,
						_("Approvers"),
						_("Nobody can approve as {0}.").format(user or role),
						"/app/user",
					)
	return issues
