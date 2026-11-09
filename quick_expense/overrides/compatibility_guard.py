"""Compatibility guard for the Mode B overrides (prompt sections 10.1 and 10.6).

On migrate and daily: if the installed India Compliance version differs from
the Verified India Compliance Version, run the self-test. On failure (and if
configured) the overrides are switched off, a red banner shows on the GSTR-3B
Report, Purchase Reconciliation Tool and Settings, and Accounts Managers get an
email.

The self-test is read-only so it can run on a live site:
1. structure  - every India Compliance class, method and field we depend on exists;
2. equivalence - one Journal Entry row and the equivalent Purchase Invoice row are
   pushed through India Compliance's own categorisation; they must land in the same
   GSTR-3B table with the same amounts, exactly once;
3. queries    - the Journal Entry report and reconciliation queries run for every company and GSTIN.
The database-level comparison with real documents in two GSTINs is in the
automated tests (tests/test_mode_b_reports.py).
"""

import inspect

import frappe
from frappe import _
from frappe.utils import now_datetime

REQUIRED = [
	(
		"india_compliance.gst_india.utils.gstr3b.gstr3b_inward_data",
		"GSTR3BInwardInvoices",
		("get_data", "get_processed_invoices", "get_invoice_wise_data", "get_section_data"),
	),
	(
		"india_compliance.gst_india.utils.gstr3b.gstr3b_outward_data",
		"GSTR3BOutwardInvoices",
		("get_inward_invoices", "get_processed_invoices"),
	),
	(
		"india_compliance.gst_india.doctype.purchase_reconciliation_tool",
		"BaseReconciliation",
		("get_unmatched_purchase_or_bill_of_entry",),
	),
	(
		"india_compliance.gst_india.doctype.purchase_reconciliation_tool",
		"ReconciledData",
		("get_all_purchase_invoice_and_bill_of_entry", "get"),
	),
	(
		"india_compliance.gst_india.doctype.purchase_reconciliation_tool",
		"BaseUtil",
		("get_fy",),
	),
	(
		"india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_tool",
		"PurchaseReconciliationTool",
		(
			"apply_action",
			"unlink_documents",
			"link_documents",
			"get_link_options",
			"reconcile_and_generate_data",
		),
	),
	(
		"india_compliance.gst_india.doctype.gst_invoice_management_system",
		"PurchaseInvoice",
		("get_all", "get_unmatched"),
	),
	(
		"india_compliance.gst_india.doctype.gst_invoice_management_system.gst_invoice_management_system",
		"GSTInvoiceManagementSystem",
		("get_link_options", "update_action"),
	),
	("india_compliance.gst_india.api_classes.public", "PublicAPI", ("get_gstin_info",)),
]
REQUIRED_FIELDS = {
	"GST Inward Supply": (
		"company_gstin",
		"supplier_gstin",
		"bill_no",
		"bill_date",
		"taxable_value",
		"igst",
		"cgst",
		"sgst",
		"cess",
		"action",
		"match_status",
		"link_name",
		"classification",
		"link_doctype",
	),
	"Purchase Invoice": (
		"company_gstin",
		"supplier_gstin",
		"place_of_supply",
		"itc_classification",
		"ineligibility_reason",
		"reconciliation_status",
		"itc_claim_period",
		"is_reverse_charge",
	),
	"Purchase Invoice Item": ("is_ineligible_for_itc", "item_tax_template", "gst_treatment", "taxable_value"),
	"Journal Entry": (
		"company_gstin",
		"qe_supplier_gstin",
		"qe_approval_status",
		"qe_itc_classification",
		"reconciliation_status",
	),
	"Item Tax Template": ("gst_rate", "gst_treatment"),
}


def installed_version() -> str:
	import india_compliance

	return india_compliance.__version__


def _structure_problems() -> list[str]:
	problems = []
	for module_name, class_name, methods in REQUIRED:
		try:
			module = frappe.get_module(module_name)
			cls = getattr(module, class_name)
		except Exception as exc:
			problems.append(f"{module_name}.{class_name}: {exc}")
			continue
		for method in methods:
			if not callable(getattr(cls, method, None)):
				problems.append(f"{class_name}.{method} is missing")

	try:
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_tool import (
			STATUS_MAP,
		)
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_utils import (
			_unlink_documents,
			get_formatted_options,
			set_reconciliation_status,
		)
	except ImportError as exc:
		problems.append(f"Purchase Reconciliation Tool helpers: {exc}")

	from india_compliance.gst_india.utils.gstr3b.gstr3b_inward_data import GSTR3BInwardInvoices

	original = getattr(GSTR3BInwardInvoices.get_data, "__wrapped__", GSTR3BInwardInvoices.get_data)
	if list(inspect.signature(original).parameters)[:2] != ["self", "doctype"]:
		problems.append("GSTR3BInwardInvoices.get_data signature changed")

	for doctype, fields in REQUIRED_FIELDS.items():
		meta = frappe.get_meta(doctype)
		for field in fields:
			if not meta.has_field(field):
				problems.append(f"{doctype}.{field} is missing")
	return problems


def _sample_row(voucher_type, **values):
	row = frappe._dict(
		voucher_type=voucher_type,
		voucher_no=f"SELF-TEST-{voucher_type}",
		posting_date=frappe.utils.today(),
		itc_classification="All Other ITC",
		ineligibility_reason="",
		place_of_supply="33-Tamil Nadu",
		gst_category="Registered Regular",
		company_gstin="33AAAAA0000A1Z5",
		supplier_gstin="33BBBBB0000B1Z5",
		is_reverse_charge=0,
		is_ineligible_for_itc=0,
		item_code="SELF-TEST",
		gst_treatment="Taxable",
		gst_hsn_code="9968",
		uom="Nos",
		qty=1,
		gst_rate=18,
		taxable_value=1000.0,
		cgst_amount=90.0,
		sgst_amount=90.0,
		igst_amount=0.0,
		cess_amount=0.0,
		total_tax=180.0,
		total_amount=1180.0,
	)
	row.update(values)
	return row


EQUIVALENCE_CASES = [
	dict(),
	dict(is_ineligible_for_itc=1, ineligibility_reason="Ineligible As Per Section 17(5)"),
	dict(ineligibility_reason="ITC restricted due to PoS rules", place_of_supply="29-Karnataka"),
	dict(itc_classification="ITC on Reverse Charge", is_reverse_charge=1),
	dict(
		gst_treatment="Exempted", gst_rate=0, cgst_amount=0, sgst_amount=0, total_tax=0, total_amount=1000.0
	),
	dict(gst_treatment="Non-GST", gst_rate=0, cgst_amount=0, sgst_amount=0, total_tax=0, total_amount=1000.0),
]


def _equivalence_problems() -> list[str]:
	from india_compliance.gst_india.utils.gstr3b.gstr3b_inward_data import GSTR3BInwardInvoices

	problems = []
	processor = GSTR3BInwardInvoices({"company": None})
	keys = (
		"invoice_category",
		"invoice_sub_category",
		"taxable_value",
		"igst_amount",
		"cgst_amount",
		"sgst_amount",
		"cess_amount",
		"inter",
		"intra",
	)
	for case in EQUIVALENCE_CASES:
		pi_rows = processor.get_processed_invoices(
			"Purchase Invoice", [_sample_row("Purchase Invoice", **case)]
		)
		je_rows = processor.get_processed_invoices("Purchase Invoice", [_sample_row("Journal Entry", **case)])
		if len(pi_rows) != len(je_rows):
			problems.append(
				f"case {case}: {len(pi_rows)} Purchase Invoice rows vs {len(je_rows)} Journal Entry rows"
			)
			continue
		for pi_row, je_row in zip(pi_rows, je_rows, strict=True):
			if any(pi_row.get(k) != je_row.get(k) for k in keys):
				problems.append(f"case {case}: categorisation differs")
	return problems


def _query_problems() -> list[str]:
	from quick_expense.overrides.ic_reports import fetch_rows
	from quick_expense.services.gstin_service import get_company_gstins

	problems = []
	today = frappe.utils.today()
	for company in frappe.get_all("Company", filters={"country": "India"}, pluck="name"):
		for gstin in [None] + [row.gstin for row in get_company_gstins(company)]:
			try:
				rows = fetch_rows(
					{"company": company, "company_gstin": gstin, "from_date": today, "to_date": today}
				)
			except Exception as exc:
				problems.append(f"query failed for {company} {gstin or ''}: {exc}")
				continue
			if gstin and any(row.company_gstin != gstin for row in rows):
				problems.append(f"rows of another GSTIN returned for {gstin}")
			try:
				from quick_expense.overrides.purchase_reconciliation import QuickExpenseJournalEntry

				QuickExpenseJournalEntry(
					company=company,
					company_gstin=gstin or "All",
					from_date=today,
					to_date=today,
					include_ignored=1,
				).get_unmatched()
			except Exception as exc:
				problems.append(f"reconciliation query failed for {company} {gstin or ''}: {exc}")
	return problems


def run_self_test() -> list[str]:
	from quick_expense.overrides.ic_reports import apply_patches

	apply_patches()
	problems = _structure_problems()
	if not problems:
		problems += _equivalence_problems() + _query_problems()
	return problems


@frappe.whitelist()
def run_self_test_now():
	frappe.only_for(("Accounts Manager", "System Manager"))
	return check_compatibility(force=True)


def check_compatibility(force=False) -> dict:
	if not frappe.db.exists("DocType", "Quick Expense Settings"):
		return {}
	settings = frappe.get_doc("Quick Expense Settings")
	version = installed_version()
	if not force and settings.verified_ic_version == version and not settings.overrides_disabled:
		return {"status": "verified", "version": version}

	problems = run_self_test()
	values = {"last_self_test_on": now_datetime()}
	if not problems:
		values.update(verified_ic_version=version, overrides_disabled=0, overrides_disabled_reason="")
		status = "passed"
	else:
		message = _("India Compliance {0} failed the Quick Expense self-test: {1}").format(
			version, "; ".join(problems)
		)[:2000]
		frappe.log_error(
			title="Quick Expense: India Compliance compatibility self-test failed", message=message
		)
		if settings.disable_overrides_on_version_mismatch:
			values.update(overrides_disabled=1, overrides_disabled_reason=message)
			notify_accounts_managers(message)
		status = "failed"

	for key, value in values.items():
		frappe.db.set_single_value("Quick Expense Settings", key, value)
	frappe.clear_document_cache("Quick Expense Settings", "Quick Expense Settings")
	return {"status": status, "version": version, "problems": problems}


def notify_accounts_managers(message):
	if frappe.flags.in_test:
		return
	users = frappe.get_all(
		"Has Role", filters={"role": "Accounts Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	emails = [
		email
		for email in frappe.get_all("User", filters={"name": ("in", users), "enabled": 1}, pluck="email")
		if email
	]
	if emails:
		from quick_expense.services.approval_service import send_email

		send_email(
			recipients=emails,
			subject=_("Quick Expense: Journal Entry report overrides switched off"),
			message=f"<p style='font-size:16px'>{message}</p><p>{_('Journal Entry mode figures are not included in GSTR-3B, the GST Purchase Register and 2B reconciliation until the self-test passes. Run it again from Quick Expense Settings after checking the README upgrade checklist.')}</p>",
		)


def daily():
	check_compatibility()
