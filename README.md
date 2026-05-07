# Frappe S3 Attachment

A Frappe app that redirects all file attachments to **any S3-compatible
object storage** provider, with a single universal configuration that works
the same way on every provider.

| Provider | Works with default config? |
|---|---|
| **AWS S3** | ✅ |
| **Cloudflare R2** | ✅ |
| **Oracle Object Storage** | ✅ |
| **MinIO** | ✅ |
| **DigitalOcean Spaces** | ✅ |
| **Wasabi / Backblaze B2 (S3 mode) / any S3-compatible API** | ✅ |

---

## Mental model

- **One bucket. One S3-API endpoint.** That's the whole config.
- **Bucket stays private.** No public access policy, no public hostname,
  no CDN binding required.
- **Every file is served the same way** — public or private — via a
  Frappe-side signed-URL redirect:
  `/api/method/frappe_s3_attachment.controller.generate_file?key=…`.
  Frappe checks permissions, signs a short-lived S3 GET URL, and 302s the
  browser to it. Anonymous (Guest) requests are allowed only when the
  matching `tabFile` row is `is_private=0`.
- **Identical behavior on every provider.** No provider-specific URL
  composition, no per-vendor branching in the request path. One code
  path, one mental model.

---

## Installation

```bash
bench get-app frappe_s3_attachment
bench --site <site-name> install-app frappe_s3_attachment
```

---

## Configuration

Open **S3 File Attachment** (single doctype) in your Frappe desk and fill
in the fields.

### Storage Credentials

| Field | Description |
|---|---|
| **Bucket Name** | Your bucket / container name |
| **Access Key ID** | Your provider's access key (leave blank to use IAM role for AWS) |
| **Secret Access Key** | Your provider's secret key |
| **Region Name** | AWS region (`us-east-1`) or provider-specific region. Leave blank if not required. |
| **Endpoint URL** | S3-API endpoint for non-AWS providers. Leave blank for native AWS S3. Must include `https://`. |
| **Folder Name** | Optional prefix prepended to every uploaded object key. |
| **Per-Company Folders** | When ticked, prepend the parent document's `Company.abbr` to the key. Cross-company masters (Customer, Item, Letter Head) and free-floating uploads land under `_shared/`. See [Per-company folders](#per-company-folders) below. |
| **Signed URL Expiry Time** | Expiry in seconds for pre-signed URLs (default: 300). |

### Advanced Options

| Field | Description |
|---|---|
| **Force Path Style** | Use `{endpoint}/{bucket}/{key}` URL format instead of virtual-hosted style. Tick this if your provider does not support virtual-hosted-style addressing. |
| **Ignore SSL Certificate Verification** | Disable TLS verification. Use only with self-signed certs on internal networks. Not for production. |
| **Disable Object ACL** | Skip the per-object `x-amz-acl` header on uploads. Tick this if your provider or bucket policy rejects ACLs (signed PUTs fail with `SignatureDoesNotMatch` when an ACL is sent and not accepted). |

The Endpoint URL is normalised on save: a bare hostname is auto-prefixed
with `https://`, trailing slashes are stripped, invalid values are
rejected.

---

## Choosing field values

The same set of fields is used on every provider. Two of the Advanced
Options exist precisely because S3-compatible providers and modern AWS
bucket policies have diverged on two dimensions: addressing style and
object ACLs. Pick the right combination based on what your provider
actually accepts:

| Field | Default | Tick when |
|---|---|---|
| **Endpoint URL** | (blank → native AWS) | Your provider gives you an S3-API endpoint URL. Paste it as-is. |
| **Region Name** | (blank) | Your provider requires a region. Some accept any string (e.g. `auto`). |
| **Force Path Style** | ☐ | Virtual-hosted-style requests fail (`{bucket}.{endpoint}` is not a valid host on your provider). Symptom: TLS / DNS errors on upload. |
| **Disable Object ACL** | ☐ | Signed PUT uploads fail with `SignatureDoesNotMatch` because the bucket / provider does not accept the `x-amz-acl` header. Modern AWS buckets in "Bucket owner enforced" mode also need this on. |

If unsure, leave both Advanced Options unticked, click **Test S3
Connection**, and toggle them based on the failure messages — the health
check produces specific hints when an upload fails for either reason.

---

## How It Works

- **Upload.** On `File.after_insert`, the file is uploaded to the
  configured bucket via a presigned PUT, and the local copy is removed.
- **Read.** Every `file_url` produced by this app has the form
  `/api/method/frappe_s3_attachment.controller.generate_file?key=…&file_name=…`.
  Frappe receives the request, generates a fresh signed S3 GET URL, and
  302-redirects the browser to it.
- **Permissions.** Public files (`is_private=0`) allow Guest access;
  private files require an authenticated session. The check happens in
  Frappe before the redirect is issued — the bucket itself stays private.

This is the universal path: one URL shape, identical on every provider,
no anonymous bucket reads required.

---

## Per-company folders

Tick **Per-Company Folders** in **S3 File Attachment** to organise uploads
by company. The S3 key becomes:

```
[<folder_name>/]<company_abbr>/<parent_doctype>/<rand>_<file_name>
```

Example with `Folder Name` blank, two ERPNext companies (abbr `EPL` and
`ENG-US`):

```
EPL/Sales Invoice/AB12CD34_INV-2026-001.pdf
EPL/Employee/Z9Y8X7W6_passport.jpg
ENG-US/Purchase Order/Q1W2E3R4_quote.pdf
_shared/Customer/M5N6B7V8_logo.png
_shared/Item/T8R7E6W5_datasheet.pdf
```

Resolution rules:

- Parent doctype has a `company` field and the value is set → `Company.abbr`
  (sanitised to `[0-9A-Za-z._-]`).
- Parent doctype has no `company` field (Customer, Supplier, Item, Address,
  Contact, Letter Head, …) → `_shared`.
- Free-floating upload (no parent doc) → `_shared`.

Existing files are **not** relocated — only new uploads use the new layout.
Copying objects in S3 plus updating `tabFile.content_hash` is risky and
unnecessary, since old paths keep working.

Apps that need a different scheme entirely can register an
`s3_key_generator` hook in their `hooks.py`; that hook overrides the whole
layout (per-company flag included).

---

## Migrating Existing Files

### Local files → S3
Open **S3 File Attachment** and click **Migrate Existing Files**. New
uploads from any source automatically go to S3 once enabled.

### Legacy direct URLs → universal redirect form
Older builds of this app could store direct `https://…` URLs in
`tabFile.file_url` when a CDN/public hostname was configured. That field
has been removed; the app converges all File rows to the universal
redirect form on `bench migrate` (patch
`v1_0.converge_legacy_urls_to_redirect`). Re-running is a no-op.

To run it manually (e.g. on a site that hasn't been migrated yet):

```bash
bench --site <site-name> execute frappe_s3_attachment.controller.migrate_urls_to_redirect --kwargs "{'dry_run': 1}"   # preview
bench --site <site-name> execute frappe_s3_attachment.controller.migrate_urls_to_redirect --kwargs "{'dry_run': 0}"   # apply
```

---

## Health check

Click **Test S3 Connection** to run a 5-stage end-to-end check: settings
sanity, credentials + LIST, presigned PUT upload, presigned GET
round-trip, and DELETE cleanup. Each stage reports PASS / FAIL / WARN with
a specific hint on failure.

---

## License

MIT
