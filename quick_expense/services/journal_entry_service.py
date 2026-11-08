"""The Quick Expense Journal Entry: rows, approval guard, submit, cancel.

The user types the supplier and bill on the header and one row per expense
(expense account, taxable amount, GST rate as an Item Tax Template, HSN / SAC,
eligibility). On every save the system rebuilds the other rows:

    Dr Expense account           taxable (+ ineligible GST)        -- user rows
    Dr Input CGST / SGST / IGST  eligible GST only                 -- system
    Cr Input Tax … RCM           reverse charge tax                -- system
    Dr / Cr Round Off            difference                        -- system
    Cr Paid Through              grand total                       -- system

and keeps the bill's GST figures on every expense row (qe_* fields) so India
Compliance's GSTR-3B, Purchase Register and Purchase Reconciliation Tool can read
them. The Journal Entry is submitted only by the approval engine.
"""

import json

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt

from quick_expense.constants import (
	ACCOUNTS_MANAGER_EDITABLE_ROW_FIELDS,
	AMOUNT_ROW_FIELDS,
	APPROVED,
	APPROVER_EDITABLE_FIELDS,
	APPROVER_EDITABLE_ROW_FIELDS,
	CANCELLED,
	DRAFT,
	EDITABLE_STATUSES,
	EXPENSE_ROW,
	JE_VOUCHER_TYPE,
	OVERSEAS,
	PENDING,
	POSTED,
	POSTING_FAILED,
	REASON_POS,
	UNREGISTERED,
)
from quick_expense.services import approval_service, validation_service
from quick_expense.services.bill import Bill, is_expense_row
from quick_expense.services.settings_service import get_company_setting, is_accounts_manager

HEADS = ("cgst", "sgst", "igst", "cess")
COMPONENT = {"cgst": "CGST", "sgst": "SGST", "igst": "IGST", "cess": "Cess"}

# set by the approval engine; never copied into a new or amended bill
SYSTEM_FIELDS = (
	"qe_approval_level",
	"qe_approval_levels_required",
	"qe_current_approver_role",
	"qe_current_approver_user",
	"qe_submitted_for_approval_on",
	"qe_last_escalated_on",
	"qe_forced_approval_reasons",
	"qe_approval_chain",
	"qe_warnings",
	"qe_posting_error",
	"qe_cancel_reason",
	"qe_self_invoice_no",
	"qe_bill_key",
	"reconciliation_status",
)
# header fields that change on every save without a user edit
VOLATILE_FIELDS = {
	"title",
	"remark",
	"total_debit",
	"total_credit",
	"difference",
	"total_amount",
	"total_amount_in_words",
	"pay_to_recd_from",
	"company_gstin",
}


def is_quick_expense(je) -> bool:
	return je.voucher_type == JE_VOUCHER_TYPE


# ============================================================================
# save


def before_validate(je, method=None):
	if not is_quick_expense(je) or je.docstatus != 0:
		return
	if je.is_new() and not je.flags.qe_internal_save:
		for field in SYSTEM_FIELDS:
			je.set(field, None)
		je.set("qe_approval_history", [])
		je.qe_approval_status = DRAFT
	je.qe_approval_status = je.qe_approval_status or DRAFT

	guard_edits(je)
	clean_user_rows(je)
	bill = Bill(je)
	validation_service.prepare(bill)
	write_back(je, bill)

	if je.flags.amount_changed_in_approval:
		result = validation_service.validate_for_approval(bill)
		write_back(je, bill)
		je.qe_forced_approval_reasons = "\n".join(result.forced)
		je.qe_warnings = "\n".join(result.warnings)
		chain = approval_service.build_chain(bill, result.forced)
		je.qe_approval_chain = json.dumps(chain)
		je.qe_approval_levels_required = len(chain)
		if je.qe_approval_status == POSTING_FAILED:
			je.qe_approval_status = PENDING
		approval_service.return_to_level_one(bill, _("Amounts changed by {0}").format(frappe.session.user))


def clean_user_rows(je):
	"""Sort the rows the user typed: the cash / bank row is the payment (its amount is set by
	the system), GST rows are rebuilt by the system and never typed by hand."""
	from india_compliance.gst_india.utils import get_all_gst_accounts

	gst_accounts = set(get_all_gst_accounts(je.company)) if je.company else set()
	expense_rows, payment_rows = [], []
	for row in je.accounts:
		component = row.get("qe_gst_component") or ""
		account_type = (
			frappe.get_cached_value("Account", row.account, "account_type") if row.account else None
		)
		if component == "Payment" or account_type in ("Cash", "Bank"):
			row.qe_gst_component = "Payment"
			payment_rows.append(row)
			continue
		if component and component != EXPENSE_ROW:
			continue  # system GST / round-off row, rebuilt below
		if row.account in gst_accounts:
			frappe.throw(
				_(
					"Row {0}: GST account {1} cannot be entered by hand. Choose the GST rate on the expense row; the system adds the GST rows."
				).format(row.idx, row.account),
				title=_("GST Row Not Allowed"),
			)
		if not flt(row.qe_taxable_amount) and flt(row.debit_in_account_currency):
			row.qe_taxable_amount = row.debit_in_account_currency  # typed only the debit
		expense_rows.append(row)

	if len(payment_rows) > 1:
		frappe.throw(
			_("Use one Cash or Bank row for the payment of a Quick Expense bill."), title=_("One Payment Row")
		)
	je.qe_paid_through_account = payment_rows[0].account if payment_rows else None
	je.flags.qe_payment_row = payment_rows[0] if payment_rows else None
	je.set("accounts", expense_rows + payment_rows)


def on_update(je, method=None):
	if is_quick_expense(je) and je.flags.amount_changed_in_approval:
		je.flags.amount_changed_in_approval = False
		bill = Bill(je)
		from quick_expense.services import duplicate_service

		duplicate_service.check_duplicate(bill)
		duplicate_service.reserve(bill)
		approval_service.notify_current_approvers(bill)


def header_ineligibility_reason(bill) -> str:
	"""Same rule India Compliance applies to a Purchase Invoice header."""
	if any(line.ineligibility_reason == REASON_POS for line in bill.lines):
		return REASON_POS
	if any(line.ineligibility_reason and line.total_tax for line in bill.lines):
		return "Ineligible As Per Section 17(5)"
	return ""


def itc_classification(bill) -> str:
	if bill.gst_treatment == OVERSEAS:
		return "Import Of Service"
	if cint(bill.is_reverse_charge):
		return "ITC on Reverse Charge"
	return "All Other ITC"


def write_back(je, bill):
	"""Bill figures -> expense rows, system rows and header fields of the Journal Entry."""
	je.qe_gst_category = {"Non-GST or Out of Scope": "Unregistered"}.get(
		bill.gst_treatment, bill.gst_treatment
	)
	je.qe_itc_classification = itc_classification(bill)
	je.qe_ineligibility_reason = header_ineligibility_reason(bill)
	je.company_gstin = bill.company_gstin
	if bill.supplier_legal_name:
		je.title = f"{bill.supplier_legal_name} {bill.supplier_invoice_no or ''}".strip()[:140]

	expense_rows = []
	for line in bill.lines:
		row = line._row
		row.qe_gst_component = EXPENSE_ROW
		row.qe_source_line = row.name
		row.qe_is_ineligible_for_itc = cint(
			bool(line.ineligibility_reason) and line.ineligibility_reason != REASON_POS and line.total_tax
		)
		row.debit_in_account_currency = flt(flt(line.taxable_amount) + flt(line.ineligible_gst_amount), 2)
		row.credit_in_account_currency = 0
		if not row.cost_center:
			row.cost_center = bill.cost_center
		expense_rows.append(row)

	payment = je.flags.qe_payment_row
	je.set("accounts", expense_rows)
	for row in system_rows(bill):
		je.append("accounts", row)
	if payment is None and bill.paid_through_account:
		payment = frappe._dict(account=bill.paid_through_account)  # user's default cash / bank account
	if payment is not None:
		cost_center = bill.cost_center or frappe.get_cached_value("Company", bill.company, "cost_center")
		values = {
			"account": payment.account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": flt(bill.grand_total, 2),
			"qe_gst_component": "Payment",
			"cost_center": payment.get("cost_center") or cost_center,
		}
		if isinstance(payment, frappe._dict):
			je.append("accounts", values)
		else:
			payment.update(values)
			je.append("accounts", payment)
		je.flags.qe_payment_row = je.accounts[-1]


def system_rows(bill) -> list[dict]:
	from india_compliance.gst_india.utils import get_gst_accounts_by_type

	input_accounts = get_gst_accounts_by_type(bill.company, "Input")
	rcm_accounts = get_gst_accounts_by_type(bill.company, "Purchase Reverse Charge", throw=False)
	cost_center = bill.cost_center or frappe.get_cached_value("Company", bill.company, "cost_center")
	rows = []

	for head in HEADS:
		eligible = flt(
			sum(flt(line.get(f"{head}_amount")) for line in bill.lines if flt(line.eligible_itc_amount)), 2
		)
		if eligible:
			account = input_accounts.get(f"{head}_account")
			if not account:
				frappe.throw(
					_("Input {0} account is not set in GST Settings for {1}.").format(
						COMPONENT[head], bill.company
					)
				)
			rows.append(
				{
					"account": account,
					"debit_in_account_currency": eligible,
					"cost_center": cost_center,
					"qe_gst_component": COMPONENT[head],
				}
			)

	if cint(bill.is_reverse_charge):
		for head in HEADS:
			liability = flt(sum(flt(line.get(f"{head}_amount")) for line in bill.lines), 2)
			if liability:
				account = rcm_accounts.get(f"{head}_account")
				if not account:
					frappe.throw(
						_("Reverse charge {0} account is not set in GST Settings for {1}.").format(
							COMPONENT[head], bill.company
						)
					)
				rows.append(
					{
						"account": account,
						"credit_in_account_currency": liability,
						"cost_center": cost_center,
						"qe_gst_component": "Reverse Charge Liability",
					}
				)

	if flt(bill.round_off):
		account = get_company_setting(bill.company).get("round_off_account") or frappe.get_cached_value(
			"Company", bill.company, "round_off_account"
		)
		key = "debit_in_account_currency" if bill.round_off > 0 else "credit_in_account_currency"
		rows.append(
			{
				"account": account,
				key: abs(flt(bill.round_off)),
				"cost_center": frappe.get_cached_value("Company", bill.company, "round_off_cost_center")
				or cost_center,
				"qe_gst_component": "Round Off",
				"user_remark": _("Round off"),
			}
		)

	return rows


def guard_edits(je):
	"""While a bill is in approval only a few fields may change (prompt 13.5)."""
	if je.is_new() or je.flags.qe_internal_save or je.qe_approval_status in EDITABLE_STATUSES:
		return
	before = je.get_doc_before_save()
	if not before:
		return

	changed_header = {
		field.fieldname
		for field in je.meta.fields
		if field.fieldname not in VOLATILE_FIELDS
		and not field.read_only
		and field.fieldtype not in ("Section Break", "Column Break", "Tab Break", "Table", "HTML")
		and (je.get(field.fieldname) or None) != (before.get(field.fieldname) or None)
	}
	old_rows = {row.name: row for row in before.accounts if is_expense_row(row)}
	new_rows = [row for row in je.accounts if is_expense_row(row)]
	rows_added_or_removed = {row.name for row in new_rows} != set(old_rows)
	changed_row_fields = set()
	for row in new_rows:
		old = old_rows.get(row.name)
		if not old:
			continue
		for field in row.meta.fields:
			if field.read_only or field.fieldtype in ("Section Break", "Column Break"):
				continue
			if field.fieldname in (
				"debit_in_account_currency",
				"credit_in_account_currency",
				"debit",
				"credit",
			):
				continue
			if (row.get(field.fieldname) or None) != (old.get(field.fieldname) or None):
				changed_row_fields.add(field.fieldname)

	def payment_account(doc):
		return next(
			(
				row.account
				for row in doc.accounts
				if row.get("qe_gst_component") == "Payment"
				or (
					row.account
					and frappe.get_cached_value("Account", row.account, "account_type") in ("Cash", "Bank")
				)
			),
			None,
		)

	if payment_account(je) != payment_account(before):
		changed_header.add("qe_paid_through_account")

	if not (changed_header or changed_row_fields or rows_added_or_removed):
		return

	bill = Bill(je)
	user_is_am = is_accounts_manager()
	if je.qe_approval_status in (POSTED, CANCELLED):
		frappe.throw(_("A posted or cancelled bill cannot be edited."))
	if je.qe_approval_status in (APPROVED, POSTING_FAILED) and not user_is_am:
		frappe.throw(_("This bill is approved. Only an Accounts Manager can change it."))
	if je.qe_approval_status == PENDING and not (
		user_is_am or approval_service.get_approval_block_reason(bill) is None
	):
		frappe.throw(
			_(
				"This bill is pending approval and cannot be edited. Ask the approver to reject it if it needs changes."
			)
		)

	allowed_rows = set(ACCOUNTS_MANAGER_EDITABLE_ROW_FIELDS if user_is_am else APPROVER_EDITABLE_ROW_FIELDS)
	disallowed_header = changed_header - set(APPROVER_EDITABLE_FIELDS)
	disallowed_rows = changed_row_fields - allowed_rows - set(AMOUNT_ROW_FIELDS)
	if disallowed_header or disallowed_rows:
		labels = [je.meta.get_label(f) for f in disallowed_header] + [
			frappe.get_meta("Journal Entry Account").get_label(f) for f in disallowed_rows
		]
		frappe.throw(
			_(
				"While in approval only the remark, cost centers{0} and the amounts can change. Not allowed: {1}."
			).format(_(", ITC eligibility") if user_is_am else "", ", ".join(labels))
		)
	if rows_added_or_removed or changed_row_fields & set(AMOUNT_ROW_FIELDS):
		je.flags.amount_changed_in_approval = True


# ============================================================================
# submit / cancel / delete


def before_submit(je, method=None):
	if not is_quick_expense(je):
		return
	if not je.flags.qe_posting:
		frappe.throw(
			_("Quick Expense bills are submitted automatically after approval. Use Submit for Approval."),
			title=_("Approval Required"),
		)
	if (
		cint(je.qe_is_reverse_charge)
		and je.qe_gst_treatment in (UNREGISTERED, OVERSEAS)
		and not je.qe_self_invoice_no
	):
		je.qe_self_invoice_no = make_self_invoice_no(je)
	je.reconciliation_status = "Unreconciled" if needs_2b(je) else "Not Applicable"
	if abs(flt(je.total_debit) - flt(je.total_credit)) > 0.005:
		frappe.throw(
			_("Journal Entry is not balanced (debit {0}, credit {1}).").format(
				je.total_debit, je.total_credit
			)
		)


def needs_2b(je) -> bool:
	"""A bill from a registered supplier with GST on it must show up in the company's GSTR-2B."""
	return bool(je.qe_supplier_gstin) and flt(je.qe_total_gst) > 0


def make_self_invoice_no(je) -> str:
	"""Self invoice series per Company GSTIN, at most 16 characters (GST invoice number rule)."""
	from quick_expense.services.tax_engine import gst_financial_year

	start, end = gst_financial_year(je.posting_date)
	fy = f"{str(start.year)[2:]}{str(end.year)[2:]}"
	return make_autoname(f"SI{je.company_gstin[:2]}{je.company_gstin[12]}/{fy}/.####")


def before_cancel(je, method=None):
	if not is_quick_expense(je):
		return
	bill = Bill(je)
	validation_service.validate_lock_date(bill, action=_("cancelled"))
	if validation_service.is_gstr3b_filed(je.company_gstin, je.qe_itc_claim_period):
		frappe.throw(
			_(
				"GSTR-3B for {0} is filed for GSTIN {1}. This bill can no longer be cancelled; record a reversal instead."
			).format(je.qe_itc_claim_period, je.company_gstin),
			title=_("Return Filed"),
		)
	if je.reconciliation_status == "Reconciled" and not (je.qe_cancel_reason or "").strip():
		frappe.throw(
			_(
				"This bill is reconciled with GSTR-2B. Give a cancellation reason; the reconciliation link will be reset."
			),
			title=_("Reason Required"),
		)


def on_cancel(je, method=None):
	if not is_quick_expense(je):
		return
	from quick_expense.services import reconciliation_service

	reconciliation_service.unlink(je, from_cancel=True)
	bill = Bill(je)
	approval_service.add_history(bill, _("Cancelled"), je.qe_cancel_reason or "")
	je.qe_approval_history[-1].db_insert()
	je.db_set("qe_approval_status", CANCELLED)


def on_trash(je, method=None):
	if (
		is_quick_expense(je)
		and je.docstatus == 0
		and (je.qe_approval_status or DRAFT) not in EDITABLE_STATUSES
	):
		frappe.throw(_("Only draft or rejected Quick Expense bills can be deleted."))


# ============================================================================
# permissions: a user with only Quick Expense roles sees and makes only Quick Expense bills

FULL_JE_ROLES = {"Accounts User", "Accounts Manager", "Auditor", "System Manager"}


def quick_expense_only(user) -> bool:
	return user != "Administrator" and not (set(frappe.get_roles(user)) & FULL_JE_ROLES)


def _own_entries_only(user) -> bool:
	from quick_expense.constants import ROLE_APPROVER, ROLE_FINANCE_MANAGER

	if not cint(frappe.db.get_single_value("Quick Expense Settings", "users_see_only_own_entries")):
		return False
	return (
		not (set(frappe.get_roles(user)) & {ROLE_APPROVER, ROLE_FINANCE_MANAGER, *FULL_JE_ROLES})
		and user != "Administrator"
	)


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	conditions = []
	if quick_expense_only(user):
		conditions.append(f"`tabJournal Entry`.`voucher_type` = {frappe.db.escape(JE_VOUCHER_TYPE)}")
	if _own_entries_only(user):
		conditions.append(f"`tabJournal Entry`.`owner` = {frappe.db.escape(user)}")
	return " and ".join(conditions)


def has_permission(doc, ptype=None, user=None, debug=False):
	user = user or frappe.session.user
	if quick_expense_only(user) and doc.voucher_type and doc.voucher_type != JE_VOUCHER_TYPE:
		return False
	if _own_entries_only(user) and doc.owner and doc.owner != user and not doc.is_new():
		return False
	return True


def validate_voucher_type_for_user(je, method=None):
	if (
		not is_quick_expense(je)
		and quick_expense_only(frappe.session.user)
		and not je.flags.ignore_permissions
	):
		frappe.throw(_("You can only create Journal Entries of type Quick Expense."), frappe.PermissionError)
