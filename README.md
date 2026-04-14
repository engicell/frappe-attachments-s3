# Frappe S3 Attachment

A Frappe app that redirects all file attachments to **any S3-compatible object storage** provider, including:

| Provider | Notes |
|---|---|
| **AWS S3** | Native support, virtual-hosted & path-style |
| **Cloudflare R2** | Set Endpoint URL, leave region blank or use `auto` |
| **Oracle Object Storage** | Set Endpoint URL + enable **Force Path Style** |
| **MinIO** | Set Endpoint URL + enable **Force Path Style** |
| **Any other S3-compatible API** | Set Endpoint URL, toggle path style as needed |

---

## Installation

```bash
bench get-app frappe_s3_attachment
bench --site <site-name> install-app frappe_s3_attachment
```

---

## Configuration

Go to **S3 File Attachment** (single doctype) in your Frappe desk and fill in the fields:

### Storage Credentials

| Field | Description |
|---|---|
| **Bucket Name** | Your bucket / container name |
| **Access Key ID** | Your provider's access key (leave blank to use IAM role for AWS) |
| **Secret Access Key** | Your provider's secret key |
| **Region Name** | AWS region (`us-east-1`) or provider-specific region. Leave blank if not required. |
| **Endpoint URL** | Custom endpoint for non-AWS providers (see examples below). Leave blank for native AWS S3. |
| **Folder Name** | Optional prefix prepended to every uploaded object key |
| **Signed URL Expiry Time** | Expiry in seconds for pre-signed URLs used for private files (default: 300) |

### Advanced Options

| Field | Description |
|---|---|
| **Force Path Style** | Use `{endpoint}/{bucket}/{key}` URL format instead of virtual-hosted style. **Required for Oracle Object Storage and MinIO.** |
| **Ignore SSL Certificate Verification** | Disable TLS verification. Use only with self-signed certs on internal networks. Not recommended for production. |

---

## Provider Quick-Start Examples

### AWS S3

```
Access Key ID:  AKIA...
Secret Key:     xxxxxx
Region:         us-east-1
Endpoint URL:   (leave blank)
Force Path Style: ☐
```

### Cloudflare R2

```
Access Key ID:  <R2 Access Key>
Secret Key:     <R2 Secret Key>
Region:         auto          ← or leave blank
Endpoint URL:   https://<account_id>.r2.cloudflarestorage.com
Force Path Style: ☐
```

### Oracle Object Storage

```
Access Key ID:  <Customer Secret Key>
Secret Key:     <Customer Secret>
Region:         (leave blank or set your OCI region)
Endpoint URL:   https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
Force Path Style: ☑  ← required
```

### MinIO

```
Access Key ID:  minioadmin
Secret Key:     minioadmin
Region:         (leave blank)
Endpoint URL:   http://localhost:9000
Force Path Style: ☑  ← required
```

---

## How It Works

- **After a file is inserted** (`File.after_insert`) the file is uploaded to the configured bucket and the local copy is removed.
- **Private files** are served via a pre-signed redirect: `/api/method/frappe_s3_attachment.controller.generate_file?key=…`
- **Public files** get a direct URL using the correct path/virtual-hosted style for the configured provider.
- **Public URL construction:**
  - Custom endpoint → always `{endpoint}/{bucket}/{key}` (path-style, widest compatibility)
  - Native AWS, path style → `https://s3.{region}.amazonaws.com/{bucket}/{key}`
  - Native AWS, virtual style → `https://{bucket}.s3.{region}.amazonaws.com/{key}`

---

## Migrating Existing Files

Open the **S3 File Attachment** doctype and click **Migrate Existing Files** to upload all locally stored files to S3.

---

## License

MIT
