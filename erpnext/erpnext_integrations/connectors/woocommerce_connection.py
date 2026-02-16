"""
WooCommerce REST API connector for ERPNext.

Handles all HTTP communication with the WooCommerce REST API v3,
including authentication, request signing, and error handling.
"""

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode, urljoin

import frappe
import requests
from frappe import _


class WooCommerceConnector:
	"""Client for WooCommerce REST API v3."""

	API_VERSION = "wp-json/wc/v3"

	def __init__(self, settings=None):
		if settings is None:
			settings = frappe.get_single("WooCommerce Settings")

		self.url = settings.woocommerce_url.rstrip("/")
		self.consumer_key = settings.consumer_key
		self.consumer_secret = settings.get_password("consumer_secret")
		self.verify_ssl = bool(settings.verify_ssl)
		self.timeout = 30

	def _get_url(self, endpoint):
		return f"{self.url}/{self.API_VERSION}/{endpoint}"

	def _get_auth(self):
		return (self.consumer_key, self.consumer_secret)

	def request(self, method, endpoint, data=None, params=None):
		url = self._get_url(endpoint)
		headers = {"Content-Type": "application/json"}

		try:
			response = requests.request(
				method=method,
				url=url,
				auth=self._get_auth(),
				headers=headers,
				json=data,
				params=params,
				verify=self.verify_ssl,
				timeout=self.timeout,
			)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.HTTPError as e:
			error_msg = str(e)
			try:
				error_body = e.response.json()
				error_msg = error_body.get("message", error_msg)
			except (ValueError, AttributeError):
				pass
			frappe.log_error(
				title=_("WooCommerce API Error"),
				message=f"{method} {endpoint}: {error_msg}",
			)
			raise
		except requests.exceptions.ConnectionError:
			frappe.log_error(
				title=_("WooCommerce Connection Error"),
				message=f"Could not connect to {self.url}",
			)
			raise
		except requests.exceptions.Timeout:
			frappe.log_error(
				title=_("WooCommerce Timeout"),
				message=f"Request to {url} timed out",
			)
			raise

	def get(self, endpoint, params=None):
		return self.request("GET", endpoint, params=params)

	def post(self, endpoint, data=None):
		return self.request("POST", endpoint, data=data)

	def put(self, endpoint, data=None):
		return self.request("PUT", endpoint, data=data)

	def delete(self, endpoint, params=None):
		return self.request("DELETE", endpoint, params=params)

	# --- Product API ---

	def get_products(self, page=1, per_page=100, **kwargs):
		params = {"page": page, "per_page": per_page}
		params.update(kwargs)
		return self.get("products", params=params)

	def get_product(self, product_id):
		return self.get(f"products/{product_id}")

	def create_product(self, data):
		return self.post("products", data=data)

	def update_product(self, product_id, data):
		return self.put(f"products/{product_id}", data=data)

	def update_product_stock(self, product_id, stock_quantity, manage_stock=True):
		return self.put(
			f"products/{product_id}",
			data={"stock_quantity": int(stock_quantity), "manage_stock": manage_stock},
		)

	def batch_update_products(self, updates):
		"""Batch update products. `updates` is a list of dicts with 'id' and fields."""
		return self.post("products/batch", data={"update": updates})

	# --- Order API ---

	def get_orders(self, page=1, per_page=100, **kwargs):
		params = {"page": page, "per_page": per_page}
		params.update(kwargs)
		return self.get("orders", params=params)

	def get_order(self, order_id):
		return self.get(f"orders/{order_id}")

	def update_order(self, order_id, data):
		return self.put(f"orders/{order_id}", data=data)

	# --- Customer API ---

	def get_customers(self, page=1, per_page=100, **kwargs):
		params = {"page": page, "per_page": per_page}
		params.update(kwargs)
		return self.get("customers", params=params)

	def get_customer(self, customer_id):
		return self.get(f"customers/{customer_id}")


def verify_webhook_signature(payload, signature, secret):
	"""Verify WooCommerce webhook signature using HMAC-SHA256."""
	if not secret or not signature:
		return False

	expected = base64.b64encode(
		hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
	).decode("utf-8")

	return hmac.compare_digest(expected, signature)
