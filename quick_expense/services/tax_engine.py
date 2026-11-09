"""Place of supply, CGST / SGST / IGST / Cess, ITC eligibility, reverse charge and
rounding (prompt section 7).

Rates are never hard coded: the line's GST rate only selects an India Compliance
Item Tax Template for the company, and the CGST / SGST / IGST / Cess rates are
read from that template's rows.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, money_in_words

from quick_expense.constants import (
	COMPOSITION,
	ELIGIBLE,
	INELIGIBLE_17_5,
	INELIGIBLE_OTHERS,
	NON_GST,
	NOT_APPLICABLE,
	OVERSEAS,
	REASON_17_5,
	REASON_NOT_ADDRESSED,
	REASON_OTHERS,
	REASON_POS,
	REASON_TIME_LIMIT,
	REASON_UNREGISTERED,
	REGISTERED,
	SEZ,
	TAX_DIFFERENCE_TOLERANCE,
	TAXABLE,
	UNREGISTERED,
	ZERO_TAX_TREATMENTS,
)
from quick_expense.services.gstin_service import STATE_BY_CODE

OVERSEAS_STATE_CODE = "96"


class TaxError(frappe.ValidationError):
	pass


# ----------------------------------------------------------------------------
# small pure helpers


def format_pos(state_code: str) -> str:
	state_code = str(state_code).zfill(2)
	return f"{state_code}-{STATE_BY_CODE.get(state_code, '')}"


def state_code_of_state(state: str | None) -> str:
	if not state:
		return ""
	if "-" in state and state[:2].isdigit():
		return state[:2]
	for code, name in STATE_BY_CODE.items():
		if name.lower() == state.strip().lower():
			return code
	return ""


def gst_financial_year(date) -> tuple[datetime.date, datetime.date]:
	"""GST financial year is always April to March, whatever the company fiscal year is."""
	date = getdate(date)
	start_year = date.year if date.month >= 4 else date.year - 1
	return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)


def gst_financial_year_label(date) -> str:
	start, end = gst_financial_year(date)
	return f"{start.year}-{str(end.year)[2:]}"


def itc_time_limit(invoice_date) -> datetime.date:
	"""Section 16(4): ITC can be claimed until 30 November after the end of the invoice's financial year."""
	_start, end = gst_financial_year(invoice_date)
	return datetime.date(end.year, 11, 30)


def period_of(date) -> str:
	date = getdate(date)
	return f"{date.month:02d}{date.year}"


def period_start(period: str) -> datetime.date | None:
	if not period or len(period) != 6 or not period.isdigit():
		return None
	month, year = int(period[:2]), int(period[2:])
	if not 1 <= month <= 12:
		return None
	return datetime.date(year, month, 1)


def round_half_up(value: float, digits: int = 0) -> float:
	factor = 10**digits
	sign = -1 if value < 0 else 1
	return sign * int(abs(value) * factor + 0.5 + 1e-9) / factor


# ----------------------------------------------------------------------------
# template rates


@frappe.request_cache
def get_gst_account_types(company: str) -> dict[str, tuple[str, str]]:
	"""{account: (account_type, tax_type)} from India Compliance GST Settings for the company."""
	settings = frappe.get_cached_doc("GST Settings")
	result = {}
	for row in settings.gst_accounts:
		if row.company != company:
			continue
		for tax_type in ("cgst", "sgst", "igst", "cess", "cess_non_advol"):
			if account := row.get(f"{tax_type}_account"):
				result[account] = (row.account_type, tax_type)
	return result


@frappe.request_cache
def get_template_for_rate(company: str, rate: float, gst_treatment: str = TAXABLE) -> str | None:
	filters = {"company": company, "disabled": 0, "gst_treatment": gst_treatment}
	if gst_treatment == TAXABLE:
		filters["gst_rate"] = flt(rate)
	names = frappe.get_all("Item Tax Template", filters=filters, pluck="name", order_by="creation asc")
	return names[0] if names else None


@frappe.request_cache
def get_template_rates(template: str, company: str) -> frappe._dict:
	"""Input-side rates in a template: {cgst, sgst, igst, cess}"""
	account_types = get_gst_account_types(company)
	rates = frappe._dict(cgst=0.0, sgst=0.0, igst=0.0, cess=0.0)
	for row in frappe.get_all(
		"Item Tax Template Detail", filters={"parent": template}, fields=["tax_type", "tax_rate"]
	):
		account_type, tax_type = account_types.get(row.tax_type, (None, None))
		if account_type != "Input" or not tax_type:
			continue
		key = "cess" if tax_type.startswith("cess") else tax_type
		rates[key] = flt(row.tax_rate)
	return rates


# ----------------------------------------------------------------------------
# entry level decisions


def supplier_state_code(doc) -> str:
	if doc.gst_treatment == OVERSEAS:
		return OVERSEAS_STATE_CODE
	if doc.supplier_gstin and doc.gst_treatment in (REGISTERED, COMPOSITION, SEZ):
		return doc.supplier_gstin[:2]
	return state_code_of_state(doc.supplier_state)


def auto_reverse_charge(doc) -> bool:
	"""Reverse charge is ticked by the user on the bill; import of services is always reverse charge."""
	if doc.gst_treatment == NON_GST:
		return False
	if doc.gst_treatment == OVERSEAS:
		return True
	return bool(cint(doc.is_reverse_charge))


def auto_place_of_supply(doc) -> str:
	company_code = doc.company_gstin[:2]
	states = set()
	for line in doc.lines:
		if line.location_of_service_state:
			states.add(state_code_of_state(line.location_of_service_state) or company_code)
		else:
			states.add(company_code)

	if len(states) > 1:
		frappe.throw(
			_(
				"Lines have different places of supply ({0}). Services taxed where they are performed (such as a hotel in another state) must be entered as a separate entry."
			).format(", ".join(format_pos(code) for code in sorted(states))),
			TaxError,
			title=_("Mixed Place of Supply"),
		)
	return format_pos(states.pop() if states else company_code)


def is_gst_chargeable(doc) -> bool:
	"""Whether GST is computed at all on this entry."""
	if doc.gst_treatment == NON_GST:
		return False
	if doc.is_reverse_charge:
		return True
	return doc.gst_treatment in (REGISTERED, SEZ)


def is_inter_state(doc) -> bool:
	if doc.gst_treatment in (SEZ, OVERSEAS):
		return True
	pos_code = (doc.place_of_supply or "")[:2]
	return bool(pos_code) and supplier_state_code(doc) not in ("", pos_code)


# ----------------------------------------------------------------------------
# main calculation


def calculate(doc, categories: dict, *, time_limit_expired: bool = False, settings=None) -> list[str]:
	"""Recalculate every line and the totals in place. Returns notes for the user
	raised by the tax rules (rounding difference accepted, etc.)."""

	settings = settings or frappe.get_cached_doc("Quick Expense Settings")
	tolerance = TAX_DIFFERENCE_TOLERANCE
	reasons = []

	chargeable = is_gst_chargeable(doc)
	inter_state = is_inter_state(doc)
	pos_code = (doc.place_of_supply or "")[:2]
	pos_restricted = (
		chargeable
		and pos_code not in ("", doc.company_gstin[:2], OVERSEAS_STATE_CODE, "97")
		and doc.gst_treatment != OVERSEAS
	)

	totals = frappe._dict.fromkeys(
		(
			"total_taxable",
			"total_cgst",
			"total_sgst",
			"total_igst",
			"total_cess",
			"total_gst",
			"total_eligible_itc",
			"total_ineligible_gst",
			"total_reverse_charge_tax",
		),
		0.0,
	)
	payable = 0.0  # what the supplier is paid before rounding
	booked = 0.0  # what is debited to expense + input tax before rounding

	for line in doc.lines:
		category = categories[line.expense_category]
		line.supply_type = category.supply_type
		line.gst_treatment = category.gst_treatment_hint or TAXABLE
		line.default_eligibility = category.default_eligibility
		if not line.hsn_sac:
			line.hsn_sac = category.default_hsn_sac
		if line.gst_rate in (None, "") and line.gst_treatment == TAXABLE:
			line.gst_rate = category.default_gst_rate

		taxable = flt(line.taxable_amount, 2)
		line_has_tax = chargeable and line.gst_treatment == TAXABLE
		bill_gst = flt(line.gst_as_per_bill, 2) if cint(line.gst_as_per_bill_entered) else None

		for key in ("cgst", "sgst", "igst", "cess"):
			line.set(f"{key}_rate", 0)
			line.set(f"{key}_amount", 0)
		line.tax_difference = 0
		line.ineligibility_reason = ""

		# the GST rate is the India Compliance Item Tax Template picked on the row
		if line.item_tax_template:
			if frappe.get_cached_value("Item Tax Template", line.item_tax_template, "company") != doc.company:
				frappe.throw(
					_("Row {0}: Item Tax Template {1} belongs to another company.").format(
						line.idx, line.item_tax_template
					),
					TaxError,
				)
		elif line_has_tax:
			frappe.throw(
				_("Row {0}: select the GST rate (Item Tax Template) printed on the bill.").format(line.idx),
				TaxError,
				title=_("GST Rate Missing"),
			)

		if line_has_tax:
			rates = get_template_rates(line.item_tax_template, doc.company)
			if inter_state:
				line.igst_rate = rates.igst or flt(rates.cgst) + flt(rates.sgst)
			else:
				line.cgst_rate = rates.cgst or flt(rates.igst) / 2
				line.sgst_rate = rates.sgst or flt(rates.igst) / 2
			line.cess_rate = rates.cess
			for key in ("cgst", "sgst", "igst", "cess"):
				line.set(f"{key}_amount", flt(taxable * flt(line.get(f"{key}_rate")) / 100, 2))

		line.total_tax = flt(line.cgst_amount + line.sgst_amount + line.igst_amount + line.cess_amount, 2)

		# --- GST printed on the bill
		extra_cost = 0.0
		if bill_gst is not None and bill_gst:
			if doc.is_reverse_charge:
				frappe.throw(
					_(
						"Row {0}: this bill is under reverse charge, so the supplier must not charge GST. Remove the GST as per Bill or correct the reverse charge setting."
					).format(line.idx),
					TaxError,
				)
			if line.gst_treatment in ZERO_TAX_TREATMENTS:
				frappe.throw(
					_("Row {0}: {1} is {2}; the bill should not show GST.").format(
						line.idx, line.expense_category, line.gst_treatment
					),
					TaxError,
				)
			if doc.gst_treatment == COMPOSITION:
				frappe.throw(
					_(
						"Row {0}: a composition supplier cannot charge GST. Ask the supplier for a correct bill of supply."
					).format(line.idx),
					TaxError,
				)
			if not line_has_tax:
				# unregistered / non-GST supplier who printed GST: whole amount is cost
				extra_cost = bill_gst
			else:
				line.tax_difference = flt(bill_gst - line.total_tax, 2)
				if abs(line.tax_difference) > tolerance:
					frappe.throw(
						_(
							"Row {0}: GST as per bill is {1} but the calculated GST is {2}. The difference of {3} is more than the allowed tolerance of {4}. Please check the rate, the taxable amount or the bill."
						).format(
							line.idx,
							frappe.format(bill_gst, "Currency"),
							frappe.format(line.total_tax, "Currency"),
							frappe.format(abs(line.tax_difference), "Currency"),
							frappe.format(tolerance, "Currency"),
						),
						TaxError,
						title=_("Tax Difference Too High"),
					)
				if line.tax_difference:
					reasons.append(
						_("Row {0}: tax difference of {1} within tolerance used").format(
							line.idx, line.tax_difference
						)
					)

		# --- eligibility
		if not line.total_tax:
			if line.eligibility_for_itc != NOT_APPLICABLE and not line_has_tax:
				line.eligibility_for_itc = NOT_APPLICABLE
			eligible = False
			if extra_cost:
				line.ineligibility_reason = REASON_UNREGISTERED
		else:
			if line.eligibility_for_itc in (None, "", NOT_APPLICABLE):
				line.eligibility_for_itc = category.default_eligibility or ELIGIBLE
				if line.eligibility_for_itc == NOT_APPLICABLE:
					line.eligibility_for_itc = ELIGIBLE

			reason = ""
			if line.eligibility_for_itc == INELIGIBLE_17_5:
				reason = REASON_17_5
			elif line.eligibility_for_itc == INELIGIBLE_OTHERS:
				reason = REASON_OTHERS

			# system rules override the user's choice (the choice is kept so it comes back if the rule clears)
			if pos_restricted:
				reason = REASON_POS
			elif not cint(doc.bill_addressed_to_company_gstin) and doc.gst_treatment != OVERSEAS:
				reason = REASON_NOT_ADDRESSED
			elif time_limit_expired:
				reason = REASON_TIME_LIMIT

			line.ineligibility_reason = reason
			eligible = not reason

		line.eligible_itc_amount = line.total_tax if eligible else 0
		line.ineligible_gst_amount = flt((line.total_tax - line.eligible_itc_amount) + extra_cost, 2)

		if doc.is_reverse_charge:
			line.line_total = taxable
			totals.total_reverse_charge_tax += line.total_tax
		else:
			paid_tax = bill_gst if bill_gst is not None and line_has_tax else line.total_tax
			line.line_total = flt(taxable + paid_tax + extra_cost, 2)

		payable += line.line_total
		booked += taxable + extra_cost + (0 if doc.is_reverse_charge else line.total_tax)

		totals.total_taxable += taxable
		totals.total_cgst += line.cgst_amount
		totals.total_sgst += line.sgst_amount
		totals.total_igst += line.igst_amount
		totals.total_cess += line.cess_amount
		totals.total_gst += line.total_tax
		totals.total_eligible_itc += line.eligible_itc_amount
		totals.total_ineligible_gst += line.ineligible_gst_amount

	for key, value in totals.items():
		doc.set(key, flt(value, 2))

	payable = flt(payable, 2)
	grand_total = round_half_up(payable) if cint(settings.round_grand_total) else payable
	doc.grand_total = flt(grand_total, 2)
	# Dr round off when we pay more than we book (bill GST above calculated, or rounding up)
	doc.round_off = flt(doc.grand_total - flt(booked, 2), 2)
	doc.in_words = money_in_words(doc.grand_total, "INR")
	return reasons
