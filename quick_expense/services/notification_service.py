"""Scheduled emails (prompt section 3.8)."""

import frappe
from frappe import _
from frappe.utils import cint

from quick_expense.constants import ROLE_ACCOUNTS_MANAGER


def accounts_managers_for(company: str) -> list[str]:
	users = frappe.get_all(
		"Has Role",
		filters={"role": ROLE_ACCOUNTS_MANAGER, "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	result = []
	for user in users:
		if user in ("Administrator", "Guest") or not frappe.db.get_value("User", user, "enabled"):
			continue
		if frappe.has_permission("Company", "read", doc=company, user=user):
			result.append(user)
	return result


def send_email(**kwargs):
	"""Email is a courtesy: a missing or broken email account must never raise."""
	try:
		frappe.sendmail(now=False, **kwargs)
	except Exception:
		frappe.clear_last_message()
		frappe.log_error(title="Quick Expense: email not sent", message=frappe.get_traceback())


def send_2b_follow_up():
	if not cint(frappe.db.get_single_value("Quick Expense Settings", "monthly_2b_follow_up_email")):
		return
	from quick_expense.services.reconciliation_service import get_follow_up_entries

	for company in frappe.get_all("Company", filters={"country": "India"}, pluck="name"):
		rows = get_follow_up_entries(company)
		if not rows:
			continue
		recipients = [frappe.db.get_value("User", u, "email") for u in accounts_managers_for(company)]
		recipients = [r for r in recipients if r]
		if not recipients:
			continue
		lines = "".join(
			f"<tr><td>{r.name}</td><td>{r.company_gstin}</td><td>{r.qe_supplier_gstin}</td><td>{frappe.utils.escape_html(r.qe_supplier_legal_name or '')}</td><td>{r.qe_supplier_invoice_no or ''}</td><td style='text-align:right'>{frappe.format(r.qe_total_eligible_itc, 'Currency')}</td><td>{r.reconciliation_status or 'Unreconciled'}</td></tr>"
			for r in rows[:200]
		)
		send_email(
			recipients=recipients,
			subject=_("Quick Expense: {0} bills not yet in GSTR-2B for {1}").format(len(rows), company),
			message=f"""<p style="font-size:16px">{_("These bills are older than one return period and are still not in GSTR-2A / 2B. Please follow up with the suppliers so that ITC is not lost.")}</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;font-size:14px">
<tr><th>{_("Journal Entry")}</th><th>{_("Company GSTIN")}</th><th>{_("Supplier GSTIN")}</th><th>{_("Supplier")}</th><th>{_("Invoice No")}</th><th>{_("Eligible ITC")}</th><th>{_("2B Status")}</th></tr>
{lines}</table>
<p><a href="{frappe.utils.get_url("/app/purchase-reconciliation-tool")}">{_("Open the Purchase Reconciliation Tool")}</a></p>""",
		)
