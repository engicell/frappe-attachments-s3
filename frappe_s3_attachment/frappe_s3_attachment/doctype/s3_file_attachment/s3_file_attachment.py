# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

from urllib.parse import urlparse

import frappe
from frappe.model.document import Document


class S3FileAttachment(Document):
	def validate(self):
		self.endpoint_url = _normalize_url(self.endpoint_url, "Endpoint URL")


def _normalize_url(value, field_label):
	"""
	Normalise a user-entered URL field:
	  - strip whitespace
	  - prepend ``https://`` if no scheme is present
	  - strip a trailing slash
	  - reject anything that does not parse to a valid host
	Empty values pass through unchanged.
	"""
	if not value:
		return value

	value = value.strip()
	if not value:
		return ""

	if not value.lower().startswith(("http://", "https://")):
		value = "https://" + value

	value = value.rstrip("/")

	parsed = urlparse(value)
	if not parsed.netloc:
		frappe.throw(
			frappe._("{0} is not a valid URL: {1}").format(field_label, value)
		)

	return value
