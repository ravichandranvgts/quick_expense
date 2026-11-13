# Contributing to Quick Expense

## Set up

You need a Frappe v16 bench with ERPNext and India Compliance (see the README's Installation section). Then:

```bash
cd apps/quick_expense
pre-commit install          # runs ruff, prettier and eslint on every commit
```

Use a separate site for tests; never run them on a site with real data:

```bash
bench new-site qetest.localhost --install-app erpnext --install-app india_compliance --install-app quick_expense
bench --site qetest.localhost set-config allow_tests true
```

## Server tests

```bash
bench --site qetest.localhost run-tests --app quick_expense
```

They build their own companies, GSTINs, accounts and users (`quick_expense/tests/utils.py`) and mock the GST portal, so they work on a brand-new site.

## Browser tests (Playwright)

```bash
cd apps/quick_expense/ui_tests
npm install
npx playwright install chromium     # first time only
npx playwright test                 # or: npx playwright test --headed
```

The tests start their own web server for `qetest.localhost` on port 8043 and create their data with `quick_expense.tests.ui_setup.setup` (which refuses to run unless the site has `allow_tests`). Set `QE_SITE` to use another test site. A failed run leaves a screenshot and a trace in `test-results/`; open one with `npx playwright show-trace <path>/trace.zip`.

To add a test, put a `*.spec.ts` file in `ui_tests/tests/`. `helpers.ts` has `fillBill`, `saveForm`, `submitByButton` and `removeBill`.

## Pull requests

Every pull request runs three checks on GitHub: **Server Tests**, **UI Tests** and **Linters**. They build a fresh bench with Frappe, ERPNext and India Compliance `version-16`, so a change that only works on your own site will show up here.

- Keep one change per pull request, with a test for it.
- If existing sites need data or settings changed, add a patch under `quick_expense/patches/` and list it in `patches.txt`.
- Note user-visible changes in `CHANGELOG.md`.
