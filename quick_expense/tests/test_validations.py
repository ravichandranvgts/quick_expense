"""Validations and edge cases (prompt section 12; test cases 10-13, 23, 24, 26)."""

import base64

import frappe
from frappe.utils import add_days, add_months, today

from quick_expense.services import duplicate_service
from quick_expense.services.duplicate_service import DuplicateBillError
from quick_expense.tests.utils import (
	COMPANY,
	COMPANY_2,
	KA_GSTIN,
	SUPPLIER_KA,
	SUPPLIER_TN,
	SUPPLIER_TN_2,
	TN_GSTIN,
	QETestCase,
	as_,
	bank,
	set_settings,
	user,
)

PNG_1PX = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def png_file(name):
	return frappe.get_doc(
		{"doctype": "File", "file_name": name, "content": PNG_1PX, "is_private": 1}
	).insert()


class TestDuplicates(QETestCase):
	def test_key_normalisation(self):
		self.assertEqual(duplicate_service.normalise_invoice_no(" inv 001 / a "), "INV001/A")
		key = duplicate_service.build_key(TN_GSTIN, SUPPLIER_TN, "inv 1", "2026-05-01")
		self.assertEqual(key, f"{TN_GSTIN}|{SUPPLIER_TN}|INV1|2026-27")

	def test_duplicate_against_submitted_entry(self):
		# test case 10
		first = self.submit(self.make_entry(supplier_invoice_no="CC-101"))
		self.assertEqual(first.docstatus, 1)
		second = self.make_entry(supplier_invoice_no=" cc-101 ")
		with self.assertRaisesRegex(DuplicateBillError, "already entered"):
			self.submit(second)

	def test_duplicate_against_posted_entry(self):
		self.post(self.make_entry(supplier_invoice_no="CC-102"))
		with self.assertRaises(DuplicateBillError):
			self.submit(self.make_entry(supplier_invoice_no="CC-102"))

	def test_duplicate_against_purchase_invoice(self):
		# test case 10 (bill already booked as a standard Purchase Invoice)
		pi = frappe.new_doc("Purchase Invoice")
		pi.update(
			{
				"name": "QE-TEST-PINV-1",
				"company": COMPANY,
				"supplier": "Standard Supplier",
				"company_gstin": TN_GSTIN,
				"supplier_gstin": SUPPLIER_TN,
				"bill_no": "CC 103",
				"bill_date": today(),
				"posting_date": today(),
				"docstatus": 1,
				"is_return": 0,
			}
		)
		pi.db_insert()
		with self.assertRaisesRegex(DuplicateBillError, "Purchase Invoice"):
			self.submit(self.make_entry(supplier_invoice_no="cc103"))

	def test_same_bill_two_gstins_allowed_same_gstin_blocked(self):
		# test case 26
		self.submit(self.make_entry(supplier_invoice_no="X-1", company_gstin=TN_GSTIN))
		other = self.submit(self.make_entry(supplier_invoice_no="X-1", company_gstin=KA_GSTIN))
		self.assertEqual(other.docstatus, 1)
		with self.assertRaises(DuplicateBillError):
			self.submit(self.make_entry(supplier_invoice_no="X-1", company_gstin=TN_GSTIN))

	def test_same_number_next_financial_year_allowed(self):
		self.submit(self.make_entry(supplier_invoice_no="FY-1", supplier_invoice_date=today()))
		key_old = duplicate_service.build_key(TN_GSTIN, SUPPLIER_TN, "FY-1", "2026-03-31")
		key_new = duplicate_service.build_key(TN_GSTIN, SUPPLIER_TN, "FY-1", today())
		self.assertNotEqual(key_old, key_new)

	def test_simultaneous_submission_only_one_wins(self):
		# test case 11: the key is re-checked under a database lock that serialises submissions
		first = self.submit(self.make_entry(supplier_invoice_no="RACE-1"))
		second = self.make_entry(supplier_invoice_no="RACE-1")
		second.duplicate_key = duplicate_service.build_key(TN_GSTIN, SUPPLIER_TN, "RACE-1", today())
		with self.assertRaises(DuplicateBillError):
			duplicate_service.reserve(second)
		lock = duplicate_service._lock_name(second.duplicate_key)
		self.assertTrue(frappe.db.sql("SELECT IS_USED_LOCK(%s)", (lock,))[0][0])
		self.assertEqual(duplicate_service.get_holder(second.duplicate_key), first.name)

	def test_cancel_releases_key(self):
		entry = self.submit(self.make_entry(supplier_invoice_no="REL-1"))
		entry.je.cancel()
		self.assertIsNone(duplicate_service.get_holder(entry.duplicate_key))
		self.assertEqual(self.submit(self.make_entry(supplier_invoice_no="REL-1")).docstatus, 1)

	def test_draft_holds_no_key(self):
		self.make_entry(supplier_invoice_no="DRAFT-1")
		self.assertEqual(self.submit(self.make_entry(supplier_invoice_no="DRAFT-1")).docstatus, 1)

	def test_unregistered_duplicate_warns(self):
		values = dict(
			gst_treatment="Unregistered",
			supplier_gstin="",
			supplier_legal_name="Tea Stall",
			supplier_invoice_no="7",
			supplier_invoice_date=today(),
		)
		self.submit(self.make_entry(**values))
		self.submit(self.make_entry(**values))
		self.assertIn("same number, date and amount", self.warnings)

	def test_attachment_hash_warns(self):
		file = png_file("bill.png")
		self.submit(self.make_entry(bill_attachment=file.file_url))
		self.submit(self.make_entry(bill_attachment=file.file_url, supplier_invoice_no="OTHER-1"))
		self.assertIn("same bill file", self.warnings)


class TestInvoiceRules(QETestCase):
	def test_invoice_date_after_posting_date(self):
		entry = self.make_entry(
			posting_date=add_days(today(), -2), supplier_invoice_date=add_days(today(), -1)
		)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be after the Posting Date"):
			self.submit(entry)

	def test_future_dates_blocked(self):
		entry = self.make_entry(posting_date=add_days(today(), 1), supplier_invoice_date=today())
		with self.assertRaisesRegex(frappe.ValidationError, "future"):
			self.submit(entry)

	def test_registered_needs_invoice_number(self):
		entry = self.make_entry(supplier_invoice_no="")
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier Invoice No is mandatory"):
			self.submit(entry)

	def test_long_invoice_number_warns(self):
		self.submit(self.make_entry(supplier_invoice_no="ABCDEFGHIJKLMNOPQ"))
		self.assertIn("longer than 16", self.warnings)

	def test_itc_time_limit_warning_and_expiry(self):
		# test case 23. Today is in November: bills of last financial year must be claimed by 30 November.
		last_fy_bill = add_months(today(), -8)  # March of the previous financial year
		near = self.submit(self.make_entry(supplier_invoice_date=last_fy_bill))
		self.assertIn("must be claimed by", self.warnings)
		self.assertFalse(near.lines[0].ineligibility_reason)

		expired = self.submit(self.make_entry(supplier_invoice_date=add_months(today(), -20)))
		self.assertEqual(expired.lines[0].ineligibility_reason, "Time limit expired")
		self.assertEqual(expired.total_eligible_itc, 0)

	def test_itc_claim_period_follows_the_posting_date(self):
		# typed or stale values are replaced: the period is read-only and always the posting month
		last_month = add_months(today(), -1)
		entry = self.make_entry(
			itc_claim_period="012020", posting_date=last_month, supplier_invoice_date=last_month
		)
		self.assertEqual(entry.itc_claim_period, frappe.utils.getdate(last_month).strftime("%m%Y"))
		entry.je.posting_date = today()
		entry.je.save()
		self.assertEqual(
			self.bill(entry.name).itc_claim_period, frappe.utils.getdate(today()).strftime("%m%Y")
		)
		meta = frappe.get_meta("Journal Entry")
		self.assertTrue(meta.get_field("qe_itc_claim_period").read_only)
		self.assertFalse(meta.has_field("qe_itc_claim_period_reason"))

	def test_filed_period_blocked(self):
		period = frappe.utils.getdate(today()).strftime("%m%Y")
		frappe.get_doc(
			{
				"doctype": "GST Return Log",
				"gstin": TN_GSTIN,
				"return_type": "GSTR3B",
				"return_period": period,
				"filing_status": "Filed",
				"company": COMPANY,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		with self.assertRaisesRegex(frappe.ValidationError, "already filed"):
			self.submit(self.make_entry())
		# the other GSTIN of the same company is not affected
		self.assertEqual(
			self.submit(self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA)).docstatus, 1
		)


class TestAmountsAndPayment(QETestCase):
	def test_zero_and_negative_amounts_blocked(self):
		for amount in (0, -100):
			with self.assertRaises(frappe.ValidationError):
				entry = self.make_entry(
					lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=amount)]
				)
				self.submit(entry)

	def test_capital_account_blocked(self):
		asset_account = frappe.db.get_value(
			"Account",
			{
				"company": COMPANY,
				"root_type": "Asset",
				"is_group": 0,
				"account_type": ("in", ("Fixed Asset", "")),
			},
			"name",
		)
		entry = self.make_entry(
			lines=[
				dict(expense_category="Office Stationery", taxable_amount=1000, expense_account=asset_account)
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Capital purchases"):
			self.submit(entry)

	def test_bank_payment_needs_reference(self):
		# test case 12
		entry = self.make_entry(paid_through_account=bank())
		self.assertEqual(entry.payment_type, "Bank")
		with self.assertRaisesRegex(frappe.ValidationError, "Reference No and Reference Date"):
			self.submit(entry)
		entry = self.make_entry(
			paid_through_account=bank(), reference_no="UTR123", reference_date=add_days(today(), 1)
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Reference Date cannot be after"):
			self.submit(entry)

	def test_cost_center_rules(self):
		other_cc = frappe.db.get_value("Cost Center", {"company": COMPANY_2, "is_group": 0})
		with self.assertRaisesRegex(frappe.ValidationError, "belong"):
			self.make_entry(
				lines=[
					dict(
						expense_category="Courier and Postage Charges",
						taxable_amount=100,
						cost_center=other_cc,
					)
				]
			)

	def test_ineligible_others_needs_reason(self):
		entry = self.make_entry(
			lines=[
				dict(
					expense_category="Courier and Postage Charges",
					taxable_amount=1000,
					eligibility_for_itc="Ineligible Others",
				)
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "reason the GST is Ineligible Others"):
			self.submit(entry)
		entry = self.submit(
			self.make_entry(
				lines=[
					dict(
						expense_category="Courier and Postage Charges",
						taxable_amount=1000,
						eligibility_for_itc="Ineligible Others",
						eligibility_change_reason="Personal use",
					)
				]
			)
		)
		self.assertEqual((entry.total_ineligible_gst, entry.total_eligible_itc), (180, 0))

	def test_supplier_name_required(self):
		entry = self.make_entry(gst_treatment="Unregistered", supplier_gstin="", supplier_legal_name="")
		with self.assertRaisesRegex(frappe.ValidationError, "shop or supplier name"):
			self.submit(entry)

	def test_disabled_quick_expense(self):
		set_settings(enable_quick_expense=0)
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			self.submit(self.make_entry())
