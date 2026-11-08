"""Supplier onboarding from a Quick Expense Journal Entry.

Creates (or reuses) an ERPNext Supplier for the bill's supplier with everything
the GSTIN gives us: legal / trade name, GSTIN, PAN, GST category, constitution
and every registered place of business as an Address. The Journal Entry itself
is not changed apart from a reference to the supplier; all Quick Expense
Journal Entries of the same GSTIN get the same reference.

Source of the data, best first:
1. the GST portal response (India Compliance API), cached by gstin_service;
2. what was captured on the bill (Journal Entry fields)
   when the API is not available.
"""

import frappe
from frappe import _
from frappe.utils import cint

from quick_expense.constants import COMPOSITION, IC_GST_CATEGORY, JE_VOUCHER_TYPE, OVERSEAS, REGISTERED, SEZ
from quick_expense.services import gstin_service

PINCODE_LENGTH = 6


class SupplierOnboardingError(frappe.ValidationError):
	pass


# ----------------------------------------------------------------------------
# collect


def _source(journal_entry: str):
	je = frappe.get_doc("Journal Entry", journal_entry)
	if je.voucher_type != JE_VOUCHER_TYPE:
		frappe.throw(
			_("Only Journal Entries of type Quick Expense can onboard a supplier."), SupplierOnboardingError
		)
	if not (je.qe_supplier_legal_name or je.qe_supplier_gstin):
		frappe.throw(_("This Journal Entry has no supplier details."), SupplierOnboardingError)
	return je


def get_supplier_data(journal_entry: str) -> frappe._dict:
	"""Everything the new supplier will get, shown to the user before creating it."""
	je = _source(journal_entry)
	gstin = gstin_service.normalise_gstin(je.qe_supplier_gstin)
	treatment = je.qe_gst_treatment or (REGISTERED if gstin else "Unregistered")

	data = frappe._dict(
		journal_entry=je.name,
		gstin=gstin,
		legal_name=je.qe_supplier_legal_name or "",
		trade_name=je.qe_supplier_trade_name or "",
		pan=gstin_service.pan_from_gstin(gstin) if gstin else "",
		gst_category=IC_GST_CATEGORY.get(treatment, "Unregistered"),
		constitution="",
		status=je.qe_gstin_status or "",
		state=je.qe_supplier_state or "",
		addresses=[],
		source="Bill",
	)

	if gstin:
		details = gstin_service.get_gstin_details(gstin)
		if details.source == "API":
			data.source = "GST portal"
			data.legal_name = details.legal_name or data.legal_name
			data.trade_name = details.trade_name or data.trade_name
			data.status = details.status or data.status
			data.state = details.state or data.state
			data.business_name = details.business_name
			if details.registration_type in (REGISTERED, COMPOSITION, SEZ):
				data.gst_category = IC_GST_CATEGORY[details.registration_type]
			data.addresses = details.addresses
		data.constitution = gstin_service.constitution_from_pan(data.pan)

	if not data.addresses and treatment != OVERSEAS:
		lines = [
			part.strip()
			for part in (je.qe_supplier_address or "").replace("\n", ",").split(",")
			if part.strip()
		]
		data.addresses = [
			{
				"address_line1": (", ".join(lines[:2]) or data.legal_name)[:140],
				"address_line2": ", ".join(lines[2:-1])[:140] if len(lines) > 3 else "",
				"city": (lines[-1] if len(lines) > 1 else data.state)[:140],
				"state": data.state,
				"pincode": je.qe_supplier_pincode or "",
				"country": "India",
				"is_principal": True,
			}
		]

	# India Compliance's business name: the trade name for proprietors / HUFs, else the legal name
	data.supplier_name = (data.get("business_name") or data.legal_name or data.trade_name).strip()
	data.existing_supplier = find_existing_supplier(gstin, data.supplier_name, data.gst_category)
	return data


def find_existing_supplier(gstin, supplier_name, gst_category):
	if gstin:
		return frappe.db.get_value(
			"Supplier", {"gstin": gstin, "disabled": 0}, "name", order_by="creation asc"
		)
	return frappe.db.get_value(
		"Supplier", {"supplier_name": supplier_name, "gst_category": gst_category, "disabled": 0}, "name"
	)


def supplier_exists_for(je) -> bool:
	"""Cheap check for the form: is this bill's supplier already in the Supplier master?"""
	if je.get("qe_supplier") and frappe.db.exists("Supplier", je.qe_supplier):
		return True
	gstin = gstin_service.normalise_gstin(je.get("qe_supplier_gstin"))
	if gstin:
		return bool(frappe.db.exists("Supplier", {"gstin": gstin, "disabled": 0}))
	name = (je.get("qe_supplier_legal_name") or "").strip()
	return bool(name) and bool(frappe.db.exists("Supplier", {"supplier_name": name, "disabled": 0}))


def set_onload(doc, method=None):
	"""Journal Entry onload: tells the form whether to offer Create Supplier."""
	if doc.voucher_type == JE_VOUCHER_TYPE and (
		doc.get("qe_supplier_gstin") or doc.get("qe_supplier_legal_name")
	):
		doc.set_onload("qe_supplier_exists", supplier_exists_for(doc))


# ----------------------------------------------------------------------------
# create


def _supplier_group():
	group = frappe.db.get_single_value("Buying Settings", "supplier_group")
	return group or frappe.db.get_value("Supplier Group", {"is_group": 0}, "name", order_by="lft asc")


def _unique_name(name, gstin):
	if not frappe.db.exists("Supplier", name):
		return name
	candidate = f"{name} - {gstin[:2]}" if gstin else f"{name} - 2"
	return (
		candidate
		if not frappe.db.exists("Supplier", candidate)
		else f"{name} - {gstin or frappe.generate_hash(length=4)}"
	)


def _create_supplier(data) -> str:
	supplier = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": _unique_name(data.supplier_name, data.gstin)[:140],
			"supplier_group": _supplier_group(),
			"supplier_type": "Company" if gstin_service_body_corporate(data) else "Individual",
			"country": None if data.gst_category == "Overseas" else "India",
			"gstin": data.gstin or "",
			"pan": data.pan or "",
			"gst_category": data.gst_category,
			"default_currency": "INR",
			"supplier_details": "\n".join(
				part
				for part in (
					data.legal_name and _("Legal name: {0}").format(data.legal_name),
					data.trade_name and _("Trade name: {0}").format(data.trade_name),
					data.constitution and _("Constitution (from PAN): {0}").format(data.constitution),
					data.status and _("GSTIN status: {0}").format(data.status),
					_("Onboarded from {0}").format(data.journal_entry),
				)
				if part
			),
		}
	)
	supplier.insert()
	return supplier.name


def gstin_service_body_corporate(data):
	return data.constitution == "Body Corporate"


def _existing_address_keys(supplier):
	address = frappe.qb.DocType("Address")
	link = frappe.qb.DocType("Dynamic Link")
	rows = (
		frappe.qb.from_(address)
		.join(link)
		.on(link.parent == address.name)
		.select(address.address_line1, address.pincode, address.gstin)
		.where(link.link_doctype == "Supplier")
		.where(link.link_name == supplier)
	).run(as_dict=True)
	return {((r.address_line1 or "").strip().lower(), r.pincode or "") for r in rows}


def _create_addresses(supplier, data) -> list[str]:
	created = []
	existing = _existing_address_keys(supplier)
	has_primary = bool(existing)
	for place in data.addresses:
		key = ((place.get("address_line1") or "").strip().lower(), place.get("pincode") or "")
		if key in existing:
			continue
		pincode = place.get("pincode") or ""
		doc = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": data.supplier_name[:100],
				"address_type": "Billing" if place.get("is_principal") else "Office",
				"address_line1": place.get("address_line1") or data.supplier_name,
				"address_line2": place.get("address_line2") or "",
				"city": place.get("city") or place.get("state") or data.state or "-",
				"state": place.get("state") or data.state,
				"pincode": pincode if pincode.isdigit() and len(pincode) == PINCODE_LENGTH else "",
				"country": place.get("country") or "India",
				"gstin": data.gstin or "",
				"gst_category": data.gst_category,
				"is_primary_address": 1 if place.get("is_principal") and not has_primary else 0,
				"links": [{"link_doctype": "Supplier", "link_name": supplier}],
			}
		)
		try:
			doc.insert()
		except frappe.ValidationError:
			# India Compliance refuses a pincode outside the state; keep the address without it
			frappe.clear_last_message()
			doc.pincode = ""
			doc.insert()
		existing.add(key)
		created.append(doc.name)
	return created


def onboard_supplier(journal_entry: str) -> frappe._dict:
	frappe.has_permission("Supplier", "create", throw=True)
	data = get_supplier_data(journal_entry)
	if data.gstin:
		gstin_service.validate_gstin(data.gstin)
	if not data.supplier_name:
		frappe.throw(_("The supplier's name is missing on this Journal Entry."), SupplierOnboardingError)

	supplier = data.existing_supplier
	is_new = not supplier
	if is_new:
		supplier = _create_supplier(data)
	addresses = _create_addresses(supplier, data)

	link_journal_entries(supplier, data.gstin, journal_entry)
	return frappe._dict(supplier=supplier, is_new=cint(is_new), addresses=addresses, source=data.source)


def link_journal_entries(supplier, gstin, journal_entry):
	"""Reference the supplier on this and every other Quick Expense Journal Entry of the same GSTIN."""
	names = [journal_entry]
	if gstin:
		names += frappe.get_all(
			"Journal Entry",
			filters={
				"voucher_type": JE_VOUCHER_TYPE,
				"qe_supplier_gstin": gstin,
				"name": ("!=", journal_entry),
			},
			pluck="name",
		)
	for name in names:
		frappe.db.set_value("Journal Entry", name, "qe_supplier", supplier, update_modified=False)


# ----------------------------------------------------------------------------
# API (Journal Entry form)


@frappe.whitelist()
def preview_supplier(journal_entry: str):
	frappe.has_permission("Journal Entry", "read", doc=journal_entry, throw=True)
	return get_supplier_data(journal_entry)


@frappe.whitelist()
def create_supplier(journal_entry: str):
	frappe.has_permission("Journal Entry", "read", doc=journal_entry, throw=True)
	return onboard_supplier(journal_entry)
