"""Create Supplier from a Quick Expense Journal Entry, with every detail the GSTIN gives."""

import frappe

from quick_expense.services import supplier_service
from quick_expense.tests.utils import (
	API_DOWN,
	SUPPLIER_INDIVIDUAL,
	SUPPLIER_TN,
	QETestCase,
	as_,
	user,
)


def supplier_addresses(supplier):
	names = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Supplier", "link_name": supplier, "parenttype": "Address"},
		pluck="parent",
	)
	return frappe.get_all(
		"Address",
		filters={"name": ("in", names)},
		fields=["*"],
		order_by="is_primary_address desc, creation asc",
	)


class TestSupplierOnboarding(QETestCase):
	def posted_je(self, **values):
		entry = self.post(self.make_entry(**values))
		self.assertEqual(entry.docstatus, 1)
		return entry.linked_journal_entry

	def test_supplier_with_all_gstin_details(self):
		je = self.posted_je()
		preview = supplier_service.preview_supplier(je)
		self.assertEqual((preview.source, preview.existing_supplier), ("GST portal", None))
		self.assertEqual(len(preview.addresses), 2)

		result = supplier_service.create_supplier(je)
		self.assertEqual(result.is_new, 1)
		supplier = frappe.get_doc("Supplier", result.supplier)
		self.assertEqual(supplier.supplier_name, "Chennai Courier Services Private Limited")
		self.assertEqual(
			(supplier.gstin, supplier.pan, supplier.gst_category),
			(SUPPLIER_TN, "AAFCS1111A", "Registered Regular"),
		)
		self.assertEqual((supplier.supplier_type, supplier.country), ("Company", "India"))
		self.assertIn("Trade name: Fast Courier", supplier.supplier_details)
		self.assertIn("Constitution (from PAN): Body Corporate", supplier.supplier_details)

		addresses = supplier_addresses(supplier.name)
		self.assertEqual(len(addresses), 2)  # principal + additional place of business
		principal = addresses[0]
		self.assertEqual(
			(
				principal.address_type,
				principal.is_primary_address,
				principal.city,
				principal.state,
				principal.pincode,
				principal.gstin,
			),
			("Billing", 1, "Chennai", "Tamil Nadu", "600002", SUPPLIER_TN),
		)
		self.assertIn("Anna Salai", principal.address_line1 + (principal.address_line2 or ""))
		self.assertEqual(
			(addresses[1].city, addresses[1].pincode, addresses[1].address_type),
			("Coimbatore", "641018", "Office"),
		)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "qe_supplier"), supplier.name)

	def test_second_bill_reuses_supplier_and_links_all_entries(self):
		first = self.posted_je()
		second = self.posted_je()
		result = supplier_service.create_supplier(first)
		self.assertEqual(frappe.db.get_value("Journal Entry", second, "qe_supplier"), result.supplier)
		again = supplier_service.create_supplier(second)
		self.assertEqual((again.supplier, again.is_new, again.addresses), (result.supplier, 0, []))
		self.assertEqual(frappe.db.count("Supplier", {"gstin": SUPPLIER_TN}), 1)
		self.assertEqual(len(supplier_addresses(result.supplier)), 2)

	def test_existing_supplier_gets_missing_addresses(self):
		existing = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Old Courier Record",
				"gstin": SUPPLIER_TN,
				"gst_category": "Registered Regular",
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
			}
		).insert()
		je = self.posted_je()
		self.assertEqual(supplier_service.preview_supplier(je).existing_supplier, existing.name)
		result = supplier_service.create_supplier(je)
		self.assertEqual((result.supplier, result.is_new, len(result.addresses)), (existing.name, 0, 2))

	def test_proprietor_uses_trade_name(self):
		je = self.posted_je(
			supplier_gstin=SUPPLIER_INDIVIDUAL,
			supplier_legal_name="x",
			lines=[dict(expense_category="Courier and Postage Charges", taxable_amount=500)],
		)
		supplier = frappe.get_doc("Supplier", supplier_service.create_supplier(je).supplier)
		self.assertEqual((supplier.supplier_name, supplier.supplier_type), ("Selvam Security", "Individual"))
		self.assertIn("Legal name: Selvam Security Services", supplier.supplier_details)

	def test_name_clash_gets_state_suffix(self):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Chennai Courier Services Private Limited",
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
			}
		).insert()
		je = self.posted_je()
		self.assertEqual(
			supplier_service.create_supplier(je).supplier, "Chennai Courier Services Private Limited - 33"
		)

	def test_api_down_uses_bill_details(self):
		API_DOWN["value"] = True
		je = self.posted_je(
			supplier_legal_name="Typed Courier Name",
			supplier_address="7 Mount Road, Guindy, Chennai",
			supplier_pincode="600032",
		)
		preview = supplier_service.preview_supplier(je)
		self.assertEqual(preview.source, "Bill")
		result = supplier_service.create_supplier(je)
		supplier = frappe.get_doc("Supplier", result.supplier)
		self.assertEqual(
			(supplier.supplier_name, supplier.gstin, supplier.pan),
			("Typed Courier Name", SUPPLIER_TN, "AAFCS1111A"),
		)
		[address] = supplier_addresses(supplier.name)
		self.assertEqual(
			(address.city, address.state, address.pincode, address.gstin),
			("Chennai", "Tamil Nadu", "600032", SUPPLIER_TN),
		)

	def test_unregistered_supplier(self):
		je = self.posted_je(
			gst_treatment="Unregistered", supplier_gstin="", supplier_legal_name="Murugan Tea Stall"
		)
		supplier = frappe.get_doc("Supplier", supplier_service.create_supplier(je).supplier)
		self.assertEqual(
			(supplier.supplier_name, supplier.gst_category, supplier.gstin or ""),
			("Murugan Tea Stall", "Unregistered", ""),
		)
		self.assertEqual(supplier_service.create_supplier(je).is_new, 0)  # found again by name and category

	def test_needs_supplier_create_permission(self):
		je = self.posted_je()
		with as_(user("user")):
			with self.assertRaises(frappe.PermissionError):
				supplier_service.create_supplier(je)

	def test_only_quick_expense_journal_entries(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"company": "_QE Test Company",
				"posting_date": frappe.utils.today(),
				"accounts": [
					{"account": "Courier and Postage Charges - QET", "debit_in_account_currency": 10},
					{
						"account": frappe.db.get_value(
							"Account", {"company": "_QE Test Company", "account_type": "Cash", "is_group": 0}
						),
						"credit_in_account_currency": 10,
					},
				],
			}
		).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "type Quick Expense"):
			supplier_service.create_supplier(je.name)


class TestCreateSupplierButton(QETestCase):
	"""The form offers Create Supplier only when the bill's supplier is not in the Supplier master."""

	def onload_flag(self, journal_entry):
		je = frappe.get_doc("Journal Entry", journal_entry)
		je.run_method("onload")
		return je.get_onload().get("qe_supplier_exists")

	def test_offered_until_supplier_exists(self):
		entry = self.post(self.make_entry())
		self.assertIs(self.onload_flag(entry.linked_journal_entry), False)  # button shown
		supplier_service.create_supplier(entry.linked_journal_entry)
		self.assertIs(self.onload_flag(entry.linked_journal_entry), True)  # button hidden

	def test_hidden_when_gstin_already_in_master(self):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Already Here",
				"gstin": SUPPLIER_TN,
				"gst_category": "Registered Regular",
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
			}
		).insert()
		entry = self.post(self.make_entry())
		self.assertIs(self.onload_flag(entry.linked_journal_entry), True)

	def test_unregistered_matched_by_name(self):
		entry = self.post(
			self.make_entry(
				gst_treatment="Unregistered", supplier_gstin="", supplier_legal_name="Corner Shop"
			)
		)
		self.assertIs(self.onload_flag(entry.linked_journal_entry), False)
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Corner Shop",
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
			}
		).insert()
		self.assertIs(self.onload_flag(entry.linked_journal_entry), True)

	def test_not_offered_on_other_journal_entries(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"company": "_QE Test Company",
				"posting_date": frappe.utils.today(),
				"accounts": [
					{"account": "Courier and Postage Charges - QET", "debit_in_account_currency": 10},
					{
						"account": frappe.db.get_value(
							"Account", {"company": "_QE Test Company", "account_type": "Cash", "is_group": 0}
						),
						"credit_in_account_currency": 10,
					},
				],
			}
		).insert()
		self.assertIsNone(self.onload_flag(je.name))
