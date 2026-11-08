"""GSTIN service and tax engine (prompt sections 2A, 6, 7; test cases 1, 2, 4-9, 14-16, 25, 27)."""

import frappe
from frappe.utils import flt

from quick_expense.services import gstin_service
from quick_expense.services.tax_engine import (
	TaxError,
	gst_financial_year_label,
	itc_time_limit,
	round_half_up,
)
from quick_expense.tests.utils import (
	API_DOWN,
	COMPANY_2_GSTIN,
	KA_GSTIN,
	SUPPLIER_CANCELLED,
	SUPPLIER_COMPOSITION,
	SUPPLIER_INDIVIDUAL,
	SUPPLIER_KA,
	SUPPLIER_MH,
	SUPPLIER_SAME_PAN,
	SUPPLIER_TN,
	TN_GSTIN,
	QETestCase,
	make_gstin,
	set_settings,
)


class TestGSTINService(QETestCase):
	def test_normalise_and_checksum(self):
		self.assertEqual(
			gstin_service.normalise_gstin(f" {SUPPLIER_TN[:5].lower()} {SUPPLIER_TN[5:]} "), SUPPLIER_TN
		)
		self.assertIsNone(gstin_service.get_validation_error(SUPPLIER_TN))
		wrong_check = SUPPLIER_TN[:14] + ("A" if SUPPLIER_TN[14] != "A" else "B")
		self.assertIn("check character", gstin_service.get_validation_error(wrong_check))
		self.assertIn("15 characters", gstin_service.get_validation_error(SUPPLIER_TN[:14]))
		self.assertIn("state code", gstin_service.get_validation_error(make_gstin("40", "AAFCS1111A")))
		self.assertIn("format", gstin_service.get_validation_error("33AAFCS1111A0Z5"))

	def test_pan_and_constitution(self):
		self.assertEqual(gstin_service.pan_from_gstin(SUPPLIER_TN), "AAFCS1111A")
		self.assertEqual(gstin_service.constitution_from_pan("AAFCS1111A"), "Body Corporate")
		self.assertEqual(gstin_service.constitution_from_pan("ABCPS3333C"), "Non-Body-Corporate")

	def test_api_details_are_cached(self):
		details = gstin_service.get_gstin_details(SUPPLIER_TN)
		self.assertEqual(details.source, "API")
		self.assertEqual(details.legal_name, "Chennai Courier Services Private Limited")
		self.assertEqual(details.state, "Tamil Nadu")
		self.assertEqual(details.body_corporate, "Body Corporate")
		self.assertEqual(gstin_service.get_gstin_details(SUPPLIER_TN).source, "API")
		self.assertEqual(self.api_mock.call_count, 1)  # the second lookup is not an API call

	def test_api_down_falls_back_to_manual(self):
		# test case 15
		API_DOWN["value"] = True
		details = gstin_service.get_gstin_details(SUPPLIER_KA)
		self.assertEqual(details.source, "Manual")
		self.assertEqual(details.state, "Karnataka")
		self.assertEqual(details.pan, "AAFCS2222B")

		entry = self.make_entry(supplier_gstin=SUPPLIER_KA, supplier_legal_name="Typed By User")
		entry = self.submit(entry)
		self.assertEqual(entry.supplier_name_entered_manually, 1)
		self.assertIn("typed manually", entry.forced_approval_reasons)
		self.assertEqual(entry.supplier_legal_name, "Typed By User")

	def test_api_switched_off(self):
		set_settings(use_gstin_api=0)
		self.assertEqual(gstin_service.get_gstin_details(SUPPLIER_TN).source, "Manual")

	def test_cancelled_gstin_blocked_by_default(self):
		# test case 14
		entry = self.make_entry(supplier_gstin=SUPPLIER_CANCELLED)
		with self.assertRaisesRegex(frappe.ValidationError, "cancelled GSTIN"):
			self.submit(entry)

	def test_cancelled_gstin_warn_forces_approval(self):
		set_settings(cancelled_gstin_action="Warn")
		entry = self.submit(self.make_entry(supplier_gstin=SUPPLIER_CANCELLED))
		self.assertIn("Cancelled", entry.forced_approval_reasons)

	def test_composition_registration_switches_treatment(self):
		entry = self.submit(self.make_entry(supplier_gstin=SUPPLIER_COMPOSITION))
		self.assertEqual(entry.gst_treatment, "Registered Composition")
		self.assertEqual(entry.total_gst, 0)
		self.assertEqual(entry.grand_total, 1000)

	def test_composition_with_gst_on_bill_blocked(self):
		entry = self.make_entry(
			supplier_gstin=SUPPLIER_COMPOSITION,
			gst_treatment="Registered Composition",
			insert=False,
			lines=[
				dict(
					expense_category="Courier and Postage Charges",
					taxable_amount=1000,
					gst_as_per_bill_entered=1,
					gst_as_per_bill=180,
				)
			],
		)
		with self.assertRaisesRegex(TaxError, "composition supplier"):
			entry.insert()

	def test_gstin_of_another_company_blocked(self):
		# test case 27
		entry = self.make_entry(supplier_gstin=COMPANY_2_GSTIN, supplier_legal_name="Sister Company")
		with self.assertRaisesRegex(gstin_service.GSTINError, "belongs to company"):
			self.submit(entry)

	def test_own_other_branch_gstin_blocked(self):
		entry = self.make_entry(supplier_gstin=KA_GSTIN, supplier_legal_name="Our Karnataka Branch")
		with self.assertRaisesRegex(gstin_service.GSTINError, "belongs to company"):
			self.submit(entry)

	def test_same_pan_as_company_blocked(self):
		entry = self.make_entry(supplier_gstin=SUPPLIER_SAME_PAN, supplier_legal_name="Unlisted Branch")
		with self.assertRaisesRegex(gstin_service.GSTINError, "same PAN"):
			self.submit(entry)

	def test_company_gstin_must_belong_to_company(self):
		with self.assertRaisesRegex(frappe.ValidationError, "not a GST address"):
			self.make_entry(company_gstin=COMPANY_2_GSTIN)


class TestTaxEngine(QETestCase):
	def test_intra_state_services_cgst_sgst(self):
		# test case 1
		entry = self.make_entry()
		line = entry.lines[0]
		self.assertEqual((line.cgst_amount, line.sgst_amount, line.igst_amount), (90, 90, 0))
		self.assertEqual(entry.place_of_supply, "33-Tamil Nadu")
		self.assertEqual(entry.total_eligible_itc, 180)
		self.assertEqual(entry.grand_total, 1180)
		self.assertEqual(
			line.item_tax_template,
			frappe.db.get_value(
				"Item Tax Template", {"company": entry.company, "gst_rate": 18, "gst_treatment": "Taxable"}
			),
		)

	def test_inter_state_goods_igst(self):
		# test case 2
		entry = self.make_entry(
			supplier_gstin=SUPPLIER_MH,
			lines=[dict(expense_category="Office Stationery", taxable_amount=2500)],
		)
		self.assertEqual(entry.lines[0].igst_amount, 450)
		self.assertEqual(entry.total_cgst + entry.total_sgst, 0)
		self.assertEqual(entry.place_of_supply, "33-Tamil Nadu")

	def test_place_of_supply_follows_selected_company_gstin(self):
		# test case 25: Karnataka supplier billed to the Karnataka GSTIN -> CGST + SGST
		entry = self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA)
		self.assertEqual(entry.place_of_supply, "29-Karnataka")
		self.assertEqual((entry.total_cgst, entry.total_sgst, entry.total_igst), (90, 90, 0))
		# the same Karnataka supplier billed to the Tamil Nadu GSTIN is inter-state
		entry2 = self.make_entry(company_gstin=TN_GSTIN, supplier_gstin=SUPPLIER_KA)
		self.assertEqual(entry2.total_igst, 180)

	def test_food_is_ineligible_17_5(self):
		# test case 4
		entry = self.make_entry(lines=[dict(expense_category="Food and Refreshments", taxable_amount=1000)])
		line = entry.lines[0]
		self.assertEqual(line.total_tax, 50)
		self.assertEqual(line.eligible_itc_amount, 0)
		self.assertEqual(line.ineligible_gst_amount, 50)
		self.assertEqual(line.ineligibility_reason, "Ineligible As Per Section 17(5)")

	def test_hotel_in_other_state_is_pos_restricted(self):
		# test case 5
		entry = self.make_entry(
			supplier_gstin=SUPPLIER_KA,
			lines=[
				dict(
					expense_category="Hotel Stay",
					taxable_amount=4000,
					gst_rate="18",
					location_of_service_state="Karnataka",
				)
			],
		)
		self.assertEqual(entry.place_of_supply, "29-Karnataka")
		line = entry.lines[0]
		self.assertEqual(
			(line.cgst_amount, line.sgst_amount), (360, 360)
		)  # hotel charges its own state's tax
		self.assertEqual(line.ineligibility_reason, "ITC restricted due to PoS rules")
		self.assertEqual(entry.total_eligible_itc, 0)

	def test_unregistered_shop_is_cost_only(self):
		# test case 6
		entry = self.make_entry(
			gst_treatment="Unregistered", supplier_gstin="", supplier_legal_name="Corner Shop"
		)
		self.assertEqual(entry.total_gst, 0)
		self.assertEqual(entry.grand_total, 1000)
		self.assertIn(entry.lines[0].eligibility_for_itc, ("", "Not Applicable"))

	def test_unregistered_with_gst_printed_goes_to_cost(self):
		entry = self.make_entry(
			gst_treatment="Unregistered",
			supplier_gstin="",
			supplier_legal_name="Corner Shop",
			lines=[
				dict(
					expense_category="Courier and Postage Charges",
					taxable_amount=1000,
					gst_as_per_bill_entered=1,
					gst_as_per_bill=180,
				)
			],
		)
		self.assertEqual(entry.total_gst, 0)
		self.assertEqual(entry.total_ineligible_gst, 180)
		self.assertEqual(entry.grand_total, 1180)

	def test_gta_reverse_charge(self):
		# test case 7
		entry = self.make_entry(
			lines=[dict(expense_category="Freight and Transport (GTA)", taxable_amount=10000)]
		)
		self.assertEqual(entry.is_reverse_charge, 1)
		self.assertEqual(entry.total_reverse_charge_tax, 500)
		self.assertEqual(entry.total_eligible_itc, 500)
		self.assertEqual(entry.grand_total, 10000)  # tax is paid by the company, not to the supplier

	def test_gta_unregistered_gets_self_invoice(self):
		entry = self.make_entry(
			gst_treatment="Unregistered",
			supplier_gstin="",
			supplier_legal_name="Local Lorry Owner",
			lines=[dict(expense_category="Freight and Transport (GTA)", taxable_amount=8000)],
		)
		self.assertEqual(entry.is_reverse_charge, 1)
		entry = self.post(entry)
		self.assertEqual(entry.approval_status, "Posted")
		self.assertTrue(entry.self_invoice_no.startswith(f"SI33{TN_GSTIN[12]}/2627/"))
		self.assertLessEqual(len(entry.self_invoice_no), 16)

	def test_overseas_software_is_import_of_services(self):
		# test case 8
		entry = self.make_entry(
			gst_treatment="Overseas",
			supplier_gstin="",
			supplier_legal_name="Cloud Software Inc",
			lines=[dict(expense_category="Software and Online Subscriptions", taxable_amount=5000)],
		)
		self.assertEqual(entry.is_reverse_charge, 1)
		self.assertEqual(entry.total_igst, 900)
		self.assertEqual(entry.total_eligible_itc, 900)
		self.assertEqual(entry.grand_total, 5000)

	def test_overseas_goods_blocked(self):
		entry = self.make_entry(
			gst_treatment="Overseas",
			supplier_gstin="",
			supplier_legal_name="Foreign Shop",
			lines=[dict(expense_category="Office Stationery", taxable_amount=100)],
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Bill of Entry"):
			self.submit(entry)

	def test_reverse_charge_is_the_users_tick(self):
		normal = self.make_entry(lines=[dict(expense_category="Security Services", taxable_amount=10000)])
		self.assertEqual((normal.is_reverse_charge, normal.grand_total), (0, 11800))
		ticked = self.make_entry(
			is_reverse_charge=1, lines=[dict(expense_category="Security Services", taxable_amount=10000)]
		)
		self.assertEqual(
			(ticked.is_reverse_charge, ticked.grand_total, ticked.total_reverse_charge_tax), (1, 10000, 1800)
		)

	def test_rent_from_unregistered_landlord_is_rcm(self):
		entry = self.make_entry(
			gst_treatment="Unregistered",
			supplier_gstin="",
			supplier_legal_name="Landlord",
			is_reverse_charge=1,  # the user ticks reverse charge for an unregistered landlord
			lines=[dict(expense_category="Rent – Commercial Property", taxable_amount=20000)],
		)
		self.assertEqual(entry.is_reverse_charge, 1)
		self.assertEqual(entry.total_reverse_charge_tax, 3600)

	def test_mixed_lines_totals_exact(self):
		# test case 9
		entry = self.make_entry(
			lines=[
				dict(expense_category="Courier and Postage Charges", taxable_amount=1234.56),
				dict(expense_category="Travel – Train and Bus", taxable_amount=789.10),
				dict(expense_category="Food and Refreshments", taxable_amount=333.33),
				dict(expense_category="Fuel – Petrol and Diesel", taxable_amount=2000),
			]
		)
		taxes = [round(l.cgst_amount + l.sgst_amount, 2) for l in entry.lines]
		self.assertEqual(taxes, [222.22, 39.46, 16.66, 0])
		self.assertEqual(entry.total_eligible_itc, flt(222.22 + 39.46, 2))
		self.assertEqual(entry.total_ineligible_gst, 16.66)
		payable = 1234.56 + 789.10 + 333.33 + 2000 + 222.22 + 39.46 + 16.66
		self.assertEqual(entry.grand_total, round_half_up(payable))
		self.assertEqual(entry.round_off, flt(entry.grand_total - payable, 2))
		self.assertEqual(entry.lines[3].gst_treatment, "Non-GST")
		self.assertEqual(
			entry.lines[3].item_tax_template,
			frappe.db.get_value("Item Tax Template", {"company": entry.company, "gst_treatment": "Non-GST"}),
		)

	def test_tax_difference_within_tolerance(self):
		# test case 16 (0.80)
		entry = self.make_entry(
			lines=[
				dict(
					expense_category="Courier and Postage Charges",
					taxable_amount=1000,
					gst_as_per_bill_entered=1,
					gst_as_per_bill=180.80,
				)
			]
		)
		self.assertEqual(entry.lines[0].tax_difference, 0.80)
		self.assertEqual(entry.lines[0].total_tax, 180)
		self.assertEqual(entry.grand_total, 1181)
		self.assertEqual(entry.round_off, 1)
		entry = self.submit(entry)
		self.assertIn("tax difference", entry.forced_approval_reasons)

	def test_tax_difference_above_tolerance_blocked(self):
		# test case 16 (5.00)
		with self.assertRaisesRegex(TaxError, "more than the allowed tolerance"):
			self.make_entry(
				lines=[
					dict(
						expense_category="Courier and Postage Charges",
						taxable_amount=1000,
						gst_as_per_bill_entered=1,
						gst_as_per_bill=185,
					)
				]
			)

	def test_mixed_place_of_supply_blocked(self):
		with self.assertRaisesRegex(TaxError, "different places of supply"):
			self.make_entry(
				lines=[
					dict(
						expense_category="Hotel Stay",
						taxable_amount=1000,
						gst_rate="18",
						location_of_service_state="Karnataka",
					),
					dict(expense_category="Courier and Postage Charges", taxable_amount=100),
				]
			)

	def test_reverse_charge_bill_must_not_show_gst(self):
		with self.assertRaisesRegex(TaxError, "must not charge GST"):
			self.make_entry(
				lines=[
					dict(
						expense_category="Freight and Transport (GTA)",
						taxable_amount=1000,
						gst_as_per_bill_entered=1,
						gst_as_per_bill=50,
					)
				]
			)

	def test_not_addressed_to_gstin_is_ineligible(self):
		# test case 28
		entry = self.make_entry(bill_addressed_to_company_gstin=0)
		self.assertEqual(entry.lines[0].ineligibility_reason, "Bill not addressed to this GSTIN")
		self.assertEqual(entry.total_eligible_itc, 0)
		entry = self.submit(entry)
		self.assertIn("not addressed", entry.forced_approval_reasons)
		self.assertTrue(
			any(step["role"] == "Accounts Manager" for step in frappe.parse_json(entry.approval_chain))
		)

	def test_rate_comes_from_item_tax_template(self):
		"""Change the template's rows and the calculation follows - nothing is hard coded."""
		template = frappe.get_doc(
			"Item Tax Template", {"company": "_QE Test Company", "gst_rate": 18, "gst_treatment": "Taxable"}
		)
		for row in template.taxes:
			if "Input Tax CGST" in row.tax_type and "RCM" not in row.tax_type:
				row.tax_rate = 9.5
		template.flags.ignore_validate = True
		template.db_update_all()
		frappe.local.request_cache.clear()
		entry = self.make_entry()
		self.assertEqual(entry.lines[0].cgst_amount, 95)

	def test_helpers(self):
		self.assertEqual(gst_financial_year_label("2026-03-31"), "2025-26")
		self.assertEqual(gst_financial_year_label("2026-04-01"), "2026-27")
		self.assertEqual(str(itc_time_limit("2025-06-01")), "2026-11-30")
		self.assertEqual(round_half_up(10.5), 11)
		self.assertEqual(round_half_up(10.49), 10)
