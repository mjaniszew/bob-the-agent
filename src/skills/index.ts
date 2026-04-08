/**
 * Skills Registry
 * Exports all available skills for the agent
 */

export { dataExtraction } from './data-extraction/index.js';
export { mathOperations } from './math-operations/index.js';
export { xComSearch } from './x-com/index.js';
export { awsS3 } from './aws-s3/index.js';

// Skill metadata for registration
export const skillRegistry = {
  'data-extraction': {
    name: 'Data Extraction',
    description: 'Extract structured data from various file formats and web sources',
    version: '1.0.0',
    params: {
      source: { type: 'object', required: true, description: 'Source to extract from (url or file)' },
      outputFormat: { type: 'string', enum: ['json', 'csv', 'markdown'], default: 'json' },
      extract: { type: 'array', default: ['text'], description: 'What to extract (tables, text, images)' }
    }
  },
  'math-operations': {
    name: 'Math Operations',
    description: 'Perform accurate mathematical calculations and statistical analysis',
    version: '1.0.0',
    params: {
      operation: { type: 'string', enum: ['calculate', 'statistics', 'convert'], required: true },
      expression: { type: 'string', description: 'Mathematical expression to evaluate' },
      data: { type: 'array', description: 'Data array for statistics' },
      precision: { type: 'number', default: 4, description: 'Decimal precision' }
    }
  },
  'aws-s3': {
    name: 'AWS S3',
    description: 'Upload files to S3 and generate URLs for access (presigned for private buckets, public URLs for public buckets)',
    version: '1.0.0',
    params: {
      action: { type: 'string', enum: ['upload', 'getUrl', 'getPublicUrl'], required: true, description: 'Action to perform: upload, getUrl, or getPublicUrl' },
      key: { type: 'string', required: true, description: 'S3 object key (path in bucket)' },
      content: { type: 'string', description: 'Content to upload (required for upload action)' },
      filePath: { type: 'string', description: 'Path to file on disk (alternative to content for upload action)' },
      contentType: { type: 'string', default: 'application/octet-stream', description: 'MIME type of the content' },
      expiresIn: { type: 'number', default: 3600, description: 'URL expiration time in seconds (for getUrl action)' }
    }
  },
  'x-com': {
    name: 'X.com Search',
    description: 'Search X.com (Twitter) directly via X API for posts, users, and timelines.',
    version: '1.0.0',
    params: {
      action: { type: 'string', enum: ['searchPosts', 'searchPostsAll', 'searchUsers', 'getUserTimeline'], required: true, description: 'Action to perform: searchPosts, searchPostsAll, searchUsers, or getUserTimeline' },
      query: { type: 'string', required: true, description: 'Search query (can include operators like from:user, #hashtag)' },
      maxResults: { type: 'number', default: 10, description: 'Maximum results to return (10-100 for posts, up to 1000 for users)' },
      nextToken: { type: 'string', description: 'Pagination token for next page of results' },
      startTime: { type: 'string', description: 'ISO datetime for start of time range (posts search)' },
      endTime: { type: 'string', description: 'ISO datetime for end of time range (posts search)' },
      sinceId: { type: 'string', description: 'Return posts newer than this ID' },
      untilId: { type: 'string', description: 'Return posts older than this ID' },
      tweetFields: { type: 'array', description: 'Tweet fields to include (created_at, public_metrics, entities, etc.)' },
      userFields: { type: 'array', description: 'User fields to include (description, public_metrics, verified, etc.)' },
      userId: { type: 'string', description: 'User ID for getUserTimeline action' },
      username: { type: 'string', description: 'Username for getUserTimeline action (alternative to userId)' }
    }
  }
};

/**
 * Execute a skill by name
 */
export async function executeSkill(skillName: string, params: Record<string, any>): Promise<any> {
  const skill = skillRegistry[skillName as keyof typeof skillRegistry];

  if (!skill) {
    throw new Error(`Unknown skill: ${skillName}`);
  }

  // Validate required parameters
  for (const [paramName, paramDef] of Object.entries(skill.params)) {
    if ((paramDef as any).required && params[paramName] === undefined) {
      throw new Error(`Missing required parameter: ${paramName}`);
    }
  }

  // Execute the skill
  switch (skillName) {
    case 'data-extraction': {
      const { dataExtraction } = await import('./data-extraction/index.js');
      return dataExtraction(params as any);
    }
    case 'math-operations': {
      const { mathOperations } = await import('./math-operations/index.js');
      return mathOperations(params as any);
    }
    case 'x-com': {
      const { xComSearch } = await import('./x-com/index.js');
      return xComSearch(params as any);
    }
    case 'aws-s3': {
      const { awsS3 } = await import('./aws-s3/index.js');
      return awsS3(params as any);
    }
    default:
      throw new Error(`Skill not implemented: ${skillName}`);
  }
}

export default executeSkill;