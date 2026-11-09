"""Quick Expense no longer has its own approval engine or per-company settings.

Bills are posted with the standard Submit button (or a Frappe Workflow on
Journal Entry), and defaults come from the Company. This removes the approval
fields from Journal Entry, the settings child tables and their values, and
renames the "Pending Approval" number card. Draft bills that were in approval
stay drafts.
"""

import frappe

JOURNAL_ENTRY_FIELDS = (
	"qe_gst_account_override_reason",
	"qe_warnings",
	"qe_approval_section",
	"qe_approval_status",
	"qe_approval_level",
	"qe_approval_levels_required",
	"qe_current_approver_role",
	"qe_current_approver_user",
	"qe_cb_7",
	"qe_submitted_for_approval_on",
	"qe_last_escalated_on",
	"qe_forced_approval_reasons",
	"qe_posting_error",
	"qe_approval_chain",
	"qe_approval_history",
)
CHILD_DOCTYPES = (
	"Quick Expense Approval History",
	"Quick Expense Approval Rule",
	"Quick Expense Company Setting",
	"Quick Expense Allowed Account",
	"Quick Expense User Default",
	"Quick Expense GST Lock Date",
)
SETTINGS_KEPT = ("enable_quick_expense", "round_grand_total", "use_gstin_api", "monthly_2b_follow_up_email")
OLD_CARD, NEW_CARD = "Quick Expense Pending Approval", "Quick Expense Drafts"


def execute():
	for fieldname in JOURNAL_ENTRY_FIELDS:
		if name := frappe.db.exists("Custom Field", {"dt": "Journal Entry", "fieldname": fieldname}):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)

	for doctype in CHILD_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
		frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")

	frappe.db.delete("Singles", {"doctype": "Quick Expense Settings", "field": ("not in", SETTINGS_KEPT)})

	rename_number_card()
	frappe.clear_cache(doctype="Journal Entry")


def rename_number_card():
	if not frappe.db.exists("Number Card", OLD_CARD):
		return
	if frappe.db.exists("Number Card", NEW_CARD):
		frappe.delete_doc("Number Card", OLD_CARD, force=True, ignore_permissions=True)
	else:
		frappe.rename_doc("Number Card", OLD_CARD, NEW_CARD, force=True)
		frappe.db.set_value(
			"Number Card",
			NEW_CARD,
			{
				"label": NEW_CARD,
				"filters_json": frappe.as_json(
					[
						["Journal Entry", "voucher_type", "=", "Quick Expense", False],
						["Journal Entry", "docstatus", "=", 0, False],
					]
				),
			},
		)
	frappe.db.sql(
		"""update `tabWorkspace` set content = replace(content, %s, %s) where name = 'Quick Expense'""",
		(OLD_CARD, NEW_CARD),
	)
	frappe.db.sql(
		"""update `tabWorkspace Number Card` set number_card_name = %s, label = %s
		where parent = 'Quick Expense' and number_card_name = %s""",
		(NEW_CARD, NEW_CARD, OLD_CARD),
	)
