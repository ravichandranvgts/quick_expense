# Quick Expense – Privacy Policy

*Last updated: 13 November 2026*

Quick Expense is an app published by VGTS that runs inside your own ERPNext site. This policy explains what happens to your data when you use it.

## Your data stays on your site

Everything you enter in Quick Expense (bills, supplier details, GSTINs, amounts, attachments) is stored in your own ERPNext database, on your own server or your Frappe Cloud site. **VGTS does not receive, collect, store or see any of it.** The app sends no usage statistics, analytics or telemetry to VGTS or anyone else.

## The only outside connection: GSTIN lookups

When you enter a supplier GSTIN, Quick Expense can fetch the supplier's legal name, address and registration status from the GST portal. It does this **through India Compliance's own GST API, using your India Compliance account**, in the same way India Compliance does for Purchase Invoices. The GSTIN is the only thing sent.

* This lookup is governed by India Compliance's terms and privacy policy, not by VGTS.
* You can switch it off in *Quick Expense Settings › Use India Compliance GSTIN API*. Supplier details are then typed in by hand.

## Emails

If *Monthly 2B Pending Follow-up Email* is on, your site sends your Accounts Managers a monthly list of bills not yet matched in GSTR-2B. It is sent by your own site's email account, to your own users.

## Hosting

If your site runs on Frappe Cloud, Frappe Technologies hosts your data under its own terms and privacy policy. If you host ERPNext yourself, your data is wherever you run it.

## Your rights and deletion

Because the data is in your own site, you control it: you can export, change or delete it with ERPNext's normal tools, and uninstalling the app does not send anything anywhere.

## Changes and contact

Changes to this policy are published in this file, with the date at the top. Questions: open an issue at https://github.com/ravichandranvgts/quick_expense/issues.
