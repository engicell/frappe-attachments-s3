# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from . import __version__ as app_version

app_name = "frappe_s3_attachment"
app_title = "Frappe S3 Attachment"
app_publisher = "Engicell"
app_description = "Frappe app to upload attachments to any S3-compatible object storage: AWS S3, Cloudflare R2, Oracle Object Storage, MinIO, and more."
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "info@engicell.com"
app_license = "MIT"
minimum_frappe_version = "13.0"
fixtures = ["Custom Field", "DocType"]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/frappe_s3_attachment/css/frappe_s3_attachment.css"
# app_include_js = "/assets/frappe_s3_attachment/js/frappe_s3_attachment.js"

# include js in doctype views
doctype_js = {
    "S3 File Attachment": "frappe_s3_attachment/doctype/s3_file_attachment/s3_file_attachment.js"
}

# Document Events
# ---------------
doc_events = {
    "File": {
        "after_insert": "frappe_s3_attachment.controller.file_upload_to_s3",
        "on_trash": "frappe_s3_attachment.controller.delete_from_cloud",
    }
}
