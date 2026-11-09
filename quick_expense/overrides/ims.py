"""Quick Expense Journal Entries in India Compliance's GST Invoice Management System (IMS).

IMS has its own books class (``PurchaseInvoice`` in the IMS module), separate from
the Purchase Reconciliation Tool's. The wrappers below add Quick Expense Journal
Entries to it the same way purchase_reconciliation.py does for the tool:
auto-matching, the invoice grid, the detail dialog and the Link dialog.

IMS actions also move the ITC claim period of the linked document. India
Compliance does that only for its own doctypes and would fail on a Journal Entry
(it has ``qe_itc_claim_period``, not ``itc_claim_period``), so the Journal Entry
part is done here with the same rules.
"""

import functools

import frappe
from frappe.query_builder.functions import IfNull

from quick_expense.constants import REASON_POS
from quick_expense.overrides.purchase_reconciliation import (
	DOCTYPE,
	QuickExpenseJournalEntry,
	reconciliation_active,
)

# fields IMS reads from a books document besides the ones every query returns
IMS_FIELDS = ("supplier", "supplier_name", "company", "gst_category", "is_return", "reconciliation_status")
DEFERRED = "Deferred"


def ims_books(company=None, company_gstin=None):
	return QuickExpenseJournalEntry(
		company=company,
		company_gstin=company_gstin or "All",
		include_ignored=1,
	)


# ----------------------------------------------------------------------------
# books class


def wrap_get_all(original):
	"""Books rows linked to the 2B invoices shown (keyed by document name)."""

	@functools.wraps(original)
	def get_all(self, names=None, filters=None):
		data = original(self, names=names, filters=filters)
		if not names or not reconciliation_active():
			return data
		source = ims_books((filters or {}).get("company"))
		query = source.get_query(IMS_FIELDS, ignore_company_gstin=True).where(source.JE.name.isin(names))
		for doc in query.run(as_dict=True):
			data[doc.name] = doc
		return data

	get_all._qe_wrapped = True
	return get_all


def wrap_get_unmatched(original):
	"""Unmatched books for auto-matching, keyed by supplier GSTIN. Quick Expense bills are
	invoices only (no credit or debit notes) and never under reverse charge in IMS."""

	@functools.wraps(original)
	def get_unmatched(self, filters):
		data = original(self, filters)
		if filters.get("doc_type") != "Invoice" or not reconciliation_active():
			return data
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool import BaseUtil

		source = ims_books(filters.get("company"), filters.get("company_gstin"))
		JE = source.JE
		docs = (
			source.get_query()
			.where(JE.reconciliation_status == "Unreconciled")
			.where(JE.qe_gst_category == "Registered Regular")
			.where(JE.qe_is_reverse_charge == 0)
			.where(IfNull(JE.qe_ineligibility_reason, "") != REASON_POS)
		).run(as_dict=True)
		for doc in docs:
			doc.fy = BaseUtil.get_fy(doc.bill_date)
			data.setdefault(doc.supplier_gstin, {})[doc.name] = doc
		return data

	get_unmatched._qe_wrapped = True
	return get_unmatched


# ----------------------------------------------------------------------------
# IMS form


def wrap_get_link_options(original):
	"""Options for the Link dialog when the user picks Journal Entry (same rules as IMS)."""

	@functools.wraps(original)
	def get_link_options(self, doctype, filters):
		if doctype != DOCTYPE:
			return original(self, doctype, filters)
		frappe.has_permission("GST Invoice Management System", "write", throw=True)
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			get_formatted_options,
		)

		filters = frappe._dict(filters or {})
		source = ims_books(self.company)
		query = source.get_query(["gst_category", "is_return"], ignore_company_gstin=True).where(
			source.JE.qe_supplier_gstin.like(f"%{filters.get('supplier_gstin') or ''}%")
		)
		if filters.get("from_date") and filters.get("to_date"):
			query = query.where(source.JE.posting_date[filters.from_date : filters.to_date])
		if not filters.get("show_matched"):
			query = query.where(source.JE.reconciliation_status == "Unreconciled")
		return {"options": get_formatted_options(query.run(as_dict=True)), "filters": filters}

	get_link_options._qe_wrapped = True
	return get_link_options


def wrap_set_claim_period(original):
	"""Accept / Reject / Pending in IMS: India Compliance moves the claim period of its own
	doctypes; Journal Entries are handled here and kept out of its call."""

	@functools.wraps(original)
	def set_itc_claim_period_on_ims_action(invoice_names, action, ims_period=None):
		invoice_names = list(invoice_names or [])
		linked = (
			frappe.get_all(
				"GST Inward Supply",
				filters={"name": ("in", invoice_names), "link_doctype": DOCTYPE},
				fields=["name", "link_name"],
			)
			if invoice_names
			else []
		)
		if not linked:
			return original(invoice_names, action, ims_period=ims_period)
		linked_names = {row.name for row in linked}
		others = [name for name in invoice_names if name not in linked_names]
		result = original(others, action, ims_period=ims_period) if others else None
		set_journal_entry_claim_period([row.link_name for row in linked], action, ims_period)
		return result

	set_itc_claim_period_on_ims_action._qe_wrapped = True
	return set_itc_claim_period_on_ims_action


def set_journal_entry_claim_period(names, action, ims_period=None):
	"""Rejected / Pending defer the ITC; Accepted claims it in the IMS period. A period whose
	GSTR-3B is already filed is never moved."""
	from quick_expense.services.validation_service import is_gstr3b_filed

	if action in ("Rejected", "Pending"):
		period = DEFERRED
	elif action == "Accepted" and ims_period:
		period = ims_period
	else:
		return
	for je in frappe.get_all(
		DOCTYPE,
		filters={"name": ("in", names), "docstatus": 1},
		fields=["name", "company_gstin", "qe_itc_claim_period"],
	):
		current = je.qe_itc_claim_period
		if current == period or (current and is_gstr3b_filed(je.company_gstin, current)):
			continue
		frappe.db.set_value(DOCTYPE, je.name, "qe_itc_claim_period", period, update_modified=True)
		frappe.get_doc(DOCTYPE, je.name).add_comment(
			"Info", frappe._("ITC Claim Period set to {0} via IMS Action ({1})").format(period, action)
		)


def whitelisted(factory):
	def make(original):
		wrapper = frappe.whitelist()(factory(original))
		wrapper._qe_wrapped = True
		return wrapper

	return make


def apply_patches(patch):
	from india_compliance.gst_india.doctype import gst_invoice_management_system as ims_module
	from india_compliance.gst_india.doctype.gst_invoice_management_system import (
		gst_invoice_management_system as ims_form,
	)

	patch(ims_module.PurchaseInvoice, "get_all", wrap_get_all)
	patch(ims_module.PurchaseInvoice, "get_unmatched", wrap_get_unmatched)
	patch(ims_form.GSTInvoiceManagementSystem, "get_link_options", whitelisted(wrap_get_link_options))
	patch(ims_form, "set_itc_claim_period_on_ims_action", wrap_set_claim_period)
