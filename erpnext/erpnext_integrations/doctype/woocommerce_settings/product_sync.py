"""
Product synchronization between WooCommerce and ERPNext.

Supports bidirectional sync of products/items including:
- Simple products: name, SKU, description, price, weight
- Variable products: creates Item Variants with attributes (size, color, etc.)
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
				product_type = product.get("type", "simple")

				if product_type == "variable":
					_sync_variable_product(product, connector, settings)
				else:
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


def _create_or_update_item(product, settings, variant_of=None, attributes=None):
	"""Create or update an ERPNext Item from a WooCommerce product.

	Args:
		product: WooCommerce product data dict
		settings: WooCommerce Settings document
		variant_of: Parent Item template code (for variants)
		attributes: List of dicts with attribute name/value (for variants)
	"""
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
	item.description = product.get("description") or product.get("short_description") or item_name
	item.stock_uom = "Nos"

	# Weight
	weight = flt(product.get("weight"))
	if weight:
		item.weight_per_unit = weight
		item.weight_uom = "Kg"

	# Set custom field for WooCommerce ID tracking
	item.set("woocommerce_id", wc_id)

	# Handle variant relationship
	if variant_of:
		item.variant_of = variant_of
		item.has_variants = 0
		if attributes:
			item.set("attributes", [])
			for attr in attributes:
				item.append("attributes", {
					"attribute": attr["attribute"],
					"attribute_value": attr["attribute_value"],
				})

	# Barcode from SKU (for POS scanning)
	if sku and not _item_has_barcode(item, sku):
		item.append("barcodes", {"barcode": sku, "barcode_type": "EAN"})

	item.flags.ignore_permissions = True
	item.flags.ignore_mandatory = True
	item.save()

	# Update price
	_update_item_price(item.item_code, product, settings)

	frappe.db.commit()
	return item.item_code


def _sync_variable_product(product, connector, settings):
	"""Sync a WooCommerce variable product and its variations as ERPNext Item Variants.

	WooCommerce variable products have:
	- A parent product with type="variable" and attributes defined
	- Child variations with specific attribute values, their own SKU/price/stock

	This maps to ERPNext's:
	- Item Template (has_variants=1) with Item Attributes
	- Item Variants (variant_of=template) with specific attribute values
	"""
	wc_id = str(product["id"])
	product_name = product.get("name", "")

	# Ensure Item Attributes exist in ERPNext
	wc_attributes = product.get("attributes", [])
	_ensure_item_attributes(wc_attributes)

	# Create or update the template item
	template_code = _create_or_update_template(product, wc_attributes, settings)

	# Fetch and sync all variations
	page = 1
	while True:
		try:
			variations = connector.get_product_variations(int(wc_id), page=page, per_page=100)
		except Exception:
			create_woocommerce_log(
				title=f"Failed to fetch variations for {product_name}",
				status="Failed",
				method="product_sync.get_variations",
				woocommerce_id=wc_id,
				error=str(traceback.format_exc()),
			)
			break

		if not variations:
			break

		for variation in variations:
			try:
				attributes = _map_variation_attributes(variation, wc_attributes)
				_create_or_update_item(variation, settings, variant_of=template_code, attributes=attributes)
			except Exception:
				create_woocommerce_log(
					title=f"Failed variation #{variation.get('id')} of {product_name}",
					status="Failed",
					method="product_sync.sync_variation",
					woocommerce_id=variation.get("id"),
					error=str(traceback.format_exc()),
				)

		if len(variations) < 100:
			break
		page += 1


def _ensure_item_attributes(wc_attributes):
	"""Ensure ERPNext Item Attributes exist for each WooCommerce product attribute.

	WooCommerce attributes look like:
	[{"id": 1, "name": "Color", "options": ["Red", "Blue", "Green"]}, ...]

	Creates matching Item Attribute documents in ERPNext if they don't exist.
	"""
	for attr in wc_attributes:
		if not attr.get("variation"):
			continue  # Skip non-variation attributes (display-only)

		attr_name = attr["name"]
		options = attr.get("options", [])

		if not frappe.db.exists("Item Attribute", attr_name):
			attribute_doc = frappe.new_doc("Item Attribute")
			attribute_doc.attribute_name = attr_name
			for opt in options:
				attribute_doc.append("item_attribute_values", {
					"attribute_value": opt,
					"abbr": opt[:3].upper(),
				})
			attribute_doc.flags.ignore_permissions = True
			attribute_doc.save()
		else:
			# Ensure all option values exist
			attribute_doc = frappe.get_doc("Item Attribute", attr_name)
			existing_values = {v.attribute_value for v in attribute_doc.item_attribute_values}
			changed = False
			for opt in options:
				if opt not in existing_values:
					attribute_doc.append("item_attribute_values", {
						"attribute_value": opt,
						"abbr": opt[:3].upper(),
					})
					changed = True
			if changed:
				attribute_doc.flags.ignore_permissions = True
				attribute_doc.save()

	frappe.db.commit()


def _create_or_update_template(product, wc_attributes, settings):
	"""Create or update the Item Template for a variable product."""
	wc_id = str(product["id"])
	sku = product.get("sku") or ""
	item_name = product.get("name", "")
	item_code = _find_item_by_wc_id(wc_id) or _find_item_by_sku(sku) or sku or f"WC-{wc_id}"

	if frappe.db.exists("Item", item_code):
		item = frappe.get_doc("Item", item_code)
	else:
		item = frappe.new_doc("Item")
		item.item_code = item_code
		item.item_group = settings.default_item_group or "All Item Groups"

	item.item_name = item_name
	item.description = product.get("description") or item_name
	item.stock_uom = "Nos"
	item.has_variants = 1
	item.set("woocommerce_id", wc_id)

	# Set variant attributes on template
	item.set("attributes", [])
	for attr in wc_attributes:
		if attr.get("variation"):
			item.append("attributes", {"attribute": attr["name"]})

	weight = flt(product.get("weight"))
	if weight:
		item.weight_per_unit = weight
		item.weight_uom = "Kg"

	item.flags.ignore_permissions = True
	item.flags.ignore_mandatory = True
	item.save()
	frappe.db.commit()

	return item.item_code


def _map_variation_attributes(variation, parent_attributes):
	"""Map WooCommerce variation attributes to ERPNext format.

	Variation attributes look like:
	[{"id": 1, "name": "Color", "option": "Red"}, ...]

	Returns list of dicts: [{"attribute": "Color", "attribute_value": "Red"}, ...]
	"""
	attributes = []
	for var_attr in variation.get("attributes", []):
		attr_name = var_attr.get("name", "")
		attr_value = var_attr.get("option", "")
		if attr_name and attr_value:
			attributes.append({
				"attribute": attr_name,
				"attribute_value": attr_value,
			})
	return attributes


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

	# Handle sale price if present
	sale_price = flt(product.get("sale_price"))
	if sale_price and sale_price != regular_price:
		sale_price_list = f"{settings.price_list} - Sale"
		# Create sale price list if it doesn't exist
		if not frappe.db.exists("Price List", sale_price_list):
			pl = frappe.new_doc("Price List")
			pl.price_list_name = sale_price_list
			pl.selling = 1
			pl.flags.ignore_permissions = True
			pl.save()

		existing_sale = frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "price_list": sale_price_list, "selling": 1},
			"name",
		)
		if existing_sale:
			frappe.db.set_value("Item Price", existing_sale, "price_list_rate", sale_price)
		else:
			sp = frappe.new_doc("Item Price")
			sp.item_code = item_code
			sp.price_list = sale_price_list
			sp.price_list_rate = sale_price
			sp.selling = 1
			sp.flags.ignore_permissions = True
			sp.save()


def _sync_erpnext_items_to_wc(connector, settings):
	"""Push ERPNext Items to WooCommerce."""
	items = frappe.get_all(
		"Item",
		filters={"woocommerce_id": ["is", "set"], "disabled": 0},
		fields=["name", "item_name", "description", "weight_per_unit", "woocommerce_id",
				"has_variants", "variant_of"],
	)

	for item in items:
		try:
			# Skip variants - they are handled via their parent
			if item.variant_of:
				continue

			item_doc = frappe.get_doc("Item", item.name)

			if item.has_variants:
				_push_variable_product_to_wc(item_doc, connector, settings)
			else:
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
				woocommerce_id=item.woocommerce_id,
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


def _push_variable_product_to_wc(template_doc, connector, settings):
	"""Push an ERPNext Item Template and its variants to WooCommerce as a variable product."""
	wc_id = template_doc.woocommerce_id

	# Build parent product data
	data = {
		"name": template_doc.item_name,
		"type": "variable",
		"description": template_doc.description or template_doc.item_name,
		"sku": template_doc.item_code,
	}

	# Build attributes from template
	if template_doc.attributes:
		wc_attrs = []
		for attr in template_doc.attributes:
			# Get all values from variants
			values = frappe.get_all(
				"Item Variant Attribute",
				filters={"attribute": attr.attribute, "parenttype": "Item"},
				fields=["attribute_value"],
				group_by="attribute_value",
			)
			wc_attrs.append({
				"name": attr.attribute,
				"visible": True,
				"variation": True,
				"options": [v.attribute_value for v in values],
			})
		data["attributes"] = wc_attrs

	if template_doc.weight_per_unit:
		data["weight"] = str(template_doc.weight_per_unit)

	if wc_id:
		connector.update_product(int(wc_id), data)
	else:
		result = connector.create_product(data)
		wc_id = str(result["id"])
		frappe.db.set_value("Item", template_doc.name, "woocommerce_id", wc_id)

	# Push variants
	variants = frappe.get_all(
		"Item",
		filters={"variant_of": template_doc.name, "disabled": 0, "woocommerce_id": ["is", "set"]},
		fields=["name", "item_code", "item_name", "woocommerce_id"],
	)

	for variant in variants:
		variant_doc = frappe.get_doc("Item", variant.name)
		var_data = _build_wc_variation_data(variant_doc, settings)

		if variant.woocommerce_id:
			connector.update_product_variation(int(wc_id), int(variant.woocommerce_id), var_data)
		else:
			result = connector.create_product_variation(int(wc_id), var_data)
			frappe.db.set_value("Item", variant.name, "woocommerce_id", str(result["id"]))


def _build_wc_variation_data(variant_doc, settings):
	"""Build WooCommerce variation payload from an ERPNext Item Variant."""
	data = {
		"sku": variant_doc.item_code,
		"manage_stock": True,
	}

	# Map attributes
	if variant_doc.attributes:
		data["attributes"] = [
			{"name": attr.attribute, "option": attr.attribute_value}
			for attr in variant_doc.attributes
		]

	# Price
	price = frappe.db.get_value(
		"Item Price",
		{"item_code": variant_doc.item_code, "price_list": settings.price_list, "selling": 1},
		"price_list_rate",
	)
	if price:
		data["regular_price"] = str(flt(price))

	# Stock
	actual_qty = flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": variant_doc.item_code, "warehouse": settings.warehouse},
			"actual_qty",
		)
	)
	data["stock_quantity"] = int(actual_qty)

	if variant_doc.weight_per_unit:
		data["weight"] = str(variant_doc.weight_per_unit)

	return data


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
