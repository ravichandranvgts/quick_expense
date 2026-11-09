"""Quick Expense Journal Entries in India Compliance's Purchase Reconciliation Tool.

India Compliance already reconciles three "books" doctypes against GSTR-2A / 2B
(Purchase Invoice, Bill of Entry, ISD Recipient Invoice). Journal Entries of
voucher type "Quick Expense" are added as a fourth one, with the same interface
as India Compliance's own ``PurchaseInvoice`` books class. Matching, the grid,
Accept / Pending / Ignore, Link and Unlink then work on them in the standard
tool. Journal Entry carries the standard ``reconciliation_status`` field (custom
field) that the tool reads and writes.

Every wrapper calls India Compliance's original method first and only adds the
Journal Entry part; nothing of India Compliance is copied.
"""

import functools

import frappe
from frappe.query_builder import Case
from frappe.query_builder.custom import ConstantColumn
from frappe.query_builder.functions import IfNull, Sum
from pypika.terms import LiteralValue

from quick_expense.constants import JE_VOUCHER_TYPE

DOCTYPE = "Journal Entry"
B2B_CATEGORIES = ("B2B",)
MATCHED = ("Reconciled", "Match Found")


def reconciliation_active() -> bool:
	from quick_expense.overrides.ic_reports import overrides_active

	return overrides_active("reconciliation")


class QuickExpenseJournalEntry:
	"""Books side for Quick Expense Journal Entries (same interface as India Compliance's PurchaseInvoice)."""

	def __init__(self, **kwargs):
		self.__dict__.update(kwargs)
		self.JE = frappe.qb.DocType("Journal Entry")
		self.ROW = frappe.qb.DocType("Journal Entry Account")

	# The filters below are written here, not taken from India Compliance's BaseUtil
	# helpers, because those helpers only exist from India Compliance 16.10.
	def get_all(self, additional_fields=None, names=None, only_names=False):
		if only_names and not names:
			return
		query = self.get_query(additional_fields, ignore_company_gstin=True)
		if only_names:
			return query.where(self.JE.name.isin(names)).run(as_dict=True)
		criterion = (
			self.company_gstin_criterion()
			& (self.JE.posting_date[self.from_date : self.to_date])
			& (IfNull(self.JE.reconciliation_status, "").notin(MATCHED))
		)
		if names:
			criterion |= self.JE.name.isin(names)
		return query.where(criterion).run(as_dict=True)

	def company_gstin_criterion(self):
		if self.company_gstin == "All":
			return IfNull(self.JE.company_gstin, "") != ""
		return self.JE.company_gstin == self.company_gstin

	def apply_filters(self, query, ignore_company_gstin=False):
		if self.company:
			query = query.where(self.JE.company == self.company)
		if self.include_ignored == 0:
			query = query.where(IfNull(self.JE.reconciliation_status, "") != "Ignored")
		if ignore_company_gstin:
			return query
		return query.where(self.company_gstin_criterion())

	def get_unmatched(self, category=None, is_return=0):
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool import BaseUtil

		data = (
			self.get_query()
			.where(IfNull(self.JE.reconciliation_status, "").notin(MATCHED))
			.where(self.JE.posting_date[self.from_date : self.to_date])
		).run(as_dict=True)
		for doc in data:
			doc.fy = BaseUtil.get_fy(doc.bill_date or doc.posting_date)
		return data

	def get_query(self, additional_fields=None, ignore_company_gstin=False):
		from india_compliance.gst_india.constants import TAXABLE_GST_TREATMENTS

		query = (
			frappe.qb.from_(self.JE)
			.inner_join(self.ROW)
			.on(self.ROW.parent == self.JE.name)
			.where(self.JE.voucher_type == JE_VOUCHER_TYPE)
			.where(self.JE.docstatus == 1)
			.where(self.JE.is_opening == "No")
			.where(IfNull(self.JE.reconciliation_status, "") != "Not Applicable")
			.where(IfNull(self.JE.qe_supplier_gstin, "") != "")
			.where(IfNull(self.ROW.qe_source_line, "") != "")
			.where(self.ROW.qe_gst_treatment.isin(TAXABLE_GST_TREATMENTS))
			.groupby(self.JE.name)
			.select(*self.get_fields(additional_fields), ConstantColumn(DOCTYPE).as_("doctype"))
		)
		return self.apply_filters(query, ignore_company_gstin)

	def get_fields(self, additional_fields=None):
		JE, ROW = self.JE, self.ROW
		fields = [
			JE.name,
			JE.qe_supplier_gstin.as_("supplier_gstin"),
			JE.company_gstin,
			JE.qe_supplier_invoice_no.as_("bill_no"),
			Case()
			.when(JE.qe_supplier_invoice_date.isnull(), JE.posting_date)
			.else_(JE.qe_supplier_invoice_date)
			.as_("bill_date"),
			JE.posting_date,
			JE.qe_place_of_supply.as_("place_of_supply"),
			JE.qe_is_reverse_charge.as_("is_reverse_charge"),
			JE.qe_itc_classification.as_("itc_classification"),
			Sum(ROW.qe_taxable_amount).as_("taxable_value"),
			Sum(ROW.qe_cgst_amount).as_("cgst"),
			Sum(ROW.qe_sgst_amount).as_("sgst"),
			Sum(ROW.qe_igst_amount).as_("igst"),
			Sum(ROW.qe_cess_amount).as_("cess"),
			LiteralValue(0).as_("cess_non_advol"),
		]
		# fields India Compliance asks Purchase Invoices for, mapped onto the Journal Entry
		mapped = {
			"supplier_name": JE.qe_supplier_legal_name,
			"company": JE.company,
			"gst_category": JE.qe_gst_category,
			"reconciliation_status": JE.reconciliation_status,
			"itc_claim_period": JE.qe_itc_claim_period,
			"is_return": LiteralValue(0),
			"supplier": LiteralValue("''"),
		}
		taken = {
			"name",
			"company_gstin",
			"supplier_gstin",
			"bill_no",
			"bill_date",
			"posting_date",
			"place_of_supply",
			"is_reverse_charge",
			"itc_classification",
		}
		for field in additional_fields or []:
			if not isinstance(field, str) or field in taken:
				continue
			taken.add(field)
			fields.append((mapped.get(field) or LiteralValue("NULL")).as_(field))
		return fields

	@staticmethod
	def query_matched(from_date=None, to_date=None):
		inward = frappe.qb.DocType("GST Inward Supply")
		je = frappe.qb.DocType("Journal Entry")
		query = (
			frappe.qb.from_(inward)
			.join(je)
			.on(je.name == inward.link_name)
			.select(inward.link_name)
			.where(inward.link_doctype == DOCTYPE)
		)
		if from_date and to_date:
			query = query.where(je.posting_date[from_date:to_date])
		return query


def books(reco, from_date=None):
	return QuickExpenseJournalEntry(
		company=reco.company,
		company_gstin=reco.company_gstin,
		from_date=from_date or reco.from_date,
		to_date=reco.to_date,
		include_ignored=reco.include_ignored,
	)


# ----------------------------------------------------------------------------
# wrappers


def wrap_get_unmatched_purchases(original):
	"""Reconciler: unmatched books for a category, keyed by supplier GSTIN."""

	@functools.wraps(original)
	def get_unmatched_purchase_or_bill_of_entry(self, category, is_return=0):
		data = original(self, category, is_return)
		if category in B2B_CATEGORIES and not is_return and reconciliation_active():
			for doc in books(self, self.purchase_from_date).get_unmatched(category):
				data.setdefault(doc.supplier_gstin, {})[doc.name] = doc
		return data

	get_unmatched_purchase_or_bill_of_entry._qe_wrapped = True
	return get_unmatched_purchase_or_bill_of_entry


def wrap_get_all_purchases(original):
	"""ReconciledData: books rows shown in the tool (linked ones plus unmatched in the period)."""

	@functools.wraps(original)
	def get_all_purchase_invoice_and_bill_of_entry(self, inward_supplies, purchase_names, only_names=False):
		data = original(self, inward_supplies, purchase_names, only_names) or {}
		if not reconciliation_active():
			return data
		if only_names:
			names = list(purchase_names or [])
		else:
			names = list(
				{doc.link_name for doc in inward_supplies or [] if doc.get("link_doctype") == DOCTYPE}
			)
		fields = [
			"supplier",
			"supplier_name",
			"company_gstin",
			"is_return",
			"gst_category",
			"reconciliation_status",
			"itc_claim_period",
			*self.dimension_fields,
		]
		for doc in books(self).get_all(fields, names, only_names) or []:
			data[doc.name] = doc
		return data

	get_all_purchase_invoice_and_bill_of_entry._qe_wrapped = True
	return get_all_purchase_invoice_and_bill_of_entry


def _journal_entry_rows(data):
	return [row for row in frappe.parse_json(data) if row.get("purchase_doctype") == DOCTYPE]


def wrap_apply_action(original):
	@functools.wraps(original)
	def apply_action(self, data, action):
		result = original(self, data, action)
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_tool import (
			STATUS_MAP,
		)
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			set_reconciliation_status,
		)

		names = []
		for row in _journal_entry_rows(data):
			is_linked = row.get("inward_supply_name") and row.get("purchase_invoice_name")
			if (action == "Ignore" and is_linked) or ("Accept" in action and not is_linked):
				continue  # same rule India Compliance applies to its own doctypes
			names.append(row.get("purchase_invoice_name"))
		set_reconciliation_status(DOCTYPE, names, STATUS_MAP.get(action))
		return result

	apply_action._qe_wrapped = True
	return apply_action


def wrap_unlink_documents(original):
	@functools.wraps(original)
	def unlink_documents(self, data, exclude_from_reconciliation=False):
		rows = frappe.parse_json(data)
		result = original(self, data, exclude_from_reconciliation)
		names = [
			r.get("purchase_invoice_name") for r in _journal_entry_rows(rows) if r.get("inward_supply_name")
		]
		if not names:
			return result
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			set_reconciliation_status,
		)

		set_reconciliation_status(DOCTYPE, names, "Unreconciled")
		purchases = [r.get("purchase_invoice_name") for r in rows if r.get("purchase_invoice_name")]
		inward = [r.get("inward_supply_name") for r in rows if r.get("inward_supply_name")]
		return self.ReconciledData.get(purchases, inward)

	unlink_documents._qe_wrapped = True
	return unlink_documents


def wrap_link_documents(original):
	@functools.wraps(original)
	def link_documents(self, purchase_invoice_name, inward_supply_name, link_doctype):
		result = original(self, purchase_invoice_name, inward_supply_name, link_doctype)
		return result

	link_documents._qe_wrapped = True
	return link_documents


def wrap_get_link_options(original):
	"""Options for the Link dialog when the user picks Journal Entry."""

	@functools.wraps(original)
	def get_link_options(self, doctype, filters):
		if doctype != DOCTYPE:
			return original(self, doctype, filters)
		frappe.has_permission("Purchase Reconciliation Tool", "write", throw=True)
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			get_formatted_options,
		)

		filters = frappe._dict(filters or {})
		filters.from_date = filters.get("from_date") or self.ReconciledData.purchase_from_date
		filters.to_date = filters.get("to_date") or self.to_date
		source = books(self.ReconciledData)
		query = (
			source.get_query(["gst_category", "is_return"])
			.where(source.JE.qe_supplier_gstin.like(f"%{filters.get('supplier_gstin') or ''}%"))
			.where(source.JE.posting_date[filters.from_date : filters.to_date])
		)
		if not filters.get("show_matched"):
			query = query.where(source.JE.name.notin(QuickExpenseJournalEntry.query_matched()))
		return {"options": get_formatted_options(query.run(as_dict=True)), "filters": filters}

	get_link_options._qe_wrapped = True
	return get_link_options


def wrap_reconcile_and_generate_data(original):
	@functools.wraps(original)
	def reconcile_and_generate_data(self):
		return original(self)

	reconcile_and_generate_data._qe_wrapped = True
	return reconcile_and_generate_data


def whitelisted(factory):
	"""Tool methods are called from the browser: the wrapper itself must be whitelisted."""

	def make(original):
		wrapper = frappe.whitelist()(factory(original))
		wrapper._qe_wrapped = True
		return wrapper

	return make


def apply_patches(patch):
	from india_compliance.gst_india.doctype.purchase_reconciliation_tool import (
		BaseReconciliation,
		ReconciledData,
	)
	from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_tool import (
		PurchaseReconciliationTool,
	)

	patch(BaseReconciliation, "get_unmatched_purchase_or_bill_of_entry", wrap_get_unmatched_purchases)
	patch(ReconciledData, "get_all_purchase_invoice_and_bill_of_entry", wrap_get_all_purchases)
	patch(PurchaseReconciliationTool, "apply_action", whitelisted(wrap_apply_action))
	patch(PurchaseReconciliationTool, "unlink_documents", whitelisted(wrap_unlink_documents))
	patch(PurchaseReconciliationTool, "link_documents", whitelisted(wrap_link_documents))
	patch(PurchaseReconciliationTool, "get_link_options", whitelisted(wrap_get_link_options))
	patch(
		PurchaseReconciliationTool,
		"reconcile_and_generate_data",
		whitelisted(wrap_reconcile_and_generate_data),
	)
