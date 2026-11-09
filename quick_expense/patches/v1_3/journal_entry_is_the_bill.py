"""v1.3: the Journal Entry is the bill. Quick Expense Entry, its lines, Quick Expense
Category, Quick Expense GSTIN Cache and Quick Expense Invoice Key are removed.

Posted Journal Entries are kept unchanged in the books; their bill details,
totals and approval history move onto the Journal Entry before the old doctypes
are dropped."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

OLD_DOCTYPES = (
	"Quick Expense Entry",
	"Quick Expense Line",
	"Quick Expense Category",
	"Quick Expense Category Account",
	"Quick Expense GSTIN Cache",
	"Quick Expense Invoice Key",
)
ENTRY_TO_JE = {
	"supplier_trade_name": "qe_supplier_trade_name",
	"supplier_address": "qe_supplier_address",
	"supplier_pincode": "qe_supplier_pincode",
	"gstin_status": "qe_gstin_status",
	"supplier_name_entered_manually": "qe_supplier_name_entered_manually",
	"paid_through_account": "qe_paid_through_account",
	"payment_type": "qe_payment_type",
	"bill_attachment": "qe_bill_attachment",
	"total_taxable": "qe_total_taxable",
	"total_cgst": "qe_total_cgst",
	"total_sgst": "qe_total_sgst",
	"total_igst": "qe_total_igst",
	"total_cess": "qe_total_cess",
	"total_gst": "qe_total_gst",
	"total_eligible_itc": "qe_total_eligible_itc",
	"total_ineligible_gst": "qe_total_ineligible_gst",
	"total_reverse_charge_tax": "qe_total_reverse_charge_tax",
	"round_off": "qe_round_off",
	"grand_total": "qe_grand_total",
	"approval_status": "qe_approval_status",
	"approval_levels_required": "qe_approval_levels_required",
	"forced_approval_reasons": "qe_forced_approval_reasons",
	"cancel_reason": "qe_cancel_reason",
	"duplicate_key": "qe_bill_key",
}


def execute():
	from quick_expense.overrides.custom_fields import CUSTOM_FIELDS, OBSOLETE_FIELDS

	create_custom_fields(CUSTOM_FIELDS, update=True)

	if frappe.db.table_exists("Quick Expense Entry"):
		move_entries_onto_journal_entries()

	# rows of older bills: tell expense, payment and round-off rows apart
	frappe.db.sql(
		"""update `tabJournal Entry Account` row join `tabJournal Entry` je on je.name = row.parent
		set row.qe_gst_component = case
			when ifnull(row.qe_source_line, '') != '' then 'Expense'
			when row.account = je.qe_paid_through_account then 'Payment'
			when ifnull(row.user_remark, '') = 'Round off' then 'Round Off'
			else row.qe_gst_component end
		where je.voucher_type = 'Quick Expense' and ifnull(row.qe_gst_component, '') in ('', 'None')"""
	)
	if frappe.db.has_column("Journal Entry", "qe_approval_status"):  # removed again in v1_4
		frappe.db.sql(
			"""update `tabJournal Entry` set qe_approval_status = if(docstatus = 2, 'Cancelled', if(docstatus = 1, 'Posted', 'Draft'))
		where voucher_type = 'Quick Expense' and ifnull(qe_approval_status, '') = ''"""
		)

	for doctype in OLD_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
	for name in OBSOLETE_FIELDS:
		frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
	for card in frappe.get_all("Number Card", filters={"document_type": ("in", OLD_DOCTYPES)}, pluck="name"):
		frappe.delete_doc("Number Card", card, force=True, ignore_permissions=True)
	frappe.delete_doc("Workspace", "Quick Expense", ignore_missing=True, force=True)


def move_entries_onto_journal_entries():
	columns = set(frappe.db.get_table_columns("Quick Expense Entry"))
	fields = ["name", "linked_journal_entry", *[f for f in ENTRY_TO_JE if f in columns]]
	for entry in frappe.get_all(
		"Quick Expense Entry", filters={"linked_journal_entry": ("is", "set")}, fields=fields
	):
		values = {target: entry.get(source) for source, target in ENTRY_TO_JE.items() if source in columns}
		frappe.db.set_value("Journal Entry", entry.linked_journal_entry, values, update_modified=False)
		if not frappe.db.table_exists("Quick Expense Approval History"):
			continue
		frappe.db.sql(
			"""update `tabQuick Expense Approval History` set parent = %s, parenttype = 'Journal Entry',
			parentfield = 'qe_approval_history' where parent = %s and parenttype = 'Quick Expense Entry'""",
			(entry.linked_journal_entry, entry.name),
		)
