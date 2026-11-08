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

	def test_duplicate_against_pending_entry(self):
		# test case 10 (pending)
		first = self.submit(self.make_entry(supplier_invoice_no="CC-101"))
		self.assertEqual(first.approval_status, "Pending Approval")
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
		self.assertEqual(other.approval_status, "Pending Approval")
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

	def test_rejection_releases_key(self):
		entry = self.submit(self.make_entry(supplier_invoice_no="REL-1"))
		with as_(user("approver")):
			self.bill(entry.name).reject("Wrong bill")
		self.assertIsNone(duplicate_service.get_holder(entry.duplicate_key))
		self.assertEqual(
			self.submit(self.make_entry(supplier_invoice_no="REL-1")).approval_status, "Pending Approval"
		)

	def test_unregistered_duplicate_warns(self):
		values = dict(
			gst_treatment="Unregistered",
			supplier_gstin="",
			supplier_legal_name="Tea Stall",
			supplier_invoice_no="7",
			supplier_invoice_date=today(),
		)
		self.submit(self.make_entry(**values))
		second = self.submit(self.make_entry(**values))
		self.assertIn("same number, date and amount", second.warnings)

	def test_attachment_hash_warns(self):
		file = png_file("bill.png")
		self.submit(self.make_entry(bill_attachment=file.file_url))
		second = self.submit(self.make_entry(bill_attachment=file.file_url, supplier_invoice_no="OTHER-1"))
		self.assertIn("same bill file", second.warnings)


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
		entry = self.submit(self.make_entry(supplier_invoice_no="ABCDEFGHIJKLMNOPQ"))
		self.assertIn("longer than 16", entry.warnings)

	def test_back_dated_blocked_for_user_forced_for_accounts_manager(self):
		old = add_days(today(), -45)
		with as_(user("user")):
			entry = self.make_entry(posting_date=old, supplier_invoice_date=old)
			with self.assertRaisesRegex(frappe.ValidationError, "Only an Accounts Manager"):
				entry.submit_for_approval()
		with as_(user("am")):
			entry = self.make_entry(posting_date=old, supplier_invoice_date=old)
			entry.submit_for_approval()
			self.assertIn("Back-dated", entry.forced_approval_reasons)

	def test_itc_time_limit_warning_and_expiry(self):
		# test case 23. Today is in November: bills of last financial year must be claimed by 30 November.
		last_fy_bill = add_months(today(), -8)  # March of the previous financial year
		set_settings(maximum_backdated_days=0, itc_time_limit_warning_days=40)
		near = self.submit(self.make_entry(supplier_invoice_date=last_fy_bill))
		self.assertIn("must be claimed by", near.warnings)
		self.assertFalse(near.lines[0].ineligibility_reason)

		expired = self.submit(self.make_entry(supplier_invoice_date=add_months(today(), -20)))
		self.assertEqual(expired.lines[0].ineligibility_reason, "Time limit expired")
		self.assertEqual(expired.total_eligible_itc, 0)

	def test_itc_claim_period_moved_needs_reason_and_approval(self):
		next_period = frappe.utils.getdate(add_months(today(), 1)).strftime("%m%Y")
		entry = self.make_entry(itc_claim_period=next_period)
		with self.assertRaisesRegex(frappe.ValidationError, "reason for moving"):
			self.submit(entry)
		entry = self.submit(
			self.make_entry(itc_claim_period=next_period, itc_claim_period_reason="Bill not yet in 2B")
		)
		self.assertIn("ITC Claim Period moved", entry.forced_approval_reasons)

	def test_itc_claim_period_before_posting_month_blocked(self):
		entry = self.make_entry(itc_claim_period="012020")
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be earlier"):
			self.submit(entry)

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
			self.submit(self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA)).approval_status,
			"Pending Approval",
		)

	def test_gst_lock_date_per_gstin(self):
		settings = frappe.get_single("Quick Expense Settings")
		settings.append(
			"gst_lock_dates", {"company": COMPANY, "company_gstin": TN_GSTIN, "lock_date": today()}
		)
		settings.save()
		with self.assertRaisesRegex(frappe.ValidationError, "GST Lock Date"):
			self.submit(self.make_entry())
		self.assertEqual(
			self.submit(self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA)).approval_status,
			"Pending Approval",
		)


class TestAmountsAndPayment(QETestCase):
	def test_zero_and_negative_amounts_blocked(self):
		for amount in (0, -100):
			with self.assertRaises(frappe.ValidationError):
				entry = self.make_entry(
					lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=amount)]
				)
				self.submit(entry)

	def test_grand_total_above_limit_blocked(self):
		# test case 24
		entry = self.make_entry(
			lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=45000)]
		)
		self.assertGreater(entry.grand_total, 50000)
		with self.assertRaisesRegex(frappe.ValidationError, "standard Purchase Invoice"):
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

	def test_account_not_allowed(self):
		settings = frappe.get_single("Quick Expense Settings")
		settings.allowed_accounts = [row for row in settings.allowed_accounts if row.account != bank()]
		settings.save()
		entry = self.make_entry(paid_through_account=bank(), reference_no="UTR9", reference_date=today())
		with self.assertRaisesRegex(frappe.ValidationError, "not an allowed"):
			self.submit(entry)

	def test_cash_limit_warns_and_forces_approval(self):
		# test case 13: 6,000 + 5,000 to the same supplier on the same day
		first = self.submit(
			self.make_entry(
				lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=6000)],
				gst_treatment="Unregistered",
				supplier_gstin="",
				supplier_legal_name="Ravi Tyres",
			)
		)
		self.assertNotIn("Cash limit", first.forced_approval_reasons or "")
		second = self.submit(
			self.make_entry(
				lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=5000)],
				gst_treatment="Unregistered",
				supplier_gstin="",
				supplier_legal_name="Ravi Tyres",
			)
		)
		self.assertIn("40A(3)", second.warnings)
		self.assertIn("Cash limit", second.forced_approval_reasons)

	def test_cash_limit_block_mode_and_transporter_limit(self):
		set_settings(cash_limit_action="Block")
		with self.assertRaisesRegex(frappe.ValidationError, "40A"):
			self.submit(
				self.make_entry(
					lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=9000)]
				)
			)
		# transporters have a higher limit (35,000)
		ok = self.submit(
			self.make_entry(
				lines=[dict(expense_category="Loading and Unloading Charges", taxable_amount=20000)]
			)
		)
		self.assertEqual(ok.approval_status, "Pending Approval")

	def test_bank_payment_has_no_cash_limit(self):
		entry = self.submit(
			self.make_entry(
				paid_through_account=bank(),
				reference_no="UTR1",
				reference_date=today(),
				lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=20000)],
			)
		)
		self.assertNotIn("Cash limit", entry.forced_approval_reasons or "")

	def test_attachment_mandatory_type_and_size(self):
		set_settings(attachment_mandatory_above=0)
		with self.assertRaisesRegex(frappe.ValidationError, "Attach the supplier"):
			self.submit(self.make_entry())
		exe = frappe.get_doc(
			{"doctype": "File", "file_name": "bill.exe", "content": b"MZ", "is_private": 1}
		).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "not allowed"):
			self.submit(self.make_entry(bill_attachment=exe.file_url))
		set_settings(max_attachment_size_mb=0.000001)
		big = png_file("big.png")
		with self.assertRaisesRegex(frappe.ValidationError, "maximum allowed"):
			self.submit(self.make_entry(bill_attachment=big.file_url))

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

	def test_different_supplier_same_day_no_cash_limit(self):
		self.submit(
			self.make_entry(lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=6000)])
		)
		other = self.submit(
			self.make_entry(
				supplier_gstin=SUPPLIER_TN_2,
				lines=[dict(expense_category="Office Stationery", taxable_amount=5000)],
			)
		)
		self.assertNotIn("Cash limit", other.forced_approval_reasons or "")
