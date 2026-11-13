# Quick Expense – Approver Guide

**For:** whoever checks and posts bills: *Quick Expense Approver*, Accounts Managers and Finance Managers.

## Check and post a bill

1. Open **Quick Expense › Quick Expense Bills** and filter on *Draft*. The workspace card *Quick Expense Drafts* shows how many are waiting.
2. Check the attachment against the supplier name and GSTIN, invoice number and date, amounts and GST rate.
3. Open *GST Details* to see the place of supply, ITC classification and any reason for ineligibility.
4. Click **Submit**. The bill posts to the books and appears in India Compliance's reports.

To send a bill back, add a comment and leave it in Draft, or ask the creator to correct it.

## Approval chains

Quick Expense has no approval engine of its own. If you need several levels (for example by amount), your System Manager can create a standard **Frappe Workflow** on Journal Entry with the condition `doc.voucher_type == "Quick Expense"`. You then see **Approve** / **Reject** actions from the workflow instead of Submit.

## Cancelling

**Cancel with Reason** on a posted bill. A reason is required once the bill has been reconciled with GSTR-2B; cancelling releases its 2B record so it can be matched again.
