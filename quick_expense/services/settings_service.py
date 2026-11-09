"""Read helpers over the single "Quick Expense Settings" doctype and the Company defaults."""

import frappe


def get_settings():
	return frappe.get_cached_doc("Quick Expense Settings")


def get_company_defaults(company: str) -> frappe._dict:
	"""Company GSTIN and cost center for a new bill, taken from the Company itself."""
	from quick_expense.services.gstin_service import get_company_gstins

	gstins = get_company_gstins(company)
	return frappe._dict(
		company_gstin=gstins[0].gstin if gstins else None,
		cost_center=frappe.get_cached_value("Company", company, "cost_center"),
	)
