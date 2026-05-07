"""
One-shot upgrade patch.

Earlier builds of this app could store direct ``http(s)://...`` file URLs
in ``tabFile.file_url`` when the legacy ``public_endpoint_url`` (CDN /
Public URL) field was set. That field has been removed; the app now uses
the universal redirect path for every file. This patch converges any
remaining legacy rows so they keep working without a CDN host.

Idempotent: re-running it does nothing because the SQL filter excludes
rows that already use the redirect form.
"""

import frappe


def execute():
    rows = frappe.db.sql(
        """SELECT name, content_hash, file_name
           FROM `tabFile`
           WHERE file_url LIKE 'http%'
             AND content_hash IS NOT NULL
             AND content_hash != ''""",
        as_dict=True,
    )
    if not rows:
        return

    method_path = "frappe_s3_attachment.controller.generate_file"
    for r in rows:
        new_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method_path, r["content_hash"], r["file_name"] or ""
        )
        frappe.db.set_value(
            "File", r["name"], "file_url", new_url, update_modified=False
        )
    frappe.db.commit()
