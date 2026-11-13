import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { BASE_URL, BENCH_PATH, SITE } from "./playwright.config";
import { AUTH_DIR, DATA_FILE, type UiData } from "./helpers";

export default async function globalSetup() {
	// test companies, accounts, tax templates and users, created on the server
	const out = execSync(`bench --site ${SITE} execute quick_expense.tests.ui_setup.setup`, {
		cwd: BENCH_PATH,
		encoding: "utf8",
	});
	const data: UiData = JSON.parse(out.trim().split("\n").pop()!);
	mkdirSync(AUTH_DIR, { recursive: true });
	writeFileSync(DATA_FILE, JSON.stringify(data, null, 2));

	// one saved login per role. The login call runs inside the page because Node itself
	// cannot resolve *.localhost names, while the browser can.
	const logins: Record<string, [string, string]> = {
		admin: ["Administrator", process.env.QE_ADMIN_PASSWORD || "admin"],
		user: [data.users.user, data.password],
		approver: [data.users.approver, data.password],
	};
	const browser = await chromium.launch();
	for (const [key, [usr, pwd]] of Object.entries(logins)) {
		const context = await browser.newContext();
		const page = await context.newPage();
		await page.goto(`${BASE_URL}/login`);
		const status = await page.evaluate(
			async ([usr, pwd]) =>
				(
					await fetch("/api/method/login", {
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ usr, pwd }),
					})
				).status,
			[usr, pwd],
		);
		if (status !== 200) throw new Error(`Login failed for ${usr} (HTTP ${status})`);
		await context.storageState({ path: path.join(AUTH_DIR, `${key}.json`) });
		await context.close();
	}
	await browser.close();
}
