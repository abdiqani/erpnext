import frappe
from frappe.model.document import Document


class WooCommerceLog(Document):
	pass


def create_woocommerce_log(
	title="",
	status="Queued",
	method="",
	reference_doctype=None,
	reference_name=None,
	woocommerce_id=None,
	request_data=None,
	response_data=None,
	error=None,
	traceback=None,
):
	log = frappe.new_doc("WooCommerce Log")
	log.title = title
	log.status = status
	log.method = method
	log.reference_doctype = reference_doctype
	log.reference_name = reference_name
	log.woocommerce_id = str(woocommerce_id) if woocommerce_id else None
	log.request_data = frappe.as_json(request_data) if request_data else None
	log.response_data = frappe.as_json(response_data) if response_data else None
	log.error = error
	log.traceback = traceback
	log.insert(ignore_permissions=True)
	frappe.db.commit()
	return log
