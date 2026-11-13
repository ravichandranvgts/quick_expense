# Quick Expense – User Guide

**For:** staff who enter expense bills.

## Enter a bill (2 minutes)

1. **Quick Expense › New Quick Expense Bill**. This opens a Journal Entry with Voucher Type *Quick Expense*.
2. **Company GSTIN**: the GSTIN printed on the bill as the buyer. Tick **Bill is addressed to the selected Company GSTIN** only if it really is; otherwise the GST is not claimed.
3. **GST Treatment**: *Registered Regular* for most bills; *Unregistered* for shops without a GSTIN; *Overseas* for foreign online services.
4. **Supplier GSTIN**: type it. Name, address and state fill in from the GST portal and are locked. If the portal can't be reached, the name stays open for you to type.
5. **Supplier Invoice No and Date**: exactly as printed. Tick **Is Reverse Charge** for GTA freight, advocates and similar.
6. **Rows**: one per expense, with Expense Account, **Taxable Amount** (before GST), **GST Rate**, HSN / SAC, and **ITC Eligibility** if the GST can't be claimed (food, cab hire … = 17(5)). Keep **one Cash or Bank row** for the payment; its amount is filled on save. For bank, fill Reference No and Date.
7. Attach the bill and **Save**. The GST, reverse charge and round-off rows are added for you, and the totals show at the top.
8. **Submit** to post the bill. If you only have the *Quick Expense User* role, leave it saved; a *Quick Expense Approver* checks and submits it.

## After posting

* **Cancel with Reason** cancels a posted bill. A reason is required when the bill is already reconciled with GSTR-2B.
* The same supplier invoice can't be entered twice for the same Company GSTIN in a financial year. The message links to the bill that already has it.
* **GSTR-2B:** open India Compliance's **Purchase Reconciliation Tool**, pick the Company GSTIN and period, click **Reconcile**. Quick Expense bills show as *Journal Entry* rows; use *Accept*, *Pending*, *Ignore*, *Link* or *Unlink* as usual. The bill's Reconciliation Status updates by itself.

## Onboard a supplier from a bill

Open the Quick Expense Journal Entry and click **Create Supplier** (shown only when the supplier is not yet in the Supplier master). Check the details and addresses shown (taken from the GST portal), then confirm. A supplier with the same GSTIN is reused instead of being created twice. You need the *Purchase Master Manager* or *Purchase Manager* role.
