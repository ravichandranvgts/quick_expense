"""Install setup, migration, API and print formats."""

import frappe
from frappe.utils import today

from quick_expense import api
from quick_expense.services import setup_service
from quick_expense.tests.utils import (
	COMPANY,
	COMPANY_2,
	COMPANY_2_GSTIN,
	SUPPLIER_TN,
	TN_GSTIN,
	QETestCase,
	as_,
	make_gstin,
	user,
)


class TestSetup(QETestCase):
	def test_removed_doctypes_are_gone(self):
		for doctype in (
			"Quick Expense Entry",
			"Quick Expense Category",
			"Quick Expense GSTIN Cache",
			"Quick Expense Invoice Key",
		):
			self.assertFalse(frappe.db.exists("DocType", doctype), doctype)
		self.assertFalse(frappe.get_all("Report", filters={"module": "Quick Expense"}))

	def test_approval_and_settings_tables_are_gone(self):
		for doctype in (
			"Quick Expense Approval History",
			"Quick Expense Approval Rule",
			"Quick Expense Company Setting",
			"Quick Expense Allowed Account",
			"Quick Expense User Default",
			"Quick Expense GST Lock Date",
		):
			self.assertFalse(frappe.db.exists("DocType", doctype), doctype)
		meta = frappe.get_meta("Journal Entry")
		for field in ("qe_approval_status", "qe_approval_history", "qe_warnings"):
			self.assertFalse(meta.has_field(field), field)
		self.assertEqual(
			[f.fieldname for f in frappe.get_meta("Quick Expense Settings").fields if f.fieldtype == "Check"],
			["enable_quick_expense", "round_grand_total", "use_gstin_api", "monthly_2b_follow_up_email"],
		)

	def test_journal_entry_is_set_up(self):
		meta = frappe.get_meta("Journal Entry")
		self.assertIn("Quick Expense", meta.get_field("voucher_type").options.split("\n"))
		for field in (
			"qe_paid_through_account",
			"reconciliation_status",
			"qe_grand_total",
		):
			self.assertTrue(meta.has_field(field), field)
		roles = {
			row.role
			for row in frappe.get_all(
				"Custom DocPerm", filters={"parent": "Journal Entry", "create": 1}, fields=["role"]
			)
		}
		self.assertTrue({"Quick Expense User", "Quick Expense Approver"} <= roles)
		submitters = {
			row.role
			for row in frappe.get_all(
				"Custom DocPerm", filters={"parent": "Journal Entry", "submit": 1}, fields=["role"]
			)
		}
		self.assertIn("Quick Expense Approver", submitters)
		self.assertNotIn("Quick Expense User", submitters)

	def test_rerun_creates_no_duplicates(self):
		counts = lambda: {
			dt: frappe.db.count(dt) for dt in ("Custom Field", "Number Card", "Workspace", "Custom DocPerm")
		}
		before = counts()
		setup_service.setup_all()
		self.assertEqual(before, counts())

	def test_workspace_points_to_india_compliance(self):
		workspace = frappe.get_doc("Workspace", "Quick Expense")
		links = {row.link_to for row in workspace.shortcuts}
		self.assertTrue({"Purchase Reconciliation Tool", "GSTR 3B Report", "GST Purchase Register"} <= links)
		self.assertEqual(
			{
				card.document_type
				for card in frappe.get_all(
					"Number Card", filters={"module": "Quick Expense"}, fields=["document_type"]
				)
			},
			{"Journal Entry"},
		)

	def test_follow_up_list(self):
		from quick_expense.services.reconciliation_service import get_follow_up_entries

		entry = self.post(self.make_entry())
		self.assertFalse(get_follow_up_entries(COMPANY))
		frappe.db.set_value(
			"Journal Entry", entry.name, {"qe_itc_claim_period": "012026", "posting_date": "2026-01-15"}
		)
		self.assertIn(entry.name, [row.name for row in get_follow_up_entries(COMPANY)])


class TestAPI(QETestCase):
	def test_get_gstin_details(self):
		self.assertEqual(api.get_gstin_details("33ABC")["valid"], 0)
		other = make_gstin("33", "AABCR5678D")
		self.assertIn("belongs to company", api.get_gstin_details(other, COMPANY, TN_GSTIN)["message"])
		details = api.get_gstin_details(SUPPLIER_TN, COMPANY, TN_GSTIN)
		self.assertEqual((details["valid"], details["source"], len(details["addresses"])), (1, "API", 2))

	def test_entry_defaults_come_from_the_company(self):
		defaults = api.get_entry_defaults(COMPANY)
		self.assertEqual(defaults["company_gstin"], TN_GSTIN)  # the primary GST address
		self.assertEqual(defaults["cost_center"], frappe.get_cached_value("Company", COMPANY, "cost_center"))
		self.assertEqual(self.make_entry(company_gstin="").company_gstin, TN_GSTIN)
		self.assertEqual(api.get_entry_defaults(COMPANY_2)["company_gstin"], COMPANY_2_GSTIN)

	def test_last_gst_rate_of_supplier(self):
		self.assertIsNone(api.get_last_gst_rate(COMPANY, SUPPLIER_TN))
		entry = self.make_entry()
		line = entry.lines[0]
		last = api.get_last_gst_rate(COMPANY, SUPPLIER_TN, line.expense_account)
		self.assertEqual(last.qe_item_tax_template, line.item_tax_template)
		cash = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0})
		self.assertIsNone(api.get_last_gst_rate(COMPANY, SUPPLIER_TN, cash))
		# the payment row keeps no GST rate
		self.assertFalse(
			[r for r in entry.je.accounts if r.qe_gst_component == "Payment" and r.qe_item_tax_template]
		)

	def test_not_a_quick_expense_journal_entry(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"company": COMPANY,
				"posting_date": today(),
				"accounts": [
					{"account": "QE Courier - QET", "debit_in_account_currency": 10},
					{
						"account": frappe.db.get_value(
							"Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}
						),
						"credit_in_account_currency": 10,
					},
				],
			}
		).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "not a Quick Expense"):
			api.cancel_with_reason(je.name, "test")


class TestPrint(QETestCase):
	def test_print_formats_render(self):
		entry = self.post(
			self.make_entry(
				gst_treatment="Unregistered",
				supplier_gstin="",
				supplier_legal_name="Lorry Owner",
				lines=[dict(expense_category="Freight and Transport (GTA)", taxable_amount=3000)],
			)
		)
		voucher = frappe.get_print("Journal Entry", entry.name, print_format="Quick Expense Voucher")
		self.assertIn("Lorry Owner", voucher)
		self.assertIn("Prepared By", voucher)
		self_invoice = frappe.get_print(
			"Journal Entry", entry.name, print_format="Quick Expense Self Invoice"
		)
		self.assertIn(entry.self_invoice_no, self_invoice)
