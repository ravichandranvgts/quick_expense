// Quick Expense bills in the Journal Entry list: approval status and bulk approve / reject.
(() => {
	const settings = (frappe.listview_settings["Journal Entry"] = frappe.listview_settings["Journal Entry"] || {});
	settings.add_fields = [...(settings.add_fields || []), "voucher_type", "qe_approval_status", "qe_approval_level", "qe_approval_levels_required"];

	const original_indicator = settings.get_indicator;
	settings.get_indicator = function (doc) {
		if (doc.voucher_type === "Quick Expense" && doc.docstatus === 0 && doc.qe_approval_status) {
			const colours = { Draft: "gray", "Pending Approval": "orange", Approved: "blue", Rejected: "red", "Approved - Posting Failed": "red" };
			let label = __(doc.qe_approval_status);
			if (doc.qe_approval_status === "Pending Approval") label += ` ${doc.qe_approval_level}/${doc.qe_approval_levels_required}`;
			return [label, colours[doc.qe_approval_status] || "gray", `qe_approval_status,=,${doc.qe_approval_status}`];
		}
		return original_indicator ? original_indicator.call(this, doc) : null;
	};

	const original_onload = settings.onload;
	settings.onload = function (listview) {
		if (original_onload) original_onload.call(this, listview);
		listview.page.add_action_item(__("Approve Quick Expense"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;
			frappe.prompt(
				[{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") }],
				(values) =>
					frappe.call({
						method: "quick_expense.api.bulk_approve",
						args: { names, comment: values.comment },
						freeze: true,
						callback({ message }) {
							const failed = (message || []).filter((r) => !r.ok);
							frappe.msgprint(
								__("{0} approved.", [(message || []).length - failed.length]) +
									(failed.length ? "<br>" + failed.map((r) => `${r.name}: ${frappe.utils.escape_html(r.message)}`).join("<br>") : ""),
							);
							listview.refresh();
						},
					}),
				__("Approve {0} bills", [names.length]),
				__("Approve"),
			);
		});
	};
})();
