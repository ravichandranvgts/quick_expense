// Loaded on India Compliance's Purchase Reconciliation Tool and IMS.

// Purchase Reconciliation Tool and IMS: Quick Expense bills are Journal Entries, so offer
// "Journal Entry" in the Link dialog and pre-select it for an invoice found only in 2B.
(() => {
	const dialog = window.reconciliation && window.reconciliation.detail_view_dialog;
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
				if (this.missing_doctype === "Purchase Invoice")
					doctype_field.default = "Journal Entry";
			}
		}
		return fields;
	};
	dialog.prototype.__qe_patched = true;
})();
