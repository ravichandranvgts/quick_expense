"""Mode B report overrides (prompt section 10).

Thin wrappers around India Compliance classes; the original method always runs
first and Quick Expense Journal Entry rows are appended in the exact shape of
Purchase Invoice item rows, so India Compliance's own categorisation puts them
in the right GSTR-3B tables and Purchase Register sections:

* GSTR3BInwardInvoices.get_data       -> GSTR-3B Table 4 / 5, GST Purchase Register, GSTR-3B Details
* GSTR3BOutwardInvoices.get_inward_invoices -> GSTR-3B Table 3.1(d) (reverse charge liability)
* Purchase Reconciliation Tool        -> Journal Entry as a books doctype (see purchase_reconciliation.py)
* gst_purchase_register.execute       -> only sets the "purchase register" context flag

Each wrapper is skipped when the compatibility guard has switched overrides off.
"""

import functools

import frappe
from frappe.query_builder.custom import ConstantColumn
from frappe.query_builder.functions import IfNull
from frappe.utils import cint

from quick_expense.constants import JE_VOUCHER_TYPE

CONTEXT_FLAG = "quick_expense_report_context"
GSTR3B = "gstr3b"
PURCHASE_REGISTER = "purchase_register"


def overrides_active(context=GSTR3B) -> bool:
	if frappe.flags.quick_expense_skip_overrides:
		return False
	try:
		settings = frappe.get_cached_doc("Quick Expense Settings")
	except Exception:
		return False
	if cint(settings.overrides_disabled):
		return False
	field = {GSTR3B: "include_in_gstr3b", PURCHASE_REGISTER: "include_in_purchase_register"}.get(context)
	# 2A / 2B reconciliation of Journal Entries is always on while the overrides are healthy
	return bool(cint(settings.get(field))) if field else True


def current_context():
	return frappe.flags.get(CONTEXT_FLAG) or GSTR3B


# ----------------------------------------------------------------------------
# data


def quick_expense_rows_query(filters, *, reverse_charge_only=False):
	"""Journal Entry expense rows of Quick Expense vouchers, shaped like Purchase Invoice item rows."""
	filters = frappe._dict(filters or {})
	je = frappe.qb.DocType("Journal Entry")
	row = frappe.qb.DocType("Journal Entry Account")
	tax = row.qe_cgst_amount + row.qe_sgst_amount + row.qe_igst_amount + row.qe_cess_amount

	query = (
		frappe.qb.from_(je)
		.inner_join(row)
		.on(row.parent == je.name)
		.select(
			ConstantColumn("Journal Entry").as_("voucher_type"),
			je.name.as_("voucher_no"),
			je.name.as_("invoice_no"),
			je.posting_date,
			IfNull(je.qe_itc_classification, "All Other ITC").as_("itc_classification"),
			IfNull(je.qe_ineligibility_reason, "").as_("ineligibility_reason"),
			IfNull(je.qe_place_of_supply, "").as_("place_of_supply"),
			IfNull(je.qe_gst_category, "").as_("gst_category"),
			je.company_gstin,
			IfNull(je.qe_supplier_gstin, "").as_("supplier_gstin"),
			je.qe_supplier_legal_name.as_("supplier_name"),
			je.qe_supplier_invoice_no.as_("bill_no"),
			je.qe_supplier_invoice_date.as_("bill_date"),
			je.qe_is_reverse_charge.as_("is_reverse_charge"),
			row.qe_is_ineligible_for_itc.as_("is_ineligible_for_itc"),
			row.account.as_("item_code"),
			IfNull(row.qe_gst_treatment, "").as_("gst_treatment"),
			row.qe_hsn_sac.as_("gst_hsn_code"),
			ConstantColumn("Nos").as_("uom"),
			ConstantColumn(1).as_("qty"),
			row.qe_gst_rate.as_("gst_rate"),
			row.qe_taxable_amount.as_("taxable_value"),
			row.qe_cgst_amount.as_("cgst_amount"),
			row.qe_sgst_amount.as_("sgst_amount"),
			row.qe_igst_amount.as_("igst_amount"),
			row.qe_cess_amount.as_("cess_amount"),
			row.qe_cess_amount.as_("total_cess_amount"),
			tax.as_("total_tax"),
			(row.qe_taxable_amount + tax).as_("total_amount"),
		)
		.where(je.voucher_type == JE_VOUCHER_TYPE)
		.where(je.docstatus == 1)
		.where(je.is_opening == "No")
		.where(IfNull(row.qe_source_line, "") != "")
		.where(je.company == filters.company)
	)
	if filters.company_gstin:
		query = query.where(je.company_gstin == filters.company_gstin)
	if reverse_charge_only:
		query = query.where(je.qe_is_reverse_charge == 1)

	if filters.get("filter_by") == "ITC Claim Period" and filters.from_date and filters.to_date:
		from india_compliance.gst_india.utils import get_periods_between_dates

		query = query.where(
			IfNull(je.qe_itc_claim_period, "").isin(
				get_periods_between_dates(filters.from_date, filters.to_date)
			)
		)
	elif filters.from_date and filters.to_date:
		query = query.where(je.posting_date[filters.from_date : filters.to_date])
	return query


def fetch_rows(filters, **kwargs):
	return quick_expense_rows_query(filters, **kwargs).run(as_dict=True)


# ----------------------------------------------------------------------------
# wrappers


def wrap_inward_get_data(original):
	@functools.wraps(original)
	def get_data(self, doctype, group_by_invoice=False):
		data = original(self, doctype, False)
		if (
			doctype == "Purchase Invoice"
			and overrides_active(current_context())
			and self.filters.get("company")
		):
			data = data + self.get_processed_invoices("Purchase Invoice", fetch_rows(self.filters))
		if not group_by_invoice:
			return data
		return self.get_invoice_wise_data(data)

	get_data._qe_wrapped = True
	return get_data


def wrap_outward_inward_invoices(original):
	@functools.wraps(original)
	def get_inward_invoices(self):
		data = original(self)
		if overrides_active(GSTR3B) and self.filters.get("company"):
			data = data + self.get_processed_invoices(
				"Purchase Invoice", fetch_rows(self.filters, reverse_charge_only=True)
			)
		return data

	get_inward_invoices._qe_wrapped = True
	return get_inward_invoices


def wrap_purchase_register_execute(original):
	@functools.wraps(original)
	def execute(filters=None):
		previous = frappe.flags.get(CONTEXT_FLAG)
		frappe.flags[CONTEXT_FLAG] = PURCHASE_REGISTER
		try:
			return original(filters)
		finally:
			frappe.flags[CONTEXT_FLAG] = previous

	execute._qe_wrapped = True
	return execute


def _patch(owner, attribute, wrapper):
	current = getattr(owner, attribute, None)
	if current is None:
		raise AttributeError(
			f"{owner.__name__ if hasattr(owner, '__name__') else owner}.{attribute} not found"
		)
	if getattr(current, "_qe_wrapped", False):
		return
	setattr(owner, attribute, wrapper(current))


_applied = False


def apply_patches():
	"""Idempotent per process. Called at request / job start (hooks) and by the guard."""
	global _applied
	if _applied:
		return
	try:
		from india_compliance.gst_india.report.gst_purchase_register import gst_purchase_register
		from india_compliance.gst_india.utils.gstr3b import gstr3b_inward_data, gstr3b_outward_data

		_patch(gstr3b_inward_data.GSTR3BInwardInvoices, "get_data", wrap_inward_get_data)
		_patch(gstr3b_outward_data.GSTR3BOutwardInvoices, "get_inward_invoices", wrap_outward_inward_invoices)
		_patch(gst_purchase_register, "execute", wrap_purchase_register_execute)
		from quick_expense.overrides import purchase_reconciliation

		purchase_reconciliation.apply_patches(_patch)
	except Exception:
		# never break a request because India Compliance changed; the guard reports it
		frappe.log_error(title="Quick Expense: could not apply India Compliance report overrides")
		return
	_applied = True
