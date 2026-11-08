"""Approval matrix resolution and the approval state machine (prompt section 13).

The chain of approvers is resolved once, when the entry is submitted for
approval, and frozen on the entry (``approval_chain``) so later edits to the
matrix never change entries already in flight.

States (docstatus 0 unless noted):
  Draft -> Pending Approval (level 1..n) -> Approved -> Posted (docstatus 1)
  Pending -> Rejected (back to editable)          Approved -> Approved - Posting Failed
  Posted -> Cancelled (docstatus 2)
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_fullname, get_url_to_form, now_datetime

from quick_expense.constants import (
	APPROVED,
	EDITABLE_STATUSES,
	PENDING,
	POSTED,
	POSTING_FAILED,
	REJECTED,
	ROLE_ACCOUNTS_MANAGER,
)
from quick_expense.services import duplicate_service, validation_service
from quick_expense.services.settings_service import get_settings, is_accounts_manager


class ApprovalError(frappe.ValidationError):
	pass


# ----------------------------------------------------------------------------
# matrix


def resolve_rule(doc, settings=None):
	settings = settings or get_settings()
	amount = flt(doc.grand_total)
	candidates = [
		row
		for row in settings.approval_rules
		if row.company == doc.company
		and (not row.company_gstin or row.company_gstin == doc.company_gstin)
		and flt(row.from_amount) <= amount <= flt(row.to_amount)
	]
	if not candidates:
		frappe.throw(
			_(
				"No approval rule covers {0} for company {1} (GSTIN {2}). Ask an Accounts Manager to update the Approval Matrix in Quick Expense Settings."
			).format(frappe.format(amount, "Currency"), doc.company, doc.company_gstin),
			ApprovalError,
			title=_("Approval Rule Missing"),
		)
	# a GSTIN-specific rule wins over a company-wide one
	candidates.sort(
		key=lambda row: (0 if row.company_gstin else 1, flt(row.to_amount) - flt(row.from_amount))
	)
	return candidates[0]


def build_chain(doc, forced_reasons: list[str], settings=None) -> list[dict]:
	settings = settings or get_settings()
	chain = []
	if cint(settings.approval_required):
		rule = resolve_rule(doc, settings)
		for level in range(1, cint(rule.approval_levels_required) + 1):
			role, user = rule.get(f"level_{level}_role"), rule.get(f"level_{level}_user")
			if not role and not user:
				frappe.throw(
					_("Approval rule for {0} to {1} has no role or user for level {2}.").format(
						rule.from_amount, rule.to_amount, level
					),
					ApprovalError,
				)
			chain.append({"level": level, "role": role or "", "user": user or ""})

	if forced_reasons and not any(step["role"] == ROLE_ACCOUNTS_MANAGER for step in chain):
		chain.append({"level": len(chain) + 1, "role": ROLE_ACCOUNTS_MANAGER, "user": "", "forced": 1})
	return chain


def get_chain(doc) -> list[dict]:
	return json.loads(doc.approval_chain or "[]")


def current_step(doc) -> dict | None:
	chain = get_chain(doc)
	level = cint(doc.approval_level)
	return chain[level - 1] if 0 < level <= len(chain) else None


# ----------------------------------------------------------------------------
# who can approve


def get_approval_block_reason(doc, user: str | None = None) -> str | None:
	"""None when the user can act on the current level, otherwise the reason why not."""
	user = user or frappe.session.user
	settings = get_settings()
	if doc.approval_status != PENDING:
		return _("This entry is not pending approval.")
	step = current_step(doc)
	if not step:
		return _("No approval level is pending.")

	if user == doc.owner and not cint(settings.allow_self_approval):
		return _("You created this entry, so you cannot approve it.")

	if step.get("user"):
		if step["user"] != user:
			return _("Level {0} must be approved by {1}.").format(step["level"], get_fullname(step["user"]))
	elif step.get("role") not in frappe.get_roles(user):
		return _("Level {0} needs the role {1}.").format(step["level"], step["role"])

	if not frappe.has_permission("Journal Entry", "read", doc=getattr(doc, "je", doc), user=user):
		return _("You do not have permission for company {0} / GSTIN {1}.").format(
			doc.company, doc.company_gstin
		)
	return None


def assert_can_approve(doc, user=None):
	if reason := get_approval_block_reason(doc, user):
		frappe.throw(reason, ApprovalError, title=_("Not Allowed"))


def get_users_for_step(doc, step) -> list[str]:
	if step.get("user"):
		return [step["user"]]
	has_role = frappe.qb.DocType("Has Role")
	user_table = frappe.qb.DocType("User")
	users = (
		frappe.qb.from_(has_role)
		.join(user_table)
		.on(user_table.name == has_role.parent)
		.select(user_table.name)
		.distinct()
		.where(has_role.role == step.get("role"))
		.where(has_role.parenttype == "User")
		.where(user_table.enabled == 1)
		.where(user_table.user_type == "System User")
		.where(user_table.name.notin(("Administrator", "Guest")))
	).run(pluck=True)
	allow_self = cint(get_settings().allow_self_approval)
	return [
		user
		for user in users
		if (allow_self or user != doc.owner)
		and frappe.has_permission("Journal Entry", "read", doc=getattr(doc, "je", doc), user=user)
	]


# ----------------------------------------------------------------------------
# history and notifications


def add_history(doc, action, comment="", level=None):
	roles = frappe.get_roles()
	step = current_step(doc)
	acting_role = (step or {}).get("role") if step and step.get("role") in roles else ""
	doc.append(
		"approval_history",
		{
			"level": level if level is not None else cint(doc.approval_level),
			"action": action,
			"user": frappe.session.user,
			"full_name": get_fullname(frappe.session.user),
			"role": acting_role,
			"action_on": now_datetime(),
			"comment": comment,
		},
	)


def notify(doc, users, subject, message):
	users = [user for user in dict.fromkeys(users) if user and user != "Guest"]
	if not users:
		return
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	link = get_url_to_form(doc.doctype, doc.name)
	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": doc.doctype,
			"document_name": doc.name,
			"subject": subject,
			"email_content": f"{message}<br><br><a href='{link}'>{_('Open {0}').format(doc.name)}</a>",
			"from_user": frappe.session.user,
		},
	)
	if frappe.flags.in_test:
		return
	send_email(
		recipients=[frappe.db.get_value("User", user, "email") or user for user in users],
		subject=subject,
		message=f"<p style='font-size:16px'>{message}</p><p><a href='{link}?qe_action=review' style='font-size:16px'>{_('Review, approve or reject')}</a></p>",
		reference_doctype=doc.doctype,
		reference_name=doc.name,
	)


def send_email(**kwargs):
	"""Email is a courtesy: a missing or broken email account must never stop an approval.
	The in-app notification above has already been created."""
	try:
		frappe.sendmail(now=False, **kwargs)
	except Exception:
		frappe.clear_last_message()
		frappe.log_error(title="Quick Expense: approval email not sent", message=frappe.get_traceback())


def describe(doc) -> str:
	return _("{0}: {1} for {2} (bill {3}, {4})").format(
		doc.name,
		frappe.format(doc.grand_total, "Currency"),
		doc.supplier_legal_name,
		doc.supplier_invoice_no or "-",
		doc.company_gstin,
	)


def notify_current_approvers(doc, escalation=False):
	if not cint(get_settings().notify_approvers):
		return
	step = current_step(doc)
	if not step:
		return
	users = get_users_for_step(doc, step)
	subject = (
		_("Escalated: Quick Expense {0} waiting for your approval").format(doc.name)
		if escalation
		else _("Quick Expense {0} needs your approval (level {1})").format(doc.name, step["level"])
	)
	notify(doc, users, subject, describe(doc))


def notify_creator(doc, subject):
	if cint(get_settings().notify_creator):
		notify(doc, [doc.owner], subject, describe(doc))


def internal_save(doc):
	"""Save done by the approval engine itself; the edit guard is skipped for this save only."""
	doc.flags.qe_internal_save = True
	try:
		doc.save()
	finally:
		doc.flags.qe_internal_save = False


def set_current_approver(doc):
	step = current_step(doc)
	doc.current_approver_role = (step or {}).get("role", "")
	doc.current_approver_user = (step or {}).get("user") or None


# ----------------------------------------------------------------------------
# state machine


def submit_for_approval(doc):
	if doc.docstatus != 0 or doc.approval_status not in EDITABLE_STATUSES:
		frappe.throw(_("Only a draft or rejected entry can be submitted for approval."), ApprovalError)

	result = validation_service.validate_for_approval(doc)
	doc.warnings = "\n".join(result.warnings)
	doc.forced_approval_reasons = "\n".join(result.forced)

	if doc.is_new():
		doc.insert()
	duplicate_service.reserve(doc)

	chain = build_chain(doc, result.forced)
	doc.approval_chain = json.dumps(chain)
	doc.approval_levels_required = len(chain)
	doc.submitted_for_approval_on = now_datetime()
	doc.last_escalated_on = None
	doc.posting_error = ""

	if not chain:
		doc.approval_level = 0
		doc.approval_status = APPROVED
		add_history(doc, _("Submitted"), _("No approval required"), level=0)
		internal_save(doc)
		return post(doc)

	doc.approval_level = 1
	doc.approval_status = PENDING
	set_current_approver(doc)
	add_history(doc, _("Submitted for Approval"), level=0)
	internal_save(doc)
	notify_current_approvers(doc)
	return doc


def approve(doc, comment=""):
	assert_can_approve(doc)
	# masters may have changed while the entry was waiting
	validation_service.validate_for_approval(doc, at_posting=True)
	add_history(doc, _("Approved"), comment)

	if cint(doc.approval_level) < cint(doc.approval_levels_required):
		doc.approval_level = cint(doc.approval_level) + 1
		doc.last_escalated_on = None
		set_current_approver(doc)
		internal_save(doc)
		notify_current_approvers(doc)
		return doc

	doc.approval_status = APPROVED
	doc.current_approver_role = ""
	doc.current_approver_user = None
	internal_save(doc)
	notify_creator(doc, _("Quick Expense {0} approved").format(doc.name))
	return post(doc)


def reject(doc, reason):
	if not (reason or "").strip():
		frappe.throw(_("A reason is mandatory to reject an entry."), ApprovalError)
	if doc.approval_status == POSTING_FAILED:
		if not is_accounts_manager():
			frappe.throw(
				_("Only an Accounts Manager can reject an entry whose posting failed."), ApprovalError
			)
	else:
		assert_can_approve(doc)
	add_history(doc, _("Rejected"), reason)
	doc.approval_status = REJECTED
	doc.approval_level = 0
	doc.current_approver_role = ""
	doc.current_approver_user = None
	internal_save(doc)
	notify_creator(doc, _("Quick Expense {0} rejected: {1}").format(doc.name, reason))
	return doc


def post(doc):
	"""Post after final approval. One transaction: either the entry and its
	Purchase Invoice / Journal Entry are all saved, or nothing is and the entry
	shows the error with a Retry button."""
	if doc.approval_status not in (APPROVED, POSTING_FAILED):
		frappe.throw(_("Only an approved entry can be posted."), ApprovalError)

	frappe.db.savepoint("quick_expense_post")
	try:
		_submit(doc)
		return doc
	except Exception as exc:
		frappe.db.rollback(save_point="quick_expense_post")
		error = frappe.utils.strip_html(str(exc)) or exc.__class__.__name__
		frappe.log_error(title=f"Quick Expense posting failed: {doc.name}", message=frappe.get_traceback())
		frappe.clear_messages()
		doc.reload()
		doc.approval_status = POSTING_FAILED
		doc.posting_error = error[:1000]
		add_history(doc, _("Posting Failed"), error[:500])
		internal_save(doc)
		if not frappe.flags.in_test:
			frappe.db.commit()  # nosemgrep: keep the failed status even if the request errors later
		frappe.msgprint(
			_("The entry is approved but could not be posted: {0}").format(error),
			title=_("Posting Failed"),
			indicator="red",
		)
		return doc


def _submit(doc):
	from quick_expense.posting import run_as_system

	doc.flags.qe_posting = True
	# the approval is the authorisation; the creator or approver needs no submit right
	doc.flags.ignore_permissions = True
	try:
		doc.approval_status = POSTED
		add_history(doc, _("Posted"), level=cint(doc.approval_levels_required))
		with run_as_system():
			doc.submit()
	finally:
		doc.flags.qe_posting = False
		doc.flags.ignore_permissions = False


def retry_posting(doc):
	if not is_accounts_manager():
		frappe.throw(_("Only an Accounts Manager can retry posting."), ApprovalError)
	if doc.approval_status != POSTING_FAILED:
		frappe.throw(_("Only an entry whose posting failed can be retried."), ApprovalError)
	validation_service.validate_for_approval(doc, at_posting=True)
	add_history(doc, _("Retry Posting"))
	internal_save(doc)
	return post(doc)


def return_to_level_one(doc, reason):
	"""Called by the controller when an amount changes while pending."""
	doc.approval_level = 1
	add_history(doc, _("Returned to Level 1"), reason)
	set_current_approver(doc)
	doc.flags.notify_after_save = True


# ----------------------------------------------------------------------------
# scheduler


def escalate_pending():
	settings = get_settings()
	days = cint(settings.escalation_after_days)
	if not days:
		return
	cutoff = add_days(now_datetime(), -days)
	from quick_expense.constants import JE_VOUCHER_TYPE
	from quick_expense.services.bill import Bill

	for name in frappe.get_all(
		"Journal Entry",
		filters={"voucher_type": JE_VOUCHER_TYPE, "qe_approval_status": PENDING, "docstatus": 0},
		pluck="name",
	):
		doc = Bill(frappe.get_doc("Journal Entry", name))
		since = doc.last_escalated_on or last_level_change(doc) or doc.submitted_for_approval_on
		if not since or since > cutoff:
			continue
		chain = get_chain(doc)
		level = cint(doc.approval_level)
		users = set()
		if level < len(chain):
			users.update(get_users_for_step(doc, chain[level]))
		users.update(get_users_for_step(doc, {"role": ROLE_ACCOUNTS_MANAGER}))
		notify(
			doc,
			list(users),
			_("Escalation: Quick Expense {0} pending at level {1} for more than {2} days").format(
				doc.name, level, days
			),
			describe(doc),
		)
		add_history(doc, _("Escalated"), _("Pending more than {0} days").format(days))
		doc.last_escalated_on = now_datetime()
		doc.flags.ignore_permissions = True
		internal_save(doc)


def last_level_change(doc):
	times = [
		row.action_on
		for row in doc.approval_history
		if row.action in (_("Approved"), _("Submitted for Approval"), _("Returned to Level 1"))
	]
	return max(times) if times else None
