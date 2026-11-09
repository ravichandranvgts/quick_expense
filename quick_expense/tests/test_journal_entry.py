"""Journal Entry posting, India Compliance reports, 2A / 2B reconciliation in India
Compliance's Purchase Reconciliation Tool, compatibility guard, cancel / amend
(prompt sections 9, 10, 11; test cases 1, 3-9, 17, 18, 20-22)."""

import json
from unittest.mock import patch

import frappe
from frappe.utils import flt, get_first_day, get_last_day, getdate, today

from quick_expense.overrides import compatibility_guard, ic_reports
from quick_expense.tests.utils import (
	COMPANY,
	KA_GSTIN,
	SUPPLIER_KA,
	SUPPLIER_MH,
	SUPPLIER_TN,
	TN_GSTIN,
	QETestCase,
	as_,
	cash,
	set_settings,
	user,
)


def je_rows(name):
	return {
		(row.account, row.qe_gst_component): flt(
			row.debit_in_account_currency - row.credit_in_account_currency, 2
		)
		for row in frappe.get_doc("Journal Entry", name).accounts
	}


def month_range():
	start = get_first_day(today())
	return start, get_last_day(start)


def inward_rows(company_gstin, filter_by="Posting Date"):
	from india_compliance.gst_india.utils.gstr3b.gstr3b_inward_data import GSTR3BInwardInvoices

	start, end = month_range()
	return GSTR3BInwardInvoices(
		{
			"company": COMPANY,
			"company_gstin": company_gstin,
			"from_date": start,
			"to_date": end,
			"filter_by": filter_by,
		}
	).get_all_data()


def gstr3b_json(company_gstin):
	report = frappe.get_doc(
		{
			"doctype": "GSTR 3B Report",
			"company": COMPANY,
			"company_gstin": company_gstin,
			"year": str(getdate(today()).year),
			"month_or_quarter": getdate(today()).strftime("%B"),
			"filter_by": "ITC Claim Period",
		}
	)
	report.get_data()
	return json.loads(report.json_output)


def row_of(rows, ty):
	return next(row for row in rows if row["ty"] == ty)


class TestJournalEntryPosting(QETestCase):
	def setUp(self):
		super().setUp()
		ic_reports.apply_patches()

	def test_journal_entry_lines(self):
		entry = self.post(
			self.make_entry(lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=1000)])
		)
		self.assertEqual(entry.approval_status, "Posted", entry.posting_error)
		je = frappe.get_doc("Journal Entry", entry.linked_journal_entry)
		self.assertEqual(
			(
				je.voucher_type,
				je.docstatus,
				je.company_gstin,
				je.qe_approval_status,
				je.reconciliation_status,
			),
			("Quick Expense", 1, TN_GSTIN, "Posted", "Unreconciled"),
		)
		rows = je_rows(je.name)
		self.assertEqual(rows[("QE Courier - QET", "Expense")], 1000)
		self.assertEqual(rows[("Input Tax CGST - QET", "CGST")], 90)
		self.assertEqual(rows[("Input Tax SGST - QET", "SGST")], 90)
		self.assertEqual(rows[(cash(), "Payment")], -1180)
		self.assertFalse(any(row.party for row in je.accounts))  # no supplier record (test case 3)
		self.assertFalse(frappe.db.exists("Supplier", {"gstin": SUPPLIER_TN}))
		self.assertFalse(frappe.db.exists("Purchase Invoice", {"bill_no": entry.supplier_invoice_no}))
		self.assertEqual(
			(entry.approval_status, je.qe_grand_total, je.qe_total_eligible_itc), ("Posted", 1180, 180)
		)

	def test_unregistered_bill_needs_no_2b(self):
		entry = self.post(
			self.make_entry(gst_treatment="Unregistered", supplier_gstin="", supplier_legal_name="Tea Shop")
		)
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "reconciliation_status"),
			"Not Applicable",
		)

	def test_ineligible_gst_goes_to_expense(self):
		entry = self.post(
			self.make_entry(lines=[dict(expense_category="Food and Refreshments", taxable_amount=1000)])
		)
		rows = je_rows(entry.linked_journal_entry)
		self.assertEqual(rows[("QE Food - QET", "Expense")], 1050)
		self.assertNotIn(("Input Tax CGST - QET", "CGST"), rows)

	def test_reverse_charge_lines(self):
		entry = self.post(
			self.make_entry(
				lines=[dict(expense_category="Freight and Transport (GTA)", taxable_amount=10000)]
			)
		)
		rows = je_rows(entry.linked_journal_entry)
		self.assertEqual(rows[("Input Tax CGST - QET", "CGST")], 250)
		self.assertEqual(rows[("Input Tax CGST RCM - QET", "Reverse Charge Liability")], -250)
		self.assertEqual(rows[(cash(), "Payment")], -10000)

	def test_overseas_import_of_service(self):
		entry = self.post(
			self.make_entry(
				gst_treatment="Overseas",
				supplier_gstin="",
				supplier_legal_name="Cloud Software Inc",
				lines=[dict(expense_category="Software and Online Subscriptions", taxable_amount=5000)],
			)
		)
		self.assertEqual(entry.approval_status, "Posted", entry.posting_error)
		rows = je_rows(entry.linked_journal_entry)
		self.assertEqual(rows[("Input Tax IGST - QET", "IGST")], 900)
		self.assertEqual(rows[("Input Tax IGST RCM - QET", "Reverse Charge Liability")], -900)
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "qe_itc_classification"),
			"Import Of Service",
		)

	def test_round_off_line(self):
		entry = self.post(
			self.make_entry(
				lines=[
					dict(
						expense_category="Courier and Postage Charges",
						taxable_amount=1000,
						gst_as_per_bill_entered=1,
						gst_as_per_bill=180.8,
					)
				]
			)
		)
		self.assertEqual(je_rows(entry.linked_journal_entry)[("Round Off - QET", "Round Off")], 1)

	def test_posting_failure_rolls_back_and_retries(self):
		entry = self.submit(self.make_entry())
		with patch(
			"quick_expense.services.journal_entry_service.needs_2b",
			side_effect=frappe.ValidationError("ledger locked"),
		):
			entry = self.approve_all(entry)
		self.assertEqual((entry.approval_status, entry.docstatus), ("Approved - Posting Failed", 0))
		self.assertIn("ledger locked", entry.posting_error)
		self.assertFalse(frappe.db.exists("GL Entry", {"voucher_no": entry.name}))
		with as_(user("approver")):
			with self.assertRaisesRegex(frappe.ValidationError, "Only an Accounts Manager"):
				self.bill(entry.name).retry_posting()
		with as_(user("am")):
			self.bill(entry.name).retry_posting()
		self.assertEqual(
			(self.bill(entry.name).approval_status, self.bill(entry.name).docstatus), ("Posted", 1)
		)

	def test_bill_attachment_stays_on_the_journal_entry(self):
		from quick_expense.tests.test_validations import png_file

		file = png_file("courier.png")
		entry = self.post(self.make_entry(bill_attachment=file.file_url))
		self.assertEqual(entry.approval_status, "Posted", entry.posting_error)
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.name, "qe_bill_attachment"), file.file_url
		)


class TestIndiaComplianceReports(QETestCase):
	def setUp(self):
		super().setUp()
		ic_reports.apply_patches()

	def post_month_of_bills(self):
		bills = [
			dict(
				lines=[
					dict(expense_category="Courier and Postage Charges", taxable_amount=1000),
					dict(expense_category="Food and Refreshments", taxable_amount=400),
				]
			),
			dict(lines=[dict(expense_category="Freight and Transport (GTA)", taxable_amount=6000)]),
			dict(
				lines=[
					dict(expense_category="Fuel – Petrol and Diesel", taxable_amount=2000),
					dict(expense_category="Electricity Charges", taxable_amount=500),
				]
			),
			dict(
				supplier_gstin=SUPPLIER_KA,
				lines=[
					dict(
						expense_category="Hotel Stay",
						taxable_amount=3000,
						gst_rate="18",
						location_of_service_state="Karnataka",
					)
				],
			),
			dict(
				supplier_gstin=SUPPLIER_MH,
				lines=[dict(expense_category="Office Stationery", taxable_amount=2500)],
			),
		]
		for bill in bills:
			posted = self.post(self.make_entry(**bill))
			self.assertEqual(posted.approval_status, "Posted", posted.posting_error)

	def test_gstr3b_tables(self):
		# test cases 1, 2, 4, 5, 7, 9 as they appear in India Compliance's GSTR-3B
		self.post_month_of_bills()
		data = gstr3b_json(TN_GSTIN)
		itc = data["itc_elg"]
		others = row_of(itc["itc_avl"], "OTH")
		self.assertEqual(
			(others["camt"], others["samt"], others["iamt"]), (100, 100, 450)
		)  # courier + food, stationery
		reverse_charge = row_of(itc["itc_avl"], "ISRC")
		self.assertEqual((reverse_charge["camt"], reverse_charge["samt"]), (150, 150))
		reversed_17_5 = row_of(itc["itc_rev"], "RUL")
		self.assertEqual((reversed_17_5["camt"], reversed_17_5["samt"]), (10, 10))  # food, 4B(1)
		pos_restricted = row_of(itc["itc_inelg"], "OTH")
		self.assertEqual(
			(pos_restricted["camt"], pos_restricted["samt"]), (270, 270)
		)  # hotel in Karnataka, 4D(2)
		self.assertEqual((itc["itc_net"]["camt"], itc["itc_net"]["iamt"]), (240, 450))
		inward = {row["ty"]: row for row in data["inward_sup"]["isup_details"]}
		self.assertEqual((inward["GST"]["intra"], inward["NONGST"]["intra"]), (500, 2000))  # Table 5
		rcm = data["sup_details"]["isup_rev"]
		self.assertEqual((rcm["txval"], rcm["camt"], rcm["samt"]), (6000, 150, 150))  # 3.1(d)
		# the other GSTIN of the company is untouched
		self.assertEqual(row_of(gstr3b_json(KA_GSTIN)["itc_elg"]["itc_avl"], "OTH")["camt"], 0)

	def test_rows_are_journal_entries_once_per_gstin(self):
		self.post(self.make_entry(company_gstin=TN_GSTIN))
		self.post(self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA))
		for gstin in (TN_GSTIN, KA_GSTIN):
			for filter_by in ("Posting Date", "ITC Claim Period"):
				rows = inward_rows(gstin, filter_by)
				self.assertEqual(len(rows), 1, (gstin, filter_by))
				self.assertEqual((rows[0].voucher_type, rows[0].company_gstin), ("Journal Entry", gstin))

	def test_purchase_register_includes_journal_entries(self):
		from india_compliance.gst_india.report.gst_purchase_register import gst_purchase_register

		entry = self.post(self.make_entry())
		start, end = month_range()
		filters = dict(
			company=COMPANY,
			company_gstin=TN_GSTIN,
			date_range=[start, end],
			sub_section="4",
			summary_by="Summary by Invoice",
		)
		_, data = gst_purchase_register.execute(frappe._dict(filters))
		rows = [row for row in data if row.get("voucher_no") == entry.linked_journal_entry]
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0]["voucher_type"], rows[0]["cgst_amount"]), ("Journal Entry", 90))

		set_settings(include_in_purchase_register=0)
		_, data = gst_purchase_register.execute(frappe._dict(filters))
		self.assertFalse([row for row in data if row.get("voucher_no") == entry.linked_journal_entry])
		self.assertEqual(len(inward_rows(TN_GSTIN)), 1)  # GSTR-3B keeps it

	def test_other_journal_entry_on_input_gst_blocked(self):
		# test case 20
		set_settings(restrict_input_gst_accounts=1)
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"company": COMPANY,
				"company_gstin": TN_GSTIN,
				"posting_date": today(),
				"accounts": [
					{"account": "Input Tax CGST - QET", "debit_in_account_currency": 100},
					{"account": cash(), "credit_in_account_currency": 100},
				],
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "reserved for Quick Expense"):
			je.insert()
		je.qe_gst_account_override_reason = "Opening ITC correction"
		with as_(user("am")):
			je.insert()
			je.submit()
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": je.name, "content": ("like", "%Opening ITC correction%")}
			)
		)


class TestPurchaseReconciliationTool(QETestCase):
	"""test case 22: the bill is reconciled in India Compliance's own tool."""

	def setUp(self):
		super().setUp()
		ic_reports.apply_patches()

	def tool(self, company_gstin=TN_GSTIN):
		start, end = month_range()
		return frappe.get_doc(
			{
				"doctype": "Purchase Reconciliation Tool",
				"company": COMPANY,
				"company_gstin": company_gstin,
				"gst_return": "GSTR 2B",
				"from_date": start,
				"to_date": end,
				"include_ignored": 0,
			}
		)

	def make_inward_supply(self, entry, **values):
		doc = frappe.get_doc(
			{
				"doctype": "GST Inward Supply",
				"company": COMPANY,
				"company_gstin": entry.company_gstin,
				"supplier_gstin": entry.supplier_gstin,
				"supplier_name": entry.supplier_legal_name,
				"bill_no": entry.supplier_invoice_no,
				"bill_date": entry.supplier_invoice_date,
				"classification": "B2B",
				"doc_type": "Invoice",
				"place_of_supply": entry.place_of_supply,
				"is_reverse_charge": entry.is_reverse_charge,
				"taxable_value": entry.total_taxable,
				"cgst": entry.total_cgst,
				"sgst": entry.total_sgst,
				"igst": entry.total_igst,
				"cess": entry.total_cess,
				"return_period_2b": getdate(today()).strftime("%m%Y"),
				"action": "No Action",
				"match_status": "",
				**values,
			}
		)
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return doc

	def row_for(self, data, journal_entry):
		return next(row for row in data if row.get("purchase_invoice_name") == journal_entry)

	def entry_status(self, entry):
		status = frappe.db.get_value("Journal Entry", entry.name, "reconciliation_status")
		inward = frappe.db.get_value(
			"GST Inward Supply",
			{"link_doctype": "Journal Entry", "link_name": entry.name},
			["name", "match_status"],
			as_dict=True,
		)
		if inward and inward.match_status == "Mismatch":
			status = "Mismatch"
		return status, inward.name if inward else None

	def test_match_accept_unlink(self):
		entry = self.post(self.make_entry())
		inward = self.make_inward_supply(entry)

		data = self.tool().reconcile_and_generate_data()
		row = self.row_for(data, entry.linked_journal_entry)
		self.assertEqual(
			(row.purchase_doctype, row.match_status, row.inward_supply_name),
			("Journal Entry", "Exact Match", inward.name),
		)
		self.assertEqual(
			frappe.db.get_value("GST Inward Supply", inward.name, ["link_doctype", "link_name"]),
			("Journal Entry", entry.linked_journal_entry),
		)
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "reconciliation_status"),
			"Match Found",
		)
		self.assertEqual(self.entry_status(entry), ("Match Found", inward.name))

		self.tool().apply_action([row], "Accept")
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "reconciliation_status"),
			"Reconciled",
		)
		self.assertEqual(frappe.db.get_value("GST Inward Supply", inward.name, "action"), "Accept")
		self.assertEqual(self.entry_status(entry), ("Reconciled", inward.name))

		self.tool().unlink_documents([row])
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "reconciliation_status"),
			"Unreconciled",
		)
		self.assertFalse(frappe.db.get_value("GST Inward Supply", inward.name, "link_name"))
		self.assertEqual(self.entry_status(entry), ("Unreconciled", None))

	def test_manual_link_and_link_options(self):
		entry = self.post(self.make_entry())
		inward = self.make_inward_supply(entry, bill_no="TOTALLY-DIFFERENT")
		tool = self.tool()
		options = tool.get_link_options("Journal Entry", {"supplier_gstin": SUPPLIER_TN})["options"]
		self.assertIn(entry.linked_journal_entry, [option.value for option in options])
		tool.link_documents(entry.linked_journal_entry, inward.name, "Journal Entry")
		self.assertEqual(
			frappe.db.get_value("GST Inward Supply", inward.name, "match_status"), "Manual Match"
		)
		self.assertEqual(self.entry_status(entry), ("Match Found", inward.name))
		# a matched Journal Entry is no longer offered
		options = tool.get_link_options("Journal Entry", {"supplier_gstin": SUPPLIER_TN})["options"]
		self.assertNotIn(entry.linked_journal_entry, [option.value for option in options])

	def test_only_in_books_and_ignore(self):
		entry = self.post(self.make_entry())
		data = self.tool().reconcile_and_generate_data()
		row = self.row_for(data, entry.linked_journal_entry)
		self.assertEqual(
			(row.match_status, row.purchase_doctype, row.classification),
			("Only in Books", "Journal Entry", "B2B"),
		)
		self.tool().apply_action([row], "Ignore")
		self.assertEqual(
			frappe.db.get_value("Journal Entry", entry.linked_journal_entry, "reconciliation_status"),
			"Ignored",
		)
		self.assertEqual(self.entry_status(entry)[0], "Ignored")

	def test_mismatch(self):
		entry = self.post(self.make_entry())
		self.make_inward_supply(entry, taxable_value=900, cgst=81, sgst=81)
		row = self.row_for(self.tool().reconcile_and_generate_data(), entry.linked_journal_entry)
		self.assertEqual(row.match_status, "Mismatch")
		self.assertEqual(self.entry_status(entry)[0], "Mismatch")

	def test_other_gstin_not_matched(self):
		entry = self.post(self.make_entry())
		self.make_inward_supply(entry, company_gstin=KA_GSTIN)
		self.tool().reconcile_and_generate_data()
		self.assertEqual(self.entry_status(entry), ("Unreconciled", None))

	def test_cancel_after_reconciliation(self):
		# test case 17
		entry = self.post(self.make_entry())
		inward = self.make_inward_supply(entry)
		row = self.row_for(self.tool().reconcile_and_generate_data(), entry.linked_journal_entry)
		self.tool().apply_action([row], "Accept")
		with self.assertRaisesRegex(frappe.ValidationError, "reconciled"):
			self.bill(entry.name).cancel()
		from quick_expense.api import cancel_with_reason

		cancel_with_reason(entry.name, "Wrong supplier invoice")
		self.assertEqual(
			frappe.db.get_value("GST Inward Supply", inward.name, ["link_name", "match_status"]),
			("", "Unlinked"),
		)
		self.assertEqual(self.entry_status(entry)[0], "Not Applicable")

	def test_tool_methods_stay_whitelisted(self):
		from india_compliance.gst_india.doctype.purchase_reconciliation_tool.purchase_reconciliation_tool import (
			PurchaseReconciliationTool,
		)

		for method in (
			"apply_action",
			"unlink_documents",
			"link_documents",
			"get_link_options",
			"reconcile_and_generate_data",
		):
			function = getattr(PurchaseReconciliationTool, method)
			self.assertTrue(getattr(function, "_qe_wrapped", False), method)
			frappe.is_whitelisted(function)


class TestCancelAndAmend(QETestCase):
	def test_cancel_records_history(self):
		entry = self.post(self.make_entry())
		entry.je.cancel()
		bill = self.bill(entry.name)
		self.assertEqual((bill.docstatus, bill.approval_status), (2, "Cancelled"))
		self.assertEqual(bill.approval_history[-1].action, "Cancelled")
		self.assertIsNone(
			__import__("quick_expense.services.duplicate_service", fromlist=["x"]).get_holder(
				entry.duplicate_key
			)
		)

	def test_amend_goes_through_approval_again(self):
		entry = self.post(self.make_entry(supplier_invoice_no="AMD-1"))
		entry.je.cancel()
		amended = frappe.copy_doc(frappe.get_doc("Journal Entry", entry.name))
		amended.docstatus = 0  # the desk "Amend" button does this
		amended.amended_from = entry.name
		amended.insert()
		bill = self.bill(amended.name)
		self.assertEqual(bill.approval_status, "Draft")
		self.assertFalse(bill.approval_history)
		self.assertEqual(self.submit(bill).approval_status, "Pending Approval")

	def test_cancel_in_filed_period_blocked(self):
		# test case 18
		entry = self.post(self.make_entry())
		frappe.get_doc(
			{
				"doctype": "GST Return Log",
				"gstin": TN_GSTIN,
				"return_type": "GSTR3B",
				"return_period": entry.itc_claim_period,
				"filing_status": "Filed",
				"company": COMPANY,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		with self.assertRaisesRegex(frappe.ValidationError, "filed"):
			self.bill(entry.name).cancel()

	def test_cancel_on_lock_date_blocked(self):
		entry = self.post(self.make_entry())
		settings = frappe.get_single("Quick Expense Settings")
		settings.append(
			"gst_lock_dates", {"company": COMPANY, "company_gstin": TN_GSTIN, "lock_date": today()}
		)
		settings.save()
		with self.assertRaisesRegex(frappe.ValidationError, "GST Lock Date"):
			self.bill(entry.name).cancel()

	def test_only_drafts_can_be_deleted(self):
		frappe.delete_doc("Journal Entry", self.make_entry().name)
		pending = self.submit(self.make_entry())
		with self.assertRaisesRegex(frappe.ValidationError, "Only draft"):
			frappe.delete_doc("Journal Entry", pending.name)


class TestJournalEntryForm(QETestCase):
	"""What the user types on the Journal Entry and what the system adds."""

	def test_resave_rebuilds_system_rows_once(self):
		entry = self.make_entry(
			lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=1000)]
		)
		je = frappe.get_doc("Journal Entry", entry.name)
		je.accounts[0].qe_taxable_amount = 2000
		je.save()
		components = [row.qe_gst_component for row in je.accounts]
		self.assertEqual(sorted(components), ["CGST", "Expense", "Payment", "SGST"])
		self.assertEqual(je_rows(je.name)[(cash(), "Payment")], -2360)
		self.assertEqual((je.qe_grand_total, je.total_debit, je.total_credit), (2360, 2360, 2360))

	def test_typed_cash_row_becomes_paid_through(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Quick Expense",
				"company": COMPANY,
				"company_gstin": TN_GSTIN,
				"posting_date": today(),
				"qe_gst_treatment": "Registered Regular",
				"qe_supplier_gstin": SUPPLIER_TN,
				"qe_supplier_legal_name": "Chennai Courier Services Private Limited",
				"qe_supplier_invoice_no": "TYPED-1",
				"qe_supplier_invoice_date": today(),
				"qe_bill_addressed_to_company_gstin": 1,
				"accounts": [
					{
						"account": "QE Courier - QET",
						"debit_in_account_currency": 500,
						"qe_item_tax_template": "GST 18% - QET",
					},
					{"account": cash(), "credit_in_account_currency": 500},
				],
			}
		).insert()
		self.assertEqual(je.qe_paid_through_account, cash())
		self.assertEqual(je_rows(je.name)[(cash(), "Payment")], -590)
		self.assertEqual(je.accounts[0].qe_taxable_amount, 500)

	def test_gst_rows_cannot_be_typed(self):
		entry = self.make_entry(insert=False)
		entry.je.append("accounts", {"account": "Input Tax CGST - QET", "debit_in_account_currency": 90})
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be entered by hand"):
			entry.je.insert()

	def test_gst_rate_is_required_for_registered_supplier(self):
		entry = self.make_entry(insert=False)
		entry.je.accounts[0].qe_item_tax_template = None
		with self.assertRaisesRegex(frappe.ValidationError, "select the GST rate"):
			entry.je.insert()

	def test_standard_submit_is_blocked(self):
		entry = self.make_entry()
		with self.assertRaisesRegex(frappe.ValidationError, "Submit for Approval"):
			frappe.get_doc("Journal Entry", entry.name).submit()

	def test_quick_expense_user_only_makes_quick_expense_bills(self):
		with as_(user("user")):
			mine = self.make_entry()
			other = frappe.get_doc(
				{
					"doctype": "Journal Entry",
					"voucher_type": "Journal Entry",
					"company": COMPANY,
					"posting_date": today(),
					"accounts": [
						{"account": "QE Courier - QET", "debit_in_account_currency": 10},
						{"account": cash(), "credit_in_account_currency": 10},
					],
				}
			)
			with self.assertRaises(frappe.PermissionError):
				other.insert()
			visible = frappe.get_list("Journal Entry", pluck="voucher_type", distinct=True)
		self.assertEqual(set(visible), {"Quick Expense"})
		self.assertTrue(mine.name)


class TestCompatibilityGuard(QETestCase):
	def setUp(self):
		super().setUp()
		ic_reports.apply_patches()

	def test_self_test_passes_and_records_version(self):
		set_settings(verified_ic_version="0.0.0")
		result = compatibility_guard.check_compatibility()
		self.assertEqual(result["status"], "passed", result.get("problems"))
		self.assertEqual(
			frappe.db.get_single_value("Quick Expense Settings", "verified_ic_version"),
			compatibility_guard.installed_version(),
		)

	def test_failed_self_test_switches_overrides_off(self):
		# test case 21
		entry = self.post(self.make_entry())
		set_settings(verified_ic_version="0.0.0")
		with patch.object(
			compatibility_guard,
			"_structure_problems",
			return_value=["GSTR3BInwardInvoices.get_data signature changed"],
		):
			self.assertEqual(compatibility_guard.check_compatibility()["status"], "failed")
		self.assertEqual(frappe.db.get_single_value("Quick Expense Settings", "overrides_disabled"), 1)
		frappe.clear_document_cache("Quick Expense Settings", "Quick Expense Settings")
		self.assertFalse(
			[row for row in inward_rows(TN_GSTIN) if row.voucher_no == entry.linked_journal_entry]
		)
		# reconciliation also stops offering Journal Entries
		start, end = month_range()
		tool = frappe.get_doc(
			{
				"doctype": "Purchase Reconciliation Tool",
				"company": COMPANY,
				"company_gstin": TN_GSTIN,
				"gst_return": "GSTR 2B",
				"from_date": start,
				"to_date": end,
				"include_ignored": 0,
			}
		)
		self.assertFalse(
			[
				row
				for row in tool.reconcile_and_generate_data()
				if row.get("purchase_doctype") == "Journal Entry"
			]
		)
		self.assertEqual(compatibility_guard.check_compatibility(force=True)["status"], "passed")
		frappe.clear_document_cache("Quick Expense Settings", "Quick Expense Settings")
		self.assertEqual(len(inward_rows(TN_GSTIN)), 1)

	def test_same_version_skips_self_test(self):
		set_settings(verified_ic_version=compatibility_guard.installed_version())
		with patch.object(compatibility_guard, "run_self_test") as run:
			self.assertEqual(compatibility_guard.check_compatibility()["status"], "verified")
			run.assert_not_called()


class TestCleanForm(QETestCase):
	"""Payment as a table row, supplier details read-only once fetched from the GSTIN."""

	def test_gstin_fills_read_only_supplier_fields_on_save(self):
		entry = self.make_entry(supplier_legal_name="", supplier_address="")
		je = frappe.get_doc("Journal Entry", entry.name)
		self.assertEqual(
			(
				je.qe_supplier_legal_name,
				je.qe_supplier_trade_name,
				je.qe_supplier_state,
				je.qe_supplier_pincode,
				je.qe_gstin_status,
				je.qe_supplier_name_entered_manually,
			),
			("Chennai Courier Services Private Limited", "Fast Courier", "Tamil Nadu", "600002", "Active", 0),
		)
		self.assertIn("Anna Salai", je.qe_supplier_address)
		meta = frappe.get_meta("Journal Entry")
		for field in (
			"qe_supplier_legal_name",
			"qe_supplier_trade_name",
			"qe_supplier_state",
			"qe_supplier_address",
			"qe_supplier_pincode",
		):
			self.assertIn("qe_supplier_gstin", meta.get_field(field).read_only_depends_on or "", field)
		for field in (
			"qe_paid_through_account",
			"qe_payment_type",
			"qe_supplier_name_entered_manually",
			"qe_approval_chain",
			"qe_bill_key",
		):
			self.assertTrue(meta.get_field(field).hidden, field)

	def test_payment_is_the_cash_row_of_the_table(self):
		entry = self.make_entry()
		je = frappe.get_doc("Journal Entry", entry.name)
		payment = [row for row in je.accounts if row.qe_gst_component == "Payment"]
		self.assertEqual(len(payment), 1)
		self.assertEqual((payment[0].account, payment[0].credit_in_account_currency), (cash(), 1180))
		self.assertEqual(je.qe_paid_through_account, cash())  # hidden, derived from the row

	def test_two_cash_bank_rows_refused(self):
		from quick_expense.tests.utils import bank

		entry = self.make_entry(insert=False)
		entry.je.append("accounts", {"account": bank()})
		with self.assertRaisesRegex(frappe.ValidationError, "one Cash or Bank row"):
			entry.je.insert()

	def test_payment_account_cannot_change_in_approval(self):
		from quick_expense.tests.utils import bank

		entry = self.submit(self.make_entry())
		with as_(user("approver")):
			je = frappe.get_doc("Journal Entry", entry.name)
			next(row for row in je.accounts if row.qe_gst_component == "Payment").account = bank()
			with self.assertRaisesRegex(frappe.ValidationError, "Not allowed"):
				je.save()


class TestInvoiceManagementSystem(QETestCase):
	"""Quick Expense Journal Entries in India Compliance's IMS."""

	def setUp(self):
		super().setUp()
		ic_reports.apply_patches()

	make_inward_supply = TestPurchaseReconciliationTool.make_inward_supply
	entry_status = TestPurchaseReconciliationTool.entry_status

	def ims(self):
		return frappe.get_doc(
			{
				"doctype": "GST Invoice Management System",
				"company": COMPANY,
				"company_gstin": TN_GSTIN,
				"period": getdate(today()).strftime("%m%Y"),
			}
		)

	def make_ims_supply(self, entry, **values):
		return self.make_inward_supply(
			entry, ims_action="No Action", previous_ims_action="No Action", **values
		)

	def ims_row(self, data, inward):
		return next(row for row in data if row.get("inward_supply_name") == inward.name)

	def test_auto_match_shows_journal_entry(self):
		entry = self.post(self.make_entry())
		inward = self.make_ims_supply(entry)
		data = self.ims().autoreconcile_and_get_data()["invoice_data"]
		row = self.ims_row(data, inward)
		self.assertEqual(
			(row.purchase_doctype, row.purchase_invoice_name, row.match_status),
			("Journal Entry", entry.linked_journal_entry, "Exact Match"),
		)
		self.assertEqual(self.entry_status(entry), ("Match Found", inward.name))
		details = self.ims().get_invoice_details(entry.linked_journal_entry, inward.name)
		self.assertEqual(details.purchase_invoice_name, entry.linked_journal_entry)

	def test_reverse_charge_not_matched_in_ims(self):
		entry = self.post(self.make_entry())
		frappe.db.set_value("Journal Entry", entry.linked_journal_entry, "qe_is_reverse_charge", 1)
		inward = self.make_ims_supply(entry)
		self.ims().autoreconcile_and_get_data()
		self.assertFalse(frappe.db.get_value("GST Inward Supply", inward.name, "link_name"))

	def test_actions_move_claim_period(self):
		entry = self.post(self.make_entry())
		inward = self.make_ims_supply(entry)
		ims = self.ims()
		ims.autoreconcile_and_get_data()
		claim = lambda: frappe.db.get_value(  # noqa: E731
			"Journal Entry", entry.linked_journal_entry, "qe_itc_claim_period"
		)

		ims.update_action([inward.name], "Rejected")
		self.assertEqual(claim(), "Deferred")
		self.assertEqual(frappe.db.get_value("GST Inward Supply", inward.name, "ims_action"), "Rejected")

		ims.update_action([inward.name], "Accepted")
		self.assertEqual(claim(), ims.period)

	def test_link_options_offer_journal_entry(self):
		entry = self.post(self.make_entry())
		inward = self.make_ims_supply(entry, bill_no="TOTALLY-DIFFERENT")
		ims = self.ims()
		options = ims.get_link_options("Journal Entry", {"supplier_gstin": SUPPLIER_TN})["options"]
		self.assertIn(entry.linked_journal_entry, [option.value for option in options])
		ims.link_documents(entry.linked_journal_entry, inward.name, "Journal Entry")
		self.assertEqual(self.entry_status(entry), ("Match Found", inward.name))
