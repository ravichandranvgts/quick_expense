"""Shared constants. Labels are user-facing, so they stay in plain English."""

ROLE_USER = "Quick Expense User"
ROLE_APPROVER = "Quick Expense Approver"
ROLE_ACCOUNTS_MANAGER = "Accounts Manager"
APP_ROLES = (ROLE_USER, ROLE_APPROVER, ROLE_ACCOUNTS_MANAGER)

# Every entry posts as a Journal Entry of this voucher type
JE_VOUCHER_TYPE = "Quick Expense"

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

# GST on the bill may differ from the calculated GST by this much (rounding on the bill)
TAX_DIFFERENCE_TOLERANCE = 1.0

# system rows of a Quick Expense Journal Entry (qe_gst_component); everything else is an expense row
EXPENSE_ROW = "Expense"
SYSTEM_COMPONENTS = ("CGST", "SGST", "IGST", "Cess", "Reverse Charge Liability", "Round Off", "Payment")

# SAC codes (services) start with 99; HSN codes of goods do not
SERVICE_HSN_PREFIX = "99"
