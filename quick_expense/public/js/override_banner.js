// Loaded on India Compliance's GSTR 3B Report, Purchase Reconciliation Tool and IMS.

// Red banner when the compatibility guard has switched the Quick Expense overrides off.
["GSTR 3B Report", "Purchase Reconciliation Tool", "GST Invoice Management System"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			frappe.db
				.get_value("Quick Expense Settings", "Quick Expense Settings", "overrides_disabled")
				.then(({ message }) => {
					if (message && Number(message.overrides_disabled)) {
						frm.dashboard.set_headline_alert(
							__(
								"Quick Expense Journal Entries are NOT included: the compatibility self-test failed after an India Compliance update. Check Quick Expense Settings.",
							),
							"red",
						);
					}
				});
		},
	});
});

// Purchase Reconciliation Tool and IMS: Quick Expense bills are Journal Entries, so offer
// "Journal Entry" in the Link dialog and pre-select it for an invoice found only in 2B.
(() => {
	const dialog = window.reconciliation && reconciliation.detail_view_dialog;
	if (!dialog || dialog.prototype.__qe_patched) return;
	const original = dialog.prototype._get_document_link_fields;
	dialog.prototype._get_document_link_fields = function () {
		const fields = original.call(this);
		const options = this.doctype_options || [];
		if (options.includes("Purchase Invoice") && !options.includes("Journal Entry")) {
			options.push("Journal Entry");
			const doctype_field = fields.find((field) => field.fieldname === "doctype");
			if (doctype_field) {
				doctype_field.read_only_depends_on = false; // IMS locks a single option; there are two now
				if (this.missing_doctype === "Purchase Invoice") doctype_field.default = "Journal Entry";
			}
		}
		return fields;
	};
	dialog.prototype.__qe_patched = true;
})();
