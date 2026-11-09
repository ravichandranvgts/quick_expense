"""Shared test fixtures.

Two companies:
* _QE Test Company   - GSTINs in Tamil Nadu (33) and Karnataka (29)
* _QE Second Company - one GSTIN in Tamil Nadu, used for "GSTIN of another company"

The India Compliance GSTIN API is replaced by FAKE_PORTAL so tests never call
the GST portal; set ``API_DOWN`` to simulate an outage.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt, random_string, today

from quick_expense.services.gstin_service import checksum_char

COMPANY = "_QE Test Company"
ABBR = "QET"
COMPANY_2 = "_QE Second Company"
ABBR_2 = "QES"


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
	base = f"{state_code}{pan}{entity}Z"
	return base + checksum_char(base)


COMPANY_PAN = "AAACQ1234C"
TN_GSTIN = make_gstin("33", COMPANY_PAN)
KA_GSTIN = make_gstin("29", COMPANY_PAN)
COMPANY_2_GSTIN = make_gstin("33", "AABCR5678D")

SUPPLIER_TN = make_gstin("33", "AAFCS1111A")  # company (body corporate)
SUPPLIER_TN_2 = make_gstin("33", "AAFCS5555E")
SUPPLIER_KA = make_gstin("29", "AAFCS2222B")
SUPPLIER_MH = make_gstin("27", "AAFCS4444D")
SUPPLIER_INDIVIDUAL = make_gstin("33", "ABCPS3333C")  # proprietor (non-body-corporate)
SUPPLIER_COMPOSITION = make_gstin("33", "AAFCS6666F")
SUPPLIER_CANCELLED = make_gstin("33", "AAFCS7777G")
SUPPLIER_SAME_PAN = make_gstin("27", COMPANY_PAN)
#: Active, with an address, and **no legal name** on the portal. Reported from
#: UAT on 06AACCG0527D1Z8: state, address, pincode and status all filled and
#: the supplier name blank — and blank is the one value the form would not
#: then let anybody type, because it had locked the field.
SUPPLIER_NO_LEGAL_NAME = make_gstin("06", "AACCG0527D")

FAKE_PORTAL = {
	SUPPLIER_TN: dict(
		lgnm="Chennai Courier Services Private Limited",
		tradeNam="Fast Courier",
		sts="Active",
		dty="Regular",
		ctb="Private Limited Company",
		adadr=[
			{
				"addr": {
					"bno": "3",
					"st": "Avinashi Road",
					"dst": "Coimbatore",
					"stcd": "Tamil Nadu",
					"pncd": "641018",
				}
			}
		],
		pradr={
			"addr": {
				"bno": "12",
				"st": "Anna Salai",
				"dst": "Chennai",
				"stcd": "Tamil Nadu",
				"pncd": "600002",
			}
		},
	),
	SUPPLIER_TN_2: dict(
		lgnm="Madras Stationers Private Limited",
		tradeNam="",
		sts="Active",
		dty="Regular",
		ctb="Private Limited Company",
		pradr={
			"addr": {"bno": "4", "st": "Mount Road", "dst": "Chennai", "stcd": "Tamil Nadu", "pncd": "600002"}
		},
	),
	SUPPLIER_KA: dict(
		lgnm="Bengaluru Hotels Private Limited",
		tradeNam="Garden Inn",
		sts="Active",
		dty="Regular",
		ctb="Private Limited Company",
		pradr={
			"addr": {"bno": "1", "st": "MG Road", "dst": "Bengaluru", "stcd": "Karnataka", "pncd": "560001"}
		},
	),
	SUPPLIER_MH: dict(
		lgnm="Mumbai Traders Private Limited",
		tradeNam="",
		sts="Active",
		dty="Regular",
		ctb="Private Limited Company",
		pradr={
			"addr": {
				"bno": "9",
				"st": "Marine Drive",
				"dst": "Mumbai",
				"stcd": "Maharashtra",
				"pncd": "400001",
			}
		},
	),
	SUPPLIER_NO_LEGAL_NAME: dict(
		lgnm="",
		tradeNam="Gurgaon Facilities",
		sts="Active",
		dty="Regular",
		ctb="Private Limited Company",
		pradr={
			"addr": {
				"bno": "Tower-B",
				"st": "Unitech Signature Tower, Sector-15",
				"dst": "Gurugram",
				"stcd": "Haryana",
				"pncd": "122002",
			}
		},
	),
	SUPPLIER_INDIVIDUAL: dict(
		lgnm="Selvam Security Services",
		tradeNam="Selvam Security",
		sts="Active",
		dty="Regular",
		ctb="Proprietorship",
		pradr={
			"addr": {
				"bno": "7",
				"st": "Gandhi Road",
				"dst": "Chennai",
				"stcd": "Tamil Nadu",
				"pncd": "600017",
			}
		},
	),
	SUPPLIER_COMPOSITION: dict(
		lgnm="Small Repairs Private Limited",
		tradeNam="",
		sts="Active",
		dty="Composition",
		ctb="Private Limited Company",
		pradr={
			"addr": {"bno": "3", "st": "Lake Road", "dst": "Chennai", "stcd": "Tamil Nadu", "pncd": "600017"}
		},
	),
	SUPPLIER_CANCELLED: dict(
		lgnm="Closed Business Private Limited",
		tradeNam="",
		sts="Cancelled",
		dty="Regular",
		ctb="Private Limited Company",
		pradr={
			"addr": {"bno": "5", "st": "Old Road", "dst": "Chennai", "stcd": "Tamil Nadu", "pncd": "600017"}
		},
	),
}

API_DOWN = {"value": False}


def fake_call_api(gstin):
	"""Same shape as india_compliance.gst_india.utils.gstin_info._get_gstin_info."""
	from india_compliance.gst_india.utils.gstin_info import (
		GST_CATEGORIES,
		TRADE_NAME_BUSINESS_TYPES,
		_get_address,
	)

	if API_DOWN["value"]:
		raise ConnectionError("GST portal timed out")
	if gstin not in FAKE_PORTAL:
		return frappe._dict(gstin=gstin, status="Invalid")
	raw = frappe._dict(FAKE_PORTAL[gstin])
	addresses = [
		_get_address(frappe.parse_json(frappe.as_json(place)))
		for place in [raw.pradr, *(raw.get("adadr") or [])]
	]
	business_name = raw.tradeNam if raw.ctb in TRADE_NAME_BUSINESS_TYPES and raw.tradeNam else raw.lgnm
	return frappe._dict(
		gstin=gstin,
		business_name=business_name,
		legal_name=raw.lgnm,
		trade_name=raw.tradeNam or "",
		gst_category=GST_CATEGORIES.get(raw.dty, ""),
		status=raw.sts,
		all_addresses=addresses,
		permanent_address=addresses[0],
	)


GOODS, SERVICES = "Goods", "Services"
PRESETS = {
	"Courier and Postage Charges": dict(account="QE Courier", rate=18, hsn="9968"),
	"Food and Refreshments": dict(
		account="QE Food", rate=5, hsn="9963", eligibility="Ineligible under Section 17(5)"
	),
	"Hotel Stay": dict(account="QE Hotel", rate=18, hsn="9963"),
	"Freight and Transport (GTA)": dict(account="QE Freight", rate=5, hsn="9965", rcm=1, transporter=1),
	"Loading and Unloading Charges": dict(account="QE Freight", rate=18, hsn="9967", transporter=1),
	"Office Stationery": dict(account="QE Stationery", rate=18, hsn="4820"),
	"Travel – Train and Bus": dict(account="QE Travel", rate=5, hsn="9964"),
	"Fuel – Petrol and Diesel": dict(account="QE Fuel", treatment="Non-GST", hsn="2710"),
	"Electricity Charges": dict(account="QE Electricity", treatment="Exempted", hsn="2716"),
	"Software and Online Subscriptions": dict(account="QE Software", rate=18, hsn="9973"),
	"Rent – Commercial Property": dict(account="QE Rent", rate=18, hsn="9972"),
	"Security Services": dict(account="QE Security", rate=18, hsn="9985"),
	"Professional and Consultancy Fees": dict(account="QE Professional Fees", rate=18, hsn="9983"),
}


def template(rate=None, treatment="Taxable", company=COMPANY):
	filters = {"company": company, "gst_treatment": treatment, "disabled": 0}
	if treatment == "Taxable":
		filters["gst_rate"] = flt(rate)
	return frappe.db.get_value("Item Tax Template", filters, "name", order_by="creation asc")


def expense_row(company, expense_category="Courier and Postage Charges", taxable_amount=1000, **line):
	preset = PRESETS[expense_category]
	abbr = ABBR if company == COMPANY else ABBR_2
	rate = line.pop("gst_rate", None)
	row = {
		"account": line.pop("expense_account", None) or f"{preset['account']} - {abbr}",
		"qe_taxable_amount": taxable_amount,
		"debit_in_account_currency": taxable_amount,
		"qe_item_tax_template": template(
			rate if rate not in (None, "") else preset.get("rate"),
			preset.get("treatment", "Taxable"),
			company,
		),
		"qe_hsn_sac": preset["hsn"],
		"qe_eligibility": line.pop("eligibility_for_itc", None) or preset.get("eligibility") or "",
		"qe_eligibility_reason": line.pop("eligibility_change_reason", None),
		"qe_location_of_service_state": line.pop("location_of_service_state", None),
		"user_remark": line.pop("description", None) or expense_category,
	}
	if line.pop("gst_as_per_bill_entered", None):
		row["qe_gst_as_per_bill"] = line.pop("gst_as_per_bill", 0)
	line.pop("gst_as_per_bill", None)
	row.update(line)
	return row


USERS = {
	"user": ("qe.user@example.com", ["Quick Expense User"]),
	"user2": ("qe.user2@example.com", ["Quick Expense User"]),
	"approver": ("qe.approver@example.com", ["Quick Expense Approver"]),
	"approver2": ("qe.approver2@example.com", ["Quick Expense Approver"]),
	"am": ("qe.am@example.com", ["Accounts Manager", "Accounts User"]),
	"fm": ("qe.fm@example.com", ["Finance Manager"]),
}


def user(key):
	return USERS[key][0]


def cash(company=COMPANY):
	return frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name")


def bank(company=COMPANY):
	return f"QE Test Bank - {ABBR if company == COMPANY else ABBR_2}"


# ----------------------------------------------------------------------------
# one-time masters (before_tests)


def ensure_test_masters():
	from quick_expense.services import setup_service

	for name, abbr, gstins in (
		(COMPANY, ABBR, (TN_GSTIN, KA_GSTIN)),
		(COMPANY_2, ABBR_2, (COMPANY_2_GSTIN,)),
	):
		if not frappe.db.exists("Company", name):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": name,
					"abbr": abbr,
					"country": "India",
					"default_currency": "INR",
					"chart_of_accounts": "Standard",
					"pan": COMPANY_PAN if name == COMPANY else "AABCR5678D",
					"gst_category": "Registered Regular",
				}
			).insert()
		for gstin in gstins:
			ensure_company_address(name, gstin)
		ensure_bank_account(name, abbr)

	for company, abbr in ((COMPANY, ABBR), (COMPANY_2, ABBR_2)):
		parent = frappe.db.get_value(
			"Account", {"company": company, "account_name": "Indirect Expenses", "is_group": 1}, "name"
		)
		for account_name in sorted({preset["account"] for preset in PRESETS.values()}):
			if not frappe.db.exists("Account", f"{account_name} - {abbr}"):
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": account_name,
						"company": company,
						"parent_account": parent,
						"root_type": "Expense",
						"account_type": "Expense Account",
					}
				).insert()

	for key, (email, roles) in USERS.items():
		if not frappe.db.exists("User", email):
			doc = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": key.title(),
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			)
			doc.insert(ignore_permissions=True)
		doc = frappe.get_doc("User", email)
		doc.add_roles(*roles)

	frappe.db.set_single_value("GST Settings", "enable_overseas_transactions", 1)
	setup_service.setup_all()
	settings = frappe.get_single("Quick Expense Settings")
	for company in (COMPANY, COMPANY_2):
		if not any(r.company == company and r.account == bank(company) for r in settings.allowed_accounts):
			settings.append("allowed_accounts", {"company": company, "account": bank(company)})
		for row in settings.company_settings:
			if row.company == company:
				row.default_company_gstin = TN_GSTIN if company == COMPANY else COMPANY_2_GSTIN
	settings.flags.from_setup = True
	settings.save()
	frappe.db.commit()  # nosemgrep: test masters must survive per-test rollbacks


def ensure_company_address(company, gstin):
	if frappe.db.exists("Address", {"gstin": gstin}):
		return
	from quick_expense.services.gstin_service import state_from_gstin

	state = state_from_gstin(gstin)
	pincode = {"Tamil Nadu": "600001", "Karnataka": "560001"}[state]
	frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": f"{company} {state}",
			"address_type": "Billing",
			"address_line1": "1 Test Street",
			"city": "Chennai" if state == "Tamil Nadu" else "Bengaluru",
			"state": state,
			"country": "India",
			"pincode": pincode,
			"gstin": gstin,
			"gst_category": "Registered Regular",
			"is_your_company_address": 1,
			"is_primary_address": 1 if state == "Tamil Nadu" else 0,
			"links": [{"link_doctype": "Company", "link_name": company}],
		}
	).insert()


def ensure_bank_account(company, abbr):
	name = f"QE Test Bank - {abbr}"
	if frappe.db.exists("Account", name):
		return
	parent = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Bank", "is_group": 1}, "name"
	) or frappe.db.get_value("Account", {"company": company, "account_name": "Bank Accounts"}, "name")
	frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": "QE Test Bank",
			"company": company,
			"parent_account": parent,
			"account_type": "Bank",
			"is_group": 0,
		}
	).insert()


# ----------------------------------------------------------------------------
# base test case


BASE_SETTINGS = dict(
	enable_quick_expense=1,
	allow_direct_journal_entry=0,
	use_gstin_api=1,
	cancelled_gstin_action="Block",
	gstin_cache_days=30,
	require_approval_manual_supplier_name=1,
	attachment_mandatory_above=10000000,
	maximum_amount_per_entry=50000,
	maximum_backdated_days=30,
	tax_difference_tolerance=1,
	cash_limit_per_person_per_day=10000,
	cash_limit_transporters=35000,
	cash_limit_action="Warn",
	itc_time_limit_warning_days=30,
	users_see_only_own_entries=0,
	approval_required=1,
	escalation_after_days=2,
	allow_self_approval=0,
	restrict_input_gst_accounts=0,
	include_in_gstr3b=1,
	include_in_purchase_register=1,
	overrides_disabled=0,
	round_grand_total=1,
)


def set_settings(**values):
	for key, value in values.items():
		frappe.db.set_single_value("Quick Expense Settings", key, value)
	frappe.clear_document_cache("Quick Expense Settings", "Quick Expense Settings")


def clear_caches():
	frappe.clear_document_cache("Quick Expense Settings", "Quick Expense Settings")
	frappe.clear_document_cache("GST Settings", "GST Settings")
	frappe.cache.delete_value("user_permissions")
	if hasattr(frappe.local, "request_cache"):
		frappe.local.request_cache.clear()


class QETestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_test_masters()

	def setUp(self):
		frappe.set_user("Administrator")
		API_DOWN["value"] = False
		self._api_patch = patch("quick_expense.services.gstin_service._call_api", side_effect=fake_call_api)
		self.api_mock = self._api_patch.start()
		set_settings(**BASE_SETTINGS)
		frappe.local.qe_gstin_details = {}
		clear_caches()

	def tearDown(self):
		self._api_patch.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		clear_caches()

	# ------------------------------------------------------------------ helpers

	def make_entry(self, lines=None, insert=True, **values):
		"""A Quick Expense Journal Entry, returned as a Bill (bill field names)."""
		from quick_expense.services.bill import HEADER_MAP, Bill

		company = values.pop("company", COMPANY)
		lines = lines or [dict(expense_category="Courier and Postage Charges", taxable_amount=1000)]
		je = frappe.new_doc("Journal Entry")
		je.update(
			{
				"voucher_type": "Quick Expense",
				"company": company,
				"company_gstin": TN_GSTIN if company == COMPANY else COMPANY_2_GSTIN,
				"posting_date": today(),
				"qe_gst_treatment": "Registered Regular",
				"qe_supplier_gstin": SUPPLIER_TN,
				"qe_supplier_legal_name": "Chennai Courier Services Private Limited",
				"qe_supplier_invoice_no": f"INV-{random_string(6).upper()}",
				"qe_supplier_invoice_date": today(),
				"qe_bill_addressed_to_company_gstin": 1,
			}
		)
		paid_through = values.pop("paid_through_account", cash(company))
		if any(
			PRESETS[line.get("expense_category", "Courier and Postage Charges")].get("rcm") for line in lines
		):
			je.qe_is_reverse_charge = 1
		if any(
			PRESETS[line.get("expense_category", "Courier and Postage Charges")].get("transporter")
			for line in lines
		):
			je.qe_is_transporter = 1
		for key, value in values.items():
			je.set(HEADER_MAP.get(key, key) if key != "linked_journal_entry" else key, value)
		for line in lines:
			je.append("accounts", expense_row(company, **line))
		if paid_through:
			je.append(
				"accounts", {"account": paid_through}
			)  # the cash / bank row; the system sets its amount
		if insert:
			je.insert()
		return Bill(je)

	def bill(self, name):
		from quick_expense.services.bill import get_bill

		return get_bill(name)

	def submit(self, doc, as_user="user"):
		with as_(user(as_user) if as_user in USERS else as_user):
			self.bill(doc.name).submit_for_approval()
		return self.bill(doc.name)

	def approve_level(self, doc, as_user, comment=""):
		with as_(user(as_user) if as_user in USERS else as_user):
			self.bill(doc.name).approve(comment)
		return self.bill(doc.name)

	def approve_all(self, doc):
		"""Approve every level with a user who holds the level's role."""
		doc = self.bill(doc.name)
		role_users = {"Quick Expense Approver": "approver", "Accounts Manager": "am", "Finance Manager": "fm"}
		import json

		for step in json.loads(doc.approval_chain or "[]")[cint(doc.approval_level) - 1 :]:
			doc = self.approve_level(doc, step.get("user") or role_users[step["role"]])
		return doc

	def post(self, doc, as_user="user"):
		doc = self.submit(doc, as_user)
		if doc.approval_status == "Pending Approval":
			doc = self.approve_all(doc)
		return doc


@contextmanager
def as_(email):
	previous = frappe.session.user
	frappe.set_user(email)
	try:
		yield
	finally:
		frappe.set_user(previous)
