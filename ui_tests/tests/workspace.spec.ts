import { expect, test } from "@playwright/test";

import { formReady, loginAs } from "../helpers";

test.use({ storageState: loginAs("admin") });

test.beforeEach(async ({ page }) => {
	await page.goto("/app/quick-expense");
	await expect(page.getByText("New Quick Expense Bill").first()).toBeVisible();
});

test("number cards show numbers, never NaN", async ({ page }) => {
	const cards = page.locator(".number-widget-box");
	await expect(cards).toHaveCount(5);
	// each card fills its number after its own request; wait until every one has a value
	await expect(page.locator(".number-widget-box .number").filter({ hasText: /\d/ })).toHaveCount(5);
	for (const text of await cards.allTextContents()) {
		expect(text, "a number card").not.toContain("NaN");
	}
});

test("Quick Expense Bills opens the filtered list in the same tab", async ({ page, context }) => {
	await page.getByText("Quick Expense Bills", { exact: true }).first().click();
	await expect(page).toHaveURL(/\/journal-entry(\/view\/list)?\?voucher_type=Quick(\+|%20)Expense/);
	expect(context.pages()).toHaveLength(1);
});

test("New Quick Expense Bill opens a Quick Expense bill in the same tab", async ({ page, context }) => {
	await page.getByText("New Quick Expense Bill", { exact: true }).first().click();
	await expect(page).toHaveURL(/\/journal-entry\/new/);
	await formReady(page);
	expect(await page.evaluate(() => (window as any).cur_frm.doc.voucher_type)).toBe("Quick Expense");
	expect(context.pages()).toHaveLength(1);
});
