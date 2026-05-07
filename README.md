# Frappe S3 Attachment

A Frappe app that redirects all file attachments to **any S3-compatible object storage** provider, including:

| Provider | Notes |
|---|---|
| **AWS S3** | Native support, virtual-hosted & path-style |
| **Cloudflare R2** | Set Endpoint URL, enable **Disable Object ACL**, set **CDN / Public URL** |
| **Oracle Object Storage** | Set Endpoint URL, enable **Force Path Style**, set **CDN / Public URL** |
| **MinIO** | Set Endpoint URL, enable **Force Path Style** |
| **DigitalOcean Spaces** | Set Endpoint URL |
| **Any other S3-compatible API** | Set Endpoint URL, toggle path style as needed |

---

## Mental model

The app works the same way for every provider:

- **One bucket.**
- **One S3-API endpoint** (`Endpoint URL`) — boto3 talks to this for all credentialed operations: uploads, signed reads, deletes.
- **Optionally one public hostname** (`CDN / Public URL`) — the URL prefix browsers use for anonymous reads of public files. Same bucket, just a different hostname pointing at it.

You do **not** need a second bucket or second endpoint for "public access". The public hostname is required only when the S3-API endpoint refuses anonymous reads (Cloudflare R2, Oracle Object Storage). Everywhere else it's optional.

---

## Installation

```bash
bench get-app frappe_s3_attachment
bench --site <site-name> install-app frappe_s3_attachment
```

---

## Configuration

Go to **S3 File Attachment** (single doctype) in your Frappe desk and fill in the fields.

### Storage Credentials

| Field | Description |
|---|---|
| **Bucket Name** | Your bucket / container name |
| **Access Key ID** | Your provider's access key (leave blank to use IAM role for AWS) |
| **Secret Access Key** | Your provider's secret key |
| **Region Name** | AWS region (`us-east-1`) or provider-specific region. Leave blank if not required. |
| **Endpoint URL** | S3-API endpoint for non-AWS providers (see examples below). Leave blank for native AWS S3. Must include `https://`. |
| **CDN / Public URL** | Hostname browsers will use to fetch public files. Required for R2 / Oracle, optional elsewhere. Must include `https://`. |
| **Folder Name** | Optional prefix prepended to every uploaded object key |
| **Signed URL Expiry Time** | Expiry in seconds for pre-signed URLs used for private files (default: 300) |

### Advanced Options

| Field | Description |
|---|---|
| **Force Path Style** | Use `{endpoint}/{bucket}/{key}` URL format instead of virtual-hosted style. Required for Oracle Object Storage and MinIO. |
| **Ignore SSL Certificate Verification** | Disable TLS verification. Use only with self-signed certs on internal networks. Not for production. |
| **Disable Object ACL** | Skip the per-object `x-amz-acl` header on uploads. Required for Cloudflare R2 (and any provider that does not support ACLs). When on, public access must be configured at the bucket level. |

URLs are normalised on save: a bare hostname like `cdn.example.com` is auto-prefixed with `https://`, trailing slashes are stripped, and invalid values are rejected.

---

## Per-provider configuration matrix

| Provider | `Endpoint URL` | `Force Path Style` | `Disable Object ACL` | `CDN / Public URL` | Bucket-side public access |
|---|---|---|---|---|---|
| **AWS S3** | *(blank)* | off | off | *(blank — derived from bucket)* | object ACL `public-read` (sent by app) or bucket policy |
| **Cloudflare R2** | `https://<acct>.r2.cloudflarestorage.com` | off | **on** | **required** — `https://<custom-domain>` or `https://pub-xxx.r2.dev` | bind custom domain or enable public dev URL |
| **Oracle Object Storage** | `https://<ns>.compat.objectstorage.<region>.oraclecloud.com` | **on** | on | **required if you want public reads** | make bucket public + use a PAR or native object URL |
| **MinIO** | `http(s)://<host>:<port>` | **on** | off (or on if using bucket policy) | optional — same host serves anonymous reads if bucket policy allows | `mc anonymous set download` on the bucket |
| **DigitalOcean Spaces** | `https://<region>.digitaloceanspaces.com` | off | off | optional CDN host | object ACL `public-read` |

---

## Provider Quick-Start Examples

### AWS S3

```
Access Key ID:       AKIA...
Secret Key:          xxxxxx
Region:              us-east-1
Endpoint URL:        (leave blank)
CDN / Public URL:    (leave blank)
Force Path Style:    ☐
Disable Object ACL:  ☐
```

### Cloudflare R2

```
Access Key ID:       <R2 Access Key>
Secret Key:          <R2 Secret Key>
Region:              auto                  ← or leave blank
Endpoint URL:        https://<account_id>.r2.cloudflarestorage.com
CDN / Public URL:    https://cdn.example.com   ← or https://pub-xxx.r2.dev
Force Path Style:    ☐
Disable Object ACL:  ☑   ← required (R2 rejects x-amz-acl)
```

On the Cloudflare side: bind `cdn.example.com` to the same bucket via R2 → Settings → Custom Domains, or enable the bucket's Public Development URL.

### Oracle Object Storage

```
Access Key ID:       <Customer Secret Key>
Secret Key:          <Customer Secret>
Region:              (leave blank or set your OCI region)
Endpoint URL:        https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
CDN / Public URL:    https://<your-public-host>   ← required for public reads
Force Path Style:    ☑   ← required
Disable Object ACL:  ☑
```

### MinIO

```
Access Key ID:       minioadmin
Secret Key:          minioadmin
Region:              (leave blank)
Endpoint URL:        http://localhost:9000
CDN / Public URL:    (optional)
Force Path Style:    ☑   ← required
Disable Object ACL:  ☐
```

---

## How It Works

- **Upload.** On `File.after_insert`, the file is uploaded to the configured bucket via a presigned PUT, and the local copy is removed.
- **Private files** are served via a pre-signed redirect: `/api/method/frappe_s3_attachment.controller.generate_file?key=…`. The signed URL is generated against `Endpoint URL` and is not affected by `CDN / Public URL`.
- **Public files** get a direct URL composed by `get_public_url()`. Object keys are URL-encoded so spaces / unicode in filenames don't produce invalid URLs.
- **Public URL construction:**
  - `CDN / Public URL` set → `{public_url}/{key}`
  - Custom endpoint, path-style → `{endpoint}/{bucket}/{key}`
  - Custom endpoint, virtual style → `{scheme}://{bucket}.{endpoint_host}/{key}`
  - Native AWS, path style → `https://s3.{region}.amazonaws.com/{bucket}/{key}`
  - Native AWS, virtual style → `https://{bucket}.s3.{region}.amazonaws.com/{key}`

---

## Migrating Existing Files

Open the **S3 File Attachment** doctype and click **Migrate Existing Files** to upload all locally-stored files to S3.

## Rewriting stored URLs after a config change

If you change the `CDN / Public URL` (or set it for the first time) after files have already been uploaded, existing `tabFile.file_url` rows still carry the old hostname. Click **Rewrite Stored File URLs** on the settings doctype: enter the old prefix, preview the count, and confirm. The button calls `frappe_s3_attachment.controller.rewrite_public_file_urls` and only matches absolute-URL prefixes, so private file URLs (which start with `/api/method/...`) are never touched.

## Health check

Click **Test S3 Connection** to run a 6-stage end-to-end check: settings sanity, credentials + LIST, presigned PUT upload, presigned GET round-trip, anonymous public GET (against `CDN / Public URL` if set), and DELETE cleanup. Each stage reports PASS / FAIL / WARN with a specific hint when it fails.

---

## License

MIT
