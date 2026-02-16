"""
POS setup and configuration for WooCommerce-ERPNext integration.

Provides utilities to:
- Create/configure a POS Profile linked to WooCommerce
- Ensure barcode scanning works for WooCommerce products
- Handle POS Invoice submission with stock push to WooCommerce
- Validate POS warehouse consistency
"""

import frappe
from frappe import _
from frappe.utils import flt


def setup_pos_for_woocommerce(settings=None):
	"""Set up POS Profile and configuration for WooCommerce integration.

	Call this from WooCommerce Settings when enable_pos is checked.
	"""
	if settings is None:
		settings = frappe.get_single("WooCommerce Settings")

	if not settings.enable_pos:
		return

	if settings.pos_profile:
		_configure_existing_pos_profile(settings)
	else:
		_create_pos_profile(settings)


def _create_pos_profile(settings):
	"""Create a new POS Profile configured for WooCommerce."""
	if frappe.db.exists("POS Profile", {"name": "WooCommerce POS"}):
		settings.pos_profile = "WooCommerce POS"
		settings.save(ignore_permissions=True)
		_configure_existing_pos_profile(settings)
		return

	company = settings.company
	warehouse = settings.pos_warehouse or settings.warehouse

	# Get default income and expense accounts
	default_income = frappe.db.get_value("Company", company, "default_income_account")
	default_expense = frappe.db.get_value("Company", company, "default_expense_account")
	cost_center = frappe.db.get_value("Company", company, "cost_center")
	write_off_account = frappe.db.get_value("Company", company, "write_off_account")

	pos_profile = frappe.new_doc("POS Profile")
	pos_profile.name = "WooCommerce POS"
	pos_profile.company = company
	pos_profile.warehouse = warehouse
	pos_profile.write_off_account = write_off_account
	pos_profile.write_off_cost_center = cost_center
	pos_profile.income_account = default_income
	pos_profile.expense_account = default_expense
	pos_profile.cost_center = cost_center
	pos_profile.selling_price_list = settings.price_list

	# Add Cash payment method
	mode_of_payment = _get_or_create_cash_payment()
	pos_profile.append(
		"payments",
		{
			"mode_of_payment": mode_of_payment,
			"default": 1,
		},
	)

	pos_profile.flags.ignore_permissions = True
	pos_profile.flags.ignore_mandatory = True
	pos_profile.save()

	settings.pos_profile = pos_profile.name
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _configure_existing_pos_profile(settings):
	"""Ensure the POS Profile has the correct warehouse and price list."""
	pos_profile = frappe.get_doc("POS Profile", settings.pos_profile)
	warehouse = settings.pos_warehouse or settings.warehouse

	changed = False
	if pos_profile.warehouse != warehouse:
		pos_profile.warehouse = warehouse
		changed = True
	if pos_profile.selling_price_list != settings.price_list:
		pos_profile.selling_price_list = settings.price_list
		changed = True

	if changed:
		pos_profile.flags.ignore_permissions = True
		pos_profile.save()
		frappe.db.commit()


def _get_or_create_cash_payment():
	"""Get or create a Cash mode of payment."""
	if frappe.db.exists("Mode of Payment", "Cash"):
		return "Cash"
	mop = frappe.new_doc("Mode of Payment")
	mop.mode_of_payment = "Cash"
	mop.type = "Cash"
	mop.flags.ignore_permissions = True
	mop.save()
	return mop.name


def ensure_barcodes_for_pos(settings=None):
	"""Ensure all WooCommerce-synced items have barcodes set up for POS scanning.

	This checks all items with woocommerce_id and ensures they have at least
	one barcode (from their SKU) so they can be scanned at the POS terminal.
	"""
	if settings is None:
		settings = frappe.get_single("WooCommerce Settings")

	items = frappe.get_all(
		"Item",
		filters={"woocommerce_id": ["is", "set"], "disabled": 0},
		fields=["name", "item_code"],
	)

	for item_data in items:
		item = frappe.get_doc("Item", item_data.name)
		if not item.barcodes:
			item.append("barcodes", {"barcode": item.item_code, "barcode_type": "EAN"})
			item.flags.ignore_permissions = True
			item.save()

	frappe.db.commit()


def get_pos_stock_summary(warehouse=None):
	"""Get a stock summary for POS-relevant items.

	Returns items with their current quantities in the POS warehouse,
	useful for POS dashboards and stock checks.
	"""
	settings = frappe.get_single("WooCommerce Settings")
	if not warehouse:
		warehouse = settings.pos_warehouse or settings.warehouse

	items = frappe.get_all(
		"Item",
		filters={"woocommerce_id": ["is", "set"], "disabled": 0},
		fields=["name", "item_code", "item_name", "woocommerce_id"],
	)

	summary = []
	for item in items:
		qty = flt(
			frappe.db.get_value(
				"Bin", {"item_code": item.item_code, "warehouse": warehouse}, "actual_qty"
			)
		)
		price = flt(
			frappe.db.get_value(
				"Item Price",
				{
					"item_code": item.item_code,
					"price_list": settings.price_list,
					"selling": 1,
				},
				"price_list_rate",
			)
		)
		summary.append(
			{
				"item_code": item.item_code,
				"item_name": item.item_name,
				"woocommerce_id": item.woocommerce_id,
				"available_qty": qty,
				"price": price,
				"warehouse": warehouse,
			}
		)

	return summary
