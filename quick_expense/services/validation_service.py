"""Business rules and edge cases (prompt sections 2A, 6, 11 and 12).

Two entry points:

* ``prepare(doc)`` runs on every save: fills defaults, resolves the posting mode,
  place of supply and reverse charge, and recalculates tax. It throws only for
  things that make a calculation impossible, so drafts (and Data Import) save.
* ``validate_for_submit(doc)`` runs when the Journal Entry is submitted. It
  applies every rule and returns a Result with the warnings to show the user.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from quick_expense.constants import (
	COMPOSITION,
	GSTIN_FILLED_SOURCES,
	GSTIN_REQUIRED_TREATMENTS,
	INVOICE_REQUIRED_TREATMENTS,
	NON_GST,
	NOT_APPLICABLE,
	OVERSEAS,
	REGISTERED,
	SEZ,
)
from quick_expense.services import duplicate_service, gstin_service, tax_engine
from quick_expense.services.settings_service import get_company_defaults, get_settings

GST_INVOICE_NO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/]*$")
# warn when the ITC time limit (Section 16(4)) ends within this many days
ITC_DEADLINE_WARNING_DAYS = 30


class QuickExpenseValidationError(frappe.ValidationError):
	pass


class Result:
	def __init__(self):
		self.warnings: list[str] = []

	def warn(self, message):
		if message and message not in self.warnings:
			self.warnings.append(message)


def throw(message, title=None):
	frappe.throw(message, QuickExpenseValidationError, title=title or _("Quick Expense"))


def get_categories(doc) -> dict:
	"""Per-row GST rules of the bill (template, HSN, location) keyed like the old categories."""
	return doc.rules


# ============================================================================
# prepare (every save)


def prepare(doc, result: Result | None = None):
	result = result or Result()
	settings = get_settings()

	if not doc.lines:
		throw(_("Add at least one expense row."))

	set_header_defaults(doc, settings)
	categories = get_categories(doc)

	normalise_supplier(doc)
	fill_from_gstin(doc)
	set_line_defaults(doc, categories, settings)

	# reverse charge is ticked on the bill (import of services always is)
	doc.is_reverse_charge = cint(tax_engine.auto_reverse_charge(doc))
	doc.flags.auto_reverse_charge = doc.is_reverse_charge

	# place of supply: the Company GSTIN state, or where the service is performed (hotel, property)
	doc.place_of_supply = tax_engine.auto_place_of_supply(doc)
	doc.flags.auto_place_of_supply = doc.place_of_supply

	# a draft is claimed in its posting month; IMS moves a submitted bill to "Deferred" or a later month
	if doc.posting_date:
		doc.itc_claim_period = tax_engine.period_of(doc.posting_date)

	time_limit_expired = False
	if doc.supplier_invoice_date and doc.gst_treatment != NON_GST:
		effective = max(
			getdate(doc.posting_date),
			tax_engine.period_start(doc.itc_claim_period) or getdate(doc.posting_date),
		)
		time_limit_expired = effective > tax_engine.itc_time_limit(doc.supplier_invoice_date)

	for reason in tax_engine.calculate(
		doc, categories, time_limit_expired=time_limit_expired, settings=settings
	):
		result.warn(reason)

	doc.title = (doc.supplier_legal_name or doc.supplier_trade_name or _("Quick Expense"))[:140]
	return result


def set_header_defaults(doc, settings):
	defaults = get_company_defaults(doc.company)

	if not doc.company_gstin:
		doc.company_gstin = defaults.company_gstin
	if not doc.company_gstin:
		throw(_("Select the Company GSTIN this bill is addressed to."))

	doc.company_gstin = gstin_service.normalise_gstin(doc.company_gstin)
	address = gstin_service.get_company_gstin_address(doc.company, doc.company_gstin)
	if not address:
		throw(
			_(
				"GSTIN {0} is not a GST address of company {1}. Choose one of the company's own GSTINs."
			).format(doc.company_gstin, doc.company),
			_("Wrong Company GSTIN"),
		)
	doc.company_address = address

	if not doc.cost_center:
		doc.cost_center = defaults.cost_center

	if doc.paid_through_account:
		account_type = frappe.get_cached_value("Account", doc.paid_through_account, "account_type")
		doc.payment_type = account_type if account_type in ("Cash", "Bank") else ""
		if (
			not doc.mode_of_payment
			and doc.payment_type == "Cash"
			and frappe.db.exists("Mode of Payment", "Cash")
		):
			doc.mode_of_payment = "Cash"


def normalise_supplier(doc):
	if doc.gst_treatment in GSTIN_REQUIRED_TREATMENTS:
		doc.supplier_gstin = gstin_service.normalise_gstin(doc.supplier_gstin)
		if doc.supplier_gstin and not gstin_service.get_validation_error(doc.supplier_gstin):
			doc.supplier_pan = gstin_service.pan_from_gstin(doc.supplier_gstin)
			if not doc.supplier_state:
				doc.supplier_state = gstin_service.state_from_gstin(doc.supplier_gstin)
			if not doc.supplier_constitution:
				doc.supplier_constitution = gstin_service.constitution_from_pan(doc.supplier_pan)
	else:
		doc.supplier_gstin = ""
		doc.gstin_status = doc.supplier_registration_type = doc.gstin_details_source = ""
		if doc.gst_treatment == OVERSEAS:
			doc.supplier_state = ""
		elif not doc.supplier_state:
			doc.supplier_state = gstin_service.state_from_gstin(doc.company_gstin)

	if doc.supplier_invoice_no:
		doc.supplier_invoice_no = doc.supplier_invoice_no.strip()


def fill_from_gstin(doc):
	"""Fill the supplier's name, address and state from the GST portal (read-only fields)
	when the GSTIN is new or changed. Blocking checks on the GSTIN run at submission."""
	if doc.gst_treatment not in GSTIN_REQUIRED_TREATMENTS or not doc.supplier_gstin:
		return
	if error := gstin_service.get_validation_error(doc.supplier_gstin):
		throw(error, _("Invalid Supplier GSTIN"))
	je = getattr(doc, "je", None)
	before = je.get_doc_before_save() if je is not None and not je.is_new() else None
	if before and before.get("qe_supplier_gstin") == doc.supplier_gstin and doc.supplier_legal_name:
		return
	try:
		details = gstin_service.get_gstin_details(doc.supplier_gstin)
	except gstin_service.GSTINError:
		frappe.clear_last_message()  # e.g. not on the portal: reported when the bill is submitted
		details = frappe._dict(source="Manual", state=gstin_service.state_from_gstin(doc.supplier_gstin))
	if details.source not in GSTIN_FILLED_SOURCES:
		doc.supplier_name_entered_manually = 1
		doc.supplier_state = doc.supplier_state or details.state
		return
	# The same trap as `apply_gstin_details`, in the other direction: this set
	# the flag to 0 unconditionally, so a portal reply with no name left the
	# field blank *and* read-only — and `validate` then refused the save with
	# a message the user could not act on.
	name = details.resolved_name or doc.supplier_legal_name
	doc.supplier_name_entered_manually = 0 if name else 1
	doc.supplier_legal_name = name
	doc.supplier_trade_name = details.trade_name
	doc.supplier_state = details.state
	doc.supplier_address = details.address
	doc.supplier_pincode = details.pincode
	doc.gstin_status = details.status


def set_line_defaults(doc, categories, settings):
	for line in doc.lines:
		if not line.cost_center:
			line.cost_center = doc.cost_center


# ============================================================================
# full validation (on submit)


def validate_for_submit(doc) -> Result:
	settings = get_settings()
	result = Result()
	prepare(doc, result)
	categories = get_categories(doc)

	if not cint(settings.enable_quick_expense):
		throw(_("Quick Expense is disabled in Quick Expense Settings."))

	validate_company(doc)
	validate_supplier(doc, settings, result)
	validate_invoice(doc, settings, result)
	validate_itc_claim_period(doc, result)
	validate_lines(doc, categories, settings, result)
	validate_amounts(doc, settings, result)
	validate_payment(doc, settings, result)
	validate_cost_centers(doc, settings)
	validate_period(doc, settings)
	set_attachment_hash(doc)

	duplicate_service.check_duplicate(doc)
	result.warn(duplicate_service.unregistered_duplicate_warning(doc))
	result.warn(duplicate_service.attachment_hash_warning(doc))

	check_ineligible_gst(doc, result)
	return result


def validate_company(doc):
	country, currency = frappe.get_cached_value("Company", doc.company, ("country", "default_currency"))
	if country != "India":
		throw(
			_("Quick Expense is only for Indian companies. Company {0} is in {1}.").format(
				doc.company, country
			)
		)
	if currency != "INR":
		throw(_("Quick Expense supports only INR. Company {0} uses {1}.").format(doc.company, currency))


def validate_supplier(doc, settings, result):
	treatment = doc.gst_treatment

	if treatment in GSTIN_REQUIRED_TREATMENTS:
		if not doc.supplier_gstin:
			throw(_("Supplier GSTIN is mandatory for GST Treatment {0}.").format(frappe.bold(treatment)))
		gstin_service.validate_gstin(doc.supplier_gstin)
		gstin_service.validate_not_own_gstin(doc.supplier_gstin, doc.company, doc.company_gstin)
		apply_gstin_details(doc, settings, result)
	elif treatment == OVERSEAS:
		if any(line.supply_type == "Goods" for line in doc.lines):
			throw(
				_(
					"Import of goods needs a Bill of Entry and cannot be booked in Quick Expense. Overseas suppliers are allowed only for services (import of services under reverse charge)."
				)
			)

	if not (doc.supplier_legal_name or "").strip():
		throw(
			_("Enter the supplier legal name.")
			if treatment in GSTIN_REQUIRED_TREATMENTS
			else _("Enter the shop or supplier name printed on the bill.")
		)


def apply_gstin_details(doc, settings, result):
	details = gstin_service.get_gstin_details(doc.supplier_gstin)
	doc.gstin_details_source = details.source
	doc.supplier_pan = details.pan

	if details.source in GSTIN_FILLED_SOURCES:
		doc.gstin_status = details.status
		doc.supplier_registration_type = details.registration_type
		# `resolved_name`, so the save path and the form agree about what this
		# supplier is called. The guard stays: a portal that answered with no
		# name at all must not wipe a name the user typed.
		if details.resolved_name:
			doc.supplier_legal_name = details.resolved_name
			doc.supplier_trade_name = details.trade_name
			doc.supplier_name_entered_manually = 0
		elif not (doc.supplier_legal_name or "").strip():
			# Nothing from the portal and nothing typed: leave the field
			# unlocked so `validate`'s refusal below is one the user can act on.
			doc.supplier_name_entered_manually = 1
		if details.state:
			doc.supplier_state = details.state
		if details.address:
			doc.supplier_address = details.address
			doc.supplier_pincode = details.pincode
		if details.constitution:
			doc.supplier_constitution = details.body_corporate
	else:
		doc.supplier_name_entered_manually = 1
		doc.supplier_state = doc.supplier_state or details.state
		result.warn(details.message)

	if gstin_service.is_blocking_status(doc.gstin_status):
		message = (
			_("ITC is not available on invoices from a cancelled GSTIN.")
			+ " "
			+ _("GSTIN {0} status is {1}.").format(doc.supplier_gstin, frappe.bold(doc.gstin_status))
		)
		throw(message, _("GSTIN Not Active"))

	registration = doc.supplier_registration_type
	if registration in (REGISTERED, COMPOSITION, SEZ) and registration != doc.gst_treatment:
		previous = doc.gst_treatment
		doc.gst_treatment = registration
		result.warn(
			_("GST Treatment changed from {0} to {1} as per the GST portal registration of {2}.").format(
				previous, registration, doc.supplier_gstin
			)
		)
		prepare(doc, result)  # treatment change alters tax


def validate_invoice(doc, settings, result):
	if doc.gst_treatment in INVOICE_REQUIRED_TREATMENTS:
		if not doc.supplier_invoice_no:
			throw(_("Supplier Invoice No is mandatory for a registered supplier."))
		if not doc.supplier_invoice_date:
			throw(_("Supplier Invoice Date is mandatory for a registered supplier."))

	posting_date = getdate(doc.posting_date)
	if posting_date > getdate(nowdate()):
		throw(_("Posting Date cannot be in the future."))

	if doc.supplier_invoice_date:
		invoice_date = getdate(doc.supplier_invoice_date)
		if invoice_date > getdate(nowdate()):
			throw(
				_("Supplier Invoice Date {0} is in the future.").format(frappe.format(invoice_date, "Date"))
			)
		if invoice_date > posting_date:
			throw(_("Supplier Invoice Date cannot be after the Posting Date."))

		deadline = tax_engine.itc_time_limit(invoice_date)
		effective = max(posting_date, tax_engine.period_start(doc.itc_claim_period) or posting_date)
		if effective > deadline:
			result.warn(
				_(
					"ITC time limit for this bill ended on {0}. GST on this bill is treated as Ineligible Others (time limit expired)."
				).format(frappe.format(deadline, "Date"))
			)
		elif (deadline - effective).days <= ITC_DEADLINE_WARNING_DAYS:
			result.warn(
				_("ITC on this bill must be claimed by {0} ({1} days left).").format(
					frappe.format(deadline, "Date"), (deadline - effective).days
				)
			)

	if doc.supplier_invoice_no:
		if len(doc.supplier_invoice_no) > 16:
			result.warn(
				_(
					"Supplier Invoice No {0} is longer than 16 characters; a GST invoice number cannot exceed 16 characters. Check it against the bill."
				).format(doc.supplier_invoice_no)
			)
		elif not GST_INVOICE_NO.match(doc.supplier_invoice_no):
			result.warn(
				_(
					"Supplier Invoice No {0} contains characters other than letters, digits, hyphen and slash."
				).format(doc.supplier_invoice_no)
			)


def validate_itc_claim_period(doc, result):
	period = doc.itc_claim_period
	start = tax_engine.period_start(period)
	if not start:
		throw(
			_("ITC Claim Period must be in MMYYYY format, for example {0}.").format(
				tax_engine.period_of(doc.posting_date)
			)
		)

	if is_gstr3b_filed(doc.company_gstin, period):
		throw(
			_(
				"GSTR-3B for {0} is already filed for GSTIN {1}. Use a posting date in a month whose return is not filed yet."
			).format(period, doc.company_gstin),
			_("Return Filed"),
		)


def is_gstr3b_filed(company_gstin, period) -> bool:
	if not frappe.db.table_exists("GST Return Log"):
		return False
	return bool(
		frappe.db.exists(
			"GST Return Log",
			{
				"gstin": company_gstin,
				"return_type": "GSTR3B",
				"return_period": period,
				"filing_status": "Filed",
			},
		)
	)


def validate_lines(doc, categories, settings, result):
	for line in doc.lines:
		if flt(line.taxable_amount) <= 0:
			throw(
				_(
					"Row {0}: Taxable Amount must be more than zero. Supplier credit notes and refunds must use a standard Purchase Invoice return."
				).format(line.idx)
			)

		if not line.expense_account:
			throw(_("Row {0}: select the expense account.").format(line.idx))
		account = frappe.get_cached_value(
			"Account",
			line.expense_account,
			("company", "is_group", "root_type", "freeze_account", "disabled"),
			as_dict=True,
		)
		if not account:
			throw(_("Row {0}: Expense Account {1} does not exist.").format(line.idx, line.expense_account))
		if account.company != doc.company:
			throw(
				_("Row {0}: Expense Account {1} belongs to another company.").format(
					line.idx, line.expense_account
				)
			)
		if account.is_group:
			throw(
				_("Row {0}: Expense Account {1} is a group account.").format(line.idx, line.expense_account)
			)
		if account.root_type in ("Asset", "Liability", "Equity"):
			throw(
				_(
					"Row {0}: {1} is a {2} account. Capital purchases and balance sheet items must use the standard Purchase Invoice."
				).format(line.idx, line.expense_account, account.root_type),
				_("Capital Purchase"),
			)
		if account.root_type != "Expense":
			throw(_("Row {0}: {1} is not an expense account.").format(line.idx, line.expense_account))
		if account.freeze_account == "Yes" or account.disabled:
			throw(
				_("Row {0}: Expense Account {1} is frozen or disabled.").format(
					line.idx, line.expense_account
				)
			)


def validate_amounts(doc, settings, result):
	if flt(doc.grand_total) <= 0:
		throw(_("Grand Total must be more than zero."))


def validate_payment(doc, settings, result):
	if not doc.paid_through_account:
		throw(_("Select the Paid Through cash or bank account."))
	account = frappe.get_cached_value(
		"Account",
		doc.paid_through_account,
		("company", "account_type", "account_currency", "is_group", "freeze_account", "disabled"),
		as_dict=True,
	)
	if account.company != doc.company:
		throw(_("Paid Through account {0} belongs to another company.").format(doc.paid_through_account))
	if account.account_type not in ("Cash", "Bank"):
		throw(_("Paid Through account must be a Cash or Bank account."))
	if account.is_group or account.freeze_account == "Yes" or account.disabled:
		throw(
			_("Paid Through account {0} is a group, frozen or disabled account.").format(
				doc.paid_through_account
			)
		)
	if (account.account_currency or "INR") != "INR":
		throw(
			_("Paid Through account {0} is in {1}. Only INR cash and bank accounts can be used.").format(
				doc.paid_through_account, account.account_currency
			)
		)

	if doc.payment_type == "Bank":
		if not doc.reference_no or not doc.reference_date:
			throw(_("Reference No and Reference Date are mandatory for a bank payment."))
		if getdate(doc.reference_date) > getdate(doc.posting_date):
			throw(_("Reference Date cannot be after the Posting Date."))


def validate_cost_centers(doc, settings):
	for label, cost_center in [(_("Header"), doc.cost_center)] + [
		(_("Row {0}").format(line.idx), line.cost_center) for line in doc.lines
	]:
		if not cost_center:
			continue
		company, is_group = frappe.get_cached_value("Cost Center", cost_center, ("company", "is_group"))
		if company != doc.company:
			throw(_("{0}: Cost Center {1} belongs to another company.").format(label, cost_center))
		if is_group:
			throw(_("{0}: Cost Center {1} is a group cost center.").format(label, cost_center))


def validate_period(doc, settings):
	from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

	try:
		get_fiscal_year(doc.posting_date, company=doc.company)
	except FiscalYearError:
		throw(
			_("Posting Date {0} is not in any open Fiscal Year of {1}.").format(doc.posting_date, doc.company)
		)

	frozen_till = frappe.db.get_value("Company", doc.company, "accounts_frozen_till_date")
	if frozen_till and getdate(doc.posting_date) <= getdate(frozen_till):
		role = frappe.db.get_value("Company", doc.company, "role_allowed_for_frozen_entries")
		if not role or role not in frappe.get_roles():
			throw(
				_("Accounts of {0} are frozen till {1}.").format(
					doc.company, frappe.format(frozen_till, "Date")
				)
			)

	target_doctype = "Journal Entry"
	period = frappe.qb.DocType("Accounting Period")
	closed = frappe.qb.DocType("Closed Document")
	rows = (
		frappe.qb.from_(period)
		.join(closed)
		.on(closed.parent == period.name)
		.select(period.name, period.exempted_role)
		.where(period.company == doc.company)
		.where(period.disabled == 0)
		.where(closed.closed == 1)
		.where(closed.document_type == target_doctype)
		.where(period.start_date <= doc.posting_date)
		.where(period.end_date >= doc.posting_date)
	).run(as_dict=True)
	if rows and not (rows[0].exempted_role and rows[0].exempted_role in frappe.get_roles()):
		throw(_("Accounting Period {0} is closed for {1}.").format(rows[0].name, target_doctype))


def set_attachment_hash(doc):
	"""Fingerprint of the attached bill, used to warn when the same file is booked twice."""
	if not doc.bill_attachment:
		doc.attachment_hash = ""
		return
	doc.attachment_hash = (
		frappe.db.get_value(
			"File", {"file_url": doc.bill_attachment}, "content_hash", order_by="creation desc"
		)
		or ""
	)


def check_ineligible_gst(doc, result):
	for line in doc.lines:
		if (
			line.eligibility_for_itc == "Ineligible Others"
			and not (line.eligibility_change_reason or "").strip()
		):
			throw(_("Row {0}: give the reason the GST is Ineligible Others.").format(line.idx))

	if not cint(doc.bill_addressed_to_company_gstin) and doc.gst_treatment != OVERSEAS and doc.total_gst:
		result.warn(
			_(
				"The bill is not addressed to {0}, so GST on it is Ineligible Others (Bill not addressed to this GSTIN)."
			).format(doc.company_gstin)
		)
