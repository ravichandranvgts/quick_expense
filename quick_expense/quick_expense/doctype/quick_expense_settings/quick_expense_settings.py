# Copyright (c) 2026, VGTS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class QuickExpenseSettings(Document):
	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)
