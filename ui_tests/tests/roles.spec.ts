import { expect, test } from "@playwright/test";

import { formReady, loginAs } from "../helpers";

test.describe("Quick Expense User", () => {
	test.use({ storageState: loginAs("user") });

	test("a new Journal Entry starts as a Quick Expense bill", async ({ page }) => {
		await page.goto("/app/journal-entry/new");
		await formReady(page);
		expect(await page.evaluate(() => (window as any).cur_frm.doc.voucher_type)).toBe("Quick Expense");
	});

	test("sees only Quick Expense Journal Entries", async ({ page }) => {
		await page.goto("/app/journal-entry");
		const others = await page.evaluate(() =>
			(window as any).frappe.db.get_list("Journal Entry", {
				filters: { voucher_type: ["!=", "Quick Expense"] },
				limit: 1,
			}),
		);
		expect(others).toEqual([]);
	});
});
