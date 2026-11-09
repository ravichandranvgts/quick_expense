"""Shared constants. Labels are user-facing, so they stay in plain English."""

ROLE_USER = "Quick Expense User"
ROLE_APPROVER = "Quick Expense Approver"
ROLE_ACCOUNTS_MANAGER = "Accounts Manager"
ROLE_FINANCE_MANAGER = "Finance Manager"
APP_ROLES = (ROLE_USER, ROLE_APPROVER, ROLE_ACCOUNTS_MANAGER, ROLE_FINANCE_MANAGER)

# Every entry posts as a Journal Entry of this voucher type
JE_VOUCHER_TYPE = "Quick Expense"

# approval statuses
DRAFT = "Draft"
PENDING = "Pending Approval"
APPROVED = "Approved"
POSTED = "Posted"
REJECTED = "Rejected"
POSTING_FAILED = "Approved - Posting Failed"
CANCELLED = "Cancelled"
EDITABLE_STATUSES = (DRAFT, REJECTED)
# statuses whose cash counts toward the per-person cash limit
LIVE_STATUSES = (PENDING, APPROVED, POSTED, POSTING_FAILED)

# GST treatment of the supplier (header)
REGISTERED = "Registered Regular"
COMPOSITION = "Registered Composition"
UNREGISTERED = "Unregistered"
SEZ = "SEZ"
OVERSEAS = "Overseas"
NON_GST = "Non-GST or Out of Scope"
GSTIN_REQUIRED_TREATMENTS = (REGISTERED, COMPOSITION, SEZ)
INVOICE_REQUIRED_TREATMENTS = (REGISTERED, COMPOSITION, SEZ)

# India Compliance gst_category for each treatment
IC_GST_CATEGORY = {
	REGISTERED: "Registered Regular",
	COMPOSITION: "Registered Composition",
	UNREGISTERED: "Unregistered",
	SEZ: "SEZ",
	OVERSEAS: "Overseas",
	NON_GST: "Unregistered",
}

# line eligibility
ELIGIBLE = "All Other ITC"
INELIGIBLE_17_5 = "Ineligible under Section 17(5)"
INELIGIBLE_OTHERS = "Ineligible Others"
NOT_APPLICABLE = "Not Applicable"

# reasons recorded on ineligible lines
REASON_17_5 = "Ineligible As Per Section 17(5)"
REASON_POS = "ITC restricted due to PoS rules"
REASON_NOT_ADDRESSED = "Bill not addressed to this GSTIN"
REASON_TIME_LIMIT = "Time limit expired"
REASON_UNREGISTERED = "Unregistered supplier without reverse charge"
REASON_OTHERS = "Others"

# line gst treatment (India Compliance item tax template gst_treatment values)
TAXABLE = "Taxable"
NIL_RATED = "Nil-Rated"
EXEMPTED = "Exempted"
NON_GST_ITEM = "Non-GST"
ZERO_TAX_TREATMENTS = (NIL_RATED, EXEMPTED, NON_GST_ITEM)

# GSTIN detail sources that fill the supplier name and address
GSTIN_FILLED_SOURCES = ("API", "Supplier Master")
GSTIN_STATUS_BLOCKING = ("Cancelled", "Suspended", "Inactive")

# Journal Entry fields a user may change while the bill is in approval
APPROVER_EDITABLE_FIELDS = ("user_remark",)
APPROVER_EDITABLE_ROW_FIELDS = ("cost_center", "project", "user_remark")
ACCOUNTS_MANAGER_EDITABLE_ROW_FIELDS = (
	*APPROVER_EDITABLE_ROW_FIELDS,
	"qe_eligibility",
	"qe_eligibility_reason",
)
# changing any of these sends the bill back to approval level 1
AMOUNT_ROW_FIELDS = (
	"account",
	"qe_taxable_amount",
	"qe_item_tax_template",
	"qe_gst_as_per_bill",
	"qe_location_of_service_state",
)

# system rows of a Quick Expense Journal Entry (qe_gst_component); everything else is an expense row
EXPENSE_ROW = "Expense"
SYSTEM_COMPONENTS = ("CGST", "SGST", "IGST", "Cess", "Reverse Charge Liability", "Round Off", "Payment")

# Default approval matrix (prompt section 13.3)
DEFAULT_APPROVAL_RULES = [
	dict(from_amount=0, to_amount=5000, levels=1, roles=(ROLE_APPROVER,)),
	dict(from_amount=5000.01, to_amount=25000, levels=2, roles=(ROLE_APPROVER, ROLE_ACCOUNTS_MANAGER)),
	dict(
		from_amount=25000.01,
		to_amount=50000,
		levels=3,
		roles=(ROLE_APPROVER, ROLE_ACCOUNTS_MANAGER, ROLE_FINANCE_MANAGER),
	),
]

# SAC codes (services) start with 99; HSN codes of goods do not
SERVICE_HSN_PREFIX = "99"
