// Quick Expense bill on the Journal Entry form (voucher type "Quick Expense").
// The user types the supplier, the bill and one row per expense; saving adds the
// GST, reverse charge, round off and cash / bank rows. The bill is posted with the
// standard Submit button, or through a Frappe Workflow set up on Journal Entry.

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
		qe_grid_columns(frm);
		if (frm.doc.voucher_type !== "Quick Expense") return;
		qe_setup_form(frm);
		qe_buttons(frm);
		qe_headline(frm);
		qe_supplier_button(frm);
	},

	voucher_type(frm) {
		qe_grid_columns(frm);
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
				frm.__qe_defaults_loaded = true;
				frm.set_df_property("company_gstin", "options", (message.company_gstins || []).join("\n"));
				if (frm.doc.docstatus !== 0) return;
				if (!frm.doc.company_gstin && message.company_gstin) frm.set_value("company_gstin", message.company_gstin);
			},
		});
	},

	// the ITC claim period is the posting month (read-only; IMS may move a submitted bill)
	posting_date(frm) {
		if (frm.doc.voucher_type !== "Quick Expense" || frm.doc.docstatus !== 0 || !frm.doc.posting_date) return;
		const [year, month] = frm.doc.posting_date.split("-");
		frm.set_value("qe_itc_claim_period", `${month}${year}`);
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
				if (["API", "Supplier Master"].includes(d.source)) {
					// `resolved_name`, not `legal_name`: the portal returns an empty
					// legal name for some registrations while still returning the
					// address and an Active status, and this used to write that empty
					// string in and then lock the field by setting the manual flag to
					// 0 — blank, read-only, and refused on save with no way out.
					const name = d.resolved_name || "";
					Object.assign(values, {
						qe_supplier_legal_name: name,
						qe_supplier_trade_name: d.trade_name,
						qe_supplier_address: d.address,
						qe_supplier_pincode: d.pincode,
						// Unlocked when nothing came back, so it can still be typed.
						qe_supplier_name_entered_manually: name ? 0 : 1,
					});
					if (QE_GSTIN_TREATMENTS.includes(d.registration_type) && d.registration_type !== frm.doc.qe_gst_treatment) {
						values.qe_gst_treatment = d.registration_type;
					}
					if (!name) {
						frappe.msgprint({
							title: __("Supplier Name Not on the Portal"),
							message: __("The GST portal returned no name for {0}. Type the name printed on the bill.", [gstin]),
							indicator: "orange",
						});
					} else if (!d.name_is_legal) {
						// Said out loud: a trade name is not the registered name, and
						// GSTR-2B matches on the registered one.
						frappe.show_alert(
							{ message: __("The portal has no legal name for this GSTIN; its trade name was used."), indicator: "orange" },
							7
						);
					}
					if (d.source !== "API") frappe.show_alert({ message: d.message, indicator: "green" }, 5);
				} else {
					values.qe_supplier_name_entered_manually = 1;
					frappe.msgprint({
						title: __("Supplier Details Not Fetched"),
						message: d.message || __("Type the supplier legal name."),
						indicator: "orange",
					});
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

const QE_ROW_GST_FIELDS = ["qe_item_tax_template", "qe_hsn_sac", "qe_eligibility"];

frappe.ui.form.on("Journal Entry Account", {
	// a new row takes the GST rate, HSN and eligibility of the expense row above it
	accounts_add(frm, cdt, cdn) {
		if (frm.doc.voucher_type !== "Quick Expense") return;
		const previous = (frm.doc.accounts || [])
			.filter((row) => row.name !== cdn && !row.qe_gst_component && row.qe_item_tax_template)
			.pop();
		if (!previous) return;
		QE_ROW_GST_FIELDS.forEach((field) => previous[field] && frappe.model.set_value(cdt, cdn, field, previous[field]));
	},
	// no rate yet: use the one on this supplier's last bill (same expense account first)
	account(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.doc.voucher_type !== "Quick Expense" || row.qe_gst_component || row.qe_item_tax_template) return;
		if (!row.account || !frm.doc.qe_supplier_gstin) return; // the server skips a cash / bank account
		frappe
			.xcall("quick_expense.api.get_last_gst_rate", {
				company: frm.doc.company,
				supplier_gstin: frm.doc.qe_supplier_gstin,
				account: row.account,
			})
			.then((last) => {
				if (!last || locals[cdt][cdn].qe_item_tax_template) return;
				frappe.model.set_value(cdt, cdn, "qe_item_tax_template", last.qe_item_tax_template);
				if (last.qe_hsn_sac && !locals[cdt][cdn].qe_hsn_sac) frappe.model.set_value(cdt, cdn, "qe_hsn_sac", last.qe_hsn_sac);
			});
	},
	// a new expense row: debit follows the taxable amount until the bill is saved
	qe_taxable_amount(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.doc.voucher_type === "Quick Expense" && !row.qe_gst_component) {
			frappe.model.set_value(cdt, cdn, "debit_in_account_currency", row.qe_taxable_amount);
		}
	},
});

// In a Quick Expense bill the user types Account, Taxable Amount and GST Rate in the table;
// Debit / Credit are worked out on save (taxable + ineligible GST, GST rows, the cash / bank row).
const QE_GRID_INPUTS = { qe_taxable_amount: 2, qe_item_tax_template: 2 };

function qe_grid_columns(frm) {
	const grid = frm.fields_dict.accounts.grid;
	const qe = frm.doc.voucher_type === "Quick Expense";
	if (grid.__qe_mode === qe) return;
	grid.__qe_mode = qe;
	Object.entries(QE_GRID_INPUTS).forEach(([field, columns]) => {
		grid.update_docfield_property(field, "in_list_view", qe ? 1 : 0);
		grid.update_docfield_property(field, "columns", columns);
	});
	["party_type", "party"].forEach((field) => grid.update_docfield_property(field, "in_list_view", qe ? 0 : 1));
	["debit_in_account_currency", "credit_in_account_currency"].forEach((field) => grid.update_docfield_property(field, "read_only", qe ? 1 : 0));
	grid.reset_grid();
}

function qe_setup_form(frm) {
	frm.fields_dict.accounts.grid.update_docfield_property("qe_location_of_service_state", "options", QE_STATES.join("\n"));
	if (frm.doc.company && !frm.__qe_defaults_loaded) frm.trigger("company");
}

function qe_buttons(frm) {
	if (frm.doc.docstatus !== 1) return;
	// a bill matched with GSTR-2B needs a reason to cancel; the reconciliation link is reset
	frm.add_custom_button(__("Cancel with Reason"), () =>
		frappe.prompt(
			[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: frm.doc.reconciliation_status === "Reconciled" }],
			(v) =>
				frappe
					.call({
						method: "quick_expense.api.cancel_with_reason",
						args: { name: frm.doc.name, reason: v.reason },
						freeze: true,
						freeze_message: __("Cancelling..."),
					})
					.then(() => frm.reload_doc()),
			__("Cancel {0}", [frm.doc.name]),
			__("Cancel Bill"),
		),
	);
}

function qe_headline(frm) {
	const esc = frappe.utils.escape_html;
	const parts = [];
	if (frm.doc.qe_grand_total) {
		parts.push(
			`<b>${__("Taxable")}:</b> ${format_currency(frm.doc.qe_total_taxable, "INR")} &nbsp; <b>${__("GST")}:</b> ${format_currency(frm.doc.qe_total_gst, "INR")}` +
				` &nbsp; <b>${__("Eligible ITC")}:</b> ${format_currency(frm.doc.qe_total_eligible_itc, "INR")} &nbsp; <b>${__("Paid")}:</b> ${format_currency(frm.doc.qe_grand_total, "INR")}`,
		);
	}
	if (frm.doc.docstatus === 1 && frm.doc.reconciliation_status) parts.push(`<b>${__("GSTR-2B")}:</b> ${esc(__(frm.doc.reconciliation_status))}`);
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
