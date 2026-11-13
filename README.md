# Quick Expense

**GST expense bills in one screen, straight into GSTR-3B and 2B reconciliation.**

Courier, repairs, hotel, freight, stationery, fuel: small GST bills that don't deserve a Purchase Invoice and a Supplier master each. Quick Expense lets you enter them **directly in an ERPNext Journal Entry** of voucher type *Quick Expense*. You type the supplier GSTIN, the taxable amount and the GST rate. The app fetches the supplier from the GST portal, builds the CGST / SGST / IGST, reverse charge and round-off rows, and blocks duplicate bills. India Compliance's own reports read the result.

* No new documents to learn: the Journal Entry is the bill.
* No reports of its own: bills appear in India Compliance's **GSTR-3B Report**, **GST Purchase Register**, **GSTR-3B Details**, **Purchase Reconciliation Tool** (2A / 2B) and **IMS**.
* Eligible ITC, 17(5) blocked credit, place-of-supply restrictions, reverse charge and import of services are all classified the way India Compliance classifies a Purchase Invoice.

Version 1.5.0 · Published by VGTS · MIT License

[![Server Tests](https://github.com/ravichandranvgts/quick_expense/actions/workflows/server-tests.yml/badge.svg)](https://github.com/ravichandranvgts/quick_expense/actions/workflows/server-tests.yml) [![UI Tests](https://github.com/ravichandranvgts/quick_expense/actions/workflows/ui-tests.yml/badge.svg)](https://github.com/ravichandranvgts/quick_expense/actions/workflows/ui-tests.yml) [![Linters](https://github.com/ravichandranvgts/quick_expense/actions/workflows/linters.yml/badge.svg)](https://github.com/ravichandranvgts/quick_expense/actions/workflows/linters.yml)

## Requirements

| App | Version |
| --- | --- |
| Frappe | v16 |
| ERPNext | v16 |
| India Compliance | v16 (verified with 16.9 – 16.11) |

The company must be an Indian company with at least one GSTIN set up in India Compliance.

## Installation

**Frappe Cloud:** install *Quick Expense* from the Marketplace on a v16 bench that has ERPNext and India Compliance.

**Self-hosted:**

```bash
bench get-app erpnext --branch version-16
bench get-app india_compliance https://github.com/resilient-tech/india-compliance --branch version-16
bench get-app https://github.com/ravichandranvgts/quick_expense
bench --site <site> install-app erpnext india_compliance quick_expense
bench --site <site> migrate
```

Install and every migrate run `setup_service.setup_all()`. It is idempotent and never overwrites your edits:

1. Creates the roles *Quick Expense User*, *Quick Expense Approver* and *Finance Manager*. *Accounts Manager* comes from ERPNext.
2. Adds the custom fields on Journal Entry and Journal Entry Account, including the standard **Reconciliation Status** field India Compliance uses, plus the voucher type *Quick Expense*.
3. Gives the Quick Expense roles Journal Entry permissions, limited to Quick Expense bills by the app's permission hooks.
4. Creates the workspace *Quick Expense*: five number cards (this month's spend, eligible ITC, ineligible GST, drafts, bills pending 2B) and shortcuts to the bill list, a new bill, and India Compliance's reports.

## Entering a bill

Open **Quick Expense › New Quick Expense Bill** (or *Journal Entry › New* with Voucher Type *Quick Expense*) and fill in:

* **Supplier and Bill:**
  - GST Treatment and **Supplier GSTIN**. The legal name, trade name, address, pincode, state and GSTIN status are filled from the GST portal and become read-only. They stay editable for unregistered suppliers, or when the portal can't be reached.
  - Supplier invoice number and date.
  - *Bill is addressed to the selected Company GSTIN*.
  - *Is Reverse Charge* (always on for overseas suppliers).
* **Accounting Entries table:**
  - one row per expense: Expense Account, **Taxable Amount**, **GST Rate (Item Tax Template)**, HSN / SAC and **ITC Eligibility** (17(5) for food, cab hire, passenger vehicles, gifts and staff welfare). Optionally *Location of Service State* (a hotel or property in another state) and *GST as per Bill*;
  - **one Cash or Bank row** for the payment. Its amount is filled by the system. For bank, put the cheque / UTR in Reference No and Date.

**Save** adds the CGST / SGST / IGST rows, the reverse charge rows and the round-off row, sets the payment row's amount, numbers the rows in order and fills the totals. The calculated GST breakdown, place of supply and ITC classification sit in the collapsed *GST Details* section.

**Submit** posts the bill, as for any Journal Entry. **Cancel with Reason** cancels it; a reason is required once the bill is reconciled with GSTR-2B.

## Roles and approval

| Role | Can do |
| --- | --- |
| Quick Expense User | Create and save Quick Expense bills. Sees only Quick Expense Journal Entries. Cannot submit. |
| Quick Expense Approver | Everything above, plus Submit and Cancel. |
| Accounts User / Accounts Manager | Full Journal Entry access, as in ERPNext. |

The app has no approval engine of its own. For an approval chain (for example by amount), create a standard **Frappe Workflow** on Journal Entry with a condition such as `doc.voucher_type == "Quick Expense"`. Workflow states, notifications and the approval history then work as for any other document.

## What a posted bill looks like

    Dr Expense account           taxable value (+ ineligible GST: 17(5), place of supply, not addressed, time limit)
    Dr Input CGST / SGST / IGST  eligible GST only
    Cr Input Tax … RCM           reverse charge tax (GTA, advocate, security, rent from unregistered, import of services)
    Dr / Cr Round Off            bill-vs-calculated difference and rupee rounding
    Cr Cash / Bank               grand total paid

Each expense row also carries that line's taxable value, CGST / SGST / IGST / Cess, rate, HSN / SAC and eligibility. That's how India Compliance's reports can show ineligible GST, which never reaches an Input GST account.

## 2A / 2B reconciliation (India Compliance's Purchase Reconciliation Tool)

Quick Expense Journal Entries are a fourth **books doctype** in the tool, next to Purchase Invoice, Bill of Entry and ISD Recipient Invoice.

* **Download and Reconcile:** India Compliance's own matching rules link the downloaded 2B record to the Journal Entry. The Journal Entry's *Reconciliation Status* becomes *Match Found*. A Journal Entry with no 2B record shows as *Only in Books*.
* **Actions:** Accept, Pending, Ignore, Unlink, and Link (where *Journal Entry* is available in the Document Type list) work as for Purchase Invoices.
* **Status on the bill:** the tool writes the result straight onto the Journal Entry's *Reconciliation Status*. Accounts Managers get a monthly email listing bills still unmatched after one return period.
* **Cancellation:** cancelling an entry releases its 2B record. A reconciled entry needs a cancellation reason.

How it's built: `overrides/purchase_reconciliation.py` adds the Journal Entry books query, written to the same interface as India Compliance's `PurchaseInvoice` class. It then wraps seven India Compliance methods:

* `BaseReconciliation.get_unmatched_purchase_or_bill_of_entry`
* `ReconciledData.get_all_purchase_invoice_and_bill_of_entry`
* `PurchaseReconciliationTool.apply_action`
* `PurchaseReconciliationTool.unlink_documents`
* `PurchaseReconciliationTool.link_documents`
* `PurchaseReconciliationTool.get_link_options`
* `PurchaseReconciliationTool.reconcile_and_generate_data`

Each wrapper calls the original first. One small script, `public/js/override_banner.js`, adds *Journal Entry* to the tool's Link dialog. No India Compliance code is copied.

## GSTR-3B and the Purchase Register

`overrides/ic_reports.py` wraps `GSTR3BInwardInvoices.get_data` and `GSTR3BOutwardInvoices.get_inward_invoices`. Each Journal Entry expense row is added in the exact shape of a Purchase Invoice item row, so India Compliance's own rules classify it:

| Bill | GSTR-3B |
| --- | --- |
| Eligible ITC | 4A(5) |
| Reverse charge | 4A(3), and the liability in 3.1(d) |
| Import of services | 4A(2) |
| 17(5) and "Ineligible Others" | 4A, reversed in 4B(1) |
| Place of supply restriction | 4D(2) |
| Exempt / nil / non-GST | Table 5 |

Rows follow the *ITC Claim Period* or *Posting Date* filter of the report, are kept per Company GSTIN, and appear once.

### After an India Compliance update

The report and reconciliation overrides wrap India Compliance methods by name. They are applied once per process; if India Compliance renames something, the override is skipped and an Error Log titled *could not apply India Compliance report overrides* is written instead of breaking the report. After every India Compliance update, run `bench migrate`, check the Error Log, and run the tests below on a copy of the site.

## Supplier onboarding from a Journal Entry

On a Quick Expense Journal Entry, **Create Supplier** opens a preview first. It shows the supplier name, legal name, trade name, GSTIN, PAN, GST category, constitution of business, GSTIN status and every registered place of business. Confirming creates the ERPNext **Supplier** and one **Address** per place of business: the principal place becomes the primary Billing address, and additional places become Office addresses, each with line 1 / line 2, city, state, pincode and GSTIN.

* **Data source:** the GST portal response through the India Compliance API (India Compliance archives responses, so repeat lookups use no credits). When the API is unavailable, the details on the bill are used instead.
* **Supplier name:** the legal name. For proprietorships and HUFs the trade name is used, as India Compliance does.
* **No duplicates:**
  - A supplier that already has the GSTIN is reused; only its missing addresses are added.
  - A different supplier with the same name gets the state code appended.
  - Unregistered suppliers are found again by name.
* **Linking:** the supplier is recorded on the Journal Entry (*Supplier (onboarded)*) and on every other Quick Expense Journal Entry of the same GSTIN. Postings are not changed.
* **Permission:** the button needs the permission to create Suppliers. In ERPNext that is *Purchase Master Manager* or *Purchase Manager*, not *Accounts Manager*.
* **GSTIN autofill:** typing a Supplier GSTIN on a Quick Expense Journal Entry fills the legal name and state from the portal.

The code is `services/supplier_service.py` and `public/js/journal_entry.js`.

## Settings

*Quick Expense Settings* has four switches:

| Setting | Default | What it does |
| --- | --- | --- |
| Enable Quick Expense | on | When off, Quick Expense bills can no longer be saved; other Journal Entries are not affected. |
| Round Grand Total to Nearest Rupee | on | Adds a round-off row so the amount paid is a whole rupee. |
| Use India Compliance GSTIN API | on | Fetches supplier details from the GST portal through India Compliance (needs an India Compliance API account). |
| Monthly 2B Pending Follow-up Email | on | Emails Accounts Managers a list of bills still not matched in GSTR-2B after one return period. |

Company defaults (primary GST address, cost center, round-off account) come from the Company.

## Multi-company and multi-GSTIN

* **GSTIN and place of supply:** each bill has one Company and one Company GSTIN. Place of supply, and CGST + SGST vs IGST, are decided against the selected GSTIN's state.
* **Bill addressed:** if *Bill is addressed to the selected Company GSTIN* is not ticked, the GST is treated as ineligible.
* **Own GSTINs refused:** supplier GSTINs of any company in the system, or with the company's own PAN, are refused.
* **Duplicate key:** Company GSTIN + Supplier GSTIN + normalised invoice no + GST financial year, stored on the Journal Entry. A submitted bill holds the key; a database lock on the key serialises simultaneous submissions. It is also checked against Purchase Invoices.
* **Per GSTIN:** reports and reconciliation work per Company GSTIN.

## Tests

Every push and pull request runs three checks on GitHub Actions. Each builds a fresh bench with Frappe, ERPNext and India Compliance `version-16`:

| Check | What it runs |
| --- | --- |
| Server Tests | 129 Python tests: GSTR-3B table values, the Purchase Register, and full reconciliation runs through India Compliance's Purchase Reconciliation Tool (exact match, Accept, Unlink, manual Link, Only in Books / Ignore, Mismatch, cancellation after reconciliation). The GST portal API is mocked. |
| UI Tests | Playwright in Chromium: the workspace cards and shortcuts, entering and saving a GST bill, the duplicate-bill refusal, and what a Quick Expense User sees. |
| Linters | pre-commit (ruff, prettier, eslint) and Frappe's semgrep rules. |

Locally, on a test site (never one with real data):

```bash
bench --site <test-site> set-config allow_tests true
bench --site <test-site> run-tests --app quick_expense

cd apps/quick_expense/ui_tests && npm install && npx playwright test
```

See [CONTRIBUTING](.github/CONTRIBUTING.md) for details.

## Guides

* [User guide](docs/user_guide.md)
* [Approver guide](docs/approver_guide.md)
* [Changelog](CHANGELOG.md)

## Support

Open an issue at https://github.com/ravichandranvgts/quick_expense/issues.

## License

MIT © 2026 VGTS
