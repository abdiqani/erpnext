frappe.ui.form.on("WooCommerce Settings", {
	refresh(frm) {
		if (frm.doc.enabled) {
			frm.add_custom_button(__("Test Connection"), function () {
				frappe.call({
					method:
						"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.WooCommerceSettings.test_connection",
					freeze: true,
					freeze_message: __("Testing connection..."),
					callback: function (r) {
						if (r.message && r.message.status === "success") {
							frappe.msgprint({
								title: __("Connection Successful"),
								indicator: "green",
								message: r.message.message +
									"<br>WooCommerce Version: " +
									r.message.wc_version,
							});
						} else {
							frappe.msgprint({
								title: __("Connection Failed"),
								indicator: "red",
								message: r.message ? r.message.message : __("Unknown error"),
							});
						}
					},
				});
			});

			frm.add_custom_button(
				__("Products"),
				function () {
					frappe.call({
						method:
							"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.WooCommerceSettings.sync_products",
						freeze: true,
						freeze_message: __("Queuing product sync..."),
						callback: function (r) {
							frappe.msgprint(__("Product sync has been queued. Check WooCommerce Log for progress."));
						},
					});
				},
				__("Sync")
			);

			frm.add_custom_button(
				__("Orders"),
				function () {
					frappe.call({
						method:
							"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.WooCommerceSettings.sync_orders",
						freeze: true,
						freeze_message: __("Queuing order sync..."),
						callback: function (r) {
							frappe.msgprint(__("Order sync has been queued. Check WooCommerce Log for progress."));
						},
					});
				},
				__("Sync")
			);

			frm.add_custom_button(
				__("Stock"),
				function () {
					frappe.call({
						method:
							"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.WooCommerceSettings.sync_stock",
						freeze: true,
						freeze_message: __("Queuing stock sync..."),
						callback: function (r) {
							frappe.msgprint(__("Stock sync has been queued. Check WooCommerce Log for progress."));
						},
					});
				},
				__("Sync")
			);

			frm.add_custom_button(
				__("Full Sync"),
				function () {
					frappe.call({
						method:
							"erpnext.erpnext_integrations.doctype.woocommerce_settings.woocommerce_settings.WooCommerceSettings.sync_all",
						freeze: true,
						freeze_message: __("Queuing full sync..."),
						callback: function (r) {
							frappe.msgprint(__("Full sync has been queued. Check WooCommerce Log for progress."));
						},
					});
				},
				__("Sync")
			);
		}

		// Show webhook URL
		if (frm.doc.enabled) {
			let webhook_url =
				window.location.origin +
				"/api/method/erpnext.erpnext_integrations.connectors.woocommerce_webhook.handle_webhook";
			frm.set_intro(
				__("Webhook URL for WooCommerce: ") +
					"<code>" +
					webhook_url +
					"</code>",
				"blue"
			);
		}
	},

	enable_pos(frm) {
		if (frm.doc.enable_pos && !frm.doc.pos_profile) {
			frappe.msgprint(
				__(
					"A POS Profile will be created automatically when you save, or you can select an existing one."
				)
			);
		}
	},
});
