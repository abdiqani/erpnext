"""
WooCommerce webhook handler.

Receives real-time notifications from WooCommerce for order
and product events, and dispatches them to the appropriate
sync functions.
"""

import base64
import hashlib
import hmac
import json
import traceback

import frappe
from frappe import _

from erpnext.erpnext_integrations.doctype.woocommerce_log.woocommerce_log import (
	create_woocommerce_log,
)


@frappe.whitelist(allow_guest=True)
def handle_webhook():
	"""Main webhook endpoint for WooCommerce.

	WooCommerce sends webhooks for events like:
	- order.created, order.updated, order.deleted
	- product.created, product.updated, product.deleted

	URL: /api/method/erpnext.erpnext_integrations.connectors.woocommerce_webhook.handle_webhook
	"""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled:
		frappe.throw(_("WooCommerce integration is not enabled"), frappe.AuthenticationError)

	# Verify webhook signature
	webhook_secret = settings.get_password("webhook_secret", raise_exception=False)
	if webhook_secret:
		signature = frappe.get_request_header("X-WC-Webhook-Signature")
		if not _verify_signature(frappe.request.data, signature, webhook_secret):
			frappe.throw(_("Invalid webhook signature"), frappe.AuthenticationError)

	# Parse payload
	try:
		payload = json.loads(frappe.request.data)
	except (json.JSONDecodeError, TypeError):
		frappe.throw(_("Invalid webhook payload"))

	# Determine event topic
	topic = frappe.get_request_header("X-WC-Webhook-Topic") or ""
	resource = frappe.get_request_header("X-WC-Webhook-Resource") or ""

	frappe.set_user("Administrator")

	create_woocommerce_log(
		title=f"Webhook: {topic}",
		status="Queued",
		method=f"webhook.{topic}",
		woocommerce_id=payload.get("id"),
		request_data=payload,
	)

	# Dispatch to handler
	if resource == "order" or topic.startswith("order."):
		frappe.enqueue(
			_handle_order_webhook,
			payload=payload,
			topic=topic,
			queue="short",
			timeout=300,
		)
	elif resource == "product" or topic.startswith("product."):
		frappe.enqueue(
			_handle_product_webhook,
			payload=payload,
			topic=topic,
			queue="short",
			timeout=300,
		)
	else:
		create_woocommerce_log(
			title=f"Unhandled webhook topic: {topic}",
			status="Failed",
			method=f"webhook.{topic}",
			error=f"No handler for topic: {topic}",
		)

	return {"status": "ok"}


def _verify_signature(payload, signature, secret):
	"""Verify WooCommerce webhook HMAC-SHA256 signature."""
	if not signature:
		return False
	expected = base64.b64encode(
		hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
	).decode("utf-8")
	return hmac.compare_digest(expected, signature)


def _handle_order_webhook(payload, topic):
	"""Handle order-related webhook events."""
	try:
		settings = frappe.get_single("WooCommerce Settings")
		if not settings.sync_orders:
			return

		from erpnext.erpnext_integrations.connectors.woocommerce_connection import (
			WooCommerceConnector,
		)
		from erpnext.erpnext_integrations.doctype.woocommerce_settings.order_sync import (
			_process_wc_order,
		)

		connector = WooCommerceConnector(settings)

		if topic in ("order.created", "order.updated"):
			_process_wc_order(payload, settings, connector)

		create_woocommerce_log(
			title=f"Webhook order processed: #{payload.get('id')}",
			status="Success",
			method=f"webhook.{topic}",
			woocommerce_id=payload.get("id"),
		)
	except Exception:
		create_woocommerce_log(
			title=f"Webhook order failed: #{payload.get('id')}",
			status="Failed",
			method=f"webhook.{topic}",
			woocommerce_id=payload.get("id"),
			error=str(traceback.format_exc()),
		)


def _handle_product_webhook(payload, topic):
	"""Handle product-related webhook events."""
	try:
		settings = frappe.get_single("WooCommerce Settings")
		if not settings.sync_products:
			return

		if settings.product_sync_direction == "ERPNext to WooCommerce":
			return

		from erpnext.erpnext_integrations.doctype.woocommerce_settings.product_sync import (
			_create_or_update_item,
		)

		if topic in ("product.created", "product.updated"):
			_create_or_update_item(payload, settings)

		create_woocommerce_log(
			title=f"Webhook product processed: {payload.get('name')}",
			status="Success",
			method=f"webhook.{topic}",
			woocommerce_id=payload.get("id"),
		)
	except Exception:
		create_woocommerce_log(
			title=f"Webhook product failed: {payload.get('name')}",
			status="Failed",
			method=f"webhook.{topic}",
			woocommerce_id=payload.get("id"),
			error=str(traceback.format_exc()),
		)
