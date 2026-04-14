from __future__ import unicode_literals

import datetime
import os
import random
import re
import string
import urllib3

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError

import frappe

import magic


class S3Operations(object):

    def __init__(self):
        """
        Initialise the S3 client from the 'S3 File Attachment' doctype settings.
        Supports any S3-compatible provider: AWS S3, Cloudflare R2, Oracle Object
        Storage, MinIO, etc.
        """
        self.s3_settings_doc = frappe.get_doc(
            'S3 File Attachment',
            'S3 File Attachment',
        )

        self.BUCKET = self.s3_settings_doc.bucket_name
        self.folder_name = self.s3_settings_doc.folder_name

        # --- SSL verification ---
        # When ignore_ssl is checked, skip certificate validation.
        self.verify_ssl = not bool(self.s3_settings_doc.get("ignore_ssl"))
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # --- Addressing style ---
        # force_path_style is required for Oracle Object Storage, MinIO, and
        # providers that don't support virtual-hosted-style bucket addressing.
        addressing_style = (
            'path'
            if self.s3_settings_doc.get("force_path_style")
            else 'auto'
        )

        s3_config = Config(
            signature_version='s3v4',
            s3={'addressing_style': addressing_style},
        )

        # Retrieve password properly for password type fields
        aws_secret = self.s3_settings_doc.get_password("aws_secret")
        if not aws_secret:
            # Fallback to the field itself in case of older un-migrated setups
            aws_secret = self.s3_settings_doc.get("aws_secret")

        # Normalise endpoint_url: treat empty string as None so boto3 uses AWS
        endpoint_url = self.s3_settings_doc.endpoint_url or None

        if (
            self.s3_settings_doc.aws_key and
            aws_secret
        ):
            self.S3_CLIENT = boto3.client(
                's3',
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=aws_secret,
                region_name=self.s3_settings_doc.region_name or None,
                endpoint_url=endpoint_url,
                config=s3_config,
                verify=self.verify_ssl,
            )
        else:
            self.S3_CLIENT = boto3.client(
                's3',
                region_name=self.s3_settings_doc.region_name or None,
                endpoint_url=endpoint_url,
                config=s3_config,
                verify=self.verify_ssl,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def strip_special_chars(self, file_name):
        """
        Strips file characters which don't match the regex.
        """
        regex = re.compile('[^0-9a-zA-Z._-]')
        file_name = regex.sub('', file_name)
        return file_name

    def key_generator(self, file_name, parent_doctype, parent_name):
        """
        Generate keys for s3 objects uploaded with file name attached.
        """
        hook_cmd = frappe.get_hooks().get("s3_key_generator")
        if hook_cmd:
            try:
                k = frappe.get_attr(hook_cmd[0])(
                    file_name=file_name,
                    parent_doctype=parent_doctype,
                    parent_name=parent_name
                )
                if k:
                    return k.rstrip('/').lstrip('/')
            except Exception:
                pass

        file_name = file_name.replace(' ', '_')
        file_name = self.strip_special_chars(file_name)
        key = ''.join(
            random.choice(
                string.ascii_uppercase + string.digits) for _ in range(8)
        )

        if self.folder_name:
            folder_stripped = self.folder_name.strip('/')
            final_key = folder_stripped + "/" + parent_doctype + "/" + key + "_" + file_name
        else:
            final_key = parent_doctype + "/" + key + "_" + file_name
        return final_key

    def get_public_url(self, key):
        """
        Build the public (non-signed) URL for a key.

        - Custom CDN/Public domain: ``{cdn}/{key}``
        - Custom endpoint (R2, Oracle, MinIO, …): always path-style
          ``{endpoint}/{bucket}/{key}``.
        - Standard AWS without a custom endpoint: respects force_path_style.
          Path-style  → ``https://s3.{region}.amazonaws.com/{bucket}/{key}``
          Virtual     → ``https://{bucket}.s3.{region}.amazonaws.com/{key}``
        """
        public_endpoint = self.s3_settings_doc.get("public_endpoint_url")
        if public_endpoint:
            return '{}/{}'.format(public_endpoint.rstrip('/'), key)

        endpoint_url = self.s3_settings_doc.endpoint_url or None
        region = self.s3_settings_doc.region_name or 'us-east-1'
        force_path = bool(self.s3_settings_doc.get("force_path_style"))

        if endpoint_url:
            if force_path:
                return '{}/{}/{}'.format(endpoint_url.rstrip('/'), self.BUCKET, key)
            else:
                from urllib.parse import urlparse
                parsed = urlparse(endpoint_url)
                return '{}://{}.{}/{}'.format(parsed.scheme, self.BUCKET, parsed.netloc, key)

        # Native AWS
        if force_path:
            return 'https://s3.{}.amazonaws.com/{}/{}'.format(region, self.BUCKET, key)
        else:
            return 'https://{}.s3.{}.amazonaws.com/{}'.format(self.BUCKET, region, key)

    # ------------------------------------------------------------------
    # Core S3 operations
    # ------------------------------------------------------------------

    def upload_files_to_s3_with_key(
            self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to S3.
        Strips the file extension to set the content_type in metadata.
        """
        mime_type = magic.from_file(file_path, mime=True)
        key = self.key_generator(file_name, parent_doctype, parent_name)
        content_type = mime_type
        try:
            if is_private:
                self.S3_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "Metadata": {
                            "ContentType": content_type,
                            "file_name": file_name
                        }
                    }
                )
            else:
                self.S3_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "ACL": 'public-read',
                        "Metadata": {
                            "ContentType": content_type,
                        }
                    }
                )

        except Exception as e:
            import traceback
            frappe.log_error(message=traceback.format_exc(), title="S3 File Attachment Upload Failed")
            return None
        return key

    def delete_from_s3(self, key):
        """Delete file from s3"""
        if self.s3_settings_doc.delete_file_from_cloud:
            try:
                self.S3_CLIENT.delete_object(
                    Bucket=self.s3_settings_doc.bucket_name,
                    Key=key
                )
            except Exception as e:
                import traceback
                frappe.log_error(message=traceback.format_exc(), title="S3 File Attachment Delete Failed")

    def read_file_from_s3(self, key):
        """
        Function to read file from a s3 file.
        """
        return self.S3_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

    def get_url(self, key, file_name=None):
        """
        Return a pre-signed URL for a private object.

        :param key: s3 object key
        :param file_name: optional filename for Content-Disposition header
        """
        expiry = self.s3_settings_doc.signed_url_expiry_time or 120

        params = {
            'Bucket': self.BUCKET,
            'Key': key,
        }
        if file_name:
            params['ResponseContentDisposition'] = 'filename={}'.format(file_name)

        url = self.S3_CLIENT.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expiry,
        )

        return url


# ------------------------------------------------------------------
# Frappe hooks / whitelisted methods
# ------------------------------------------------------------------

@frappe.whitelist()
def file_upload_to_s3(doc, method):
    """
    Check and upload files to S3. Called via the File after_insert hook.
    """
    if not frappe.db.get_single_value("S3 File Attachment", "enabled"):
        return

    try:
        s3_upload = S3Operations()
    except Exception as e:
        import traceback
        frappe.log_error(message=traceback.format_exc(), title="S3 Operations Init Failed")
        return

    path = doc.file_url
    site_path = frappe.utils.get_site_path()
    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    ignore_s3_upload_for_doctype = (
        frappe.local.conf.get('ignore_s3_upload_for_doctype') or ['Data Import']
    )
    if parent_doctype not in ignore_s3_upload_for_doctype:
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path

        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name,
            doc.is_private, parent_doctype,
            parent_name
        )
        if not key:
            return

        if doc.is_private:
            method = "frappe_s3_attachment.controller.generate_file"
            file_url = """/api/method/{0}?key={1}&file_name={2}""".format(
                method, key, doc.file_name
            )
        else:
            file_url = s3_upload.get_public_url(key)

        os.remove(file_path)
        frappe.db.sql(
            """UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s WHERE name=%s""",
            (file_url, 'Home/Attachments', 'Home/Attachments', key, doc.name)
        )

        doc.file_url = file_url

        if parent_doctype and frappe.get_meta(parent_doctype).get('image_field'):
            frappe.db.set_value(
                parent_doctype, parent_name,
                frappe.get_meta(parent_doctype).get('image_field'),
                file_url
            )

        frappe.db.commit()


@frappe.whitelist()
def generate_file(key=None, file_name=None):
    """
    Stream a private file from S3 via a pre-signed redirect.
    """
    if key:
        s3_upload = S3Operations()
        signed_url = s3_upload.get_url(key, file_name)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = signed_url
    else:
        frappe.local.response['body'] = "Key not found."
    return


def upload_existing_files_s3(name):
    """
    Function to upload all existing files.
    """
    file_doc_name = frappe.db.get_value('File', {'name': name})
    if file_doc_name:
        doc = frappe.get_doc('File', name)
        s3_upload = S3Operations()
        path = doc.file_url
        site_path = frappe.utils.get_site_path()
        parent_doctype = doc.attached_to_doctype
        parent_name = doc.attached_to_name
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path

        # File exists?
        if not os.path.exists(file_path):
            return

        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name,
            doc.is_private, parent_doctype,
            parent_name
        )
        if not key:
            return

        if doc.is_private:
            method = "frappe_s3_attachment.controller.generate_file"
            file_url = """/api/method/{0}?key={1}""".format(method, key)
        else:
            file_url = s3_upload.get_public_url(key)

        # Remove file from local.
        os.remove(file_path)

        frappe.db.sql(
            """UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s WHERE name=%s""",
            (file_url, "Home/Attachments", "Home/Attachments", key, doc.name),
        )
        frappe.db.commit()


def s3_file_regex_match(file_url):
    """
    Match the public file regex match.
    """
    return re.match(
        r'^(https?:|/api/method/frappe_s3_attachment.controller.generate_file)',
        file_url
    )


@frappe.whitelist()
def migrate_existing_files():
    """
    Function to migrate the existing files to s3.
    """
    if not frappe.db.get_single_value("S3 File Attachment", "enabled"):
        frappe.msgprint(frappe._("Please enable S3 File Attachment settings first."))
        return False

    frappe.enqueue(
        "frappe_s3_attachment.controller.run_migration_in_background",
        queue="long",
        timeout=3600
    )
    frappe.msgprint(frappe._("File migration started in the background. You can safely close this window."))
    return True


def run_migration_in_background():
    """
    Background job to process all local files to S3.
    """
    files_list = frappe.get_all(
        'File',
        fields=['name', 'file_url']
    )
    for file in files_list:
        if file.get('file_url'):
            if not s3_file_regex_match(file.get('file_url')):
                upload_existing_files_s3(file.get('name'))


def delete_from_cloud(doc, method):
    """Delete file from s3"""
    if not frappe.db.get_single_value("S3 File Attachment", "enabled"):
        return
        
    try:
        s3 = S3Operations()
        s3.delete_from_s3(doc.content_hash)
    except Exception as e:
        import traceback
        frappe.log_error(message=traceback.format_exc(), title="S3 Delete Operations Failed")


@frappe.whitelist()
def ping():
    """
    Test function to check if api function work.
    """
    return "pong"
