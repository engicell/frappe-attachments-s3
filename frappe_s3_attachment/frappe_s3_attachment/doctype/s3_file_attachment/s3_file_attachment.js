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
	rewrite_file_urls: function (frm) {
		// Two-step UX: prompt for old/new prefixes → dry-run preview →
		// confirm → real run. Mirrors the test_s3_connection structured
		// response pattern so the modal stays open until dismissed.
		var current_public = (frm.doc.public_endpoint_url || "").replace(/\/+$/, "");
		var d = new frappe.ui.Dialog({
			title: __("Rewrite Stored File URLs"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "old_prefix",
					label: __("Old Prefix"),
					reqd: 1,
					description: __("Existing file_url prefix to replace, e.g. https://&lt;bucket&gt;.&lt;account&gt;.r2.cloudflarestorage.com")
				},
				{
					fieldtype: "Data",
					fieldname: "new_prefix",
					label: __("New Prefix"),
					reqd: 1,
					default: current_public,
					description: __("Replacement prefix. Defaults to the current CDN / Public URL.")
				}
			],
			primary_action_label: __("Preview"),
			primary_action: function (values) {
				frappe.call({
					method: "frappe_s3_attachment.controller.rewrite_public_file_urls",
					args: {
						old_prefix: values.old_prefix,
						new_prefix: values.new_prefix,
						dry_run: 1
					},
					freeze: true,
					freeze_message: __("Counting matching rows..."),
					callback: function (r) {
						if (r.exc || !r.message) return;
						d.hide();
						var preview = r.message;
						if (preview.count === 0) {
							frappe.msgprint({
								title: __("Nothing to Rewrite"),
								indicator: "orange",
								message: __("No File rows matched the prefix {0}.", [frappe.utils.escape_html(values.old_prefix)])
							});
							return;
						}
						var sample_rows = (preview.sample || []).map(function (s) {
							return (
								"<tr>" +
								'<td style="padding:4px 8px"><code>' +
								frappe.utils.escape_html(s.name) +
								"</code></td>" +
								'<td style="padding:4px 8px;color:#dc3545">' +
								frappe.utils.escape_html(s.old) +
								"</td>" +
								'<td style="padding:4px 8px;color:#28a745">' +
								frappe.utils.escape_html(s.new) +
								"</td>" +
								"</tr>"
							);
						}).join("");
						frappe.confirm(
							__("Rewrite {0} File row(s)?", [preview.count]) +
								'<br><br><table style="border-collapse:collapse;font-size:12px">' +
								"<thead><tr>" +
								'<th style="padding:4px 8px">Name</th>' +
								'<th style="padding:4px 8px">Old</th>' +
								'<th style="padding:4px 8px">New</th>' +
								"</tr></thead><tbody>" +
								sample_rows +
								"</tbody></table>" +
								(preview.count > preview.sample.length
									? "<p><i>(showing " + preview.sample.length + " of " + preview.count + ")</i></p>"
									: ""),
							function () {
								frappe.call({
									method: "frappe_s3_attachment.controller.rewrite_public_file_urls",
									args: {
										old_prefix: values.old_prefix,
										new_prefix: values.new_prefix,
										dry_run: 0
									},
									freeze: true,
									freeze_message: __("Rewriting file URLs..."),
									callback: function (rr) {
										if (rr.exc || !rr.message) return;
										frappe.msgprint({
											title: __("Rewrite Complete"),
											indicator: "green",
											message: __("Rewrote {0} File row(s).", [rr.message.count])
										});
									}
								});
							}
						);
					}
				});
			}
		});
		d.show();
	},
	test_s3_connection: function(frm) {
		frappe.call({
			method: "frappe_s3_attachment.controller.test_s3_connection",
			btn: frm.fields_dict.test_s3_connection.$btn,
			freeze: true,
			freeze_message: __("Running full S3 health check..."),
			callback: function(r) {
				// Server returns a structured report:
				//   { title, indicator: 'green'|'orange'|'red', message: <html> }
				// Render it via frappe.msgprint with wide:true and an indicator
				// so the modal stays open until the user clicks Close.
				if (r.message && r.message.message) {
					frappe.msgprint({
						title: r.message.title || __("S3 Health Check"),
						indicator: r.message.indicator || 'green',
						message: r.message.message,
						wide: true,
						as_table: false
					});
				} else if (r.exc) {
					frappe.msgprint({
						title: __("Error"),
						indicator: 'red',
						message: __(r.exc),
						wide: true
					});
				}
			},
			error: function(r) {
				frappe.msgprint({
					title: __("Error"),
					indicator: 'red',
					message: __("Request failed. Check browser console."),
					wide: true
				});
			}
		});
	}
});
