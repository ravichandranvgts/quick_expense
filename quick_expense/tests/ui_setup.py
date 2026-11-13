"""Test data for the Playwright UI tests in ui_tests/.

    bench --site <test-site> execute quick_expense.tests.ui_setup.setup

Creates the same companies, GSTINs, accounts, tax templates and users as the server
tests, gives the test users a password so the browser can log in as them, and returns
the names the UI tests need as JSON. Refuses to run unless the site has allow_tests on,
so it can never add test companies to a real site.
"""

import frappe

UI_PASSWORD = "Ui-Test-Pass-1"
UI_INVOICE_PREFIX = "UI-"  # ui_tests/helpers.ts numbers its bills UI-<digits>


def ui_supplier_gstin():
	from quick_expense.tests.utils import make_gstin

	return make_gstin("33", "AAFCU9999U")


def remove_ui_bills():
	"""Cancel and delete bills a crashed UI run left behind.

	ERPNext deletes a transaction's GL and payment ledger entries with it only when Accounts
	Settings says so; without that, a deleted bill's ledger rows block the next bill that
	reuses its number. This runs on test sites only (setup() checks allow_tests)."""
	frappe.db.set_single_value("Accounts Settings", "delete_linked_ledger_entries", 1)
	for name, docstatus in frappe.get_all(
		"Journal Entry",
		filters={"qe_supplier_invoice_no": ("like", f"{UI_INVOICE_PREFIX}%")},
		fields=["name", "docstatus"],
		as_list=True,
	):
		if docstatus == 1:
			frappe.get_doc("Journal Entry", name).cancel()
		frappe.delete_doc("Journal Entry", name, ignore_permissions=True)


def setup():
	if not frappe.conf.allow_tests:
		frappe.throw("Run this only on a test site: bench --site <site> set-config allow_tests true")

	from frappe.utils.password import update_password

	from quick_expense.tests import utils
	from quick_expense.tests.setup import before_tests

	before_tests()
	remove_ui_bills()
	for email, _roles in utils.USERS.values():
		update_password(email, UI_PASSWORD)
	frappe.db.commit()  # nosemgrep: bench execute does not commit on its own

	return {
		"company": utils.COMPANY,
		"company_gstin": utils.TN_GSTIN,
		# a supplier no server test uses, so UI bills never change a server test's result
		"supplier_gstin": ui_supplier_gstin(),
		"expense_account": f"QE Courier - {utils.ABBR}",
		"cash_account": frappe.db.get_value(
			"Account", {"company": utils.COMPANY, "account_type": "Cash", "is_group": 0}, "name"
		),
		"gst_18": utils.template(18),
		"users": {key: email for key, (email, _roles) in utils.USERS.items()},
		"password": UI_PASSWORD,
	}
