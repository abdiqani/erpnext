"""
POS setup and configuration for WooCommerce-ERPNext integration.

Provides utilities to:
- Create/configure a POS Profile linked to WooCommerce
- Ensure barcode scanning works for WooCommerce products
- Handle POS Invoice submission with stock push to WooCommerce
- Validate POS warehouse consistency
- Support multiple payment methods (Cash, Card, Mobile)
"""

import frappe
from frappe import _
from frappe.utils import flt


def setup_pos_for_woocommerce(settings=None):
	"""Set up POS Profile and configuration for WooCommerce integration.

	Call this from WooCommerce Settings when enable_pos is checked.
	Creates a fully configured POS Profile with:
	- Correct warehouse and price list
	- Multiple payment methods (Cash, Card)
	- Item groups for WooCommerce products
	- Barcode scanning support
	"""
	if settings is None:
		settings = frappe.get_single("WooCommerce Settings")

	if not settings.enable_pos:
		return

	if settings.pos_profile:
		_configure_existing_pos_profile(settings)
	else:
		_create_pos_profile(settings)

	# Ensure all synced items have barcodes
	ensure_barcodes_for_pos(settings)


def _create_pos_profile(settings):
	"""Create a new POS Profile configured for WooCommerce."""
	if frappe.db.exists("POS Profile", {"name": "WooCommerce POS"}):
		settings.pos_profile = "WooCommerce POS"
		settings.save(ignore_permissions=True)
		_configure_existing_pos_profile(settings)
		return

	company = settings.company
	warehouse = settings.pos_warehouse or settings.warehouse

	# Get default accounts
	company_doc = frappe.get_cached_doc("Company", company)
	default_income = company_doc.default_income_account
	default_expense = company_doc.default_expense_account
	cost_center = company_doc.cost_center
	write_off_account = company_doc.write_off_account

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
	pos_profile.currency = frappe.db.get_value("Company", company, "default_currency")
	pos_profile.customer = _get_or_create_pos_customer(settings)

	# Add Cash payment method
	cash_mode = _get_or_create_payment_mode("Cash", "Cash")
	pos_profile.append(
		"payments",
		{
			"mode_of_payment": cash_mode,
			"default": 1,
		},
	)

	# Add Card payment method
	card_mode = _get_or_create_payment_mode("Credit Card", "Bank")
	pos_profile.append(
		"payments",
		{
			"mode_of_payment": card_mode,
			"default": 0,
		},
	)

	# Add applicable item groups
	item_group = settings.default_item_group or "All Item Groups"
	pos_profile.append("item_groups", {"item_group": item_group})

	pos_profile.flags.ignore_permissions = True
	pos_profile.flags.ignore_mandatory = True
	pos_profile.save()

	settings.pos_profile = pos_profile.name
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _configure_existing_pos_profile(settings):
	"""Ensure the POS Profile has the correct warehouse, price list, and payments."""
	pos_profile = frappe.get_doc("POS Profile", settings.pos_profile)
	warehouse = settings.pos_warehouse or settings.warehouse

	changed = False
	if pos_profile.warehouse != warehouse:
		pos_profile.warehouse = warehouse
		changed = True
	if pos_profile.selling_price_list != settings.price_list:
		pos_profile.selling_price_list = settings.price_list
		changed = True

	# Ensure at least one payment method exists
	if not pos_profile.payments:
		cash_mode = _get_or_create_payment_mode("Cash", "Cash")
		pos_profile.append("payments", {"mode_of_payment": cash_mode, "default": 1})
		changed = True

	if changed:
		pos_profile.flags.ignore_permissions = True
		pos_profile.save()
		frappe.db.commit()


def _get_or_create_payment_mode(name, payment_type):
	"""Get or create a Mode of Payment."""
	if frappe.db.exists("Mode of Payment", name):
		return name
	mop = frappe.new_doc("Mode of Payment")
	mop.mode_of_payment = name
	mop.type = payment_type
	mop.flags.ignore_permissions = True
	mop.save()
	return mop.name


def _get_or_create_pos_customer(settings):
	"""Get or create a default walk-in customer for POS transactions."""
	customer_name = "Walk-in Customer"
	if frappe.db.exists("Customer", customer_name):
		return customer_name

	customer = frappe.new_doc("Customer")
	customer.customer_name = customer_name
	customer.customer_type = "Individual"
	customer.customer_group = settings.default_customer_group or frappe.db.get_single_value(
		"Selling Settings", "customer_group"
	)
	customer.territory = frappe.db.get_single_value("Selling Settings", "territory")
	customer.flags.ignore_permissions = True
	customer.flags.ignore_mandatory = True
	customer.save()
	frappe.db.commit()
	return customer.name


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
		fields=["name", "item_code", "item_name", "woocommerce_id", "variant_of"],
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
				"is_variant": bool(item.variant_of),
			}
		)

	return summary


def validate_pos_setup(settings=None):
	"""Validate that POS is correctly configured for WooCommerce.

	Returns a dict with status and any issues found.
	"""
	if settings is None:
		settings = frappe.get_single("WooCommerce Settings")

	issues = []

	if not settings.enable_pos:
		return {"status": "disabled", "issues": ["POS integration is not enabled"]}

	if not settings.pos_profile:
		issues.append("No POS Profile configured")
	else:
		if not frappe.db.exists("POS Profile", settings.pos_profile):
			issues.append(f"POS Profile '{settings.pos_profile}' does not exist")
		else:
			pos_profile = frappe.get_doc("POS Profile", settings.pos_profile)
			if not pos_profile.payments:
				issues.append("POS Profile has no payment methods configured")
			if pos_profile.company != settings.company:
				issues.append("POS Profile company does not match WooCommerce Settings company")

	warehouse = settings.pos_warehouse or settings.warehouse
	if not frappe.db.exists("Warehouse", warehouse):
		issues.append(f"POS Warehouse '{warehouse}' does not exist")

	# Check for items without barcodes
	items_without_barcodes = frappe.db.sql("""
		SELECT i.name
		FROM `tabItem` i
		LEFT JOIN `tabItem Barcode` ib ON ib.parent = i.name
		WHERE i.woocommerce_id IS NOT NULL
		AND i.woocommerce_id != ''
		AND i.disabled = 0
		AND ib.name IS NULL
	""")
	if items_without_barcodes:
		issues.append(f"{len(items_without_barcodes)} synced items are missing barcodes for POS scanning")

	return {
		"status": "ok" if not issues else "issues_found",
		"issues": issues,
	}
