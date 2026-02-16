"""
Custom fields for WooCommerce integration.

Adds tracking fields to Item, Customer, and Sales Order so that
ERPNext documents can be linked back to their WooCommerce counterparts.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup_custom_fields():
	"""Create custom fields required for WooCommerce integration."""
	custom_fields = {
		"Item": [
			{
				"fieldname": "woocommerce_id",
				"label": "WooCommerce Product ID",
				"fieldtype": "Data",
				"insert_after": "item_name",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"unique": 0,
				"description": "Linked WooCommerce product ID",
			},
		],
		"Customer": [
			{
				"fieldname": "woocommerce_customer_id",
				"label": "WooCommerce Customer ID",
				"fieldtype": "Data",
				"insert_after": "customer_name",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"description": "Linked WooCommerce customer ID",
			},
		],
		"Sales Order": [
			{
				"fieldname": "woocommerce_order_id",
				"label": "WooCommerce Order ID",
				"fieldtype": "Data",
				"insert_after": "title",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"unique": 0,
				"in_standard_filter": 1,
				"description": "Linked WooCommerce order ID",
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "woocommerce_order_id",
				"label": "WooCommerce Order ID",
				"fieldtype": "Data",
				"insert_after": "title",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"description": "Linked WooCommerce order ID (from Sales Order)",
			},
		],
	}

	create_custom_fields(custom_fields, update=True)


def remove_custom_fields():
	"""Remove WooCommerce custom fields (for cleanup)."""
	fields_to_remove = [
		("Item", "woocommerce_id"),
		("Customer", "woocommerce_customer_id"),
		("Sales Order", "woocommerce_order_id"),
		("Sales Invoice", "woocommerce_order_id"),
	]

	for doctype, fieldname in fields_to_remove:
		if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
			frappe.delete_doc("Custom Field", f"{doctype}-{fieldname}", force=True)

	frappe.db.commit()
