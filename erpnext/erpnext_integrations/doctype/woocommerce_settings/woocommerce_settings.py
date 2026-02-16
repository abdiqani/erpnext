"""
WooCommerce Settings controller.

Manages configuration, connection testing, and exposes sync
entry points for product, order, and stock synchronization.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext.erpnext_integrations.connectors.woocommerce_connection import (
	WooCommerceConnector,
)
from erpnext.erpnext_integrations.doctype.woocommerce_log.woocommerce_log import (
	create_woocommerce_log,
)


class WooCommerceSettings(Document):
	def validate(self):
		if self.enabled:
			self._validate_url()
			self._validate_credentials()

	def _validate_url(self):
		if not self.woocommerce_url:
			frappe.throw(_("WooCommerce Store URL is required"))
		if not self.woocommerce_url.startswith(("http://", "https://")):
			frappe.throw(_("WooCommerce Store URL must start with http:// or https://"))

	def _validate_credentials(self):
		if not self.consumer_key or not self.get_password("consumer_secret", raise_exception=False):
			frappe.throw(_("Consumer Key and Consumer Secret are required"))

	@staticmethod
	@frappe.whitelist()
	def test_connection():
		settings = frappe.get_single("WooCommerce Settings")
		if not settings.enabled:
			frappe.throw(_("WooCommerce integration is not enabled"))
		try:
			connector = WooCommerceConnector(settings)
			result = connector.get("system_status")
			return {
				"status": "success",
				"message": _("Connected to WooCommerce store: {0}").format(
					result.get("environment", {}).get("site_url", settings.woocommerce_url)
				),
				"wc_version": result.get("environment", {}).get("version", "unknown"),
			}
		except Exception as e:
			return {"status": "error", "message": str(e)}

	@staticmethod
	@frappe.whitelist()
	def sync_products():
		frappe.enqueue(
			"erpnext.erpnext_integrations.doctype.woocommerce_settings.product_sync.run_product_sync",
			queue="long",
			timeout=1500,
		)
		return {"status": "queued", "message": _("Product sync has been queued")}

	@staticmethod
	@frappe.whitelist()
	def sync_orders():
		frappe.enqueue(
			"erpnext.erpnext_integrations.doctype.woocommerce_settings.order_sync.run_order_sync",
			queue="long",
			timeout=1500,
		)
		return {"status": "queued", "message": _("Order sync has been queued")}

	@staticmethod
	@frappe.whitelist()
	def sync_stock():
		frappe.enqueue(
			"erpnext.erpnext_integrations.doctype.woocommerce_settings.stock_sync.run_stock_sync",
			queue="long",
			timeout=1500,
		)
		return {"status": "queued", "message": _("Stock sync has been queued")}

	@staticmethod
	@frappe.whitelist()
	def sync_all():
		frappe.enqueue(
			"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.run_full_sync",
			queue="long",
			timeout=3000,
		)
		return {"status": "queued", "message": _("Full sync has been queued")}


def run_full_sync():
	"""Run a complete sync: products, orders, then stock."""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled:
		return

	from erpnext.erpnext_integrations.doctype.woocommerce_settings.order_sync import (
		run_order_sync,
	)
	from erpnext.erpnext_integrations.doctype.woocommerce_settings.product_sync import (
		run_product_sync,
	)
	from erpnext.erpnext_integrations.doctype.woocommerce_settings.stock_sync import (
		run_stock_sync,
	)

	if settings.sync_products:
		run_product_sync()
	if settings.sync_orders:
		run_order_sync()
	if settings.sync_stock:
		run_stock_sync()

	settings.reload()
	settings.last_sync_datetime = now_datetime()
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def scheduled_sync():
	"""Called by the scheduler hook. Runs sync if enabled and frequency matches."""
	settings = frappe.get_single("WooCommerce Settings")
	if not settings.enabled:
		return

	frappe.enqueue(
		"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.run_full_sync",
		queue="long",
		timeout=3000,
	)
