"""doc_events hooks on ERPNext / India Compliance doctypes."""

from quick_expense.constants import JE_VOUCHER_TYPE
from quick_expense.services import journal_entry_service

# ----------------------------------------------------------------------------
# Journal Entry


def journal_entry_before_validate(doc, method=None):
	journal_entry_service.before_validate(doc, method)


def journal_entry_validate(doc, method=None):
	journal_entry_service.validate_voucher_type_for_user(doc, method)


def journal_entry_before_submit(doc, method=None):
	journal_entry_service.before_submit(doc, method)


def journal_entry_before_cancel(doc, method=None):
	journal_entry_service.before_cancel(doc, method)


def journal_entry_on_cancel(doc, method=None):
	journal_entry_service.on_cancel(doc, method)


def journal_entry_onload(doc, method=None):
	if doc.voucher_type != JE_VOUCHER_TYPE:
		return
	from quick_expense.services import supplier_service

	supplier_service.set_onload(doc)
