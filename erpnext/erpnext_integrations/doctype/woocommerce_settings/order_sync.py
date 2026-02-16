"""
Order synchronization from WooCommerce to ERPNext.

Imports WooCommerce orders as ERPNext Sales Orders + Sales Invoices,
creates Customers and Addresses as needed, and updates WooCommerce
order status after successful import.
"""

import traceback

import frappe
from frappe import _
from frappe.utils import cstr, flt, getdate, now_datetime

from erpnext.erpnext_integrations.connectors.woocommerce_connection import (
	WooCommerceConnector,
)
from erpnext.erpnext_integrations.doctype.woocommerce_log.woocommerce_log import (
	create_woocommerce_log,
)


def run_order_sync():
	"""Entry point for order synchronization."""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled or not settings.sync_orders:
		return

	connector = WooCommerceConnector(settings)
	statuses = [s.strip() for s in (settings.order_status_filter or "processing").split(",")]

	for status in statuses:
		_sync_orders_by_status(connector, settings, status)

	settings.reload()
	settings.last_order_sync = now_datetime()
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _sync_orders_by_status(connector, settings, status):
	"""Fetch and sync all orders with a given WooCommerce status."""
	page = 1
	while True:
		try:
			orders = connector.get_orders(page=page, per_page=100, status=status)
		except Exception:
			create_woocommerce_log(
				title=f"Order Fetch Failed (status={status})",
				status="Failed",
				method="order_sync.get_orders",
				error=str(traceback.format_exc()),
			)
			break

		if not orders:
			break

		for order in orders:
			try:
				_process_wc_order(order, settings, connector)
				create_woocommerce_log(
					title=f"Synced order #{order.get('id')}",
					status="Success",
					method="order_sync.process_order",
					reference_doctype="Sales Order",
					woocommerce_id=order.get("id"),
				)
			except Exception:
				create_woocommerce_log(
					title=f"Failed order #{order.get('id')}",
					status="Failed",
					method="order_sync.process_order",
					woocommerce_id=order.get("id"),
					request_data=order,
					error=str(traceback.format_exc()),
				)

		if len(orders) < 100:
			break
		page += 1


def _process_wc_order(order, settings, connector):
	"""Process a single WooCommerce order into ERPNext documents."""
	wc_order_id = str(order["id"])

	# Skip if already imported
	if frappe.db.exists("Sales Order", {"woocommerce_order_id": wc_order_id}):
		return

	# Create/find customer
	customer_name = _get_or_create_customer(order, settings)

	# Create billing address
	_create_address_if_needed(order.get("billing", {}), customer_name, "Billing")

	# Create shipping address
	_create_address_if_needed(order.get("shipping", {}), customer_name, "Shipping")

	# Create Sales Order
	so = _create_sales_order(order, customer_name, settings)

	# If order is already completed/processing, also create Sales Invoice
	if order.get("status") in ("completed", "processing"):
		_create_sales_invoice_from_so(so, settings)


def _get_or_create_customer(order, settings):
	"""Find or create an ERPNext Customer from WooCommerce order data."""
	billing = order.get("billing", {})
	wc_customer_id = str(order.get("customer_id", 0))
	email = billing.get("email", "")

	# Try to find by WooCommerce customer ID
	if wc_customer_id and wc_customer_id != "0":
		existing = frappe.db.get_value(
			"Customer", {"woocommerce_customer_id": wc_customer_id}, "name"
		)
		if existing:
			return existing

	# Try to find by email via Dynamic Link in Contact
	if email:
		contacts = frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Customer", "parenttype": "Contact"},
			fields=["link_name"],
			parent_doctype="Contact",
		)
		for contact in contacts:
			contact_doc = frappe.db.get_value(
				"Contact", {"name": contact.link_name}, "email_id"
			)
			if contact_doc == email:
				return contact.link_name

	# Create new customer
	first_name = billing.get("first_name", "")
	last_name = billing.get("last_name", "")
	customer_name_str = f"{first_name} {last_name}".strip() or f"WC Customer {wc_customer_id}"

	customer = frappe.new_doc("Customer")
	customer.customer_name = customer_name_str
	customer.customer_type = "Individual"
	customer.customer_group = settings.default_customer_group or frappe.db.get_single_value(
		"Selling Settings", "customer_group"
	)
	customer.territory = frappe.db.get_single_value("Selling Settings", "territory")
	customer.woocommerce_customer_id = wc_customer_id if wc_customer_id != "0" else None
	customer.flags.ignore_permissions = True
	customer.flags.ignore_mandatory = True
	customer.save()

	# Create contact with email
	if email:
		contact = frappe.new_doc("Contact")
		contact.first_name = first_name or customer_name_str
		contact.last_name = last_name
		contact.email_id = email
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		if billing.get("phone"):
			contact.append("phone_nos", {"phone": billing["phone"], "is_primary_phone": 1})
		contact.flags.ignore_permissions = True
		contact.flags.ignore_mandatory = True
		contact.save()

	frappe.db.commit()
	return customer.name


def _create_address_if_needed(addr_data, customer_name, address_type):
	"""Create an Address linked to the customer if data is present."""
	if not addr_data or not addr_data.get("address_1"):
		return

	# Check for existing address to avoid duplicates
	existing = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Customer", "link_name": customer_name, "parenttype": "Address"},
		"parent",
	)
	if existing:
		return existing

	address = frappe.new_doc("Address")
	address.address_title = customer_name
	address.address_type = address_type
	address.address_line1 = addr_data.get("address_1", "")
	address.address_line2 = addr_data.get("address_2", "")
	address.city = addr_data.get("city", "")
	address.state = addr_data.get("state", "")
	address.pincode = addr_data.get("postcode", "")
	address.country = _get_country(addr_data.get("country", ""))
	address.phone = addr_data.get("phone", "")
	address.email_id = addr_data.get("email", "")
	address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
	address.flags.ignore_permissions = True
	address.flags.ignore_mandatory = True
	address.save()
	frappe.db.commit()
	return address.name


def _get_country(country_code):
	"""Convert ISO country code to ERPNext Country name."""
	if not country_code:
		return frappe.db.get_default("country") or "United States"
	country = frappe.db.get_value("Country", {"code": country_code.lower()}, "name")
	return country or frappe.db.get_default("country") or "United States"


def _create_sales_order(order, customer_name, settings):
	"""Create an ERPNext Sales Order from a WooCommerce order."""
	so = frappe.new_doc("Sales Order")
	so.customer = customer_name
	so.company = settings.company
	so.set_warehouse = settings.warehouse
	so.woocommerce_order_id = str(order["id"])
	so.po_no = str(order.get("number", order["id"]))
	so.transaction_date = getdate(order.get("date_created", None))
	so.delivery_date = getdate(order.get("date_created", None))
	so.currency = order.get("currency", "USD")

	# Add line items
	for line in order.get("line_items", []):
		item_code = _find_item_for_line(line, settings)
		so.append(
			"items",
			{
				"item_code": item_code,
				"item_name": line.get("name", ""),
				"qty": flt(line.get("quantity", 1)),
				"rate": flt(line.get("price", 0)),
				"warehouse": settings.warehouse,
			},
		)

	# Add taxes from WooCommerce
	_add_taxes(so, order, settings)

	# Add shipping as a charge
	_add_shipping_charges(so, order, settings)

	so.flags.ignore_permissions = True
	so.flags.ignore_mandatory = True
	so.save()
	so.submit()
	frappe.db.commit()
	return so


def _find_item_for_line(line, settings):
	"""Find the ERPNext Item matching a WooCommerce line item."""
	wc_product_id = str(line.get("product_id", ""))
	sku = line.get("sku", "")

	# By WooCommerce product ID
	if wc_product_id:
		item = frappe.db.get_value("Item", {"woocommerce_id": wc_product_id}, "name")
		if item:
			return item

	# By SKU
	if sku and frappe.db.exists("Item", sku):
		return sku

	# Create a placeholder item
	item_name = line.get("name", f"WC Product {wc_product_id}")
	item_code = sku or f"WC-{wc_product_id}"
	if not frappe.db.exists("Item", item_code):
		item_doc = frappe.new_doc("Item")
		item_doc.item_code = item_code
		item_doc.item_name = item_name
		item_doc.item_group = settings.default_item_group or "All Item Groups"
		item_doc.stock_uom = "Nos"
		item_doc.woocommerce_id = wc_product_id
		if sku:
			item_doc.append("barcodes", {"barcode": sku, "barcode_type": "EAN"})
		item_doc.flags.ignore_permissions = True
		item_doc.save()
		frappe.db.commit()

	return item_code


def _add_taxes(so, order, settings):
	"""Add tax lines from WooCommerce order to Sales Order."""
	total_tax = flt(order.get("total_tax", 0))
	if not total_tax or not settings.tax_account:
		return

	so.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": settings.tax_account,
			"description": "WooCommerce Tax",
			"tax_amount": total_tax,
			"cost_center": frappe.db.get_value("Company", settings.company, "cost_center"),
		},
	)


def _add_shipping_charges(so, order, settings):
	"""Add shipping charges from WooCommerce order."""
	shipping_total = flt(order.get("shipping_total", 0))
	if not shipping_total or not settings.shipping_account:
		return

	so.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": settings.shipping_account,
			"description": "WooCommerce Shipping",
			"tax_amount": shipping_total,
			"cost_center": frappe.db.get_value("Company", settings.company, "cost_center"),
		},
	)


def _create_sales_invoice_from_so(sales_order, settings):
	"""Create and submit a Sales Invoice from a Sales Order."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	si = make_sales_invoice(sales_order.name)
	si.flags.ignore_permissions = True
	si.flags.ignore_mandatory = True
	si.save()
	si.submit()
	frappe.db.commit()
	return si
