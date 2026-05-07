# Frappe S3 Attachment

A Frappe app that redirects all file attachments to **any S3-compatible object storage** provider, with a single universal configuration that works the same way on every provider.

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

- **One bucket. One S3 API endpoint.** That's the whole config.
- **Both public and private files** are served by Frappe via a signed-URL redirect (`/api/method/...generate_file?key=...`). The redirect uses a short-lived signed URL, so it works on every S3-compatible provider — including R2 and Oracle, whose S3 endpoints don't serve anonymous reads directly.
- **No second endpoint, no second bucket, no provider-specific public-hosting setup is required.**

The optional `CDN / Public URL` field exists only as a **performance optimization** for sites that want public files delivered directly from a CDN (browser-cacheable, no Frappe hop). Most deployments should leave it blank.

---

## Installation

```bash
bench get-app frappe_s3_attachment
bench --site <site-name> install-app frappe_s3_attachment
```

---

## Configuration

Open **S3 File Attachment** (single doctype) in your Frappe desk and fill in the fields.

### Storage Credentials

| Field | Description |
|---|---|
| **Bucket Name** | Your bucket / container name |
| **Access Key ID** | Your provider's access key (leave blank to use IAM role for AWS) |
| **Secret Access Key** | Your provider's secret key |
| **Region Name** | AWS region (`us-east-1`) or provider-specific region. Leave blank if not required. |
| **Endpoint URL** | S3-API endpoint for non-AWS providers. Leave blank for native AWS S3. Must include `https://`. |
| **CDN / Public URL** | **Optional, performance only.** Leave blank for the universal default. Set only if you want public files served directly from a CDN host. |
| **Folder Name** | Optional prefix prepended to every uploaded object key |
| **Signed URL Expiry Time** | Expiry in seconds for pre-signed URLs (default: 300) |

### Advanced Options

| Field | Description |
|---|---|
| **Force Path Style** | Use `{endpoint}/{bucket}/{key}` URL format instead of virtual-hosted style. Required for Oracle Object Storage and MinIO. |
| **Ignore SSL Certificate Verification** | Disable TLS verification. Use only with self-signed certs on internal networks. Not for production. |
| **Disable Object ACL** | Skip the per-object `x-amz-acl` header on uploads. Required for Cloudflare R2 (and any provider that does not support ACLs). |

URLs are normalised on save: bare hostnames are auto-prefixed with `https://`, trailing slashes stripped, invalid values rejected.

---

## Per-provider Quick-Start

The `CDN / Public URL` field is **left blank** in every example below. The app works identically on every provider with that field empty.

### AWS S3

```
Endpoint URL:        (blank)
Region:              us-east-1
Force Path Style:    ☐
Disable Object ACL:  ☐
```

### Cloudflare R2

```
Endpoint URL:        https://<account_id>.r2.cloudflarestorage.com
Region:              auto                  ← or blank
Force Path Style:    ☐
Disable Object ACL:  ☑   ← required (R2 rejects x-amz-acl)
```

### Oracle Object Storage

```
Endpoint URL:        https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
Region:              (blank or your OCI region)
Force Path Style:    ☑   ← required
Disable Object ACL:  ☑
```

### MinIO

```
Endpoint URL:        http://localhost:9000
Region:              (blank)
Force Path Style:    ☑   ← required
Disable Object ACL:  ☐
```

---

## How It Works

- **Upload.** On `File.after_insert`, the file is uploaded to the configured bucket via a presigned PUT, and the local copy is removed.
- **Public files** (default) get a `file_url` of `/api/method/frappe_s3_attachment.controller.generate_file?key=…&file_name=…`. Frappe receives the request, generates a fresh signed GET URL, and 302-redirects the browser to S3. Anonymous (Guest) users are allowed for public files; Frappe verifies the matching `tabFile` row is `is_private=0` before signing.
- **Private files** use the same redirect endpoint. Guest access is rejected — only logged-in sessions can resolve a private key.
- **CDN-cached delivery (opt-in).** When `CDN / Public URL` is set, the upload path emits a direct URL (`{cdn}/{key}`) for public files instead of the redirect form. Use this only when you have a public hostname bound to the bucket and want browser-cacheable delivery.

### URL composition (when `CDN / Public URL` is set)
- `{cdn}/{key}` — same regardless of provider
- Object keys are URL-encoded (`%20` for spaces, etc.) so unusual filenames produce valid URLs.

### URL composition (when `CDN / Public URL` is blank — the default)
- `/api/method/frappe_s3_attachment.controller.generate_file?key={key}&file_name={name}` — Frappe-side signed redirect, identical on every provider.

---

## Migrating Existing Files

### Local files → S3
Open **S3 File Attachment** and click **Migrate Existing Files**. New uploads from any source automatically go to S3.

### Old direct URLs → universal redirect form
If you upgraded from an earlier build that stored direct `https://...` URLs in `tabFile.file_url`, run the one-shot migration to converge them to the universal redirect form:

```bash
bench --site <site-name> execute frappe_s3_attachment.controller.migrate_urls_to_redirect --kwargs "{'dry_run': 1}"   # preview
bench --site <site-name> execute frappe_s3_attachment.controller.migrate_urls_to_redirect --kwargs "{'dry_run': 0}"   # apply
```

This rewrites every `tabFile` row whose `file_url` is `http(s)://...` and whose `content_hash` is set — i.e. anything this app uploaded — into the `/api/method/...generate_file?key=...` form. Idempotent.

### Hostname-only rewrite (rare)
If you set `CDN / Public URL` and want to swap one public hostname for another, use the **Rewrite Stored File URLs** button on the settings doctype (preview → confirm).

---

## Health check

Click **Test S3 Connection** to run a 6-stage end-to-end check: settings sanity, credentials + LIST, presigned PUT upload, presigned GET round-trip, public read path (universal redirect by default, or anonymous GET against `CDN / Public URL` when set), and DELETE cleanup. Each stage reports PASS / FAIL / WARN with a specific hint on failure.

---

## License

MIT
