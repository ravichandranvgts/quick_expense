"""Read helpers over the single "Quick Expense Settings" doctype."""

import frappe
from frappe.utils import getdate

from quick_expense.constants import ROLE_ACCOUNTS_MANAGER


def get_settings():
	return frappe.get_cached_doc("Quick Expense Settings")


def get_company_setting(company: str, settings=None):
	settings = settings or get_settings()
	for row in settings.company_settings:
		if row.company == company:
			return row
	return frappe._dict(company=company)


def get_allowed_accounts(company: str, settings=None) -> dict[str, frappe._dict]:
	settings = settings or get_settings()
	return {row.account: row for row in settings.allowed_accounts if row.company == company}


def get_user_default(user: str, company: str | None = None, settings=None):
	settings = settings or get_settings()
	rows = [row for row in settings.user_defaults if row.user == user]
	if company:
		rows = [row for row in rows if row.company == company]
	return rows[0] if rows else None


def get_lock_date(company: str, company_gstin: str, settings=None):
	settings = settings or get_settings()
	dates = [
		getdate(row.lock_date)
		for row in settings.gst_lock_dates
		if row.company == company and row.company_gstin == company_gstin and row.lock_date
	]
	return max(dates) if dates else None


def is_accounts_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or ROLE_ACCOUNTS_MANAGER in frappe.get_roles(user)
