"""A Quick Expense Journal Entry seen as a GST expense bill.

The tax engine and validations work with "bill" field names
(``supplier_gstin``, ``lines``, ``line.taxable_amount`` …). ``Bill`` maps those
names onto the Journal Entry's ``qe_*`` fields and its expense rows, so every
rule is written once and the Journal Entry stays the only document.
Names without a Journal Entry field (``in_words``, ``supplier_pan`` …) are kept
on the Bill object only.
"""

import frappe
from frappe.utils import cint, flt

from quick_expense.constants import (
	ELIGIBLE,
	EXPENSE_ROW,
	SERVICE_HSN_PREFIX,
	SYSTEM_COMPONENTS,
	TAXABLE,
)

HEADER_MAP = {
	"gst_treatment": "qe_gst_treatment",
	"supplier_gstin": "qe_supplier_gstin",
	"supplier_legal_name": "qe_supplier_legal_name",
	"supplier_trade_name": "qe_supplier_trade_name",
	"supplier_state": "qe_supplier_state",
	"supplier_address": "qe_supplier_address",
	"supplier_pincode": "qe_supplier_pincode",
	"gstin_status": "qe_gstin_status",
	"supplier_name_entered_manually": "qe_supplier_name_entered_manually",
	"supplier_invoice_no": "qe_supplier_invoice_no",
	"supplier_invoice_date": "qe_supplier_invoice_date",
	"bill_addressed_to_company_gstin": "qe_bill_addressed_to_company_gstin",
	"is_reverse_charge": "qe_is_reverse_charge",
	"itc_claim_period": "qe_itc_claim_period",
	"place_of_supply": "qe_place_of_supply",
	"self_invoice_no": "qe_self_invoice_no",
	"paid_through_account": "qe_paid_through_account",
	"payment_type": "qe_payment_type",
	"reference_no": "cheque_no",
	"reference_date": "cheque_date",
	"remarks": "user_remark",
	"bill_attachment": "qe_bill_attachment",
	"total_taxable": "qe_total_taxable",
	"total_cgst": "qe_total_cgst",
	"total_sgst": "qe_total_sgst",
	"total_igst": "qe_total_igst",
	"total_cess": "qe_total_cess",
	"total_gst": "qe_total_gst",
	"total_eligible_itc": "qe_total_eligible_itc",
	"total_ineligible_gst": "qe_total_ineligible_gst",
	"total_reverse_charge_tax": "qe_total_reverse_charge_tax",
	"round_off": "qe_round_off",
	"grand_total": "qe_grand_total",
	"cancel_reason": "qe_cancel_reason",
	"duplicate_key": "qe_bill_key",
	"linked_journal_entry": "name",
}
# same name on the Journal Entry
PASS_THROUGH = {
	"name",
	"doctype",
	"company",
	"company_gstin",
	"posting_date",
	"owner",
	"docstatus",
	"mode_of_payment",
	"amended_from",
}

LINE_MAP = {
	"description": "user_remark",
	"taxable_amount": "qe_taxable_amount",
	"item_tax_template": "qe_item_tax_template",
	"hsn_sac": "qe_hsn_sac",
	"eligibility_change_reason": "qe_eligibility_reason",
	"ineligibility_reason": "qe_ineligibility_reason",
	"location_of_service_state": "qe_location_of_service_state",
	"gst_as_per_bill": "qe_gst_as_per_bill",
	"gst_rate": "qe_gst_rate",
	"gst_treatment": "qe_gst_treatment",
	"supply_type": "qe_supply_type",
	"cgst_amount": "qe_cgst_amount",
	"sgst_amount": "qe_sgst_amount",
	"igst_amount": "qe_igst_amount",
	"cess_amount": "qe_cess_amount",
	"expense_account": "account",
	"cost_center": "cost_center",
	"idx": "idx",
	"name": "name",
}
ELIGIBILITY_OPTIONS = ("All Other ITC", "Ineligible under Section 17(5)", "Ineligible Others")


def get_bill(name: str) -> "Bill":
	return Bill(frappe.get_doc("Journal Entry", name))


def is_expense_row(row) -> bool:
	return (row.get("qe_gst_component") or "") not in SYSTEM_COMPONENTS


class Line:
	"""One expense row of the Journal Entry."""

	def __init__(self, row):
		object.__setattr__(self, "_row", row)
		object.__setattr__(self, "_extra", {})
		if not row.name:
			row.name = frappe.generate_hash(length=10)
		self._extra["expense_category"] = row.name  # key into Bill.rules

	def __getattr__(self, key):
		row, extra = self._row, self._extra
		if key == "eligibility_for_itc":
			return extra.get(key, row.get("qe_eligibility") or "")
		if key == "gst_as_per_bill_entered":
			return 1 if flt(row.get("qe_gst_as_per_bill")) else 0
		if key in LINE_MAP:
			return row.get(LINE_MAP[key])
		if key in extra:
			return extra[key]
		return self._stored(key)

	def _stored(self, key):
		"""Figures of a saved row, for a bill read back from the database."""
		row = self._row
		tax = (
			flt(row.get("qe_cgst_amount"))
			+ flt(row.get("qe_sgst_amount"))
			+ flt(row.get("qe_igst_amount"))
			+ flt(row.get("qe_cess_amount"))
		)
		if key == "total_tax":
			return flt(tax, 2)
		if key == "eligible_itc_amount":
			return 0 if row.get("qe_ineligibility_reason") else flt(tax, 2)
		if key == "ineligible_gst_amount":
			return flt(flt(row.get("debit_in_account_currency")) - flt(row.get("qe_taxable_amount")), 2)
		if key == "tax_difference":
			return (
				flt(flt(row.get("qe_gst_as_per_bill")) - tax, 2) if flt(row.get("qe_gst_as_per_bill")) else 0
			)
		if key == "line_total":
			return flt(row.get("debit_in_account_currency"), 2)
		return None

	def __setattr__(self, key, value):
		if key == "eligibility_for_itc":
			self._extra[key] = value
			if value in ELIGIBILITY_OPTIONS:
				self._row.qe_eligibility = value  # "Not Applicable" (no GST) stays on the bill only
			return
		if key in LINE_MAP:
			self._row.set(LINE_MAP[key], value)
		else:
			self._extra[key] = value

	get = __getattr__
	set = __setattr__


class Bill:
	def __init__(self, je):
		object.__setattr__(self, "je", je)
		object.__setattr__(self, "_extra", {})
		object.__setattr__(self, "lines", [Line(row) for row in je.accounts if is_expense_row(row)])
		object.__setattr__(self, "rules", self._rules())

	# ---------------------------------------------------------------- attribute mapping
	def __getattr__(self, key):
		je = self.je
		if key in HEADER_MAP:
			return je.get(HEADER_MAP[key])
		if key in PASS_THROUGH:
			return je.get(key)
		if key == "doctype":
			return je.doctype
		if key == "flags":
			return je.flags
		return self._extra.get(key)

	def __setattr__(self, key, value):
		if key in HEADER_MAP:
			if HEADER_MAP[key] != "name":
				self.je.set(HEADER_MAP[key], value)
		elif key in PASS_THROUGH:
			self.je.set(key, value)
		else:
			self._extra[key] = value

	def get(self, key, default=None):
		value = getattr(self, key)
		return default if value is None else value

	def set(self, key, value):
		setattr(self, key, value)

	# ---------------------------------------------------------------- document methods
	@property
	def doctype(self):
		return self.je.doctype

	@property
	def flags(self):
		return self.je.flags

	def is_new(self):
		return self.je.is_new()

	def append(self, table, row):
		return self.je.append(HEADER_MAP.get(table, table), row)

	def check_permission(self, ptype):
		return self.je.check_permission(ptype)

	def save(self):
		self.je.save()
		self._refresh()

	def insert(self):
		self.je.insert()
		self._refresh()

	def reload(self):
		self.je.reload()
		self._refresh()

	def submit(self):
		self.je.submit()

	def db_set(self, *args, **kwargs):
		return self.je.db_set(*args, **kwargs)

	def add_comment(self, *args, **kwargs):
		return self.je.add_comment(*args, **kwargs)

	def cancel(self):
		self.je.cancel()
		self.reload()

	def _refresh(self):
		object.__setattr__(self, "lines", [Line(row) for row in self.je.accounts if is_expense_row(row)])
		object.__setattr__(self, "rules", self._rules())

	# ---------------------------------------------------------------- rules per line
	def _rules(self):
		"""What a category used to say, now read from the row (template, HSN, location)."""
		rules = {}
		for line in self.lines:
			template = line.item_tax_template
			gst_rate, treatment = (
				frappe.get_cached_value("Item Tax Template", template, ("gst_rate", "gst_treatment"))
				if template
				else (0, TAXABLE)
			)
			hsn = (line.hsn_sac or "").strip()
			rules[line.expense_category] = frappe._dict(
				category_name=line.description or "",
				supply_type="Goods" if hsn and not hsn.startswith(SERVICE_HSN_PREFIX) else "Services",
				gst_treatment_hint=treatment or TAXABLE,
				default_hsn_sac=hsn,
				default_gst_rate=flt(gst_rate),
				default_eligibility=line.eligibility_for_itc or ELIGIBLE,
				place_of_supply_rule="Location of Service"
				if line.location_of_service_state
				else "Recipient Location",
				tds_warning=0,
				is_active=1,
				rate_as_per_bill=1,
				allowed_gst_rates="",
				accounts=[],
			)
			line.gst_rate = flt(gst_rate) if template else 0
		return rules
