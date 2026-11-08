"""v1.1: Journal Entry is the only posting mode; 2A / 2B reconciliation moves into
India Compliance's Purchase Reconciliation Tool; the app's own reports are removed."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

OLD_REPORTS = (
	"Quick Expense Register",
	"Quick Expense Approval Pending",
	"Quick Expense 2B Pending Follow-up",
	"Quick Expense Cash Limit Exceptions",
	"Quick Expense 2B Reconciliation",
)
OLD_CUSTOM_FIELDS = (
	"Purchase Invoice-quick_expense_entry",
	"Purchase Invoice-qe_actual_supplier_name",
	"Purchase Invoice-qe_self_invoice_no",
	"Purchase Invoice Item-quick_expense_line",
	"GST Inward Supply-qe_journal_entry",
	"Journal Entry-qe_two_b_status",
	"Journal Entry-qe_linked_gst_inward_supply",
)


def execute():
	from quick_expense.overrides.custom_fields import CUSTOM_FIELDS

	create_custom_fields(CUSTOM_FIELDS, update=True)  # adds Journal Entry.reconciliation_status

	# 2B links made by v1.0's own matching become India Compliance's native links
	if frappe.db.has_column("GST Inward Supply", "qe_journal_entry"):
		for row in frappe.get_all(
			"GST Inward Supply",
			filters={"qe_journal_entry": ("is", "set")},
			fields=["name", "qe_journal_entry"],
		):
			frappe.db.set_value(
				"GST Inward Supply",
				row.name,
				{"link_doctype": "Journal Entry", "link_name": row.qe_journal_entry},
			)
			frappe.db.set_value("Journal Entry", row.qe_journal_entry, "reconciliation_status", "Reconciled")

	frappe.db.sql(
		"""update `tabJournal Entry` set reconciliation_status = if(ifnull(qe_supplier_gstin, '') = '', 'Not Applicable', 'Unreconciled')
		where voucher_type = 'Quick Expense' and docstatus = 1 and ifnull(reconciliation_status, '') = ''"""
	)

	for report in OLD_REPORTS:
		frappe.delete_doc("Report", report, ignore_missing=True, force=True)
	for name in OLD_CUSTOM_FIELDS:
		frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
	# rebuilt by setup_service.after_migrate with India Compliance shortcuts
	frappe.delete_doc("Workspace", "Quick Expense", ignore_missing=True, force=True)
