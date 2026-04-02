/**
 * AWS S3 Skill Implementation
 * Uploads files to S3 buckets and generates presigned URLs for access
 */

import { S3Client, PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { readFileSync, existsSync } from 'fs';
import { extname } from 'path';

// MIME types for common file extensions
const MIME_TYPES: Record<string, string> = {
  '.txt': 'text/plain',
  '.json': 'application/json',
  '.pdf': 'application/pdf',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.html': 'text/html',
  '.htm': 'text/html',
  '.csv': 'text/csv',
  '.md': 'text/markdown',
  '.xml': 'application/xml',
  '.zip': 'application/zip',
  '.mp3': 'audio/mpeg',
  '.mp4': 'video/mp4',
  '.wav': 'audio/wav'
};

interface AwsS3Params {
  action: 'upload' | 'getUrl' | 'getPublicUrl';
  key: string;
  content?: string | Buffer;
  filePath?: string;
  contentType?: string;
  expiresIn?: number;
}

interface AwsS3Result {
  success: boolean;
  key?: string;
  url?: string;
  expiresAt?: string;
  error?: string;
}

// Create S3 client from environment variables
function createS3Client(): S3Client {
  const region = process.env.AWS_S3_REGION || 'us-east-1';
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;

  return new S3Client({
    region,
    credentials: accessKeyId && secretAccessKey ? {
      accessKeyId,
      secretAccessKey
    } : undefined
  });
}

// Get bucket name from environment
function getBucketName(): string {
  const bucket = process.env.AWS_S3_BUCKET;
  if (!bucket) {
    throw new Error('AWS_S3_BUCKET environment variable is not set');
  }
  return bucket;
}

// Read file from disk and detect content type
function readFileFromPath(filePath: string): { content: Buffer; contentType: string } {
  if (!existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }

  const content = readFileSync(filePath);
  const ext = extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';

  return { content, contentType };
}

// Upload file to S3
async function uploadFile(
  client: S3Client,
  bucket: string,
  key: string,
  content: string | Buffer,
  contentType: string = 'application/octet-stream'
): Promise<{ key: string; etag?: string }> {
  const body = typeof content === 'string' ? Buffer.from(content) : content;

  const command = new PutObjectCommand({
    Bucket: bucket,
    Key: key,
    Body: body,
    ContentType: contentType
  });

  const response = await client.send(command);

  return {
    key,
    etag: response.ETag
  };
}

// Generate presigned URL for object
async function generatePresignedUrl(
  client: S3Client,
  bucket: string,
  key: string,
  expiresIn: number = 3600
): Promise<{ url: string; expiresAt: string }> {
  const command = new GetObjectCommand({
    Bucket: bucket,
    Key: key
  });

  const url = await getSignedUrl(client, command, { expiresIn });
  const expiresAt = new Date(Date.now() + expiresIn * 1000).toISOString();

  return { url, expiresAt };
}

// Generate public URL for object in public bucket
function getPublicUrlFor(bucket: string, key: string, region: string): string {
  // Encode the key but preserve forward slashes (they are path separators in S3)
  const encodedKey = key.split('/').map(encodeURIComponent).join('/');
  return `https://${bucket}.s3.${region}.amazonaws.com/${encodedKey}`;
}

// Main skill function
export async function awsS3(params: AwsS3Params): Promise<AwsS3Result> {
  const { action, key, content, filePath, contentType, expiresIn = 3600 } = params;

  // Validate required parameters
  if (!key) {
    return {
      success: false,
      error: 'Missing required parameter: key'
    };
  }

  // Check bucket configuration (required for all actions)
  let bucket: string;
  try {
    bucket = getBucketName();
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'S3 bucket configuration error'
    };
  }

  // getPublicUrl doesn't need credentials - it just constructs a URL
  if (action === 'getPublicUrl') {
    const region = process.env.AWS_S3_REGION || 'us-east-1';
    const url = getPublicUrlFor(bucket, key, region);
    return {
      success: true,
      key,
      url
    };
  }

  // Check AWS credentials (required for upload and getUrl)
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;

  if (!accessKeyId || !secretAccessKey) {
    return {
      success: false,
      error: 'AWS credentials not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables.'
    };
  }

  try {
    const client = createS3Client();

    switch (action) {
      case 'upload': {
        // Validate that either content or filePath is provided
        if (!content && !filePath) {
          return {
            success: false,
            error: 'Missing required parameter: either content or filePath must be provided for upload action'
          };
        }

        // Validate mutual exclusivity
        if (content && filePath) {
          return {
            success: false,
            error: 'Parameters content and filePath are mutually exclusive. Provide only one.'
          };
        }

        let uploadContent: string | Buffer;
        let uploadContentType: string = contentType || 'application/octet-stream';

        // Handle file path
        if (filePath) {
          try {
            const fileData = readFileFromPath(filePath);
            uploadContent = fileData.content;
            // Auto-detect content type if not specified
            if (!contentType) {
              uploadContentType = fileData.contentType;
            }
          } catch (error) {
            return {
              success: false,
              error: error instanceof Error ? error.message : 'Failed to read file'
            };
          }
        } else {
          // Use direct content
          uploadContent = content!;
        }

        const result = await uploadFile(client, bucket, key, uploadContent, uploadContentType);
        return {
          success: true,
          key: result.key
        };
      }

      case 'getUrl': {
        const result = await generatePresignedUrl(client, bucket, key, expiresIn);
        return {
          success: true,
          key,
          url: result.url,
          expiresAt: result.expiresAt
        };
      }

      default:
        return {
          success: false,
          error: `Invalid action: ${action}. Must be 'upload', 'getUrl', or 'getPublicUrl'.`
        };
    }
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown S3 error'
    };
  }
}

export default awsS3;