"""Simpler bill form: the transporter tick (used only by the removed cash limit) and the
reason for moving the ITC claim period (the period now always follows the posting
date) are removed. Old rows with no ITC eligibility are eligible, so say so."""

import frappe

from quick_expense.overrides.custom_fields import OBSOLETE_FIELDS


def execute():
	for name in OBSOLETE_FIELDS:
		frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
	frappe.db.sql(
		"""update `tabJournal Entry Account` row join `tabJournal Entry` je on je.name = row.parent
		set row.qe_eligibility = 'All Other ITC'
		where je.voucher_type = 'Quick Expense' and row.qe_gst_component = 'Expense'
		and ifnull(row.qe_eligibility, '') = ''"""
	)
