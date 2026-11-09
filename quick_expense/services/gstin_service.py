"""Validate, fetch and cache GSTIN details (prompt sections 2A and 6).

The India Compliance public GSTIN API is the primary source. When it is
switched off, out of credits or down, the state and PAN are derived from the
GSTIN itself and the user types the legal name; that path never blocks entry,
it only forces approval (decided in approval_service).
"""

import re

import frappe
from frappe import _
from frappe.utils import cint
from india_compliance.gst_india.constants import STATE_NUMBERS

from quick_expense.constants import GSTIN_STATUS_BLOCKING

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][Z1-9ABD-J][0-9A-Z]$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
CODE_POINTS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATE_BY_CODE = {code: state for state, code in STATE_NUMBERS.items()}

# India Compliance "dty" (taxpayer type) -> our GST Treatment
REGISTRATION_TYPES = {
	"Regular": "Registered Regular",
	"Composition": "Registered Composition",
	"SEZ Unit": "SEZ",
	"SEZ Developer": "SEZ",
	"Input Service Distributor (ISD)": "Registered Regular",
}

# constitution of business values that are a body corporate (Companies Act 2(11))
BODY_CORPORATE_CONSTITUTIONS = (
	"Public Limited Company",
	"Private Limited Company",
	"Public Sector Undertaking",
	"Unlimited Company",
	"Limited Liability Partnership",
	"Foreign Company",
	"Foreign Limited Liability Partnership",
)


class GSTINError(frappe.ValidationError):
	pass


def normalise_gstin(gstin: str | None) -> str:
	return re.sub(r"\s+", "", (gstin or "")).upper()


def checksum_char(first_14: str) -> str:
	"""GSTIN check digit (Luhn mod 36 variant used by GSTN)."""
	factor, total, mod = 1, 0, len(CODE_POINTS)
	for char in first_14:
		digit = factor * CODE_POINTS.index(char)
		total += digit // mod + digit % mod
		factor = 2 if factor == 1 else 1
	return CODE_POINTS[(mod - total % mod) % mod]


def get_validation_error(gstin: str) -> str | None:
	"""Return a plain-English problem with the GSTIN, or None when it is valid."""
	if len(gstin) != 15:
		return _("GSTIN {0} must have exactly 15 characters; it has {1}.").format(gstin, len(gstin))
	if not GSTIN_PATTERN.match(gstin):
		return _(
			"GSTIN {0} is not in the correct format (2-digit state code, 10-character PAN, entity number, Z, check character)."
		).format(gstin)
	if gstin[:2] not in STATE_BY_CODE or gstin[:2] == "96":
		return _("GSTIN {0} starts with {1}, which is not a valid state code.").format(gstin, gstin[:2])
	if any(c not in CODE_POINTS for c in gstin) or checksum_char(gstin[:14]) != gstin[14]:
		return _("GSTIN {0} has a wrong check character. Please check that it was typed correctly.").format(
			gstin
		)
	return None


def validate_gstin(gstin: str | None, label: str = "Supplier GSTIN") -> str:
	gstin = normalise_gstin(gstin)
	if error := get_validation_error(gstin):
		frappe.throw(error, GSTINError, title=_("Invalid {0}").format(label))
	return gstin


def state_from_gstin(gstin: str) -> str:
	return STATE_BY_CODE.get(gstin[:2], "")


def pan_from_gstin(gstin: str) -> str:
	pan = gstin[2:12]
	return pan if PAN_PATTERN.match(pan) else ""


def constitution_from_pan(pan: str) -> str:
	"""4th PAN character: C = company. Everything else is treated as non-body-corporate.

	LLPs carry an F (firm) PAN, so they come out as non-body-corporate here; the
	API's constitution of business corrects that when it is available.
	"""
	if not pan:
		return ""
	return "Body Corporate" if pan[3] == "C" else "Non-Body-Corporate"


# ----------------------------------------------------------------------------
# Company GSTIN helpers


def get_company_gstins(company: str) -> list[dict]:
	"""GST addresses of one company: [{gstin, address, state, state_code}]"""
	address = frappe.qb.DocType("Address")
	link = frappe.qb.DocType("Dynamic Link")
	rows = (
		frappe.qb.from_(address)
		.join(link)
		.on(link.parent == address.name)
		.select(address.name.as_("address"), address.gstin, address.gst_state, address.gst_state_number)
		.where(link.link_doctype == "Company")
		.where(link.link_name == company)
		.where(link.parenttype == "Address")
		.where(address.gstin.isnotnull())
		.where(address.gstin != "")
		.where(address.disabled == 0)
		.orderby(address.is_primary_address, order=frappe.qb.desc)
		.orderby(address.creation)
	).run(as_dict=True)

	seen, result = set(), []
	for row in rows:
		if row.gstin in seen:
			continue
		seen.add(row.gstin)
		result.append(
			frappe._dict(
				gstin=row.gstin,
				address=row.address,
				state=row.gst_state or state_from_gstin(row.gstin),
				state_code=row.gst_state_number or row.gstin[:2],
			)
		)
	return result


def get_company_gstin_address(company: str, company_gstin: str) -> str | None:
	for row in get_company_gstins(company):
		if row.gstin == company_gstin:
			return row.address
	return None


def get_all_company_gstins() -> dict[str, str]:
	"""{gstin: company} for every company in the system."""
	address = frappe.qb.DocType("Address")
	link = frappe.qb.DocType("Dynamic Link")
	rows = (
		frappe.qb.from_(address)
		.join(link)
		.on(link.parent == address.name)
		.select(address.gstin, link.link_name)
		.where(link.link_doctype == "Company")
		.where(link.parenttype == "Address")
		.where(address.gstin.isnotnull())
		.where(address.gstin != "")
	).run(as_dict=True)
	return {row.gstin: row.link_name for row in rows}


def get_company_pan(company: str, company_gstin: str | None = None) -> str:
	pan = frappe.db.get_value("Company", company, "pan") if frappe.db.has_column("Company", "pan") else None
	if not pan and company_gstin:
		pan = pan_from_gstin(company_gstin)
	return (pan or "").upper()


def validate_not_own_gstin(supplier_gstin: str, company: str, company_gstin: str):
	"""Section 2A: inter-branch / inter-company supplies must use the proper process."""
	owner_company = get_all_company_gstins().get(supplier_gstin)
	if owner_company:
		frappe.throw(
			_(
				"Supplier GSTIN {0} belongs to company {1} in this system. Supplies between branches or companies of the same group must be booked through the proper inter-company process, not Quick Expense."
			).format(supplier_gstin, owner_company),
			GSTINError,
			title=_("Own GSTIN Used as Supplier"),
		)

	company_pan = get_company_pan(company, company_gstin)
	if company_pan and pan_from_gstin(supplier_gstin) == company_pan:
		frappe.throw(
			_(
				"Supplier GSTIN {0} has the same PAN ({1}) as company {2}. A distinct person supply between your own registrations cannot be booked in Quick Expense."
			).format(supplier_gstin, company_pan, company),
			GSTINError,
			title=_("Same PAN as Company"),
		)


# ----------------------------------------------------------------------------
# Fetch and cache


def get_settings():
	return frappe.get_cached_doc("Quick Expense Settings")


class GSTINAPIUnavailable(Exception):
	pass


def _call_api(gstin: str) -> frappe._dict:
	"""GSTIN details through India Compliance (it archives portal responses, so repeat
	lookups do not spend API credits). Kept in one place for the compatibility guard."""
	from india_compliance.gst_india.utils import is_api_enabled
	from india_compliance.gst_india.utils.gstin_info import _get_gstin_info

	if not is_api_enabled():
		raise GSTINAPIUnavailable(_("India Compliance API is not enabled in GST Settings."))
	return _get_gstin_info(gstin, throw_error=True)


def get_gstin_details(gstin: str, force_refresh: bool = False) -> frappe._dict:
	"""Details for a supplier GSTIN. Never raises because the API is down.

	``addresses`` holds every registered place of business (principal first) in
	ERPNext Address field names."""
	gstin = validate_gstin(gstin)
	cache = getattr(frappe.local, "qe_gstin_details", None)
	if cache is None:
		cache = frappe.local.qe_gstin_details = {}
	if not force_refresh and gstin in cache:
		return frappe._dict(cache[gstin])

	pan = pan_from_gstin(gstin)
	base = frappe._dict(
		gstin=gstin,
		state=state_from_gstin(gstin),
		state_code=gstin[:2],
		pan=pan,
		body_corporate=constitution_from_pan(pan),
		addresses=[],
	)
	if not cint(get_settings().use_gstin_api):
		return _without_api(base, _("GSTIN API is switched off in Quick Expense Settings."))

	try:
		info = _call_api(gstin)
		if not info or (not info.get("legal_name") and (info.get("status") or "").title() in ("", "Invalid")):
			frappe.throw(_("GSTIN {0} was not found on the GST portal.").format(gstin), GSTINError)
	except GSTINError:
		raise
	except Exception as exc:
		frappe.clear_last_message()
		if not isinstance(exc, GSTINAPIUnavailable):
			frappe.log_error(
				title=f"Quick Expense: GSTIN API failed for {gstin}", message=frappe.get_traceback()
			)
		return _without_api(
			base,
			_("GSTIN details could not be fetched from the GST portal ({0}).").format(
				str(exc)[:140] or exc.__class__.__name__
			),
		)

	addresses = [dict(address) for address in info.get("all_addresses") or []]
	for index, address in enumerate(addresses):
		address["is_principal"] = index == 0
	principal = addresses[0] if addresses else {}
	details = frappe._dict(
		base,
		legal_name=info.get("legal_name") or "",
		trade_name=info.get("trade_name") or "",
		business_name=info.get("business_name") or "",
		status=info.get("status") or "",
		registration_type=info.get("gst_category") or "",
		state=principal.get("state") or base.state,
		address=", ".join(
			part
			for part in (
				principal.get("address_line1"),
				principal.get("address_line2"),
				principal.get("city"),
			)
			if part
		),
		pincode=principal.get("pincode") or "",
		addresses=addresses,
		source="API",
	)
	cache[gstin] = dict(details)
	return details


def _without_api(base: frappe._dict, reason: str) -> frappe._dict:
	"""No portal lookup: use the Supplier master when it already has this GSTIN,
	otherwise the user types the name."""
	if details := from_supplier_master(base):
		return details
	return frappe._dict(
		base,
		source="Manual",
		message=_(
			"{0} Type the supplier legal name, or ask your administrator to add the India Compliance API Secret in GST Settings."
		).format(reason),
	)


def from_supplier_master(base: frappe._dict) -> frappe._dict | None:
	"""Name and address of an existing Supplier with this GSTIN (enabled suppliers first)."""
	supplier = frappe.db.get_value(
		"Supplier",
		{"gstin": base.gstin},
		["name", "supplier_name", "gst_category", "supplier_primary_address"],
		as_dict=True,
		order_by="disabled asc, creation asc",
	)
	if not supplier:
		return None
	address = frappe._dict()
	address_name = supplier.supplier_primary_address or frappe.db.get_value(
		"Address", {"gstin": base.gstin, "disabled": 0}, "name", order_by="is_primary_address desc"
	)
	if address_name:
		address = (
			frappe.db.get_value(
				"Address",
				address_name,
				["address_line1", "address_line2", "city", "state", "pincode"],
				as_dict=True,
			)
			or frappe._dict()
		)
	return frappe._dict(
		base,
		legal_name=supplier.supplier_name,
		trade_name=supplier.supplier_name,
		business_name=supplier.supplier_name,
		status="",
		registration_type=supplier.gst_category or "",
		state=address.state or base.state,
		address=", ".join(
			part for part in (address.address_line1, address.address_line2, address.city) if part
		),
		pincode=address.pincode or "",
		supplier=supplier.name,
		source="Supplier Master",
		message=_("Supplier details filled from Supplier {0}.").format(supplier.name),
	)


def is_blocking_status(status: str | None) -> bool:
	return bool(status) and status.strip().title() in GSTIN_STATUS_BLOCKING
