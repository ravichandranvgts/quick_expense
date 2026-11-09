"""Whitelisted API used by the Journal Entry form (and a mobile front end later)."""

import json

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull

from quick_expense.constants import JE_VOUCHER_TYPE
from quick_expense.services import approval_service, gstin_service
from quick_expense.services.bill import Bill
from quick_expense.services.settings_service import get_company_setting, get_settings, get_user_default


def _load(value):
	return json.loads(value) if isinstance(value, str) else value


def _bill(name, ptype="read") -> Bill:
	je = frappe.get_doc("Journal Entry", name)
	if je.voucher_type != JE_VOUCHER_TYPE:
		frappe.throw(_("{0} is not a Quick Expense Journal Entry.").format(name))
	je.check_permission(ptype)
	return Bill(je)


def _result(bill):
	return {
		"name": bill.name,
		"approval_status": bill.approval_status,
		"approval_level": bill.approval_level,
		"posting_error": bill.posting_error,
	}


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
	details.cancelled_action = get_settings().cancelled_gstin_action
	return details


@frappe.whitelist()
def get_company_gstins(company: str):
	frappe.has_permission("Company", "read", doc=company, throw=True)
	return gstin_service.get_company_gstins(company)


@frappe.whitelist()
def get_entry_defaults(company: str):
	"""Company GSTIN, cash / bank account and cost center for a new bill."""
	frappe.has_permission("Journal Entry", "create", throw=True)
	settings = get_settings()
	company_setting = get_company_setting(company, settings)
	user_default = get_user_default(frappe.session.user, company, settings)
	allowed = [row.account for row in settings.allowed_accounts if row.company == company]
	gstins = gstin_service.get_company_gstins(company)
	return {
		"company_gstin": (user_default and user_default.company_gstin)
		or company_setting.get("default_company_gstin")
		or (gstins[0].gstin if gstins else None),
		"paid_through_account": (user_default and user_default.paid_through_account)
		or (allowed[0] if len(allowed) == 1 else None),
		"cost_center": (user_default and user_default.cost_center)
		or company_setting.get("default_cost_center"),
		"company_gstins": [row.gstin for row in gstins],
		"allowed_accounts": allowed,
	}


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
def submit_for_approval(name: str):
	bill = _bill(name, "write")
	approval_service.submit_for_approval(bill)
	return _result(bill)


@frappe.whitelist()
def approve(name: str, comment: str | None = None):
	bill = _bill(name)
	approval_service.approve(bill, comment or "")
	return _result(bill)


@frappe.whitelist()
def reject(name: str, reason: str):
	bill = _bill(name)
	approval_service.reject(bill, reason)
	return _result(bill)


@frappe.whitelist()
def retry_posting(name: str):
	bill = _bill(name)
	approval_service.retry_posting(bill)
	return _result(bill)


@frappe.whitelist()
def bulk_approve(names, comment: str | None = None):
	"""List view action: approve several bills; each one succeeds or fails on its own."""
	results = []
	for name in _load(names):
		frappe.db.savepoint("qe_bulk_approve")
		try:
			bill = _bill(name)
			approval_service.approve(bill, comment or "")
			results.append({"name": name, "ok": 1, "status": bill.approval_status})
		except Exception as exc:
			frappe.db.rollback(save_point="qe_bulk_approve")
			frappe.clear_messages()
			results.append({"name": name, "ok": 0, "message": frappe.utils.strip_html(str(exc))})
	return results


@frappe.whitelist()
def cancel_with_reason(name: str, reason: str | None = None):
	bill = _bill(name, "cancel")
	if reason:
		bill.je.db_set("qe_cancel_reason", reason)
		bill.je.qe_cancel_reason = reason
	bill.je.cancel()
	return {"name": name, "approval_status": "Cancelled"}
