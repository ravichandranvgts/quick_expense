"""doc_events hooks on ERPNext / India Compliance doctypes."""

import frappe
from frappe import _
from frappe.utils import cint

from quick_expense.constants import JE_VOUCHER_TYPE
from quick_expense.services import journal_entry_service

# ----------------------------------------------------------------------------
# Journal Entry


def journal_entry_before_validate(doc, method=None):
	journal_entry_service.before_validate(doc, method)


def journal_entry_validate(doc, method=None):
	journal_entry_service.validate_voucher_type_for_user(doc, method)
	protect_input_gst_accounts(doc)


def journal_entry_on_update(doc, method=None):
	journal_entry_service.on_update(doc, method)


def journal_entry_before_submit(doc, method=None):
	journal_entry_service.before_submit(doc, method)


def journal_entry_on_submit(doc, method=None):
	if touched := protect_input_gst_accounts(doc):
		doc.add_comment(
			"Comment",
			_("Input GST accounts {0} used outside Quick Expense by {1}. Reason: {2}").format(
				", ".join(touched), frappe.session.user, doc.qe_gst_account_override_reason
			),
		)


def journal_entry_before_cancel(doc, method=None):
	journal_entry_service.before_cancel(doc, method)


def journal_entry_on_cancel(doc, method=None):
	journal_entry_service.on_cancel(doc, method)


def journal_entry_on_trash(doc, method=None):
	journal_entry_service.on_trash(doc, method)


def journal_entry_onload(doc, method=None):
	if doc.voucher_type != JE_VOUCHER_TYPE:
		return
	from quick_expense.services import approval_service, supplier_service
	from quick_expense.services.bill import Bill
	from quick_expense.services.settings_service import is_accounts_manager

	supplier_service.set_onload(doc)
	bill = Bill(doc)
	doc.set_onload(
		"quick_expense",
		{
			"approval_block_reason": approval_service.get_approval_block_reason(bill)
			if doc.qe_approval_status == "Pending Approval"
			else None,
			"is_accounts_manager": is_accounts_manager(),
			"overrides_disabled": cint(frappe.get_cached_doc("Quick Expense Settings").overrides_disabled),
		},
	)


def protect_input_gst_accounts(doc):
	"""Prompt 9.4: other Journal Entries may touch Input GST accounts only with an Accounts Manager's reason."""
	if doc.voucher_type == JE_VOUCHER_TYPE or doc.flags.from_quick_expense:
		return []
	settings = frappe.get_cached_doc("Quick Expense Settings")
	if not cint(settings.restrict_input_gst_accounts):
		return []

	from india_compliance.gst_india.utils import get_gst_accounts_by_type

	input_accounts = {a for a in get_gst_accounts_by_type(doc.company, "Input", throw=False).values() if a}
	touched = sorted({row.account for row in doc.accounts if row.account in input_accounts})
	if not touched:
		return []

	from quick_expense.services.settings_service import is_accounts_manager

	if not is_accounts_manager() or not (doc.get("qe_gst_account_override_reason") or "").strip():
		frappe.throw(
			_(
				"Input GST accounts ({0}) are reserved for Quick Expense Journal Entries. An Accounts Manager must enter a reason in 'Reason for Using Input GST Accounts' to post this entry."
			).format(", ".join(touched)),
			title=_("Input GST Account Restricted"),
		)
	return touched


# ----------------------------------------------------------------------------
# Company


def company_on_update(doc, method=None):
	"""Runs after India Compliance's own Company on_update, which creates the GST accounts."""
	if doc.country != "India" or frappe.flags.in_setup_wizard:
		return
	if not frappe.db.exists("DocType", "Quick Expense Settings"):
		return
	if frappe.db.exists(
		"Quick Expense Company Setting", {"company": doc.name, "parent": "Quick Expense Settings"}
	):
		return
	from quick_expense.services import setup_service

	setup_service.setup_company(doc.name)
