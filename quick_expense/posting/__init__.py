from contextlib import contextmanager

import frappe


@contextmanager
def run_as_system():
	"""Posting after the final approval is a system action: approvers need no
	accounting permissions (ERPNext checks Account read rights while building the
	Journal Entry). Who approved is in the approval history."""
	session = frappe.local.session
	if session.user == "Administrator":
		yield
		return
	saved = (session.user, session.sid, session.data, frappe.local.form_dict)
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(saved[0])
		session.sid, session.data = saved[1], saved[2]
		frappe.local.form_dict = saved[3]
