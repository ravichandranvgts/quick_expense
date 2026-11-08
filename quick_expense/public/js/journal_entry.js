// Quick Expense bill on the Journal Entry form (voucher type "Quick Expense").
// The user types the supplier, the bill and one row per expense; saving adds the
// GST, reverse charge, round off and cash / bank rows. The bill is submitted by
// the approval engine, never with the standard Submit button.

const QE_STATUS_COLOURS = {
	Draft: "gray",
	"Pending Approval": "orange",
	Approved: "blue",
	Posted: "green",
	Rejected: "red",
	"Approved - Posting Failed": "red",
	Cancelled: "red",
};
const QE_STATES = [
	"Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
	"Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana",
	"Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep Islands",
	"Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Other Territory",
	"Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
	"Uttarakhand", "West Bengal",
];
const QE_GSTIN_TREATMENTS = ["Registered Regular", "Registered Composition", "SEZ"];

frappe.ui.form.on("Journal Entry", {
	setup(frm) {
		frm.set_query("qe_item_tax_template", "accounts", () => ({ filters: { company: frm.doc.company, disabled: 0 } }));
	},

	onload(frm) {
		if (frm.is_new() && frappe.route_options && frappe.route_options.voucher_type === "Quick Expense") {
			frm.set_value("voucher_type", "Quick Expense");
		}
	},

	refresh(frm) {
		if (frm.doc.voucher_type !== "Quick Expense") return;
		qe_setup_form(frm);
		qe_buttons(frm);
		qe_headline(frm);
		qe_supplier_button(frm);
	},

	voucher_type(frm) {
		if (frm.doc.voucher_type !== "Quick Expense") return;
		if (!frm.doc.qe_gst_treatment) frm.set_value("qe_gst_treatment", "Registered Regular");
		frm.trigger("company");
		qe_setup_form(frm);
	},

	company(frm) {
		if (frm.doc.voucher_type !== "Quick Expense" || !frm.doc.company) return;
		frappe.call({
			method: "quick_expense.api.get_entry_defaults",
			args: { company: frm.doc.company },
			callback({ message }) {
				if (!message) return;
				frm.__qe_allowed_accounts = message.allowed_accounts;
				frm.set_df_property("company_gstin", "options", (message.company_gstins || []).join("\n"));
				if (frm.doc.docstatus !== 0) return;
				if (!frm.doc.company_gstin && message.company_gstin) frm.set_value("company_gstin", message.company_gstin);
				// the cash / bank account is a row in the table; the system fills its amount on save
				const has_payment = (frm.doc.accounts || []).some((row) => row.qe_gst_component === "Payment" || (frm.__qe_allowed_accounts || []).includes(row.account));
				if (!has_payment && message.paid_through_account) {
					const row = frm.add_child("accounts", { account: message.paid_through_account, qe_gst_component: "Payment" });
					frm.refresh_field("accounts");
					frappe.show_alert({ message: __("Cash / bank row {0} added; its amount is filled on save.", [row.account]), indicator: "blue" }, 5);
				}
			},
		});
	},

	qe_gst_treatment(frm) {
		if (!QE_GSTIN_TREATMENTS.includes(frm.doc.qe_gst_treatment)) frm.set_value("qe_supplier_gstin", "");
	},

	// fill the supplier from the GST portal as soon as the GSTIN is typed
	qe_supplier_gstin(frm) {
		const gstin = (frm.doc.qe_supplier_gstin || "").replace(/\s+/g, "").toUpperCase();
		if (gstin !== frm.doc.qe_supplier_gstin) return frm.set_value("qe_supplier_gstin", gstin);
		if (gstin.length !== 15 || frm.doc.docstatus !== 0) return;
		frappe.call({
			method: "quick_expense.api.get_gstin_details",
			args: { gstin, company: frm.doc.company, company_gstin: frm.doc.company_gstin },
			freeze: true,
			freeze_message: __("Checking GSTIN..."),
			callback({ message: d }) {
				if (!d) return;
				if (!d.valid) {
					frappe.msgprint({ title: __("GSTIN Problem"), message: d.message, indicator: "red" });
					return;
				}
				const values = { qe_supplier_state: d.state, qe_gstin_status: d.status || "" };
				if (d.source === "API") {
					Object.assign(values, {
						qe_supplier_legal_name: d.legal_name,
						qe_supplier_trade_name: d.trade_name,
						qe_supplier_address: d.address,
						qe_supplier_pincode: d.pincode,
						qe_supplier_name_entered_manually: 0,
					});
					if (QE_GSTIN_TREATMENTS.includes(d.registration_type) && d.registration_type !== frm.doc.qe_gst_treatment) {
						values.qe_gst_treatment = d.registration_type;
					}
				} else {
					values.qe_supplier_name_entered_manually = 1;
					frappe.show_alert({ message: d.message || __("Type the supplier legal name."), indicator: "orange" }, 8);
				}
				frm.set_value(values);
				if (d.blocking_status) {
					frappe.msgprint({
						title: __("GSTIN {0}", [d.status]),
						message: __("ITC is not available on invoices from a cancelled GSTIN."),
						indicator: "red",
					});
				}
			},
		});
	},
});

frappe.ui.form.on("Journal Entry Account", {
	// a new expense row: debit follows the taxable amount until the bill is saved
	qe_taxable_amount(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.doc.voucher_type === "Quick Expense" && !row.qe_gst_component) {
			frappe.model.set_value(cdt, cdn, "debit_in_account_currency", row.qe_taxable_amount);
		}
	},
});

function qe_setup_form(frm) {
	frm.fields_dict.accounts.grid.update_docfield_property("qe_location_of_service_state", "options", QE_STATES.join("\n"));
	if (frm.doc.company && !frm.__qe_allowed_accounts) frm.trigger("company");
}

function qe_editable(frm) {
	return frm.doc.docstatus === 0 && ["", "Draft", "Rejected", undefined, null].includes(frm.doc.qe_approval_status);
}

function qe_buttons(frm) {
	const info = (frm.doc.__onload && frm.doc.__onload.quick_expense) || {};
	const status = frm.doc.qe_approval_status || "Draft";
	const call = (method, args, message) =>
		frappe
			.call({ method: `quick_expense.api.${method}`, args: { name: frm.doc.name, ...args }, freeze: true, freeze_message: message })
			.then(() => frm.reload_doc());

	if (frm.doc.docstatus === 0) {
		frm.page.clear_primary_action();
		if (!frm.is_new() && qe_editable(frm) && !frm.is_dirty()) {
			frm.page.set_primary_action(__("Submit for Approval"), () => call("submit_for_approval", {}, __("Submitting...")));
		} else {
			frm.page.set_primary_action(__("Save"), () => frm.save());
		}
	}
	if (status === "Pending Approval" && !info.approval_block_reason) {
		frm.add_custom_button(__("Approve"), () =>
			frappe.prompt(
				[{ fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)") }],
				(v) => call("approve", { comment: v.comment }, __("Approving...")),
				__("Approve {0}", [frm.doc.name]),
				__("Approve"),
			),
		).addClass("btn-primary qe-big-button");
	}
	if ((status === "Pending Approval" && !info.approval_block_reason) || (status === "Approved - Posting Failed" && info.is_accounts_manager)) {
		frm.add_custom_button(__("Reject"), () =>
			frappe.prompt(
				[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason for rejection"), reqd: 1 }],
				(v) => call("reject", { reason: v.reason }, __("Rejecting...")),
				__("Reject {0}", [frm.doc.name]),
				__("Reject"),
			),
		).addClass("btn-danger qe-big-button");
	}
	if (status === "Approved - Posting Failed" && info.is_accounts_manager) {
		frm.add_custom_button(__("Retry Posting"), () => call("retry_posting", {}, __("Posting..."))).addClass("btn-primary");
	}
	if (frm.doc.docstatus === 1) {
		frm.add_custom_button(__("Cancel with Reason"), () =>
			frappe.prompt(
				[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: frm.doc.reconciliation_status === "Reconciled" }],
				(v) => call("cancel_with_reason", { reason: v.reason }, __("Cancelling...")),
				__("Cancel {0}", [frm.doc.name]),
				__("Cancel Bill"),
			),
		);
	}
	if (status === "Pending Approval" && info.approval_block_reason) frm.dashboard.add_comment(info.approval_block_reason, "blue", true);
}

function qe_headline(frm) {
	const status = frm.doc.qe_approval_status || "Draft";
	if (frm.doc.docstatus === 0) frm.page.set_indicator(__(status), QE_STATUS_COLOURS[status] || "gray");
	const esc = frappe.utils.escape_html;
	const parts = [];
	if (frm.doc.qe_grand_total) {
		parts.push(
			`<b>${__("Taxable")}:</b> ${format_currency(frm.doc.qe_total_taxable, "INR")} &nbsp; <b>${__("GST")}:</b> ${format_currency(frm.doc.qe_total_gst, "INR")}` +
				` &nbsp; <b>${__("Eligible ITC")}:</b> ${format_currency(frm.doc.qe_total_eligible_itc, "INR")} &nbsp; <b>${__("Paid")}:</b> ${format_currency(frm.doc.qe_grand_total, "INR")}`,
		);
	}
	if (status === "Pending Approval" && frm.doc.qe_approval_levels_required) {
		parts.push(`<b>${__("Level")}:</b> ${frm.doc.qe_approval_level} / ${frm.doc.qe_approval_levels_required} (${esc(frm.doc.qe_current_approver_user || frm.doc.qe_current_approver_role || "")})`);
	}
	if (frm.doc.docstatus === 1 && frm.doc.reconciliation_status) parts.push(`<b>${__("GSTR-2B")}:</b> ${esc(__(frm.doc.reconciliation_status))}`);
	if (frm.doc.qe_posting_error) parts.push(`<span class="text-danger"><b>${__("Posting error")}:</b> ${esc(frm.doc.qe_posting_error)}</span>`);
	const forced = (frm.doc.qe_forced_approval_reasons || "").split("\n").filter(Boolean);
	if (forced.length) parts.push(`<b>${__("Accounts Manager approval needed")}:</b> ${forced.map(esc).join("; ")}`);
	const warnings = (frm.doc.qe_warnings || "").split("\n").filter(Boolean);
	if (warnings.length) parts.push(`<span class="text-warning">${warnings.map(esc).join("<br>")}</span>`);
	if (parts.length) frm.dashboard.set_headline(`<div class="qe-headline">${parts.join("<br>")}</div>`);
}

// ---------------------------------------------------------------------------
// supplier onboarding

function qe_supplier_button(frm) {
	// only for Quick Expense bills whose supplier is not yet in the Supplier master
	if (frm.is_new() || frm.doc.voucher_type !== "Quick Expense") return;
	if (!(frm.doc.qe_supplier_gstin || frm.doc.qe_supplier_legal_name)) return;
	const onload = frm.doc.__onload || {};
	if (onload.qe_supplier_exists !== false) return;
	if (!frappe.model.can_create("Supplier")) return;
	frm.add_custom_button(__("Create Supplier"), () => qe_onboard_supplier(frm));
}

function qe_onboard_supplier(frm) {
	frappe.call({
		method: "quick_expense.services.supplier_service.preview_supplier",
		args: { journal_entry: frm.doc.name },
		freeze: true,
		freeze_message: __("Fetching supplier details from the GSTIN..."),
		callback({ message: d }) {
			if (!d) return;
			const esc = frappe.utils.escape_html;
			const addresses = (d.addresses || [])
				.map((a) => `<li>${a.is_principal ? `<b>${__("Principal place")}</b>: ` : ""}${esc([a.address_line1, a.address_line2, a.city, a.state, a.pincode].filter(Boolean).join(", "))}</li>`)
				.join("");
			const rows = [
				[__("Supplier Name"), d.supplier_name],
				[__("Legal Name"), d.legal_name],
				[__("Trade Name"), d.trade_name],
				[__("GSTIN"), d.gstin || __("Unregistered")],
				[__("PAN"), d.pan],
				[__("GST Category"), d.gst_category],
				[__("Constitution"), d.constitution],
				[__("GSTIN Status"), d.status],
				[__("State"), d.state],
			]
				.filter(([, v]) => v)
				.map(([k, v]) => `<tr><td class="text-muted" style="width:35%">${k}</td><td><b>${esc(v)}</b></td></tr>`)
				.join("");
			const dialog = new frappe.ui.Dialog({
				title: __("Create Supplier from GSTIN"),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "preview" }],
				primary_action_label: __("Create Supplier"),
				primary_action() {
					dialog.hide();
					frappe.call({
						method: "quick_expense.services.supplier_service.create_supplier",
						args: { journal_entry: frm.doc.name },
						freeze: true,
						callback({ message: r }) {
							frappe.show_alert({ message: __("Supplier {0} created with {1} address(es).", [r.supplier, r.addresses.length]), indicator: "green" });
							frm.reload_doc();
						},
					});
				},
			});
			dialog.fields_dict.preview.$wrapper.html(
				`<p class="text-muted">${__("Source")}: ${esc(d.source)}</p><table class="table table-bordered">${rows}</table>` +
					`<p><b>${__("Addresses")}</b></p><ul>${addresses || `<li>${__("None")}</li>`}</ul>`,
			);
			dialog.show();
		},
	});
}
