# Changelog

## v1.3.0 – 07 Nov 2026

* The Journal Entry is the bill. Removed doctypes: Quick Expense Entry, Quick Expense Line, Quick Expense Category, Quick Expense Category Account, Quick Expense GSTIN Cache, Quick Expense Invoice Key.
* Supplier, bill, payment, totals, GST result and approval fields on Journal Entry; per row: taxable amount, GST rate (Item Tax Template), HSN / SAC, eligibility, location of service, GST as per bill. Saving builds the GST, reverse charge, round off and cash / bank rows.
* Approval matrix, escalation, retry and cancel-with-reason on the Journal Entry form and list.
* Duplicate bill key kept on the Journal Entry with a database lock; GSTIN details from India Compliance's own lookup.
* Quick Expense roles get Journal Entry rights limited to Quick Expense bills.
* Print formats on Journal Entry. Patch `v1_3.journal_entry_is_the_bill` migrates existing bills.

## v1.2.0 – 07 Nov 2026

* **Create Supplier** on Quick Expense Journal Entries: preview, then a Supplier master with GSTIN, PAN, GST category, constitution, legal / trade name and one Address per registered place of business from the GST portal (bill details as fallback). Existing suppliers are reused; all Journal Entries of the GSTIN are linked (new field *Supplier (onboarded)*).
* Supplier GSTIN autofill on the Quick Expense Journal Entry form.
* 149 automated tests.

## v1.1.0 – 07 Nov 2026

* Journal Entry is the only posting mode. The Purchase Invoice mode, its supplier / item creation and the posting-mode switch are removed.
* 2A / 2B reconciliation now happens in India Compliance's Purchase Reconciliation Tool: Quick Expense Journal Entries are a books doctype there (match, Accept, Pending, Ignore, Link, Unlink). Journal Entry gets India Compliance's standard Reconciliation Status field.
* The app's own reports are removed; the workspace links to India Compliance's GSTR-3B Report, GST Purchase Register, GSTR-3B Details and Purchase Reconciliation Tool.
* Patch `v1_1.journal_entry_only` cleans up old reports and custom fields and converts 2B links.
* 140 automated tests.

## v1.0.0 – 06 Nov 2026

First release, built from Development Prompt v1.2.

* Quick Expense Entry: one screen for GST expense bills, live tax calculation, GSTIN autofill through the India Compliance API with manual fallback.
* Two posting modes chosen in Settings with an Effective From date: paid Purchase Invoice (Mode A) or Journal Entry (Mode B). The mode is stamped on every entry.
* Multi-company, multi-GSTIN rules: place of supply per selected GSTIN, bill-addressed check, own-GSTIN / same-PAN refusal, GSTIN-wise duplicate key, lock dates and filed periods.
* Tax engine driven only by India Compliance Item Tax Templates: CGST / SGST / IGST / Cess, ITC eligibility (17(5), place of supply, not addressed, time limit), reverse charge (always, unregistered, non-body-corporate, import of services), tolerance and rounding.
* Mandatory amount-based approval matrix (1–3 levels, roles or users, per GSTIN), forced Accounts Manager approval triggers, escalation, posting rollback with retry.
* 30 standard expense categories created on install and for every new company.
* Mode B: GSTR-3B, GST Purchase Register and 2B reconciliation support through thin India Compliance wrappers, Input GST account protection, direct entry in Journal Entry, compatibility guard with self-test.
* Reports: Quick Expense Register, Approval Pending, 2B Pending Follow-up, Cash Limit Exceptions, 2B Reconciliation. Workspace with number cards.
* Print formats: Quick Expense Voucher, Self Invoice.
* Whitelisted API for a mobile front end.
* 153 automated tests.
