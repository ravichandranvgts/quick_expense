"""Install / new-company setup, Setup Check, migration, API and print formats."""

import json

import frappe
from frappe.utils import today

from quick_expense import api
from quick_expense.services import setup_service
from quick_expense.tests.utils import (
	COMPANY,
	COMPANY_2,
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

	def test_company_rows_and_rules(self):
		settings = frappe.get_single("Quick Expense Settings")
		for company in (COMPANY, COMPANY_2):
			self.assertEqual(len([r for r in settings.approval_rules if r.company == company]), 3)
			self.assertEqual(len([r for r in settings.company_settings if r.company == company]), 1)
			self.assertTrue([r for r in settings.allowed_accounts if r.company == company])

	def test_journal_entry_is_set_up(self):
		meta = frappe.get_meta("Journal Entry")
		self.assertIn("Quick Expense", meta.get_field("voucher_type").options.split("\n"))
		for field in (
			"qe_paid_through_account",
			"qe_approval_status",
			"qe_approval_history",
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

	def test_rerun_creates_no_duplicates(self):
		counts = lambda: {
			dt: frappe.db.count(dt) for dt in ("Custom Field", "Number Card", "Workspace", "Custom DocPerm")
		}
		rules_before = len(frappe.get_single("Quick Expense Settings").approval_rules)
		before = counts()
		setup_service.setup_all()
		self.assertEqual(before, counts())
		self.assertEqual(len(frappe.get_single("Quick Expense Settings").approval_rules), rules_before)

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

	def test_new_company_is_set_up_automatically(self):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "_QE New Company",
				"abbr": "QEN",
				"country": "India",
				"default_currency": "INR",
				"chart_of_accounts": "Standard",
			}
		).insert()
		settings = frappe.get_single("Quick Expense Settings")
		self.assertTrue(any(r.company == company.name for r in settings.company_settings))
		self.assertEqual(len([r for r in settings.approval_rules if r.company == company.name]), 3)

	def test_setup_check_reports_missing_template(self):
		frappe.db.set_value(
			"Item Tax Template",
			{"company": COMPANY, "gst_rate": 5, "gst_treatment": "Taxable"},
			"disabled",
			1,
		)
		frappe.local.request_cache.clear()
		issues = setup_service.get_setup_issues()
		self.assertTrue(any(i["company"] == COMPANY and "GST 5" in i["message"] for i in issues), issues)

	def test_setup_check_reports_missing_approvers(self):
		frappe.db.delete("Has Role", {"role": "Finance Manager", "parent": user("fm")})
		frappe.clear_cache(user=user("fm"))
		issues = setup_service.get_setup_issues()
		self.assertTrue(any(i["area"] == "Approvers" and "Finance Manager" in i["message"] for i in issues))

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

	def test_submit_approve_reject_via_api(self):
		entry = self.make_entry()
		with as_(user("user")):
			self.assertEqual(api.submit_for_approval(entry.name)["approval_status"], "Pending Approval")
		with as_(user("approver")):
			self.assertEqual(api.approve(entry.name, "ok")["approval_status"], "Posted")
		second = self.make_entry()
		api.submit_for_approval(second.name)
		with as_(user("approver")):
			self.assertEqual(api.reject(second.name, "duplicate photo")["approval_status"], "Rejected")

	def test_entry_defaults(self):
		defaults = api.get_entry_defaults(COMPANY)
		self.assertEqual(defaults["company_gstin"], TN_GSTIN)
		self.assertIn(
			frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}),
			defaults["allowed_accounts"],
		)

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

	def test_bulk_approve(self):
		names = [self.submit(self.make_entry()).name for _ in range(2)]
		with as_(user("approver")):
			result = api.bulk_approve(json.dumps(names))
		self.assertTrue(all(row["ok"] for row in result), result)

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
			api.submit_for_approval(je.name)


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
		self.assertIn("Approval History", voucher)
		self_invoice = frappe.get_print(
			"Journal Entry", entry.name, print_format="Quick Expense Self Invoice"
		)
		self.assertIn(entry.self_invoice_no, self_invoice)
