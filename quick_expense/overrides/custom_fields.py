"""Custom fields that turn a Journal Entry of voucher type "Quick Expense" into a GST expense bill.

The user fills the supplier and bill on the header and one row per expense
(account, taxable amount, Item Tax Template, eligibility). On save the system
adds the CGST / SGST / IGST, reverse charge, round off and cash / bank rows.

``company_gstin`` (Journal Entry) and ``gst_tax_type`` (Journal Entry Account)
come from India Compliance and are reused. ``reconciliation_status`` is the
field India Compliance's Purchase Reconciliation Tool reads and writes.
Created on install and every migrate (setup_service.setup_all).
"""

from quick_expense.constants import JE_VOUCHER_TYPE

QE_ONLY = f"eval:doc.voucher_type=='{JE_VOUCHER_TYPE}'"
QE_ROW_ONLY = f"eval:parent.voucher_type=='{JE_VOUCHER_TYPE}'"
GST_TREATMENTS = (
	"\nRegistered Regular\nRegistered Composition\nUnregistered\nSEZ\nOverseas\nNon-GST or Out of Scope"
)
APPROVAL_STATUSES = (
	"\nDraft\nPending Approval\nApproved\nPosted\nRejected\nApproved - Posting Failed\nCancelled"
)


def ro(**field):
	return {"read_only": 1, "no_copy": 1, **field}


def currency(fieldname, label, after, **extra):
	return {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": "Currency",
		"options": "INR",
		"insert_after": after,
		**ro(**extra),
	}


def column(fieldname, after):
	return {"fieldname": fieldname, "fieldtype": "Column Break", "insert_after": after}


FETCHED = "eval:doc.qe_supplier_gstin && !doc.qe_supplier_name_entered_manually"  # filled from the GST portal


def shown_if(field):
	return f"eval:doc.{field}"


JOURNAL_ENTRY = [
	{
		"fieldname": "qe_gst_account_override_reason",
		"label": "Reason for Using Input GST Accounts",
		"fieldtype": "Small Text",
		"insert_after": "voucher_type",
		"no_copy": 1,
		"depends_on": f"eval:doc.voucher_type!='{JE_VOUCHER_TYPE}'",
		"description": "Required when Quick Expense restricts Input GST accounts. Accounts Manager only.",
	},
	# ---------------------------------------------------------------- supplier and bill (what the user types)
	{
		"fieldname": "qe_section",
		"label": "Supplier and Bill",
		"fieldtype": "Section Break",
		"insert_after": "qe_gst_account_override_reason",
		"depends_on": QE_ONLY,
	},
	{
		"fieldname": "qe_gst_treatment",
		"label": "GST Treatment",
		"fieldtype": "Select",
		"options": GST_TREATMENTS,
		"insert_after": "qe_section",
		"no_copy": 1,
		"mandatory_depends_on": QE_ONLY,
	},
	{
		"fieldname": "qe_supplier_gstin",
		"label": "Supplier GSTIN",
		"fieldtype": "Data",
		"insert_after": "qe_gst_treatment",
		"no_copy": 1,
		"depends_on": "eval:in_list(['Registered Regular','Registered Composition','SEZ'], doc.qe_gst_treatment)",
		"description": "Name, address and state are filled from the GST portal.",
	},
	{
		"fieldname": "qe_supplier_legal_name",
		"label": "Supplier Legal Name / Shop Name",
		"fieldtype": "Data",
		"insert_after": "qe_supplier_gstin",
		"no_copy": 1,
		"read_only_depends_on": FETCHED,
	},
	{
		"fieldname": "qe_supplier_trade_name",
		"label": "Supplier Trade Name",
		"fieldtype": "Data",
		"insert_after": "qe_supplier_legal_name",
		"no_copy": 1,
		"read_only_depends_on": FETCHED,
		"depends_on": "eval:doc.qe_supplier_trade_name || !doc.qe_supplier_gstin",
	},
	{
		"fieldname": "qe_supplier",
		"label": "Supplier (onboarded)",
		"fieldtype": "Link",
		"options": "Supplier",
		"insert_after": "qe_supplier_trade_name",
		"depends_on": shown_if("qe_supplier"),
		**ro(allow_on_submit=1),
	},
	column("qe_cb_1", "qe_supplier"),
	{
		"fieldname": "qe_supplier_state",
		"label": "Supplier State",
		"fieldtype": "Data",
		"insert_after": "qe_cb_1",
		"no_copy": 1,
		"read_only_depends_on": FETCHED,
	},
	{
		"fieldname": "qe_supplier_address",
		"label": "Supplier Address",
		"fieldtype": "Small Text",
		"insert_after": "qe_supplier_state",
		"no_copy": 1,
		"read_only_depends_on": FETCHED,
	},
	{
		"fieldname": "qe_supplier_pincode",
		"label": "Supplier Pincode",
		"fieldtype": "Data",
		"insert_after": "qe_supplier_address",
		"no_copy": 1,
		"read_only_depends_on": FETCHED,
	},
	{
		"fieldname": "qe_gstin_status",
		"label": "GSTIN Status",
		"fieldtype": "Data",
		"insert_after": "qe_supplier_pincode",
		"depends_on": shown_if("qe_gstin_status"),
		**ro(),
	},
	{
		"fieldname": "qe_supplier_name_entered_manually",
		"label": "Supplier Name Entered Manually",
		"fieldtype": "Check",
		"insert_after": "qe_gstin_status",
		**ro(hidden=1),
	},
	column("qe_cb_2", "qe_supplier_name_entered_manually"),
	{
		"fieldname": "qe_supplier_invoice_no",
		"label": "Supplier Invoice No",
		"fieldtype": "Data",
		"insert_after": "qe_cb_2",
		"no_copy": 1,
	},
	{
		"fieldname": "qe_supplier_invoice_date",
		"label": "Supplier Invoice Date",
		"fieldtype": "Date",
		"insert_after": "qe_supplier_invoice_no",
		"no_copy": 1,
	},
	{
		"fieldname": "qe_bill_addressed_to_company_gstin",
		"label": "Bill is addressed to the selected Company GSTIN",
		"fieldtype": "Check",
		"default": "1",
		"insert_after": "qe_supplier_invoice_date",
		"description": "Untick only when our GSTIN is not printed on the bill; its GST then cannot be claimed.",
	},
	{
		"fieldname": "qe_is_reverse_charge",
		"label": "Is Reverse Charge",
		"fieldtype": "Check",
		"insert_after": "qe_bill_addressed_to_company_gstin",
		"no_copy": 1,
		"description": "GTA, advocate, security by a non-body-corporate, rent from an unregistered landlord. Overseas suppliers always are.",
	},
	{
		"fieldname": "qe_is_transporter",
		"label": "Payment to a Transporter",
		"fieldtype": "Check",
		"insert_after": "qe_is_reverse_charge",
		"no_copy": 1,
	},
	{
		"fieldname": "qe_bill_attachment",
		"label": "Bill Attachment",
		"fieldtype": "Attach",
		"insert_after": "qe_is_transporter",
		"no_copy": 1,
	},
	# the cash / bank account is a row in the accounts table; these two are derived from it
	{
		"fieldname": "qe_paid_through_account",
		"label": "Paid Through (Cash or Bank)",
		"fieldtype": "Link",
		"options": "Account",
		"insert_after": "qe_bill_attachment",
		**ro(hidden=1),
	},
	{
		"fieldname": "qe_payment_type",
		"label": "Payment Type",
		"fieldtype": "Data",
		"insert_after": "qe_paid_through_account",
		**ro(hidden=1),
	},
	# ---------------------------------------------------------------- totals (all calculated)
	{
		"fieldname": "qe_payment_section",
		"label": "Bill Totals",
		"fieldtype": "Section Break",
		"insert_after": "qe_payment_type",
		"depends_on": "eval:doc.voucher_type=='Quick Expense' && doc.qe_grand_total",
	},
	currency("qe_total_taxable", "Total Taxable", "qe_payment_section"),
	currency("qe_total_gst", "Total GST", "qe_total_taxable"),
	column("qe_cb_3", "qe_total_gst"),
	currency("qe_total_eligible_itc", "Eligible ITC", "qe_cb_3"),
	currency("qe_total_ineligible_gst", "Ineligible GST (to cost)", "qe_total_eligible_itc"),
	column("qe_cb_4", "qe_total_ineligible_gst"),
	currency("qe_grand_total", "Grand Total (paid)", "qe_cb_4", bold=1, in_list_view=1),
	# ---------------------------------------------------------------- GST details (collapsed)
	{
		"fieldname": "qe_gst_section",
		"label": "GST Details",
		"fieldtype": "Section Break",
		"insert_after": "qe_grand_total",
		"depends_on": QE_ONLY,
		"collapsible": 1,
	},
	currency("qe_total_cgst", "CGST", "qe_gst_section"),
	currency("qe_total_sgst", "SGST", "qe_total_cgst"),
	currency("qe_total_igst", "IGST", "qe_total_sgst"),
	currency("qe_total_cess", "Cess", "qe_total_igst"),
	currency(
		"qe_total_reverse_charge_tax",
		"Reverse Charge Tax",
		"qe_total_cess",
		depends_on=shown_if("qe_total_reverse_charge_tax"),
	),
	currency("qe_round_off", "Round Off", "qe_total_reverse_charge_tax", depends_on=shown_if("qe_round_off")),
	column("qe_cb_5", "qe_round_off"),
	{
		"fieldname": "qe_place_of_supply",
		"label": "Place of Supply",
		"fieldtype": "Data",
		"insert_after": "qe_cb_5",
		**ro(),
	},
	{
		"fieldname": "qe_gst_category",
		"label": "GST Category",
		"fieldtype": "Data",
		"insert_after": "qe_place_of_supply",
		**ro(),
	},
	{
		"fieldname": "qe_itc_classification",
		"label": "ITC Classification",
		"fieldtype": "Data",
		"insert_after": "qe_gst_category",
		**ro(),
	},
	{
		"fieldname": "qe_ineligibility_reason",
		"label": "Reason for Ineligibility",
		"fieldtype": "Data",
		"insert_after": "qe_itc_classification",
		"depends_on": shown_if("qe_ineligibility_reason"),
		**ro(),
	},
	{
		"fieldname": "qe_self_invoice_no",
		"label": "Self Invoice No",
		"fieldtype": "Data",
		"insert_after": "qe_ineligibility_reason",
		"depends_on": shown_if("qe_self_invoice_no"),
		**ro(allow_on_submit=1),
	},
	{
		# same field and options India Compliance puts on Purchase Invoice / Bill of Entry
		"fieldname": "reconciliation_status",
		"label": "Reconciliation Status",
		"fieldtype": "Select",
		"options": "\nNot Applicable\nReconciled\nUnreconciled\nIgnored\nMatch Found",
		"insert_after": "qe_self_invoice_no",
		"print_hide": 1,
		"depends_on": "eval:doc.voucher_type=='Quick Expense' && doc.docstatus==1",
		**ro(allow_on_submit=1),
	},
	column("qe_cb_6", "reconciliation_status"),
	{
		"fieldname": "qe_itc_claim_period",
		"label": "ITC Claim Period (MMYYYY)",
		"fieldtype": "Data",
		"insert_after": "qe_cb_6",
		"no_copy": 1,
		"length": 8,
		"description": 'Defaults to the posting month. "Deferred" when the invoice is Rejected or Pending in IMS.',
	},
	{
		"fieldname": "qe_itc_claim_period_reason",
		"label": "Reason for Moving ITC Claim Period",
		"fieldtype": "Small Text",
		"insert_after": "qe_itc_claim_period",
		"no_copy": 1,
		"depends_on": shown_if("qe_itc_claim_period"),
	},
	{
		"fieldname": "qe_bill_key",
		"label": "Bill Key",
		"fieldtype": "Data",
		"insert_after": "qe_itc_claim_period_reason",
		"search_index": 1,
		**ro(hidden=1),
	},
	{
		"fieldname": "qe_warnings",
		"label": "Warnings",
		"fieldtype": "Small Text",
		"insert_after": "qe_bill_key",
		"depends_on": shown_if("qe_warnings"),
		**ro(),
	},
	# ---------------------------------------------------------------- approval (collapsed)
	{
		"fieldname": "qe_approval_section",
		"label": "Approval",
		"fieldtype": "Section Break",
		"insert_after": "qe_warnings",
		"depends_on": "eval:doc.voucher_type=='Quick Expense' && doc.qe_approval_status && doc.qe_approval_status!='Draft'",
		"collapsible": 1,
	},
	{
		"fieldname": "qe_approval_status",
		"label": "Approval Status",
		"fieldtype": "Select",
		"options": APPROVAL_STATUSES,
		"insert_after": "qe_approval_section",
		"in_standard_filter": 1,
		**ro(allow_on_submit=1),
	},
	{
		"fieldname": "qe_approval_level",
		"label": "Current Approval Level",
		"fieldtype": "Int",
		"insert_after": "qe_approval_status",
		"depends_on": "eval:doc.qe_approval_status=='Pending Approval'",
		**ro(),
	},
	{
		"fieldname": "qe_approval_levels_required",
		"label": "Approval Levels Required",
		"fieldtype": "Int",
		"insert_after": "qe_approval_level",
		"depends_on": shown_if("qe_approval_levels_required"),
		**ro(),
	},
	{
		"fieldname": "qe_current_approver_role",
		"label": "Current Approver Role",
		"fieldtype": "Data",
		"insert_after": "qe_approval_levels_required",
		"depends_on": shown_if("qe_current_approver_role"),
		**ro(),
	},
	{
		"fieldname": "qe_current_approver_user",
		"label": "Current Approver User",
		"fieldtype": "Link",
		"options": "User",
		"insert_after": "qe_current_approver_role",
		"depends_on": shown_if("qe_current_approver_user"),
		**ro(),
	},
	column("qe_cb_7", "qe_current_approver_user"),
	{
		"fieldname": "qe_submitted_for_approval_on",
		"label": "Submitted for Approval On",
		"fieldtype": "Datetime",
		"insert_after": "qe_cb_7",
		**ro(),
	},
	{
		"fieldname": "qe_last_escalated_on",
		"label": "Last Escalated On",
		"fieldtype": "Datetime",
		"insert_after": "qe_submitted_for_approval_on",
		"depends_on": shown_if("qe_last_escalated_on"),
		**ro(),
	},
	{
		"fieldname": "qe_forced_approval_reasons",
		"label": "Accounts Manager Approval Forced Because",
		"fieldtype": "Small Text",
		"insert_after": "qe_last_escalated_on",
		"depends_on": shown_if("qe_forced_approval_reasons"),
		**ro(),
	},
	{
		"fieldname": "qe_posting_error",
		"label": "Posting Error",
		"fieldtype": "Small Text",
		"insert_after": "qe_forced_approval_reasons",
		"depends_on": shown_if("qe_posting_error"),
		**ro(),
	},
	{
		"fieldname": "qe_cancel_reason",
		"label": "Cancellation Reason",
		"fieldtype": "Small Text",
		"insert_after": "qe_posting_error",
		"depends_on": shown_if("qe_cancel_reason"),
		**ro(allow_on_submit=1),
	},
	{
		"fieldname": "qe_approval_chain",
		"label": "Approval Chain",
		"fieldtype": "Code",
		"options": "JSON",
		"insert_after": "qe_cancel_reason",
		**ro(hidden=1),
	},
	{
		"fieldname": "qe_approval_history",
		"label": "Approval History",
		"fieldtype": "Table",
		"options": "Quick Expense Approval History",
		"insert_after": "qe_approval_chain",
		**ro(allow_on_submit=1),
	},
]

JOURNAL_ENTRY_ACCOUNT = [
	# what the user types on an expense row
	{
		"fieldname": "qe_section",
		"label": "Quick Expense",
		"fieldtype": "Section Break",
		"insert_after": "user_remark",
		"depends_on": "eval:parent.voucher_type=='Quick Expense' && doc.qe_gst_component!='Payment' && in_list(['', 'Expense', null, undefined], doc.qe_gst_component)",
	},
	{
		"fieldname": "qe_taxable_amount",
		"label": "Taxable Amount",
		"fieldtype": "Currency",
		"options": "INR",
		"insert_after": "qe_section",
	},
	{
		"fieldname": "qe_item_tax_template",
		"label": "GST Rate (Item Tax Template)",
		"fieldtype": "Link",
		"options": "Item Tax Template",
		"insert_after": "qe_taxable_amount",
	},
	{
		"fieldname": "qe_hsn_sac",
		"label": "HSN / SAC",
		"fieldtype": "Data",
		"insert_after": "qe_item_tax_template",
	},
	{
		"fieldname": "qe_eligibility",
		"label": "Eligibility for ITC",
		"fieldtype": "Select",
		"options": "\nAll Other ITC\nIneligible under Section 17(5)\nIneligible Others",
		"insert_after": "qe_hsn_sac",
		"description": "Empty = eligible. Food, cab hire, passenger vehicle repair, gifts and staff welfare are usually 17(5).",
	},
	{
		"fieldname": "qe_eligibility_reason",
		"label": "Reason (Ineligible Others)",
		"fieldtype": "Data",
		"insert_after": "qe_eligibility",
		"depends_on": "eval:doc.qe_eligibility=='Ineligible Others'",
	},
	{
		"fieldname": "qe_location_of_service_state",
		"label": "Location of Service State",
		"fieldtype": "Autocomplete",
		"insert_after": "qe_eligibility_reason",
		"description": "Only for a hotel or property in another state.",
	},
	{
		"fieldname": "qe_gst_as_per_bill",
		"label": "GST as per Bill",
		"fieldtype": "Currency",
		"options": "INR",
		"insert_after": "qe_location_of_service_state",
		"description": "Only if the bill's GST differs from the calculated GST by a few paise.",
	},
	# calculated by the system (collapsed)
	{
		"fieldname": "qe_calc_section",
		"label": "GST Calculated",
		"fieldtype": "Section Break",
		"insert_after": "qe_gst_as_per_bill",
		"depends_on": "eval:parent.voucher_type=='Quick Expense' && doc.qe_gst_component",
		"collapsible": 1,
	},
	{
		"fieldname": "qe_gst_component",
		"label": "Row Type",
		"fieldtype": "Select",
		"options": "\nExpense\nCGST\nSGST\nIGST\nCess\nReverse Charge Liability\nRound Off\nPayment\nNone",
		"insert_after": "qe_calc_section",
		**ro(),
	},
	{
		"fieldname": "qe_gst_treatment",
		"label": "GST Treatment",
		"fieldtype": "Data",
		"insert_after": "qe_gst_component",
		**ro(),
	},
	{
		"fieldname": "qe_gst_rate",
		"label": "GST Rate (%)",
		"fieldtype": "Float",
		"insert_after": "qe_gst_treatment",
		**ro(),
	},
	{
		"fieldname": "qe_is_ineligible_for_itc",
		"label": "Is Ineligible for ITC",
		"fieldtype": "Check",
		"insert_after": "qe_gst_rate",
		**ro(),
	},
	{
		"fieldname": "qe_ineligibility_reason",
		"label": "Reason for Ineligibility",
		"fieldtype": "Data",
		"insert_after": "qe_is_ineligible_for_itc",
		**ro(),
	},
	column("qe_cb_1", "qe_ineligibility_reason"),
	currency("qe_cgst_amount", "CGST", "qe_cb_1"),
	currency("qe_sgst_amount", "SGST", "qe_cgst_amount"),
	currency("qe_igst_amount", "IGST", "qe_sgst_amount"),
	currency("qe_cess_amount", "Cess", "qe_igst_amount"),
	column("qe_cb_2", "qe_cess_amount"),
	{
		"fieldname": "qe_supply_type",
		"label": "Supply Type",
		"fieldtype": "Data",
		"insert_after": "qe_cb_2",
		**ro(),
	},
	{
		"fieldname": "qe_source_line",
		"label": "Source Line ID",
		"fieldtype": "Data",
		"insert_after": "qe_supply_type",
		**ro(hidden=1),
	},
]

CUSTOM_FIELDS = {"Journal Entry": JOURNAL_ENTRY, "Journal Entry Account": JOURNAL_ENTRY_ACCOUNT}

# fields of earlier versions that no longer exist (deleted by the v1.3 patch)
OBSOLETE_FIELDS = (
	"Journal Entry-quick_expense_entry",
	"Journal Entry Account-qe_expense_category",
)
