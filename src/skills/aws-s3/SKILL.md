---
name: aws-s3
description: Use this skill to upload files to AWS S3 buckets and generate URLs for accessing uploaded files (presigned URLs for private buckets, public URLs for public buckets), especially if it's required to: upload files to cloud storage, generate shareable URLs, persist generated artifacts (reports, documents, images), share files with external users.
---

# AWS S3 Skill

Use this skill to upload files to AWS S3 buckets and generate URLs for accessing uploaded files.

## When to Use

Use this skill when:
- You need to upload files to cloud storage
- You need to generate shareable URLs for files
- You need to persist generated artifacts (reports, documents, images)
- You need to share files with external users

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{"action":"upload","key":"path/to/file.txt","content":"File content here","contentType":"text/plain"}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| action | string | Yes | - | Action to perform: `upload`, `getUrl`, or `getPublicUrl` |
| key | string | Yes | - | S3 object key (path in bucket) |
| content | string/Buffer | No* | - | Content to upload (string or Buffer). Mutually exclusive with `filePath`. |
| filePath | string | No* | - | Path to file on disk. Mutually exclusive with `content`. Auto-detects content type from extension. |
| contentType | string | No | application/octet-stream | MIME type of the content (auto-detected from `filePath` if not specified) |
| expiresIn | number | No | 3600 | URL expiration time in seconds (for `getUrl` action) |

*For `upload` action: One of `content` or `filePath` is required. They are mutually exclusive.

## Actions

### Upload

Uploads a file to the configured S3 bucket.

**Parameters:**
- `key`: S3 object key (path in bucket)
- `content`: File content (string or Buffer) - mutually exclusive with `filePath`
- `filePath`: Path to file on disk - mutually exclusive with `content`
- `contentType`: MIME type (optional, auto-detected from `filePath` if not specified)

**Examples:**

Upload with direct content:
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "reports/2024-04-02/report.pdf",
  "content": "Report content here...",
  "contentType": "application/pdf"
}'
```

Upload from file path:
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "reports/2024-04-02/report.pdf",
  "filePath": "/data/reports/report.pdf"
}'
```

### GetUrl

Generates a presigned URL for accessing an object in S3.

**Parameters:**
- `key`: S3 object key
- `expiresIn`: URL expiration time in seconds (default: 3600)

**Example:**
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "getUrl",
  "key": "reports/2024-04-02/report.pdf",
  "expiresIn": 7200
}'
```

### GetPublicUrl

Generates a permanent public URL for an object in a publicly accessible S3 bucket.

**Use this when:**
- The bucket has public read access enabled
- You need a permanent URL (no expiration)
- You don't have AWS credentials configured

**Parameters:**
- `key`: S3 object key

**Example:**
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "getPublicUrl",
  "key": "public-images/logo.png"
}'
```

**Note:** This action does NOT require AWS credentials. It simply constructs the public URL using the bucket name and region. Only use this for buckets with public read access enabled.

## Output

### Upload Response
```json
{
  "success": true,
  "key": "reports/2024-04-02/report.pdf"
}
```

### GetUrl Response
```json
{
  "success": true,
  "key": "reports/2024-04-02/report.pdf",
  "url": "https://bucket.s3.region.amazonaws.com/key?...",
  "expiresAt": "2024-04-02T12:00:00.000Z"
}
```

### GetPublicUrl Response
```json
{
  "success": true,
  "key": "public-images/logo.png",
  "url": "https://my-bucket.s3.us-east-1.amazonaws.com/public-images/logo.png"
}
```

### Error Response
```json
{
  "success": false,
  "error": "Error message describing what went wrong"
}
```

## Environment Variables

Required (for `upload` and `getUrl` actions):
- `AWS_S3_BUCKET`: S3 bucket name
- `AWS_ACCESS_KEY_ID`: AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: AWS secret access key

Required (for `getPublicUrl` action):
- `AWS_S3_BUCKET`: S3 bucket name (credentials not required)

Optional:
- `AWS_S3_REGION`: S3 region (default: us-east-1)

## Common Content Types

| Extension | Content Type |
|-----------|--------------|
| .txt | text/plain |
| .json | application/json |
| .pdf | application/pdf |
| .docx | application/vnd.openxmlformats-officedocument.wordprocessingml.document |
| .xlsx | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet |
| .png | image/png |
| .jpg/.jpeg | image/jpeg |
| .html | text/html |
| .csv | text/csv |

## Examples

### Upload Text File (Direct Content)
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "data/output.txt",
  "content": "Lorem ipsum dolor sit amet...",
  "contentType": "text/plain"
}'
```

### Upload File from Disk (Auto Content-Type)
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "reports/monthly.pdf",
  "filePath": "/data/reports/monthly-report.pdf"
}'
```

### Upload File with Custom Content-Type
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "data/custom.bin",
  "filePath": "/data/binary-file.bin",
  "contentType": "application/x-custom-binary"
}'
```

### Upload JSON Data
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "upload",
  "key": "data/results.json",
  "content": "{\"status\": \"complete\", \"count\": 42}",
  "contentType": "application/json"
}'
```

### Generate Shareable Link
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "getUrl",
  "key": "reports/2024-04-02/report.pdf",
  "expiresIn": 86400
}'
```

### Get Public URL (No Credentials Required)
```bash
node /app/scripts/skill-runner.mjs --skill aws-s3 --params '{
  "action": "getPublicUrl",
  "key": "public-images/logo.png"
}'
```

## Notes

- Files are stored with the exact key provided (no path normalization)
- Use forward slashes (`/`) for nested paths in keys
- Presigned URLs expire after `expiresIn` seconds (max 7 days)
- Content can be a string or Buffer (for binary data)
- `filePath` can be used instead of `content` to upload files from disk
- Content-Type is auto-detected from file extension when using `filePath`
- The bucket must exist before uploading files
- Ensure IAM policies allow `s3:PutObject` and `s3:GetObject` actions
- `getPublicUrl` does NOT require AWS credentials - it constructs a URL for public buckets
- Only use `getPublicUrl` for buckets with public read access enabled