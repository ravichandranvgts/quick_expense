"""Approval matrix (prompt section 13; test case 29)."""

import json

import frappe
from frappe.utils import add_days, now_datetime

from quick_expense.services import approval_service
from quick_expense.services.approval_service import ApprovalError
from quick_expense.tests.utils import (
	COMPANY,
	KA_GSTIN,
	SUPPLIER_KA,
	QETestCase,
	as_,
	set_settings,
	user,
)


def amount_lines(taxable):
	return [dict(expense_category="Courier and Postage Charges", taxable_amount=taxable)]


class TestApprovalMatrix(QETestCase):
	def levels(self, entry):
		return [step["role"] for step in json.loads(entry.approval_chain)]

	def test_levels_by_amount(self):
		# 4,000 -> 1 level; 18,000 -> 2 levels; 40,000 -> 3 levels (grand totals, GST included)
		one = self.submit(self.make_entry(lines=amount_lines(3389.83)))
		self.assertEqual(one.grand_total, 4000)
		self.assertEqual(self.levels(one), ["Quick Expense Approver"])

		two = self.submit(self.make_entry(lines=amount_lines(15254.24)))
		self.assertEqual(two.grand_total, 18000)
		self.assertEqual(self.levels(two), ["Quick Expense Approver", "Accounts Manager"])

		three = self.submit(self.make_entry(lines=amount_lines(33898.31), **bank_values()))
		self.assertEqual(three.grand_total, 40000)
		self.assertEqual(
			self.levels(three), ["Quick Expense Approver", "Accounts Manager", "Finance Manager"]
		)

	def test_full_three_level_flow_posts(self):
		entry = self.submit(self.make_entry(lines=amount_lines(33898.31), **bank_values()))
		entry = self.approve_level(entry, "approver")
		self.assertEqual((entry.approval_status, entry.approval_level), ("Pending Approval", 2))
		entry = self.approve_level(entry, "am")
		self.assertEqual(entry.approval_level, 3)
		with self.assertRaisesRegex(ApprovalError, "Finance Manager"):
			self.approve_level(entry, "approver")
		entry = self.approve_level(entry, "fm", "OK to pay")
		self.assertEqual(entry.approval_status, "Posted")
		self.assertEqual(entry.docstatus, 1)
		actions = [(row.level, row.action, row.user) for row in entry.approval_history]
		self.assertIn((3, "Approved", user("fm")), actions)
		self.assertEqual(actions[-1][1], "Posted")

	def test_creator_cannot_approve_own_entry(self):
		with as_(user("approver")):
			entry = self.make_entry()
			entry.submit_for_approval()
			with self.assertRaisesRegex(ApprovalError, "cannot approve"):
				entry.approve()
		set_settings(allow_self_approval=1)
		with as_(user("approver")):
			entry.reload()
			entry.approve()
			self.assertEqual(entry.approval_status, "Posted")

	def test_wrong_role_cannot_approve(self):
		entry = self.submit(self.make_entry())
		with self.assertRaisesRegex(ApprovalError, "needs the role"):
			self.approve_level(entry, "user2")

	def test_user_specific_level(self):
		settings = frappe.get_single("Quick Expense Settings")
		for row in settings.approval_rules:
			if row.company == COMPANY and row.from_amount == 0:
				row.level_1_role = None
				row.level_1_user = user("approver2")
		settings.save()
		entry = self.submit(self.make_entry())
		with self.assertRaisesRegex(ApprovalError, "must be approved by"):
			self.approve_level(entry, "approver")
		self.assertEqual(self.approve_level(entry, "approver2").approval_status, "Posted")

	def test_gstin_specific_rule_wins(self):
		settings = frappe.get_single("Quick Expense Settings")
		settings.append(
			"approval_rules",
			{
				"company": COMPANY,
				"company_gstin": KA_GSTIN,
				"from_amount": 0,
				"to_amount": 50000,
				"approval_levels_required": "1",
				"level_1_role": "Finance Manager",
			},
		)
		settings.save()
		entry = self.submit(self.make_entry(company_gstin=KA_GSTIN, supplier_gstin=SUPPLIER_KA))
		self.assertEqual(self.levels(entry), ["Finance Manager"])

	def test_overlapping_rules_rejected(self):
		settings = frappe.get_single("Quick Expense Settings")
		settings.append(
			"approval_rules",
			{
				"company": COMPANY,
				"from_amount": 100,
				"to_amount": 200,
				"approval_levels_required": "1",
				"level_1_role": "Finance Manager",
			},
		)
		with self.assertRaisesRegex(frappe.ValidationError, "overlap"):
			settings.save()

	def test_missing_rule_message(self):
		settings = frappe.get_single("Quick Expense Settings")
		settings.approval_rules = [row for row in settings.approval_rules if row.company != COMPANY]
		settings.save()
		with self.assertRaisesRegex(ApprovalError, "No approval rule"):
			self.submit(self.make_entry())

	def test_forced_reason_adds_accounts_manager_level(self):
		entry = self.submit(self.make_entry(bill_addressed_to_company_gstin=0))
		self.assertEqual(self.levels(entry), ["Quick Expense Approver", "Accounts Manager"])
		self.assertIn("not addressed", entry.forced_approval_reasons)

	def test_approval_switched_off_posts_directly_unless_forced(self):
		set_settings(approval_required=0)
		entry = self.submit(self.make_entry())
		self.assertEqual(entry.approval_status, "Posted")
		forced = self.submit(self.make_entry(bill_addressed_to_company_gstin=0))
		self.assertEqual(self.levels(forced), ["Accounts Manager"])

	def test_rejection_needs_reason_and_returns_to_draft(self):
		entry = self.submit(self.make_entry())
		with as_(user("approver")):
			doc = self.bill(entry.name)
			with self.assertRaisesRegex(ApprovalError, "reason is mandatory"):
				doc.reject("  ")
			doc.reject("Bill is not legible")
		entry.reload()
		self.assertEqual(entry.approval_status, "Rejected")
		# creator corrects and resubmits
		with as_(user("user")):
			entry.remarks = "Clear copy attached"
			entry.save()
			entry.submit_for_approval()
		self.assertEqual(entry.approval_status, "Pending Approval")

	def test_amount_edit_at_level_two_returns_to_level_one(self):
		entry = self.submit(self.make_entry(lines=amount_lines(15254.24)))
		entry = self.approve_level(entry, "approver")
		self.assertEqual(entry.approval_level, 2)
		with as_(user("am")):
			doc = self.bill(entry.name)
			doc.lines[0].taxable_amount = 15000
			doc.save()
		entry.reload()
		self.assertEqual(entry.approval_level, 1)
		self.assertEqual(entry.approval_history[-1].action, "Returned to Level 1")
		self.assertEqual(entry.grand_total, 17700)

	def test_approver_may_edit_remarks_and_cost_center_only(self):
		entry = self.submit(self.make_entry())
		with as_(user("approver")):
			doc = self.bill(entry.name)
			doc.remarks = "Checked"
			doc.save()
			doc.reload()
			doc.supplier_invoice_no = "CHANGED"
			with self.assertRaisesRegex(frappe.ValidationError, "Not allowed"):
				doc.save()

	def test_creator_cannot_edit_while_pending(self):
		with as_(user("user")):
			entry = self.make_entry()
			entry.submit_for_approval()
			entry.reload()
			entry.remarks = "sneaky"
			with self.assertRaisesRegex(frappe.ValidationError, "pending approval"):
				entry.save()

	def test_standard_submit_is_blocked(self):
		entry = self.make_entry()
		with self.assertRaisesRegex(frappe.ValidationError, "Submit for Approval"):
			entry.submit()

	def test_escalation_after_days(self):
		entry = self.submit(self.make_entry())
		frappe.db.set_value(
			"Journal Entry", entry.name, "qe_submitted_for_approval_on", add_days(now_datetime(), -3)
		)
		frappe.db.set_value(
			"Quick Expense Approval History",
			{"parent": entry.name},
			"action_on",
			add_days(now_datetime(), -3),
		)
		approval_service.escalate_pending()
		entry.reload()
		self.assertEqual(entry.approval_history[-1].action, "Escalated")
		self.assertTrue(entry.last_escalated_on)
		# not escalated again the same day
		approval_service.escalate_pending()
		entry.reload()
		self.assertEqual(sum(1 for row in entry.approval_history if row.action == "Escalated"), 1)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"document_name": entry.name, "subject": ("like", "Escalation%")}
			)
		)

	def test_approvers_are_notified(self):
		entry = self.submit(self.make_entry())
		self.assertTrue(
			frappe.db.exists("Notification Log", {"document_name": entry.name, "for_user": user("approver")})
		)
		self.assertFalse(
			frappe.db.exists("Notification Log", {"document_name": entry.name, "for_user": "Administrator"})
		)

	def test_approver_needs_company_permission(self):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user("approver2"),
				"allow": "Company",
				"for_value": "_QE Second Company",
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		entry = self.submit(self.make_entry())
		self.assertIsNotNone(approval_service.get_approval_block_reason(entry, user("approver2")))
		self.assertIsNone(approval_service.get_approval_block_reason(entry, user("approver")))

	def test_own_entries_only(self):
		set_settings(users_see_only_own_entries=1)
		with as_(user("user")):
			mine = self.make_entry()
		with as_(user("user2")):
			self.assertNotIn(mine.name, frappe.get_list("Journal Entry", pluck="name"))
		with as_(user("approver")):
			self.assertIn(mine.name, frappe.get_list("Journal Entry", pluck="name"))


def bank_values():
	from quick_expense.tests.utils import bank

	return dict(paid_through_account=bank(), reference_no="UTR-1", reference_date=frappe.utils.today())


class TestEmailNeverBlocks(QETestCase):
	def test_broken_email_account_does_not_stop_approval(self):
		from unittest.mock import patch

		with patch("frappe.sendmail", side_effect=frappe.OutgoingEmailError("no account")):
			approval_service.send_email(recipients=["x@example.com"], subject="s", message="m")
		self.assertTrue(frappe.db.exists("Error Log", {"method": "Quick Expense: approval email not sent"}))
