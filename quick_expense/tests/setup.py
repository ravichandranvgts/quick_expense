import frappe
from frappe.utils import getdate


def before_tests():
	from quick_expense.tests.utils import ensure_test_masters

	complete_setup_wizard()
	ensure_test_masters()


def complete_setup_wizard():
	"""On a brand-new site ERPNext cannot create a company until its setup wizard has run
	(it adds the Transit warehouse type and other masters), so run it the way India
	Compliance's own tests do."""
	if frappe.db.a_row_exists("Company"):
		return

	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	today = getdate()
	year = today.year if today.month > 3 else today.year - 1
	setup_complete(
		{
			"currency": "INR",
			"full_name": "Test User",
			"company_name": "QE Setup Company",
			"company_abbr": "QEW",
			"timezone": "Asia/Kolkata",
			"country": "India",
			"fy_start_date": f"{year}-04-01",
			"fy_end_date": f"{year + 1}-03-31",
			"language": "English",
			"email": "test@example.com",
			"password": "test",
			"chart_of_accounts": "Standard",
		}
	)
	frappe.db.commit()  # nosemgrep
