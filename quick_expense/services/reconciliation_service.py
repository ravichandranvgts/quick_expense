"""2A / 2B status of Quick Expense bills.

Matching happens in India Compliance's own Purchase Reconciliation Tool, where
Quick Expense Journal Entries are a books doctype (overrides/purchase_reconciliation.py)
and the tool keeps the Journal Entry's ``reconciliation_status``. This module only
releases the 2B record when a bill is cancelled and lists bills that are still
missing from 2B after a return period (monthly follow-up email).
"""

import frappe
from frappe.utils import add_months, getdate, nowdate

from quick_expense.constants import JE_VOUCHER_TYPE
from quick_expense.services.tax_engine import period_start


def is_overdue(row) -> bool:
	"""Older than one return period: the supplier's GSTR-1 for the month is due by the 11th of the next month."""
	start = period_start(row.get("qe_itc_claim_period") or row.get("itc_claim_period")) or getdate(
		row.posting_date
	)
	return getdate(nowdate()) > add_months(start, 2)


def unlink(je, from_cancel=False):
	"""Release the GSTR-2B record of a cancelled bill so it can be matched again."""
	names = frappe.get_all(
		"GST Inward Supply", filters={"link_doctype": "Journal Entry", "link_name": je.name}, pluck="name"
	)
	if names:
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			_unlink_documents,
		)

		_unlink_documents(names, exclude_from_reconciliation=from_cancel)
	je.db_set(
		"reconciliation_status", "Not Applicable" if from_cancel else "Unreconciled", update_modified=False
	)


def get_follow_up_entries(company=None, company_gstin=None) -> list:
	"""Posted bills with eligible ITC that are still not matched to GSTR-2A / 2B after a return period."""
	filters = {
		"voucher_type": JE_VOUCHER_TYPE,
		"docstatus": 1,
		"reconciliation_status": ("in", ("Unreconciled", "")),
		"qe_supplier_gstin": ("is", "set"),
		"qe_total_eligible_itc": (">", 0),
	}
	if company:
		filters["company"] = company
	if company_gstin:
		filters["company_gstin"] = company_gstin
	rows = frappe.get_all(
		"Journal Entry",
		filters=filters,
		fields=[
			"name",
			"company",
			"company_gstin",
			"posting_date",
			"qe_itc_claim_period",
			"qe_supplier_gstin",
			"qe_supplier_legal_name",
			"qe_supplier_invoice_no",
			"qe_total_eligible_itc",
			"reconciliation_status",
		],
		order_by="company_gstin asc, posting_date asc",
	)
	return [row for row in rows if is_overdue(row)]
