# Copyright (c) 2026, VGTS and contributors
# For license information, please see license.txt

from itertools import pairwise

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class QuickExpenseSettings(Document):
	def validate(self):
		before = self.get_doc_before_save()
		self.validate_naming_series()
		if not self.flags.from_setup:
			self.validate_restricted_changes(before)
		self.validate_company_rows()
		self.validate_allowed_accounts()
		self.validate_user_defaults()
		self.validate_lock_dates()
		self.validate_approval_rules()
		for field in (
			"maximum_amount_per_entry",
			"tax_difference_tolerance",
			"cash_limit_per_person_per_day",
			"cash_limit_transporters",
		):
			if flt(self.get(field)) < 0:
				frappe.throw(_("{0} cannot be negative.").format(self.meta.get_label(field)))

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)

	def validate_naming_series(self):
		for series in [self.default_naming_series] + [
			row.naming_series for row in self.company_settings if row.naming_series
		]:
			if series and ".#" not in series:
				frappe.throw(
					_("Naming series {0} must end with a number pattern such as .#####").format(series)
				)

	def validate_restricted_changes(self, before):
		if not before:
			return
		from quick_expense.services.settings_service import is_accounts_manager

		if cint(before.approval_required) and not cint(self.approval_required) and not is_accounts_manager():
			frappe.throw(_("Only an Accounts Manager can switch off Approval Required."))

	def validate_company_rows(self):
		seen = set()
		from quick_expense.services.gstin_service import get_company_gstins

		for row in self.company_settings:
			if row.company in seen:
				frappe.throw(_("Company {0} appears twice in Company Settings.").format(row.company))
			seen.add(row.company)
			if row.default_company_gstin and row.default_company_gstin not in {
				g.gstin for g in get_company_gstins(row.company)
			}:
				frappe.throw(
					_("Row {0}: {1} is not a GSTIN of {2}.").format(
						row.idx, row.default_company_gstin, row.company
					)
				)

	def validate_allowed_accounts(self):
		seen = set()
		for row in self.allowed_accounts:
			account = frappe.get_cached_value(
				"Account",
				row.account,
				("company", "account_type", "is_group", "account_currency"),
				as_dict=True,
			)
			if (row.company, row.account) in seen:
				frappe.throw(_("Allowed account {0} is listed twice.").format(row.account))
			seen.add((row.company, row.account))
			if account.company != row.company:
				frappe.throw(
					_("Allowed account {0} does not belong to {1}.").format(row.account, row.company)
				)
			if account.account_type not in ("Cash", "Bank") or account.is_group:
				frappe.throw(
					_("Allowed account {0} must be a non-group Cash or Bank account.").format(row.account)
				)
			if (account.account_currency or "INR") != "INR":
				frappe.throw(
					_("Allowed account {0} is in {1}; only INR accounts are allowed.").format(
						row.account, account.account_currency
					)
				)
			row.account_type = account.account_type

	def validate_user_defaults(self):
		from quick_expense.services.gstin_service import get_company_gstins

		for row in self.user_defaults:
			if row.company_gstin and row.company_gstin not in {
				g.gstin for g in get_company_gstins(row.company)
			}:
				frappe.throw(
					_("User Defaults row {0}: {1} is not a GSTIN of {2}.").format(
						row.idx, row.company_gstin, row.company
					)
				)
			if row.paid_through_account and not any(
				a.account == row.paid_through_account and a.company == row.company
				for a in self.allowed_accounts
			):
				frappe.throw(
					_("User Defaults row {0}: {1} is not an Allowed Cash or Bank account.").format(
						row.idx, row.paid_through_account
					)
				)

	def validate_lock_dates(self):
		from quick_expense.services.gstin_service import get_company_gstins

		for row in self.gst_lock_dates:
			if row.company_gstin not in {g.gstin for g in get_company_gstins(row.company)}:
				frappe.throw(
					_("GST Lock Date row {0}: {1} is not a GSTIN of {2}.").format(
						row.idx, row.company_gstin, row.company
					)
				)

	def validate_approval_rules(self):
		groups = {}
		for row in self.approval_rules:
			if flt(row.from_amount) > flt(row.to_amount):
				frappe.throw(_("Approval rule row {0}: From Amount is more than To Amount.").format(row.idx))
			for level in range(1, cint(row.approval_levels_required) + 1):
				if not row.get(f"level_{level}_role") and not row.get(f"level_{level}_user"):
					frappe.throw(
						_("Approval rule row {0}: set a role or a user for level {1}.").format(row.idx, level)
					)
			for level in range(cint(row.approval_levels_required) + 1, 4):
				row.set(f"level_{level}_role", None)
				row.set(f"level_{level}_user", None)
			groups.setdefault((row.company, row.company_gstin or ""), []).append(row)
		for (company, gstin), rows in groups.items():
			rows = sorted(rows, key=lambda r: flt(r.from_amount))
			for previous, current in pairwise(rows):
				if flt(current.from_amount) <= flt(previous.to_amount):
					frappe.throw(
						_("Approval rules for {0}{1} overlap: rows {2} and {3}.").format(
							company, f" / {gstin}" if gstin else "", previous.idx, current.idx
						)
					)
