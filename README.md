# Quick Expense

GST expense bills (courier, repairs, hotel, freight, stationery, fuel …) entered **directly in an ERPNext Journal Entry** of voucher type **Quick Expense**, with India Compliance. The app has no documents of its own. It adds GST fields to the Journal Entry and builds the GST rows; an amount-based approval matrix posts the Journal Entry, and India Compliance's reports read it.

Version 1.3.0 · Built from Development Prompt v1.2 (06 Nov 2026)

## Entering a bill

Go to *Journal Entry › New*, set **Voucher Type = Quick Expense** (or use the workspace shortcut *New Quick Expense Bill*), and fill in:

* **Supplier and Bill:**
  - GST Treatment and **Supplier GSTIN**. On save the legal name, trade name, address, pincode, state and GSTIN status are filled from the GST portal and become **read-only**. They stay editable only for unregistered suppliers, or when the portal can't be reached (then an Accounts Manager approves).
  - Supplier invoice number and date.
  - *Bill is addressed to the selected Company GSTIN*.
  - *Is Reverse Charge* (always on for overseas suppliers).
  - *Payment to a Transporter*.
* **Accounting Entries table:**
  - one row per expense, with the Expense Account, **Taxable Amount**, **GST Rate (Item Tax Template)**, HSN / SAC and **Eligibility for ITC** (empty = eligible; 17(5) for food, cab hire, passenger vehicles, gifts and staff welfare). Optionally add *Location of Service State* (a hotel or property in another state) and *GST as per Bill*;
  - **one Cash or Bank row** for the payment. Its amount is filled by the system, and the user's default cash account is added automatically. For bank, the cheque / UTR goes in Reference No and Date.

The calculated GST breakdown, place of supply and ITC classification sit in the collapsed *GST Details* section. The approval details appear only once the bill is in approval.

**Save** adds the CGST / SGST / IGST rows, the reverse charge rows and the round-off row, sets the cash / bank row's amount, and fills the totals. **Submit for Approval** sends the bill through the matrix; the Journal Entry is submitted automatically after the last approval. The standard Submit button is blocked for Quick Expense bills.

Users with only the *Quick Expense User* or *Quick Expense Approver* role see and create only Quick Expense Journal Entries.

Quick Expense adds **no reports of its own**. Its Journal Entries appear in India Compliance's own:

* **GSTR-3B Report**: Tables 4, 5 and 3.1(d)
* **GST Purchase Register** and **GSTR-3B Details**
* **Purchase Reconciliation Tool**: 2A / 2B matching, with Accept / Pending / Ignore / Link / Unlink

Version 1.3.0 · Built from Development Prompt v1.2 (06 Nov 2026)

## Verified versions

| App | Version | Branch |
| --- | --- | --- |
| Frappe | 16.55.2 | version-16 |
| ERPNext | 16.54.0 | version-16 |
| India Compliance | 16.11.0 | version-16 |
| Python / Node | 3.14 / 24 | |

## Installation

```bash
bench get-app erpnext --branch version-16
bench get-app india_compliance https://github.com/resilient-tech/india-compliance --branch version-16
bench get-app quick_expense <repository-url>
bench --site <site> install-app erpnext india_compliance quick_expense
bench --site <site> migrate
```

Install and every migrate run `setup_service.setup_all()`. It is idempotent and never overwrites your edits:

1. Creates the roles *Quick Expense User*, *Quick Expense Approver* and *Finance Manager*. *Accounts Manager* comes from ERPNext.
2. Adds custom fields on Journal Entry and Journal Entry Account, including the standard **Reconciliation Status** field India Compliance uses, plus the voucher type *Quick Expense*.
3. Gives the Quick Expense roles Journal Entry permissions (limited to Quick Expense bills by the app's permission hooks).
4. For every Indian company: a Company Settings row, the default cash / bank account and three default approval rules.
5. Creates the workspace *Quick Expense*: five number cards on Quick Expense Journal Entries, plus shortcuts to India Compliance's Purchase Reconciliation Tool, GSTR-3B Report, GST Purchase Register and GSTR-3B Details.

A company created later gets step 4 automatically. **Run Setup Check** in Settings lists anything missing, per company and GSTIN.

**Upgrading:**
* from 1.0, the patch `v1_1/journal_entry_only` removes the old app reports and the Purchase Invoice custom fields;
* from 1.2, the patch `v1_3/journal_entry_is_the_bill` moves each posted bill's details, totals and approval history onto its Journal Entry, then deletes *Quick Expense Entry*, its lines, *Quick Expense Category*, *Quick Expense GSTIN Cache* and *Quick Expense Invoice Key*. Posted Journal Entries are not changed in the books. Drafts that had no Journal Entry yet are dropped.

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

### Compatibility guard

On every migrate, and daily, the installed India Compliance version is compared with the *Verified India Compliance Version*. If they differ, a read-only self-test runs. It checks that every class, method and field used still exists, that Journal Entry rows and Purchase Invoice rows are classified identically, and that all queries run for every company and GSTIN.

If the self-test fails, the overrides switch off. A red banner then shows on the GSTR-3B Report, Purchase Reconciliation Tool and Settings, and Accounts Managers get an email. **Run Compatibility Self-test** in Settings re-checks it.

**After every India Compliance update:**

1. Run `bench migrate`.
2. Check *Settings › India Compliance Reports*.
3. Run the automated tests on a copy of the site.
4. Click **Run Compatibility Self-test**.

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

## Settings walkthrough

| Tab | What to set |
| --- | --- |
| General | Naming series, rounding, direct entry in Journal Entry, Company Settings rows, **Allowed Cash and Bank Accounts**, User Defaults |
| GSTIN | India Compliance GSTIN API (primary source), cancelled / suspended GSTIN action, cache days, approval when the name is typed manually |
| Controls | Attachments, maximum amount per entry (₹50,000), back-dated days, tax tolerance, cash limits (Income Tax 40A(3)), ITC time-limit warning, GST Lock Date per Company GSTIN, own-entries-only |
| Approval | Approval Required, escalation days, self approval, the Approval Matrix |
| India Compliance Reports | Input GST account protection, include in GSTR-3B / Purchase Register, compatibility guard |
| Notifications | Approver, creator and monthly 2B follow-up emails |

## Multi-company and multi-GSTIN

* **GSTIN and place of supply:** each entry has one Company and one Company GSTIN. Place of supply, and CGST + SGST vs IGST, are decided against the selected GSTIN's state.
* **Bill addressed:** *Bill is addressed to the selected Company GSTIN* must be ticked. Otherwise the GST is ineligible and an Accounts Manager must approve.
* **Own GSTINs refused:** supplier GSTINs of any company in the system, or with the company's own PAN, are refused.
* **Duplicate key:** Company GSTIN + Supplier GSTIN + normalised invoice no + GST financial year, stored on the Journal Entry. A bill holds the key while it's in approval or posted. A database lock on the key serialises simultaneous submissions. It is also checked against standard Purchase Invoices.
* **Per GSTIN:** lock dates, filed-period checks, reports and reconciliation all work per GSTIN.

## Approval matrix

| Grand total | Levels |
| --- | --- |
| up to 5,000 | Quick Expense Approver |
| 5,000.01 – 25,000 | Quick Expense Approver → Accounts Manager |
| 25,000.01 – 50,000 | Quick Expense Approver → Accounts Manager → Finance Manager |
| above 50,000 | not allowed – use a standard Purchase Invoice |

An Accounts Manager level is added when any of these applies:

* eligibility is changed;
* reverse charge or the place of supply is overridden;
* the supplier name is typed manually;
* the bill is not addressed to the selected GSTIN;
* the bill is back-dated;
* the cash limit is exceeded;
* the tax tolerance is used;
* the category is Miscellaneous;
* the ITC claim period is moved;
* the GSTIN is cancelled and the action is set to Warn.

Other approval rules:

* The approval chain is frozen on the entry when it is submitted.
* Approvers may change only Remarks and Cost Center. An amount change sends the entry back to Level 1.
* Creators cannot approve their own entries.
* If posting fails, the entry shows *Approved – Posting Failed* with a Retry button for the Accounts Manager.

## Tests

```bash
bench --site <test-site> set-config allow_tests true
bench --site <test-site> run-tests --app quick_expense
```

149 tests (every edge case now runs on Journal Entries). They include GSTR-3B table values, the Purchase Register, and full reconciliation runs through India Compliance's Purchase Reconciliation Tool: exact match, Accept, Unlink, manual Link, Only in Books / Ignore, Mismatch, and cancellation after reconciliation. The GST portal API is mocked.

## Demo site (quickexpense.localhost)

| User | Password | Role |
| --- | --- | --- |
| Administrator | admin | everything |
| clerk@vgts.example | Demo@12345 | Quick Expense User |
| approver@vgts.example | Demo@12345 | Quick Expense Approver |
| accounts@vgts.example | Demo@12345 | Accounts Manager |
| finance@vgts.example | Demo@12345 | Finance Manager |

VGTS Traders Private Limited has GSTINs in Tamil Nadu (33AAKCS2468M1ZW) and Karnataka (29AAKCS2468M1ZL).

The demo includes sample GSTR-2B records: one exact match (already accepted), one amount mismatch, and one invoice that is in 2B but not in the books. Open *Purchase Reconciliation Tool*, choose GSTIN 33AAKCS2468M1ZW and this month, and click *Reconcile*.

## Guides

* [User guide](docs/user_guide.md)
* [Approver guide](docs/approver_guide.md)
* [Changelog](CHANGELOG.md)

## License

MIT
