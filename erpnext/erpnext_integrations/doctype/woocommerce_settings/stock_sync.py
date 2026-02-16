"""
Stock/inventory synchronization between WooCommerce and ERPNext.

Keeps stock quantities aligned across both systems. Supports
pushing ERPNext stock to WooCommerce and pulling WooCommerce
stock into ERPNext via Stock Reconciliation.
"""

import traceback

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, nowdate, nowtime

from erpnext.erpnext_integrations.connectors.woocommerce_connection import (
	WooCommerceConnector,
)
from erpnext.erpnext_integrations.doctype.woocommerce_log.woocommerce_log import (
	create_woocommerce_log,
)


def run_stock_sync():
	"""Entry point for stock synchronization."""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled or not settings.sync_stock:
		return

	direction = settings.stock_sync_direction
	connector = WooCommerceConnector(settings)

	if direction in ("ERPNext to WooCommerce", "Bidirectional"):
		_push_stock_to_wc(connector, settings)

	if direction in ("WooCommerce to ERPNext", "Bidirectional"):
		_pull_stock_from_wc(connector, settings)


def _push_stock_to_wc(connector, settings):
	"""Push current ERPNext stock levels to WooCommerce for all synced items."""
	items = frappe.get_all(
		"Item",
		filters={"woocommerce_id": ["is", "set"], "disabled": 0},
		fields=["name", "item_code", "woocommerce_id"],
	)

	if not items:
		return

	# Build batch update payload
	batch_updates = []
	for item in items:
		qty = _get_total_available_qty(item.item_code, settings)
		batch_updates.append(
			{
				"id": int(item.woocommerce_id),
				"stock_quantity": int(qty),
				"manage_stock": True,
			}
		)

	# WooCommerce batch API supports up to 100 items per call
	for i in range(0, len(batch_updates), 100):
		chunk = batch_updates[i : i + 100]
		try:
			connector.batch_update_products(chunk)
			create_woocommerce_log(
				title=f"Stock push batch ({len(chunk)} items)",
				status="Success",
				method="stock_sync.push_to_wc",
			)
		except Exception:
			create_woocommerce_log(
				title=f"Stock push batch failed",
				status="Failed",
				method="stock_sync.push_to_wc",
				error=str(traceback.format_exc()),
			)


def _get_total_available_qty(item_code, settings):
	"""Get total available quantity across relevant warehouses.

	If POS is enabled, sums stock from both the main warehouse and the
	POS warehouse to get the true available quantity. Otherwise uses
	only the configured warehouse.
	"""
	warehouses = [settings.warehouse]
	if settings.enable_pos and settings.pos_warehouse and settings.pos_warehouse != settings.warehouse:
		warehouses.append(settings.pos_warehouse)

	total = 0
	for wh in warehouses:
		total += flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": wh}, "actual_qty")
		)
	return total


def _pull_stock_from_wc(connector, settings):
	"""Pull stock levels from WooCommerce and reconcile in ERPNext."""
	items_needing_reconciliation = []
	page = 1

	while True:
		try:
			products = connector.get_products(page=page, per_page=100)
		except Exception:
			create_woocommerce_log(
				title="Stock pull fetch failed",
				status="Failed",
				method="stock_sync.pull_from_wc",
				error=str(traceback.format_exc()),
			)
			break

		if not products:
			break

		for product in products:
			if not product.get("manage_stock"):
				continue

			wc_id = str(product["id"])
			wc_qty = flt(product.get("stock_quantity", 0))
			item_code = frappe.db.get_value("Item", {"woocommerce_id": wc_id}, "name")

			if not item_code:
				continue

			current_qty = flt(
				frappe.db.get_value(
					"Bin",
					{"item_code": item_code, "warehouse": settings.warehouse},
					"actual_qty",
				)
			)

			if current_qty != wc_qty:
				items_needing_reconciliation.append(
					{
						"item_code": item_code,
						"warehouse": settings.warehouse,
						"qty": wc_qty,
						"wc_id": wc_id,
					}
				)

		if len(products) < 100:
			break
		page += 1

	if items_needing_reconciliation:
		_create_stock_reconciliation(items_needing_reconciliation, settings)


def _create_stock_reconciliation(items, settings):
	"""Create a Stock Reconciliation to adjust ERPNext stock to match WooCommerce."""
	try:
		sr = frappe.new_doc("Stock Reconciliation")
		sr.company = settings.company
		sr.purpose = "Stock Reconciliation"
		sr.set_posting_time = 1
		sr.posting_date = nowdate()
		sr.posting_time = nowtime()

		for entry in items:
			sr.append(
				"items",
				{
					"item_code": entry["item_code"],
					"warehouse": entry["warehouse"],
					"qty": entry["qty"],
				},
			)

		sr.flags.ignore_permissions = True
		sr.save()
		sr.submit()
		frappe.db.commit()

		create_woocommerce_log(
			title=f"Stock reconciliation ({len(items)} items)",
			status="Success",
			method="stock_sync.reconciliation",
			reference_doctype="Stock Reconciliation",
			reference_name=sr.name,
		)
	except Exception:
		create_woocommerce_log(
			title="Stock reconciliation failed",
			status="Failed",
			method="stock_sync.reconciliation",
			error=str(traceback.format_exc()),
		)


def on_stock_update_push_to_wc(doc, method=None):
	"""Hook called after stock entries to push updated quantities to WooCommerce.

	Registered via doc_events for Stock Entry and Stock Reconciliation.
	"""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled or not settings.sync_stock:
		return
	if settings.stock_sync_direction == "WooCommerce to ERPNext":
		return

	item_codes = set()
	if doc.doctype == "Stock Entry":
		for row in doc.items:
			item_codes.add(row.item_code)
	elif doc.doctype == "Stock Reconciliation":
		for row in doc.items:
			item_codes.add(row.item_code)
	elif doc.doctype == "POS Invoice":
		for row in doc.items:
			item_codes.add(row.item_code)

	if not item_codes:
		return

	# Find which of these items are synced with WooCommerce
	synced_items = frappe.get_all(
		"Item",
		filters={"name": ["in", list(item_codes)], "woocommerce_id": ["is", "set"]},
		fields=["name", "item_code", "woocommerce_id"],
	)

	if not synced_items:
		return

	# Enqueue the actual push to avoid slowing down the transaction
	frappe.enqueue(
		_push_specific_items_to_wc,
		items=synced_items,
		settings_name="WooCommerce Settings",
		queue="short",
		timeout=300,
	)


def _push_specific_items_to_wc(items, settings_name):
	"""Push stock for specific items to WooCommerce."""
	settings = frappe.get_single(settings_name)
	connector = WooCommerceConnector(settings)

	updates = []
	for item in items:
		qty = _get_total_available_qty(item["item_code"], settings)
		updates.append(
			{
				"id": int(item["woocommerce_id"]),
				"stock_quantity": int(qty),
				"manage_stock": True,
			}
		)

	try:
		connector.batch_update_products(updates)
		create_woocommerce_log(
			title=f"Real-time stock push ({len(updates)} items)",
			status="Success",
			method="stock_sync.realtime_push",
		)
	except Exception:
		create_woocommerce_log(
			title="Real-time stock push failed",
			status="Failed",
			method="stock_sync.realtime_push",
			error=str(traceback.format_exc()),
		)
