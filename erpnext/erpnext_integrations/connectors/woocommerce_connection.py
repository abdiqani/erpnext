"""
WooCommerce REST API connector for ERPNext.

Handles all HTTP communication with the WooCommerce REST API v3,
including authentication (Basic Auth over HTTPS, OAuth 1.0a over HTTP),
request signing, retry logic with exponential backoff, and error handling.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import frappe
import requests
from frappe import _


class WooCommerceConnector:
	"""Client for WooCommerce REST API v3.

	Automatically selects authentication method:
	- HTTPS stores: Basic Auth (consumer_key / consumer_secret)
	- HTTP stores: OAuth 1.0a signature (required by WooCommerce for non-SSL)

	Includes retry logic with exponential backoff for transient failures.
	"""

	API_VERSION = "wp-json/wc/v3"
	MAX_RETRIES = 3
	RETRY_BACKOFF_BASE = 2  # seconds
	RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

	def __init__(self, settings=None):
		if settings is None:
			settings = frappe.get_single("WooCommerce Settings")

		self.url = settings.woocommerce_url.rstrip("/")
		self.consumer_key = settings.consumer_key
		self.consumer_secret = settings.get_password("consumer_secret")
		self.verify_ssl = bool(settings.verify_ssl)
		self.timeout = 30
		self.is_ssl = self.url.startswith("https")

	def _get_url(self, endpoint):
		return f"{self.url}/{self.API_VERSION}/{endpoint}"

	def _get_auth(self):
		"""Return auth tuple for Basic Auth (HTTPS only)."""
		return (self.consumer_key, self.consumer_secret)

	def _build_oauth_params(self, method, url, params=None):
		"""Build OAuth 1.0a parameters for HTTP (non-SSL) stores.

		WooCommerce requires OAuth 1.0a when the store is not on HTTPS.
		This implements the one-legged OAuth flow (no token needed).
		"""
		import hashlib
		import secrets
		import time
		from urllib.parse import quote, urlencode

		oauth_params = {
			"oauth_consumer_key": self.consumer_key,
			"oauth_nonce": secrets.token_hex(20),
			"oauth_signature_method": "HMAC-SHA256",
			"oauth_timestamp": str(int(time.time())),
		}

		# Merge with any existing query params
		all_params = {}
		if params:
			all_params.update(params)
		all_params.update(oauth_params)

		# Sort parameters and create base string
		sorted_params = urlencode(sorted(all_params.items()))
		base_string = f"{method.upper()}&{quote(url, safe='')}&{quote(sorted_params, safe='')}"

		# Sign with consumer_secret& (no token secret in one-legged OAuth)
		signing_key = f"{quote(self.consumer_secret, safe='')}&"
		signature = base64.b64encode(
			hmac.new(
				signing_key.encode("utf-8"),
				base_string.encode("utf-8"),
				hashlib.sha256,
			).digest()
		).decode("utf-8")

		oauth_params["oauth_signature"] = signature
		return oauth_params

	def request(self, method, endpoint, data=None, params=None):
		"""Make an API request with automatic retry on transient failures."""
		url = self._get_url(endpoint)
		headers = {"Content-Type": "application/json"}
		request_params = dict(params) if params else {}

		last_exception = None
		for attempt in range(self.MAX_RETRIES + 1):
			try:
				kwargs = {
					"method": method,
					"url": url,
					"headers": headers,
					"json": data,
					"verify": self.verify_ssl,
					"timeout": self.timeout,
				}

				if self.is_ssl:
					kwargs["auth"] = self._get_auth()
					kwargs["params"] = request_params
				else:
					# OAuth 1.0a for HTTP stores
					oauth_params = self._build_oauth_params(method, url, request_params)
					kwargs["params"] = {**request_params, **oauth_params}

				response = requests.request(**kwargs)

				# Check for retryable status codes
				if response.status_code in self.RETRYABLE_STATUS_CODES and attempt < self.MAX_RETRIES:
					wait_time = self.RETRY_BACKOFF_BASE ** (attempt + 1)
					frappe.log_error(
						title=_("WooCommerce API Retry"),
						message=f"Status {response.status_code} on {method} {endpoint}, retrying in {wait_time}s (attempt {attempt + 1}/{self.MAX_RETRIES})",
					)
					time.sleep(wait_time)
					continue

				response.raise_for_status()
				return response.json()

			except requests.exceptions.HTTPError as e:
				last_exception = e
				error_msg = str(e)
				try:
					error_body = e.response.json()
					error_msg = error_body.get("message", error_msg)
				except (ValueError, AttributeError):
					pass

				# Don't retry client errors (4xx) except 429 (rate limit)
				status_code = getattr(e.response, "status_code", 0)
				if status_code not in self.RETRYABLE_STATUS_CODES or attempt >= self.MAX_RETRIES:
					frappe.log_error(
						title=_("WooCommerce API Error"),
						message=f"{method} {endpoint}: {error_msg}",
					)
					raise

				wait_time = self.RETRY_BACKOFF_BASE ** (attempt + 1)
				time.sleep(wait_time)

			except requests.exceptions.ConnectionError as e:
				last_exception = e
				if attempt >= self.MAX_RETRIES:
					frappe.log_error(
						title=_("WooCommerce Connection Error"),
						message=f"Could not connect to {self.url} after {self.MAX_RETRIES + 1} attempts",
					)
					raise

				wait_time = self.RETRY_BACKOFF_BASE ** (attempt + 1)
				frappe.log_error(
					title=_("WooCommerce Connection Retry"),
					message=f"Connection failed to {self.url}, retrying in {wait_time}s (attempt {attempt + 1}/{self.MAX_RETRIES})",
				)
				time.sleep(wait_time)

			except requests.exceptions.Timeout as e:
				last_exception = e
				if attempt >= self.MAX_RETRIES:
					frappe.log_error(
						title=_("WooCommerce Timeout"),
						message=f"Request to {url} timed out after {self.MAX_RETRIES + 1} attempts",
					)
					raise

				wait_time = self.RETRY_BACKOFF_BASE ** (attempt + 1)
				time.sleep(wait_time)

		# Should not reach here, but just in case
		if last_exception:
			raise last_exception

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

	# --- Product Variations API ---

	def get_product_variations(self, product_id, page=1, per_page=100):
		"""Get all variations for a variable product."""
		return self.get(
			f"products/{product_id}/variations",
			params={"page": page, "per_page": per_page},
		)

	def get_product_variation(self, product_id, variation_id):
		return self.get(f"products/{product_id}/variations/{variation_id}")

	def create_product_variation(self, product_id, data):
		return self.post(f"products/{product_id}/variations", data=data)

	def update_product_variation(self, product_id, variation_id, data):
		return self.put(f"products/{product_id}/variations/{variation_id}", data=data)

	def batch_update_variations(self, product_id, updates):
		"""Batch update variations for a product."""
		return self.post(f"products/{product_id}/variations/batch", data={"update": updates})

	# --- Product Attributes API ---

	def get_product_attributes(self):
		return self.get("products/attributes")

	def create_product_attribute(self, data):
		return self.post("products/attributes", data=data)

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
