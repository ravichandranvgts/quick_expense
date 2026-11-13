import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

export const AUTH_DIR = path.join(__dirname, ".auth");
export const DATA_FILE = path.join(AUTH_DIR, "data.json");

export type UiData = {
	company: string;
	company_gstin: string;
	supplier_gstin: string;
	expense_account: string;
	cash_account: string;
	gst_18: string;
	users: Record<string, string>;
	password: string;
};

export const data = (): UiData => JSON.parse(readFileSync(DATA_FILE, "utf8"));
export const loginAs = (role: "admin" | "user" | "approver") => path.join(AUTH_DIR, `${role}.json`);

/** Wait until Frappe has booted and the current form (cur_frm) is ready. */
export async function formReady(page: Page, doctype = "Journal Entry") {
	await page.waitForFunction(
		(dt) => (window as any).cur_frm?.doctype === dt && (window as any).cur_frm.doc,
		doctype,
	);
}

/**
 * Fill a Quick Expense bill through the form's own API (frm.set_value / add_child), so
 * the app's form script runs exactly as when a person types. The GST portal is not
 * reachable from tests, so the supplier name is typed in the way the form allows then.
 */
export async function fillBill(page: Page, taxable = 500) {
	const d = data();
	const invoiceNo = `UI-${Date.now().toString().slice(-8)}`;
	await page.evaluate(
		async ({ d, invoiceNo, taxable }) => {
			const w = window as any;
			const frm = w.cur_frm;
			await frm.set_value("company", d.company);
			await frm.set_value("company_gstin", d.company_gstin);
			await frm.set_value({ qe_gst_treatment: "Registered Regular", qe_supplier_gstin: d.supplier_gstin });
			// the portal lookup ends with either a filled name or "Supplier Details Not Fetched"
			for (let i = 0; i < 40 && !frm.doc.qe_supplier_legal_name && !w.$(".modal.show").length; i++) {
				await new Promise((r) => setTimeout(r, 250));
			}
			w.$(".modal.show .btn-modal-close").click();
			if (!frm.doc.qe_supplier_legal_name) await frm.set_value("qe_supplier_legal_name", "UI Test Supplier Pvt Ltd");
			await frm.set_value({
				qe_supplier_invoice_no: invoiceNo,
				qe_supplier_invoice_date: w.frappe.datetime.get_today(),
				qe_bill_addressed_to_company_gstin: 1,
			});
			frm.clear_table("accounts");
			frm.add_child("accounts", { account: d.expense_account, qe_taxable_amount: taxable, qe_item_tax_template: d.gst_18 });
			frm.add_child("accounts", { account: d.cash_account });
			frm.refresh_field("accounts");
		},
		{ d, invoiceNo, taxable },
	);
	return invoiceNo;
}

/** Save through the form and fail with Frappe's own message if it refuses. */
export async function saveForm(page: Page, action?: "Submit") {
	// when the server refuses a save, frm.save() shows the message and its promise never
	// settles, so stop waiting after 20 s and report the message instead
	const result = await page.evaluate(
		(action) =>
			Promise.race([
				(window as any).cur_frm.save(action).then(
					() => "ok",
					(e: any) => `failed: ${e?.message || ""}`,
				),
				new Promise((r) => setTimeout(() => r("no answer"), 20_000)),
			]),
		action,
	);
	// read any open message without waiting for one (a successful save shows none)
	const message = await page.evaluate(() => (window as any).$(".modal.show .modal-body").text().trim());
	expect(result, `save refused: ${message}`).toBe("ok");
	expect(await page.evaluate(() => (window as any).cur_frm.is_dirty())).toBe(false);
}

/** Submit a saved draft the way a person does: close notices, click Submit, confirm Yes. */
export async function submitByButton(page: Page) {
	await page.evaluate(() => (window as any).$(".modal.show .btn-modal-close").click());
	await expect(page.locator(".modal.show")).toHaveCount(0);
	await page.locator(".page-actions .primary-action").filter({ hasText: "Submit" }).click();
	await page.locator(".modal.show").getByRole("button", { name: "Yes", exact: true }).click();
}

/** Cancel and delete a bill the test created, so runs don't pile up records. */
export async function removeBill(page: Page, name: string) {
	const error = await page.evaluate(async (name) => {
		const f = (window as any).frappe;
		try {
			const doc = await f.db.get_doc("Journal Entry", name);
			if (doc.docstatus === 1)
				await f.xcall("quick_expense.api.cancel_with_reason", { name, reason: "UI test clean-up" });
			await f.xcall("frappe.client.delete", { doctype: "Journal Entry", name });
			return null;
		} catch (e: any) {
			return (window as any).$(".modal.show .modal-body").text().trim() || String(e?.message || e);
		}
	}, name);
	expect(error, `could not remove ${name}`).toBeNull();
}
