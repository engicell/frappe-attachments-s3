from __future__ import unicode_literals

import datetime
import os
import random
import re
import string
import urllib3
from urllib.parse import quote, urlparse

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
            "S3 File Attachment",
            "S3 File Attachment",
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
            "path" if self.s3_settings_doc.get("force_path_style") else "auto"
        )

        s3_config = Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing_style, "payload_signing_enabled": False},
        )

        # Retrieve password properly for password type fields
        aws_secret = self.s3_settings_doc.get_password("aws_secret")
        if not aws_secret:
            # Fallback to the field itself in case of older un-migrated setups
            aws_secret = self.s3_settings_doc.get("aws_secret")

        # Normalise endpoint_url: treat empty string as None so boto3 uses AWS
        endpoint_url = self.s3_settings_doc.endpoint_url or None

        if self.s3_settings_doc.aws_key and aws_secret:
            self.S3_CLIENT = boto3.client(
                "s3",
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=aws_secret,
                region_name=self.s3_settings_doc.region_name or None,
                endpoint_url=endpoint_url,
                config=s3_config,
                verify=self.verify_ssl,
            )
        else:
            self.S3_CLIENT = boto3.client(
                "s3",
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
        regex = re.compile("[^0-9a-zA-Z._-]")
        file_name = regex.sub("", file_name)
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
                    parent_name=parent_name,
                )
                if k:
                    return k.rstrip("/").lstrip("/")
            except Exception:
                pass

        file_name = file_name.replace(" ", "_")
        file_name = self.strip_special_chars(file_name)
        key = "".join(
            random.choice(string.ascii_uppercase + string.digits) for _ in range(8)
        )

        if parent_doctype is None:
            parent_doctype = "File"

        if self.folder_name:
            folder_stripped = self.folder_name.strip("/")
            final_key = (
                folder_stripped + "/" + parent_doctype + "/" + key + "_" + file_name
            )
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

        The ``key`` is URL-encoded so spaces / parentheses / unicode in the
        object name don't produce technically-invalid URLs that some browsers
        or CDNs reject. Slashes are preserved so the path structure is intact.
        """
        encoded_key = quote(key, safe="/")

        public_endpoint = self.s3_settings_doc.get("public_endpoint_url") or ""
        public_endpoint = public_endpoint.strip().rstrip("/")
        if public_endpoint:
            # Safety net for setups that bypassed the doctype validate() —
            # always emit an absolute URL, never a relative one.
            if not public_endpoint.lower().startswith(("http://", "https://")):
                public_endpoint = "https://" + public_endpoint
            return "{}/{}".format(public_endpoint, encoded_key)

        endpoint_url = self.s3_settings_doc.endpoint_url or None
        region = self.s3_settings_doc.region_name or "us-east-1"
        force_path = bool(self.s3_settings_doc.get("force_path_style"))

        if endpoint_url:
            if force_path:
                return "{}/{}/{}".format(
                    endpoint_url.rstrip("/"), self.BUCKET, encoded_key
                )
            else:
                parsed = urlparse(endpoint_url)
                return "{}://{}.{}/{}".format(
                    parsed.scheme, self.BUCKET, parsed.netloc, encoded_key
                )

        # Native AWS
        if force_path:
            return "https://s3.{}.amazonaws.com/{}/{}".format(
                region, self.BUCKET, encoded_key
            )
        else:
            return "https://{}.s3.{}.amazonaws.com/{}".format(
                self.BUCKET, region, encoded_key
            )

    # ------------------------------------------------------------------
    # Core S3 operations
    # ------------------------------------------------------------------

    def upload_files_to_s3_with_key(
        self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to S3 using presigned URL (fixes Oracle Cloud compatibility).
        """
        import requests as req_lib

        mime_type = magic.from_file(file_path, mime=True)
        key = self.key_generator(file_name, parent_doctype, parent_name)
        content_type = mime_type

        try:
            with open(file_path, "rb") as f:
                file_content = f.read()

            extra_args = {"ContentType": content_type}
            # Skip ACL when configured (e.g. Cloudflare R2 doesn't support
            # x-amz-acl and will fail SigV4 validation if it's signed).
            if not is_private and not self.s3_settings_doc.get("disable_acl"):
                extra_args["ACL"] = "public-read"

            presigned_url = self.S3_CLIENT.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.BUCKET, "Key": key, **extra_args},
                ExpiresIn=300,
            )

            response = req_lib.put(
                presigned_url,
                data=file_content,
                headers={"Content-Type": content_type},
                verify=self.verify_ssl,
                timeout=60,
            )

            if response.status_code not in (200, 201, 204):
                frappe.log_error(
                    message=f"Upload failed with status {response.status_code}: {response.text}",
                    title="S3 File Attachment Upload Failed",
                )
                return None

        except Exception as e:
            import traceback

            frappe.log_error(
                message=traceback.format_exc(), title="S3 File Attachment Upload Failed"
            )
            return None
        return key

    def delete_from_s3(self, key):
        """Delete file from s3"""
        if self.s3_settings_doc.delete_file_from_cloud:
            try:
                self.S3_CLIENT.delete_object(
                    Bucket=self.s3_settings_doc.bucket_name, Key=key
                )
            except Exception as e:
                import traceback

                frappe.log_error(
                    message=traceback.format_exc(),
                    title="S3 File Attachment Delete Failed",
                )

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
            "Bucket": self.BUCKET,
            "Key": key,
        }
        if file_name:
            params["ResponseContentDisposition"] = "filename={}".format(file_name)

        url = self.S3_CLIENT.generate_presigned_url(
            "get_object",
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

        frappe.log_error(
            message=traceback.format_exc(), title="S3 Operations Init Failed"
        )
        return

    path = doc.file_url
    site_path = frappe.utils.get_site_path()
    parent_doctype = doc.attached_to_doctype or "File"
    parent_name = doc.attached_to_name
    ignore_s3_upload_for_doctype = frappe.local.conf.get(
        "ignore_s3_upload_for_doctype"
    ) or ["Data Import"]
    if parent_doctype not in ignore_s3_upload_for_doctype:
        if not doc.is_private:
            file_path = site_path + "/public" + path
        else:
            file_path = site_path + path

        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name, doc.is_private, parent_doctype, parent_name
        )
        if not key:
            return

        # Decide where the browser will fetch this file:
        #  - Private files always go through the redirect endpoint (signed
        #    URL with a permission check).
        #  - Public files default to the redirect endpoint too — that path
        #    works on every S3-compatible provider, including R2 and Oracle
        #    whose S3-API hosts refuse anonymous reads. Direct CDN URLs are
        #    opt-in: only used when `public_endpoint_url` is configured.
        public_endpoint = (
            s3_upload.s3_settings_doc.get("public_endpoint_url") or ""
        ).strip()
        if doc.is_private or not public_endpoint:
            method_path = "frappe_s3_attachment.controller.generate_file"
            file_url = "/api/method/{0}?key={1}&file_name={2}".format(
                method_path, key, doc.file_name
            )
        else:
            file_url = s3_upload.get_public_url(key)

        os.remove(file_path)
        frappe.db.sql(
            """UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s WHERE name=%s""",
            (file_url, "Home/Attachments", "Home/Attachments", key, doc.name),
        )

        doc.file_url = file_url

        if parent_doctype and frappe.get_meta(parent_doctype).get("image_field"):
            frappe.db.set_value(
                parent_doctype,
                parent_name,
                frappe.get_meta(parent_doctype).get("image_field"),
                file_url,
            )

        frappe.db.commit()


@frappe.whitelist(allow_guest=True)
def generate_file(key=None, file_name=None):
    """
    Stream a file from S3 via a pre-signed redirect.

    Used for both public and private files (public files default to this
    path so the app works universally on every S3-compatible provider).

    Guest access is allowed only when the matching ``tabFile`` record is
    public. Private files require a logged-in session — anonymous callers
    holding a key cannot retrieve them.
    """
    if not key:
        frappe.local.response["body"] = "Key not found."
        return

    # The S3 key is stored in the File record's `content_hash` field.
    # If a public File row exists for this key, treat the request as public.
    # Otherwise (private-only match, or no match for legacy/manual rows)
    # require an authenticated session.
    is_public = bool(
        frappe.db.exists("File", {"content_hash": key, "is_private": 0})
    )
    if not is_public and frappe.session.user == "Guest":
        raise frappe.PermissionError(
            frappe._("You must be logged in to access this file.")
        )

    s3_upload = S3Operations()
    signed_url = s3_upload.get_url(key, file_name)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = signed_url
    return


def upload_existing_files_s3(name):
    """
    Function to upload all existing files.
    """
    file_doc_name = frappe.db.get_value("File", {"name": name})
    if file_doc_name:
        doc = frappe.get_doc("File", name)
        s3_upload = S3Operations()
        path = doc.file_url
        site_path = frappe.utils.get_site_path()
        parent_doctype = doc.attached_to_doctype
        parent_name = doc.attached_to_name
        if not doc.is_private:
            file_path = site_path + "/public" + path
        else:
            file_path = site_path + path

        # File exists?
        if not os.path.exists(file_path):
            return

        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name, doc.is_private, parent_doctype, parent_name
        )
        if not key:
            return

        # Mirror file_upload_to_s3: redirect-by-default for both private and
        # public files; only emit a direct CDN URL when public_endpoint_url
        # is set.
        public_endpoint = (
            s3_upload.s3_settings_doc.get("public_endpoint_url") or ""
        ).strip()
        if doc.is_private or not public_endpoint:
            method_path = "frappe_s3_attachment.controller.generate_file"
            file_url = "/api/method/{0}?key={1}&file_name={2}".format(
                method_path, key, doc.file_name or ""
            )
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
        r"^(https?:|/api/method/frappe_s3_attachment.controller.generate_file)",
        file_url,
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
        timeout=3600,
    )
    frappe.msgprint(
        frappe._(
            "File migration started in the background. You can safely close this window."
        )
    )
    return True


def run_migration_in_background():
    """
    Background job to process all local files to S3.
    """
    files_list = frappe.get_all("File", fields=["name", "file_url"])
    for file in files_list:
        if file.get("file_url"):
            if not s3_file_regex_match(file.get("file_url")):
                upload_existing_files_s3(file.get("name"))


def delete_from_cloud(doc, method):
    """Delete file from s3"""
    if not frappe.db.get_single_value("S3 File Attachment", "enabled"):
        return

    try:
        s3 = S3Operations()
        s3.delete_from_s3(doc.content_hash)
    except Exception as e:
        import traceback

        frappe.log_error(
            message=traceback.format_exc(), title="S3 Delete Operations Failed"
        )


@frappe.whitelist()
def test_s3_connection():
    """
    Full end-to-end S3 health check.

    Runs every code path the app actually uses, end-to-end against the live
    bucket, and returns a structured report. Intended to be rendered by the
    JS form handler in a sticky `frappe.msgprint({wide:true, indicator:..})`
    dialog that the user must dismiss explicitly.

    Stages:
      1. Settings sanity      — fields present + provider-specific advice
      2. Credentials + LIST   — list_objects_v2 (no Content-Length, works on
                                 every provider including Oracle OCI)
      3. Upload (PUT)         — presigned PUT via requests (bypasses boto3
                                 chunked-transfer issues)
      4. Private read flow    — presigned GET; verify body matches what we
                                 wrote (this is the path used by every
                                 `is_private=1` file in the system)
      5. Public read flow     — anonymous GET against `public_endpoint_url`
                                 if set; SKIP with a clear warning if not.
                                 Public files in the system will be
                                 unreachable to browsers without this.
      6. Delete (DELETE)      — clean up the test object and verify it is
                                 actually gone via head_object 404.

    A failure short-circuits the run for any stage that would invalidate
    later stages (no point testing reads if the upload didn't happen).
    Stages that are non-fatal (missing public host, ACL config advisory)
    surface as WARN, not FAIL.

    Returns:
        dict with keys: title, indicator (green|orange|red), message (HTML).
    """
    import requests as req_lib

    # ── Helpers ──────────────────────────────────────────────────────────────
    PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"
    ICON = {
        PASS: '<span style="color:#28a745">&#10003;</span>',  # ✓
        FAIL: '<span style="color:#dc3545">&#10007;</span>',  # ✗
        WARN: '<span style="color:#fd7e14">&#9888;</span>',   # ⚠
        SKIP: '<span style="color:#6c757d">&#8854;</span>',   # ⊘
    }
    LABEL = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP"}
    stages = []

    def add(name, status, detail=""):
        stages.append({"name": name, "status": status, "detail": detail})

    def render():
        any_fail = any(s["status"] == FAIL for s in stages)
        any_warn = any(s["status"] == WARN for s in stages)
        if any_fail:
            indicator, banner = "red", '<b style="color:#dc3545">S3 Health Check FAILED</b>'
        elif any_warn:
            indicator, banner = "orange", '<b style="color:#fd7e14">S3 Health Check passed with WARNINGS</b>'
        else:
            indicator, banner = "green", '<b style="color:#28a745">All S3 checks passed</b>'
        rows = "".join(
            f"<tr>"
            f'<td style="white-space:nowrap;padding:4px 10px">{ICON[s["status"]]} <b>{LABEL[s["status"]]}</b></td>'
            f'<td style="padding:4px 10px"><b>{frappe.utils.escape_html(s["name"])}</b></td>'
            f'<td style="padding:4px 10px">{s["detail"]}</td>'
            f"</tr>"
            for s in stages
        )
        message = (
            f'<div style="font-size:13px;line-height:1.5">'
            f'<p style="margin:0 0 10px 0">{banner}</p>'
            f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
            f'</div>'
        )
        return {"title": "S3 Health Check", "indicator": indicator, "message": message}

    # ── Pre-flight: enabled flag ────────────────────────────────────────────
    if not frappe.db.get_single_value("S3 File Attachment", "enabled"):
        add("Settings", FAIL, "S3 File Attachment is not enabled. Tick "
                              "<b>Enable S3 Attachment</b> and save first.")
        return render()

    # ── Stage 1: Settings sanity ────────────────────────────────────────────
    try:
        s3 = S3Operations()
    except Exception as e:
        import traceback
        frappe.log_error(message=traceback.format_exc(), title="S3 Test Failed")
        add("Settings", FAIL, f"Could not initialise S3 client: "
                              f"{frappe.utils.escape_html(str(e))}")
        return render()

    settings = s3.s3_settings_doc
    endpoint = (settings.endpoint_url or "").rstrip("/")
    public_endpoint = (settings.get("public_endpoint_url") or "").rstrip("/")

    sanity_lines = []
    if not settings.bucket_name:
        sanity_lines.append("Bucket Name is empty.")
    if not settings.aws_key:
        sanity_lines.append("Access Key ID is empty.")
    if not settings.get_password("aws_secret", raise_exception=False):
        sanity_lines.append("Secret Access Key is empty.")
    # provider-specific advisories
    if "r2.cloudflarestorage" in endpoint and not settings.get("disable_acl"):
        sanity_lines.append(
            "Endpoint looks like Cloudflare R2 but <b>Disable Object ACL</b> "
            "is OFF. R2 will reject signed uploads with x-amz-acl. "
            "Tick the checkbox.")
    if sanity_lines:
        critical = any("empty" in l for l in sanity_lines)
        add("Settings", FAIL if critical else WARN, "<br>".join(sanity_lines))
        if critical:
            return render()
    else:
        add("Settings", PASS, f"Endpoint: <code>{frappe.utils.escape_html(endpoint or 'native AWS')}</code>")

    # ── Stage 2: credentials + LIST ────────────────────────────────────────
    try:
        s3.S3_CLIENT.list_objects_v2(Bucket=s3.BUCKET, MaxKeys=1)
        add("Credentials & LIST permission", PASS,
            f"Bucket <code>{frappe.utils.escape_html(s3.BUCKET)}</code> reachable.")
    except Exception as e:
        msg = str(e)
        if "NoSuchBucket" in msg or "does not exist" in msg:
            hint = "Bucket does not exist. Check the <b>Bucket Name</b>."
        elif "AccessDenied" in msg or "403" in msg:
            hint = ("Access denied. Credentials are wrong, or the IAM/token "
                    "is missing the LIST permission.")
        elif "InvalidAccessKeyId" in msg or "SignatureDoesNotMatch" in msg:
            hint = "Access Key or Secret is wrong (or copied with whitespace)."
        else:
            hint = "Endpoint URL or Region may be wrong."
        add("Credentials & LIST permission", FAIL,
            f"{hint}<br><small>{frappe.utils.escape_html(msg)[:400]}</small>")
        return render()

    # ── Stage 3: Upload via presigned PUT ──────────────────────────────────
    test_key = "s3_connection_test_" + frappe.generate_hash()[:8] + ".txt"
    if s3.folder_name:
        test_key = s3.folder_name.strip("/") + "/" + test_key
    body_bytes = b"S3 Connection Test - ERPNext S3 Attachment"

    try:
        put_args = {"Bucket": s3.BUCKET, "Key": test_key, "ContentType": "text/plain"}
        # Mirror the upload path's ACL behaviour: only sign with public-read
        # if both not-private semantics AND user has not opted out via
        # `disable_acl` (R2/Oracle compat).
        will_send_acl = not settings.get("disable_acl")
        if will_send_acl:
            put_args["ACL"] = "public-read"
        presigned_put = s3.S3_CLIENT.generate_presigned_url(
            "put_object", Params=put_args, ExpiresIn=120)
        resp = req_lib.put(
            presigned_put, data=body_bytes,
            headers={"Content-Type": "text/plain"},
            verify=s3.verify_ssl, timeout=30)
        if resp.status_code in (200, 201, 204):
            add("Upload (PUT)", PASS,
                f"Stored at <code>{frappe.utils.escape_html(test_key)}</code> "
                f"({len(body_bytes)} bytes)" +
                (" with ACL=public-read" if will_send_acl else " without ACL"))
        else:
            extra = ""
            if will_send_acl and "SignatureDoesNotMatch" in resp.text:
                extra = ("<br><b>Likely cause:</b> provider does not support "
                         "x-amz-acl (e.g. Cloudflare R2). Tick "
                         "<b>Disable Object ACL</b> and retest.")
            add("Upload (PUT)", FAIL,
                f"HTTP {resp.status_code}<br>"
                f"<small>{frappe.utils.escape_html(resp.text[:400])}</small>{extra}")
            return render()
    except Exception as e:
        add("Upload (PUT)", FAIL, frappe.utils.escape_html(str(e))[:400])
        return render()

    # ── Stage 4: Private read flow (presigned GET) ─────────────────────────
    try:
        signed_get = s3.S3_CLIENT.generate_presigned_url(
            "get_object",
            Params={"Bucket": s3.BUCKET, "Key": test_key},
            ExpiresIn=120)
        r = req_lib.get(signed_get, verify=s3.verify_ssl, timeout=30)
        if r.status_code == 200 and r.content == body_bytes:
            add("Private read flow (presigned GET)", PASS,
                "Signed URL returned the file contents byte-for-byte. "
                "All <i>is_private=1</i> uploads will work end-to-end.")
        else:
            add("Private read flow (presigned GET)", FAIL,
                f"HTTP {r.status_code}, got {len(r.content)} bytes "
                f"(expected {len(body_bytes)})")
    except Exception as e:
        add("Private read flow (presigned GET)", FAIL,
            frappe.utils.escape_html(str(e))[:400])

    # ── Stage 5: Public read flow ──────────────────────────────────────────
    if public_endpoint:
        public_url = f"{public_endpoint}/{test_key}"
        try:
            r = req_lib.get(public_url, verify=s3.verify_ssl, timeout=30,
                            allow_redirects=True)
            if r.status_code == 200 and r.content == body_bytes:
                add("Public read flow (anonymous GET)", PASS,
                    f'Anonymous GET against <a href="{frappe.utils.escape_html(public_endpoint)}" '
                    f'target="_blank"><code>{frappe.utils.escape_html(public_endpoint)}</code></a> '
                    f"returned the file. Public files are reachable in browsers.")
            else:
                add("Public read flow (anonymous GET)", FAIL,
                    f"GET <code>{frappe.utils.escape_html(public_url)}</code> "
                    f"returned HTTP {r.status_code}. Verify the public host "
                    f"actually maps to this bucket (R2 custom domain bound? "
                    f"Public dev URL enabled?).")
        except Exception as e:
            add("Public read flow (anonymous GET)", FAIL,
                frappe.utils.escape_html(str(e))[:400])
    else:
        # No public endpoint set — this is the universal default. Public
        # files are served via the Frappe-side signed-URL redirect, which
        # works on every S3-compatible provider. Treat as PASS with an
        # informational note, not WARN.
        add("Public read flow (Frappe redirect)", PASS,
            "<b>CDN / Public URL</b> is not set — using the universal "
            "redirect path. Public files will be served via "
            "<code>/api/method/frappe_s3_attachment.controller.generate_file</code> "
            "with a fresh signed URL on each request. Works on every "
            "S3-compatible provider. Set <b>CDN / Public URL</b> only if "
            "you want browser-cacheable delivery from a CDN.")

    # ── Stage 6: Delete (cleanup) ──────────────────────────────────────────
    try:
        s3.S3_CLIENT.delete_object(Bucket=s3.BUCKET, Key=test_key)
        # verify it's actually gone
        try:
            s3.S3_CLIENT.head_object(Bucket=s3.BUCKET, Key=test_key)
            add("Delete (cleanup)", FAIL,
                "Delete returned OK but object still exists. Bucket may "
                "have versioning or object lock enabled.")
        except Exception:
            add("Delete (cleanup)", PASS,
                "Test object removed and verified gone.")
    except Exception as e:
        add("Delete (cleanup)", FAIL,
            "Could not delete test object — credentials may lack DELETE "
            "permission. Old uploads may stay in the bucket forever.<br>"
            f"<small>{frappe.utils.escape_html(str(e))[:400]}</small>")

    return render()


# ------------------------------------------------------------------
# Rewrite stored file_url prefixes (e.g. after changing the public host)
# ------------------------------------------------------------------


@frappe.whitelist()
def rewrite_public_file_urls(old_prefix=None, new_prefix=None, dry_run=1):
    """
    Bulk-rewrite ``tabFile.file_url`` rows whose value starts with
    ``old_prefix`` to start with ``new_prefix`` instead.

    Use case: the public hostname (CDN / Public URL) was changed or set after
    files were already uploaded. Existing rows still carry the old hostname
    (or the S3-API hostname, which doesn't serve anonymous reads on R2 /
    Oracle), so browsers 404 on them. This method updates them in one pass.

    Both prefixes must start with ``http://`` or ``https://`` so private file
    URLs (which begin with ``/api/method/...``) are never matched.

    :param old_prefix: existing URL prefix to match (required).
    :param new_prefix: replacement prefix (required).
    :param dry_run:    when truthy, return the count + a sample without
                       writing. When falsy, perform the UPDATE.
    :returns: ``{count, sample, dry_run}``
    """
    if not old_prefix or not new_prefix:
        frappe.throw(frappe._("old_prefix and new_prefix are both required."))

    old_prefix = str(old_prefix).strip().rstrip("/")
    new_prefix = str(new_prefix).strip().rstrip("/")

    if not old_prefix.lower().startswith(("http://", "https://")):
        frappe.throw(
            frappe._("old_prefix must be an absolute URL (http:// or https://).")
        )
    if not new_prefix.lower().startswith(("http://", "https://")):
        frappe.throw(
            frappe._("new_prefix must be an absolute URL (http:// or https://).")
        )
    if old_prefix == new_prefix:
        return {"count": 0, "sample": [], "dry_run": bool(int(dry_run))}

    frappe.only_for("System Manager")

    like_pattern = old_prefix + "%"
    rows = frappe.db.sql(
        """SELECT name, file_url FROM `tabFile` WHERE file_url LIKE %s""",
        (like_pattern,),
        as_dict=True,
    )
    count = len(rows)
    sample = [
        {"name": r["name"], "old": r["file_url"],
         "new": new_prefix + r["file_url"][len(old_prefix):]}
        for r in rows[:5]
    ]

    if int(dry_run):
        return {"count": count, "sample": sample, "dry_run": True}

    if count:
        frappe.db.sql(
            """UPDATE `tabFile`
               SET file_url = CONCAT(%s, SUBSTRING(file_url, %s))
               WHERE file_url LIKE %s""",
            (new_prefix, len(old_prefix) + 1, like_pattern),
        )
        frappe.db.commit()

    return {"count": count, "sample": sample, "dry_run": False}


@frappe.whitelist()
def migrate_urls_to_redirect(dry_run=1):
    """
    One-shot migration: convert direct ``https://...`` File URLs (from older
    public-file uploads) to the universal redirect form
    ``/api/method/frappe_s3_attachment.controller.generate_file?key=...&file_name=...``.

    Targets only rows whose ``content_hash`` is set, i.e. files that were
    actually uploaded to S3 by this app. Idempotent: rows that already use
    the redirect form (or any non-http URL) are skipped by the SQL filter.

    Use this after upgrading to a build where public files default to the
    redirect endpoint, so legacy direct URLs converge to the same shape and
    keep working without a configured CDN host.

    :param dry_run: when truthy, return the count + a sample without
                    writing. When falsy, perform the UPDATE.
    :returns: ``{count, sample, dry_run}``
    """
    frappe.only_for("System Manager")

    rows = frappe.db.sql(
        """SELECT name, content_hash, file_name, file_url
           FROM `tabFile`
           WHERE file_url LIKE 'http%'
             AND content_hash IS NOT NULL
             AND content_hash != ''""",
        as_dict=True,
    )

    method_path = "frappe_s3_attachment.controller.generate_file"
    converted = []
    for r in rows:
        new_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method_path, r["content_hash"], r["file_name"] or ""
        )
        converted.append(
            {"name": r["name"], "old": r["file_url"], "new": new_url}
        )

    count = len(converted)
    sample = converted[:5]

    if int(dry_run):
        return {"count": count, "sample": sample, "dry_run": True}

    for r in converted:
        frappe.db.set_value(
            "File", r["name"], "file_url", r["new"], update_modified=False
        )
    if count:
        frappe.db.commit()

    return {"count": count, "sample": sample, "dry_run": False}
