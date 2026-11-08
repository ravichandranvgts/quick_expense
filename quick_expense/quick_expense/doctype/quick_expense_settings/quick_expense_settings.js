// Copyright (c) 2026, VGTS and contributors
// For license information, please see license.txt

frappe.ui.form.on("Quick Expense Settings", {
	refresh(frm) {
		if (frm.doc.overrides_disabled) {
			frm.dashboard.set_headline_alert(
				`<div class="qe-red-banner">${__(
					"Journal Entry report overrides are OFF"
				)}: ${frappe.utils.escape_html(frm.doc.overrides_disabled_reason || "")}</div>`,
				"red"
			);
		}
		frm.add_custom_button(__("Run Setup Check"), () => {
			frappe.call({
				method: "quick_expense.services.setup_service.run_setup_check",
				freeze: true,
				freeze_message: __("Checking every company and GSTIN..."),
				callback: ({ message }) => {
					frm.doc.setup_check_result = JSON.stringify(message || []);
					qe_render_setup_check(frm);
					frm.scroll_to_field("setup_check_html");
				},
			});
		});
		frm.add_custom_button(__("Run Compatibility Self-test"), () => {
			frappe.call({
				method: "quick_expense.overrides.compatibility_guard.run_self_test_now",
				freeze: true,
				callback: ({ message }) => {
					frappe.msgprint({
						title: __("Self-test {0}", [message.status]),
						indicator: message.status === "failed" ? "red" : "green",
						message:
							__("India Compliance version {0}", [message.version]) +
							((message.problems || []).length
								? "<br>" + message.problems.map(frappe.utils.escape_html).join("<br>")
								: ""),
					});
					frm.reload_doc();
				},
			});
		});
		qe_render_setup_check(frm);
		qe_company_gstin_options(frm);
	},
});

function qe_render_setup_check(frm) {
	let issues = [];
	try {
		issues = JSON.parse(frm.doc.setup_check_result || "[]");
	} catch (e) {
		issues = [];
	}
	const wrapper = frm.get_field("setup_check_html").$wrapper;
	if (!frm.doc.setup_check_result) {
		wrapper.html(
			`<p class="text-muted">${__(
				"Click Run Setup Check to list what is missing for each company and GSTIN."
			)}</p>`
		);
		return;
	}
	if (!issues.length) {
		wrapper.html(`<p class="text-success qe-big">${__("Everything needed is set up.")}</p>`);
		return;
	}
	const rows = issues
		.map(
			(i) => `<tr>
		<td>${frappe.utils.escape_html(i.company || "")}</td><td>${frappe.utils.escape_html(i.gstin || "")}</td>
		<td>${frappe.utils.escape_html(i.area)}</td><td>${frappe.utils.escape_html(i.message)}</td>
		<td>${i.link ? `<a href="${encodeURI(i.link)}">${__("Fix")}</a>` : ""}</td></tr>`
		)
		.join("");
	wrapper.html(`<table class="table table-bordered qe-setup-table"><thead><tr>
		<th>${__("Company")}</th><th>${__("GSTIN")}</th><th>${__("Area")}</th><th>${__(
		"What is missing"
	)}</th><th></th>
		</tr></thead><tbody>${rows}</tbody></table>`);
}

function qe_company_gstin_options(frm) {
	const companies = [
		...new Set(
			[
				...(frm.doc.company_settings || []),
				...(frm.doc.user_defaults || []),
				...(frm.doc.gst_lock_dates || []),
				...(frm.doc.approval_rules || []),
			]
				.map((r) => r.company)
				.filter(Boolean)
		),
	];
	Promise.all(
		companies.map((company) => frappe.xcall("quick_expense.api.get_company_gstins", { company }))
	).then((lists) => {
		const all = lists.flat().map((g) => g.gstin);
		[
			["company_settings", "default_company_gstin"],
			["user_defaults", "company_gstin"],
			["gst_lock_dates", "company_gstin"],
			["approval_rules", "company_gstin"],
		].forEach(([table, field]) =>
			frm.fields_dict[table].grid.update_docfield_property(field, "options", all.join("\n"))
		);
	});
}
