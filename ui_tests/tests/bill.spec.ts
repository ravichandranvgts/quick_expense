import { expect, test } from "@playwright/test";

import { fillBill, formReady, loginAs, removeBill, saveForm, submitByButton } from "../helpers";

test.use({ storageState: loginAs("admin") });

let created: string | undefined;

test.afterEach(async ({ page }) => {
	if (created) await removeBill(page, created);
	created = undefined;
});

test("saving a GST bill builds the tax rows, numbered in order", async ({ page }) => {
	await page.goto("/app/journal-entry/new?voucher_type=Quick%20Expense");
	await formReady(page);
	await fillBill(page, 500);
	await saveForm(page);

	const doc = await page.evaluate(() => {
		const d = (window as any).cur_frm.doc;
		return {
			name: d.name,
			total: d.qe_grand_total,
			rows: d.accounts.map((r: any) => ({ idx: r.idx, type: r.qe_gst_component, credit: r.credit_in_account_currency })),
		};
	});
	created = doc.name;

	// 500 at 18% inside Tamil Nadu: CGST 45 + SGST 45, paid 590
	expect(doc.rows.map((r: any) => r.type)).toEqual(["Expense", "CGST", "SGST", "Payment"]);
	expect(doc.rows.map((r: any) => r.idx)).toEqual([1, 2, 3, 4]);
	expect(doc.total).toBe(590);
	expect(doc.rows[3].credit).toBe(590);

	// the grid on screen shows the same numbering (it once showed 1, 2, 3, 3)
	const shown = page.locator(".frappe-control[data-fieldname=accounts] .grid-body .grid-row .row-index span");
	await expect(shown).toHaveText(["1", "2", "3", "4"]);
});

test("a submitted bill can't be entered a second time", async ({ page }) => {
	await page.goto("/app/journal-entry/new?voucher_type=Quick%20Expense");
	await formReady(page);
	const invoiceNo = await fillBill(page, 200);
	await saveForm(page, "Submit");
	created = await page.evaluate(() => (window as any).cur_frm.doc.name);

	// same supplier, invoice number and Company GSTIN again
	await page.goto("/app/journal-entry/new?voucher_type=Quick%20Expense");
	await formReady(page);
	await fillBill(page, 200);
	await page.evaluate((no) => (window as any).cur_frm.set_value("qe_supplier_invoice_no", no), invoiceNo);
	// a duplicate may sit as a draft; it is refused when it would claim the GST, on Submit
	await saveForm(page);
	const second = await page.evaluate(() => (window as any).cur_frm.doc.name);
	await submitByButton(page);
	await expect(page.locator(".modal.show").filter({ hasText: "Duplicate Bill" })).toContainText("already entered");
	expect(await page.evaluate(() => (window as any).cur_frm.doc.docstatus)).toBe(0);
	await removeBill(page, second);
});
