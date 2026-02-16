"""
Product synchronization between WooCommerce and ERPNext.

Supports bidirectional sync of products/items including:
- Name, SKU, description, price, weight
- Barcode mapping for POS usage
- WooCommerce product ID tracking via custom field
"""

import traceback

import frappe
from frappe import _
from frappe.utils import cstr, flt, now_datetime

from erpnext.erpnext_integrations.connectors.woocommerce_connection import (
	WooCommerceConnector,
)
from erpnext.erpnext_integrations.doctype.woocommerce_log.woocommerce_log import (
	create_woocommerce_log,
)


def run_product_sync():
	"""Entry point for product synchronization."""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled or not settings.sync_products:
		return

	direction = settings.product_sync_direction
	connector = WooCommerceConnector(settings)

	if direction in ("WooCommerce to ERPNext", "Bidirectional"):
		_sync_wc_products_to_erpnext(connector, settings)

	if direction in ("ERPNext to WooCommerce", "Bidirectional"):
		_sync_erpnext_items_to_wc(connector, settings)

	settings.reload()
	settings.last_product_sync = now_datetime()
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _sync_wc_products_to_erpnext(connector, settings):
	"""Pull products from WooCommerce and create/update Items in ERPNext."""
	page = 1
	while True:
		try:
			products = connector.get_products(page=page, per_page=100)
		except Exception:
			create_woocommerce_log(
				title="Product Fetch Failed",
				status="Failed",
				method="product_sync.get_products",
				error=str(traceback.format_exc()),
			)
			break

		if not products:
			break

		for product in products:
			try:
				_create_or_update_item(product, settings)
				create_woocommerce_log(
					title=f"Synced product: {product.get('name')}",
					status="Success",
					method="product_sync.wc_to_erpnext",
					reference_doctype="Item",
					woocommerce_id=product.get("id"),
				)
			except Exception:
				create_woocommerce_log(
					title=f"Failed to sync product: {product.get('name')}",
					status="Failed",
					method="product_sync.wc_to_erpnext",
					woocommerce_id=product.get("id"),
					request_data=product,
					error=str(traceback.format_exc()),
				)

		if len(products) < 100:
			break
		page += 1


def _create_or_update_item(product, settings):
	"""Create or update an ERPNext Item from a WooCommerce product."""
	wc_id = str(product.get("id"))
	sku = product.get("sku") or ""
	item_name = product.get("name", "")

	# Find existing item by woocommerce_id custom field or SKU
	item_code = _find_item_by_wc_id(wc_id) or _find_item_by_sku(sku)

	if item_code:
		item = frappe.get_doc("Item", item_code)
	else:
		item = frappe.new_doc("Item")
		item.item_code = sku or f"WC-{wc_id}"
		item.item_group = settings.default_item_group or "All Item Groups"

	item.item_name = item_name
	item.description = product.get("description") or item_name
	item.stock_uom = "Nos"

	# Weight
	weight = flt(product.get("weight"))
	if weight:
		item.weight_per_unit = weight
		item.weight_uom = "Kg"

	# Set custom field for WooCommerce ID tracking
	item.set("woocommerce_id", wc_id)

	# Barcode from SKU (for POS scanning)
	if sku and not _item_has_barcode(item, sku):
		item.append("barcodes", {"barcode": sku, "barcode_type": "EAN"})

	item.flags.ignore_permissions = True
	item.save()

	# Update price
	_update_item_price(item.item_code, product, settings)

	frappe.db.commit()
	return item.item_code


def _find_item_by_wc_id(wc_id):
	"""Find an ERPNext Item by its WooCommerce product ID custom field."""
	result = frappe.db.get_value("Item", {"woocommerce_id": wc_id}, "name")
	return result


def _find_item_by_sku(sku):
	"""Find an ERPNext Item by SKU (item_code)."""
	if not sku:
		return None
	if frappe.db.exists("Item", sku):
		return sku
	return None


def _item_has_barcode(item, barcode):
	"""Check if the item already has a given barcode."""
	for row in item.get("barcodes", []):
		if row.barcode == barcode:
			return True
	return False


def _update_item_price(item_code, product, settings):
	"""Create or update Item Price from WooCommerce product price."""
	regular_price = flt(product.get("regular_price") or product.get("price"))
	if not regular_price:
		return

	existing = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": settings.price_list, "selling": 1},
		"name",
	)

	if existing:
		frappe.db.set_value("Item Price", existing, "price_list_rate", regular_price)
	else:
		price_doc = frappe.new_doc("Item Price")
		price_doc.item_code = item_code
		price_doc.price_list = settings.price_list
		price_doc.price_list_rate = regular_price
		price_doc.selling = 1
		price_doc.flags.ignore_permissions = True
		price_doc.save()


def _sync_erpnext_items_to_wc(connector, settings):
	"""Push ERPNext Items to WooCommerce."""
	items = frappe.get_all(
		"Item",
		filters={"woocommerce_id": ["is", "set"], "disabled": 0},
		fields=["name", "item_name", "description", "weight_per_unit", "woocommerce_id"],
	)

	for item in items:
		try:
			item_doc = frappe.get_doc("Item", item.name)
			data = _build_wc_product_data(item_doc, settings)
			wc_id = item.woocommerce_id

			if wc_id:
				connector.update_product(int(wc_id), data)
			else:
				result = connector.create_product(data)
				frappe.db.set_value("Item", item.name, "woocommerce_id", str(result["id"]))

			create_woocommerce_log(
				title=f"Pushed item: {item.item_name}",
				status="Success",
				method="product_sync.erpnext_to_wc",
				reference_doctype="Item",
				reference_name=item.name,
				woocommerce_id=wc_id,
			)
		except Exception:
			create_woocommerce_log(
				title=f"Failed to push item: {item.item_name}",
				status="Failed",
				method="product_sync.erpnext_to_wc",
				reference_doctype="Item",
				reference_name=item.name,
				error=str(traceback.format_exc()),
			)

	frappe.db.commit()


def _build_wc_product_data(item_doc, settings):
	"""Build WooCommerce product payload from an ERPNext Item."""
	data = {
		"name": item_doc.item_name,
		"type": "simple",
		"description": item_doc.description or item_doc.item_name,
		"sku": item_doc.item_code,
		"manage_stock": True,
	}

	if item_doc.weight_per_unit:
		data["weight"] = str(item_doc.weight_per_unit)

	# Get price
	price = frappe.db.get_value(
		"Item Price",
		{"item_code": item_doc.item_code, "price_list": settings.price_list, "selling": 1},
		"price_list_rate",
	)
	if price:
		data["regular_price"] = str(flt(price))

	# Get current stock for the configured warehouse
	actual_qty = flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": item_doc.item_code, "warehouse": settings.warehouse},
			"actual_qty",
		)
	)
	data["stock_quantity"] = int(actual_qty)

	return data
