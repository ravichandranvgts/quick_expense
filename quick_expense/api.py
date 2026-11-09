"""Whitelisted API used by the Journal Entry form (and a mobile front end later)."""

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull

from quick_expense.constants import JE_VOUCHER_TYPE
from quick_expense.services import gstin_service
from quick_expense.services.settings_service import get_company_defaults


@frappe.whitelist()
def get_gstin_details(
	gstin: str, company: str | None = None, company_gstin: str | None = None, force_refresh: int = 0
):
	"""Supplier details for the form. Validation problems come back as a message, never as a crash."""
	frappe.has_permission("Journal Entry", "create", throw=True)
	gstin = gstin_service.normalise_gstin(gstin)
	if error := gstin_service.get_validation_error(gstin):
		return {"valid": 0, "message": error}
	if company and company_gstin:
		try:
			gstin_service.validate_not_own_gstin(gstin, company, company_gstin)
		except gstin_service.GSTINError as exc:
			frappe.clear_last_message()
			return {"valid": 0, "message": str(exc)}
	try:
		details = gstin_service.get_gstin_details(gstin, force_refresh=bool(int(force_refresh or 0)))
	except gstin_service.GSTINError as exc:
		frappe.clear_last_message()
		return {"valid": 0, "message": str(exc)}
	details.valid = 1
	details.blocking_status = int(gstin_service.is_blocking_status(details.status))
	return details


@frappe.whitelist()
def get_company_gstins(company: str):
	frappe.has_permission("Company", "read", doc=company, throw=True)
	return gstin_service.get_company_gstins(company)


@frappe.whitelist()
def get_entry_defaults(company: str):
	"""Company GSTIN and cost center for a new bill, from the Company and its GST addresses."""
	frappe.has_permission("Journal Entry", "create", throw=True)
	defaults = get_company_defaults(company)
	defaults.company_gstins = [row.gstin for row in gstin_service.get_company_gstins(company)]
	return defaults


@frappe.whitelist()
def get_last_gst_rate(company: str, supplier_gstin: str, account: str | None = None):
	"""GST rate (Item Tax Template) of this supplier's latest bill, preferring the same expense account."""
	frappe.has_permission("Journal Entry", "create", throw=True)
	if not (company and supplier_gstin):
		return None
	if account and frappe.get_cached_value("Account", account, "account_type") in ("Cash", "Bank"):
		return None  # the payment row has no GST rate
	je = frappe.qb.DocType("Journal Entry")
	row = frappe.qb.DocType("Journal Entry Account")
	query = (
		frappe.qb.from_(row)
		.join(je)
		.on(je.name == row.parent)
		.select(row.qe_item_tax_template, row.qe_hsn_sac)
		.where(je.voucher_type == JE_VOUCHER_TYPE)
		.where(je.docstatus < 2)
		.where(je.company == company)
		.where(je.qe_supplier_gstin == gstin_service.normalise_gstin(supplier_gstin))
		.where(IfNull(row.qe_item_tax_template, "") != "")
		.orderby(je.posting_date, order=frappe.qb.desc)
		.orderby(je.creation, order=frappe.qb.desc)
		.limit(1)
	)
	if account and (same_account := query.where(row.account == account).run(as_dict=True)):
		return same_account[0]
	rows = query.run(as_dict=True)
	return rows[0] if rows else None


@frappe.whitelist()
def cancel_with_reason(name: str, reason: str | None = None):
	je = frappe.get_doc("Journal Entry", name)
	if je.voucher_type != JE_VOUCHER_TYPE:
		frappe.throw(_("{0} is not a Quick Expense Journal Entry.").format(name))
	je.check_permission("cancel")
	if reason:
		je.db_set("qe_cancel_reason", reason)
		je.qe_cancel_reason = reason
	je.cancel()
	return {"name": name, "docstatus": je.docstatus}
