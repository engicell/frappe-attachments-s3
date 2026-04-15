// Copyright (c) 2018, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('S3 File Attachment', {
	refresh: function(frm) {
		console.log("S3 File Attachment form loaded");
	},
	migrate_existing_files: function (frm) {
		if (!frm.doc.enabled) {
			frappe.msgprint({
				title: __("S3 Not Enabled"),
				indicator: 'red',
				message: __("Please enable S3 File Attachment first.")
			});
			return;
		}
		
		frappe.call({
			method: "frappe_s3_attachment.controller.migrate_existing_files",
			btn: frm.fields_dict.migrate_existing_files.$btn,
			freeze: true,
			freeze_message: __("Migrating files to S3..."),
			callback: function (r) {
				if (r.exc) {
					frappe.msgprint({
						title: __("Migration Error"),
						indicator: 'red',
						message: __(r.exc)
					});
				} else if (r.message === false) {
					frappe.msgprint({
						title: __("Migration Failed"),
						indicator: 'orange',
						message: __("Please check S3 settings.")
					});
				} else if (r.message === true) {
					frappe.msgprint({
						title: __("Migration Started"),
						indicator: 'green',
						message: __("File migration has been queued in background.")
					});
				}
			},
			error: function(r) {
				frappe.msgprint({
					title: __("Error"),
					indicator: 'red',
					message: __("Request failed. Check browser console.")
				});
			}
		});
	},
	test_s3_connection: function(frm) {
		frappe.call({
			method: "frappe_s3_attachment.controller.test_s3_connection",
			btn: frm.fields_dict.test_s3_connection.$btn,
			freeze: true,
			freeze_message: __("Testing S3 connection..."),
			callback: function(r) {
				if (r.message && r.message.message) {
					frappe.msgprint({
						title: r.message.title || __("S3 Connection Test"),
						indicator: 'green',
						message: r.message.message
					});
				} else if (r.exc) {
					frappe.msgprint({
						title: __("Error"),
						indicator: 'red',
						message: __(r.exc)
					});
				}
			},
			error: function(r) {
				frappe.msgprint({
					title: __("Error"),
					indicator: 'red',
					message: __("Request failed. Check browser console.")
				});
			}
		});
	}
});
