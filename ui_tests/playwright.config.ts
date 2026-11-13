import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// QE_SITE is a bench site with allow_tests on. Locally the tests start their own web
// server for it on port 8043, because a bench with serve_default_site answers every
// host name with the default site. In CI, set QE_BASE_URL to the running bench instead.
export const SITE = process.env.QE_SITE || "qetest.localhost";
const PORT = Number(process.env.QE_PORT || 8043);
export const BASE_URL = process.env.QE_BASE_URL || `http://${SITE}:${PORT}`;
// ui_tests/ sits in apps/quick_expense, so the bench is three levels up
export const BENCH_PATH = process.env.BENCH_PATH || path.resolve(__dirname, "../../..");

export default defineConfig({
	testDir: "./tests",
	globalSetup: "./global-setup.ts",
	timeout: 60_000,
	expect: { timeout: 15_000 },
	// tests share one site and its records, so run them one after another
	workers: 1,
	fullyParallel: false,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
	use: {
		baseURL: BASE_URL,
		trace: "retain-on-failure",
		screenshot: "only-on-failure",
	},
	projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 1000 } } }],
	webServer: process.env.QE_BASE_URL
		? undefined
		: {
				command: `bench --site ${SITE} serve --port ${PORT} --noreload`,
				cwd: BENCH_PATH,
				// Node can't resolve *.localhost; the server is pinned to SITE, so any host works
				url: `http://127.0.0.1:${PORT}/api/method/ping`,
				reuseExistingServer: true,
				timeout: 60_000,
			},
});
