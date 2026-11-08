"""Unique bill key (prompt section 12.1), kept on the Journal Entry itself.

Key = Company GSTIN + Supplier GSTIN + normalised Supplier Invoice No + GST
financial year of the invoice date, stored in ``qe_bill_key``. A bill holds its
key while it is in approval or posted; drafts, rejected and cancelled bills do
not. Two users submitting the same bill at the same moment are serialised by a
database named lock on the key, so the second one sees the first and is refused.
"""

import re

import frappe
from frappe import _
from frappe.utils import flt
from pypika.functions import Replace, Upper

from quick_expense.constants import JE_VOUCHER_TYPE, LIVE_STATUSES
from quick_expense.services.tax_engine import gst_financial_year, gst_financial_year_label


class DuplicateBillError(frappe.ValidationError):
	pass


def normalise_invoice_no(invoice_no: str | None) -> str:
	return re.sub(r"\s+", "", (invoice_no or "")).upper()


def build_key(company_gstin, supplier_gstin, invoice_no, invoice_date) -> str | None:
	invoice_no = normalise_invoice_no(invoice_no)
	if not (company_gstin and supplier_gstin and invoice_no and invoice_date):
		return None
	return "|".join((company_gstin, supplier_gstin, invoice_no, gst_financial_year_label(invoice_date)))


def get_holder(key: str, exclude: str | None = None) -> str | None:
	"""Another Quick Expense Journal Entry, in approval or posted, that holds the key."""
	return frappe.db.get_value(
		"Journal Entry",
		{
			"voucher_type": JE_VOUCHER_TYPE,
			"qe_bill_key": key,
			"docstatus": ("<", 2),
			"qe_approval_status": ("in", LIVE_STATUSES),
			"name": ("!=", exclude or ""),
		},
		"name",
	)


def check_duplicate(doc) -> None:
	key = build_key(doc.company_gstin, doc.supplier_gstin, doc.supplier_invoice_no, doc.supplier_invoice_date)
	doc.duplicate_key = key
	if not key:
		return
	if holder := get_holder(key, doc.name):
		raise_duplicate(doc, holder)
	if existing_pi := find_purchase_invoice(doc):
		frappe.throw(
			_(
				"Supplier invoice {0} from GSTIN {1} is already booked under Company GSTIN {2} in Purchase Invoice {3}. The same bill cannot be claimed twice."
			).format(
				frappe.bold(doc.supplier_invoice_no),
				doc.supplier_gstin,
				doc.company_gstin,
				frappe.utils.get_link_to_form("Purchase Invoice", existing_pi),
			),
			DuplicateBillError,
			title=_("Duplicate Bill"),
		)


def raise_duplicate(doc, holder):
	frappe.throw(
		_(
			"Supplier invoice {0} from GSTIN {1} for financial year {2} is already entered under Company GSTIN {3} in {4}. The same bill can never be claimed twice under one GSTIN."
		).format(
			frappe.bold(doc.supplier_invoice_no),
			doc.supplier_gstin,
			gst_financial_year_label(doc.supplier_invoice_date),
			doc.company_gstin,
			frappe.utils.get_link_to_form("Journal Entry", holder),
		),
		DuplicateBillError,
		title=_("Duplicate Bill"),
	)


def find_purchase_invoice(doc) -> str | None:
	"""A standard Purchase Invoice (draft or submitted) already booked for the same bill."""
	start, end = gst_financial_year(doc.supplier_invoice_date)
	pi = frappe.qb.DocType("Purchase Invoice")
	rows = (
		frappe.qb.from_(pi)
		.select(pi.name)
		.where(pi.docstatus < 2)
		.where(pi.is_return == 0)
		.where(pi.supplier_gstin == doc.supplier_gstin)
		.where(pi.company_gstin == doc.company_gstin)
		.where(Upper(Replace(pi.bill_no, " ", "")) == normalise_invoice_no(doc.supplier_invoice_no))
		.where(pi.bill_date[start:end])
		.limit(1)
	).run()
	return rows[0][0] if rows else None


def _lock_name(key):
	return f"qe_bill_{frappe.generate_hash(key, length=20)}" if len(key) > 50 else f"qe_bill_{key}"


def reserve(doc) -> None:
	"""Hold the bill key until this transaction ends, then re-check under the lock."""
	key = doc.duplicate_key
	if not key:
		return
	lock = _lock_name(key)
	if not frappe.db.sql("SELECT GET_LOCK(%s, 15)", (lock,))[0][0]:
		frappe.throw(
			_("Another user is submitting the same bill right now. Please try again."), DuplicateBillError
		)

	def release_lock():
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock,))

	frappe.db.after_commit.add(release_lock)
	frappe.db.after_rollback.add(release_lock)
	if holder := get_holder(key, doc.name):
		raise_duplicate(doc, holder)


def release(doc) -> None:
	"""Nothing to delete: a bill stops holding its key as soon as it leaves approval."""


# ----------------------------------------------------------------------------
# warnings (do not block)


def unregistered_duplicate_warning(doc) -> str | None:
	"""Unregistered bills: same shop name + invoice no + date + amount."""
	if doc.supplier_gstin or not doc.supplier_legal_name:
		return None
	filters = {
		"voucher_type": JE_VOUCHER_TYPE,
		"name": ("!=", doc.name or ""),
		"docstatus": ("<", 2),
		"company": doc.company,
		"qe_supplier_legal_name": doc.supplier_legal_name,
		"qe_grand_total": flt(doc.grand_total, 2),
		"qe_approval_status": ("in", LIVE_STATUSES),
	}
	if doc.supplier_invoice_no:
		filters["qe_supplier_invoice_no"] = doc.supplier_invoice_no
	if doc.supplier_invoice_date:
		filters["qe_supplier_invoice_date"] = doc.supplier_invoice_date
	else:
		filters["posting_date"] = doc.posting_date
	if other := frappe.db.get_value("Journal Entry", filters, "name"):
		return _("A bill from {0} with the same number, date and amount already exists in {1}.").format(
			doc.supplier_legal_name, other
		)
	return None


def attachment_hash_warning(doc) -> str | None:
	if not doc.attachment_hash:
		return None
	files = frappe.get_all("File", filters={"content_hash": doc.attachment_hash}, pluck="file_url")
	other = frappe.db.get_value(
		"Journal Entry",
		{
			"voucher_type": JE_VOUCHER_TYPE,
			"qe_bill_attachment": ("in", files or [""]),
			"name": ("!=", doc.name or ""),
			"docstatus": ("<", 2),
		},
		"name",
	)
	if other:
		return _("The same bill file is already attached to {0}.").format(other)
	return None
