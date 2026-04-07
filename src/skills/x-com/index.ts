/**
 * X.com Search Skill Implementation
 * Direct X.com API access for posts, users, and timelines
 *
 * This skill uses the native X API directly instead of Grok,
 * providing a more cost-effective solution for X.com searches.
 */

const X_API_BASE = 'https://api.x.com/2';

// Action types
type XComAction = 'searchPosts' | 'searchPostsAll' | 'searchUsers' | 'getUserTimeline';

// Input parameters interface
interface XComSearchParams {
  action: XComAction;
  query: string;
  maxResults?: number;
  nextToken?: string;
  startTime?: string;
  endTime?: string;
  sinceId?: string;
  untilId?: string;
  tweetFields?: string[];
  userFields?: string[];
  userId?: string;
  username?: string;
}

// Response interfaces
interface XComTweet {
  id: string;
  text: string;
  author_id?: string;
  created_at?: string;
  public_metrics?: {
    like_count?: number;
    retweet_count?: number;
    reply_count?: number;
    quote_count?: number;
  };
  entities?: Record<string, any>;
}

interface XComUser {
  id: string;
  username: string;
  name: string;
  verified?: boolean;
  description?: string;
  public_metrics?: {
    followers_count?: number;
    following_count?: number;
    tweet_count?: number;
  };
  created_at?: string;
}

interface XComSearchResult {
  success: boolean;
  action: string;
  data: XComTweet[] | XComUser[];
  meta: {
    resultCount: number;
    nextToken?: string;
    previousToken?: string;
  };
  error?: string;
  executionTime?: number;
}

// Internal result type for helper functions (without executionTime)
interface InternalResult {
  success: boolean;
  action: string;
  data: XComTweet[] | XComUser[];
  meta: {
    resultCount: number;
    nextToken?: string;
    previousToken?: string;
  };
  error?: string;
}

// Default field configurations
const DEFAULT_TWEET_FIELDS = [
  'created_at',
  'public_metrics',
  'entities',
  'author_id',
  'in_reply_to_user_id',
  'referenced_tweets'
];

const DEFAULT_USER_FIELDS = [
  'created_at',
  'description',
  'public_metrics',
  'verified',
  'profile_image_url'
];

/**
 * Get the API key from environment variables
 * Priority: XAI_SEARCH_API_KEY > XAI_API_KEY
 */
function getApiKey(): string | null {
  return process.env.XAI_SEARCH_API_KEY || process.env.XAI_API_KEY || null;
}

/**
 * Make a request to the X API
 */
async function xApiRequest(
  endpoint: string,
  apiKey: string,
  params: Record<string, string | number | undefined> = {}
): Promise<{ data: any; meta: any }> {
  const url = new URL(`${X_API_BASE}${endpoint}`);

  // Add query parameters
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      url.searchParams.append(key, String(value));
    }
  }

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    }
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({})) as any;
    throw {
      status: response.status,
      statusText: response.statusText,
      error: errorData
    };
  }

  return response.json();
}

/**
 * Search recent posts (last 7 days)
 */
async function searchRecentPosts(
  params: XComSearchParams,
  apiKey: string
): Promise<InternalResult> {
  const { query, maxResults = 10, nextToken, startTime, endTime, sinceId, untilId, tweetFields } = params;

  const requestParams: Record<string, string | number | undefined> = {
    query,
    max_results: Math.min(maxResults, 100),
    'tweet.fields': (tweetFields || DEFAULT_TWEET_FIELDS).join(',')
  };

  if (nextToken) requestParams.next_token = nextToken;
  if (startTime) requestParams.start_time = startTime;
  if (endTime) requestParams.end_time = endTime;
  if (sinceId) requestParams.since_id = sinceId;
  if (untilId) requestParams.until_id = untilId;

  try {
    const response = await xApiRequest('/tweets/search/recent', apiKey, requestParams);

    return {
      success: true,
      action: 'searchPosts',
      data: response.data || [],
      meta: {
        resultCount: response.meta?.result_count || 0,
        nextToken: response.meta?.next_token,
        previousToken: response.meta?.previous_token
      }
    };
  } catch (error: any) {
    return {
      success: false,
      action: 'searchPosts',
      data: [],
      meta: { resultCount: 0 },
      error: formatError(error)
    };
  }
}

/**
 * Search all posts (full archive)
 */
async function searchAllPosts(
  params: XComSearchParams,
  apiKey: string
): Promise<InternalResult> {
  const { query, maxResults = 10, nextToken, startTime, endTime, sinceId, untilId, tweetFields } = params;

  const requestParams: Record<string, string | number | undefined> = {
    query,
    max_results: Math.min(maxResults, 500),
    'tweet.fields': (tweetFields || DEFAULT_TWEET_FIELDS).join(',')
  };

  if (nextToken) requestParams.next_token = nextToken;
  if (startTime) requestParams.start_time = startTime;
  if (endTime) requestParams.end_time = endTime;
  if (sinceId) requestParams.since_id = sinceId;
  if (untilId) requestParams.until_id = untilId;

  try {
    const response = await xApiRequest('/tweets/search/all', apiKey, requestParams);

    return {
      success: true,
      action: 'searchPostsAll',
      data: response.data || [],
      meta: {
        resultCount: response.meta?.result_count || 0,
        nextToken: response.meta?.next_token,
        previousToken: response.meta?.previous_token
      }
    };
  } catch (error: any) {
    return {
      success: false,
      action: 'searchPostsAll',
      data: [],
      meta: { resultCount: 0 },
      error: formatError(error)
    };
  }
}

/**
 * Search users by query
 */
async function searchUsers(
  params: XComSearchParams,
  apiKey: string
): Promise<InternalResult> {
  const { query, maxResults = 10, nextToken, userFields } = params;

  const requestParams: Record<string, string | number | undefined> = {
    query,
    count: Math.min(maxResults, 1000),
    'user.fields': (userFields || DEFAULT_USER_FIELDS).join(',')
  };

  if (nextToken) requestParams.next_token = nextToken;

  try {
    const response = await xApiRequest('/users/search', apiKey, requestParams);

    return {
      success: true,
      action: 'searchUsers',
      data: response.data || [],
      meta: {
        resultCount: response.meta?.result_count || 0,
        nextToken: response.meta?.next_token,
        previousToken: response.meta?.previous_token
      }
    };
  } catch (error: any) {
    return {
      success: false,
      action: 'searchUsers',
      data: [],
      meta: { resultCount: 0 },
      error: formatError(error)
    };
  }
}

/**
 * Get user by username
 */
async function getUserByUsername(
  username: string,
  apiKey: string
): Promise<XComUser | null> {
  try {
    const response = await xApiRequest(`/users/by/username/${username}`, apiKey, {
      'user.fields': DEFAULT_USER_FIELDS.join(',')
    });
    return response.data;
  } catch (error) {
    return null;
  }
}

/**
 * Get user timeline (tweets by user)
 */
async function getUserTimeline(
  params: XComSearchParams,
  apiKey: string
): Promise<InternalResult> {
  const startTime = Date.now();

  // Determine user ID
  let userId = params.userId;

  if (!userId && params.username) {
    const user = await getUserByUsername(params.username, apiKey);
    if (!user) {
      return {
        success: false,
        action: 'getUserTimeline',
        data: [],
        meta: { resultCount: 0 },
        error: `User not found: ${params.username}`
      };
    }
    userId = user.id;
  }

  if (!userId) {
    return {
      success: false,
      action: 'getUserTimeline',
      data: [],
      meta: { resultCount: 0 },
      error: 'userId or username is required for getUserTimeline action'
    };
  }

  const { maxResults = 10, nextToken, tweetFields } = params;

  const requestParams: Record<string, string | number | undefined> = {
    max_results: Math.min(maxResults, 100),
    'tweet.fields': (tweetFields || DEFAULT_TWEET_FIELDS).join(',')
  };

  if (nextToken) requestParams.next_token = nextToken;

  try {
    const response = await xApiRequest(`/users/${userId}/tweets`, apiKey, requestParams);

    return {
      success: true,
      action: 'getUserTimeline',
      data: response.data || [],
      meta: {
        resultCount: response.meta?.result_count || 0,
        nextToken: response.meta?.next_token,
        previousToken: response.meta?.previous_token
      }
    };
  } catch (error: any) {
    return {
      success: false,
      action: 'getUserTimeline',
      data: [],
      meta: { resultCount: 0 },
      error: formatError(error)
    };
  }
}

/**
 * Format error for user-friendly message
 */
function formatError(error: any): string {
  if (error.status === 401) {
    return 'Unauthorized: Invalid API key. Check XAI_SEARCH_API_KEY environment variable.';
  }
  if (error.status === 429) {
    return 'Rate limit exceeded. Please wait before making more requests.';
  }
  if (error.status === 404) {
    return 'Not found: The requested resource does not exist.';
  }
  if (error.status === 403) {
    return 'Forbidden: Access to this resource is not permitted with your current access level.';
  }
  if (error.error?.detail) {
    return error.error.detail;
  }
  if (error.error?.title) {
    return error.error.title;
  }
  if (error.message) {
    return error.message;
  }
  return `API error: ${error.status || 'Unknown'}`;
}

/**
 * Main X.com Search function
 */
export async function xComSearch(params: XComSearchParams): Promise<XComSearchResult> {
  const startTime = Date.now();
  const executionTime = () => Date.now() - startTime;

  // Check API key
  const apiKey = getApiKey();
  if (!apiKey) {
    return {
      success: false,
      action: params.action,
      data: [],
      meta: { resultCount: 0 },
      error: 'XAI_SEARCH_API_KEY or XAI_API_KEY environment variable is required. Use XAI_SEARCH_API_KEY (recommended) for direct X API access.',
      executionTime: executionTime()
    };
  }

  // Route to appropriate handler based on action
  switch (params.action) {
    case 'searchPosts':
      const recentResult = await searchRecentPosts(params, apiKey);
      return { ...recentResult, executionTime: executionTime() };

    case 'searchPostsAll':
      const allResult = await searchAllPosts(params, apiKey);
      return { ...allResult, executionTime: executionTime() };

    case 'searchUsers':
      const usersResult = await searchUsers(params, apiKey);
      return { ...usersResult, executionTime: executionTime() };

    case 'getUserTimeline':
      const timelineResult = await getUserTimeline(params, apiKey);
      return { ...timelineResult, executionTime: executionTime() };

    default:
      return {
        success: false,
        action: params.action,
        data: [],
        meta: { resultCount: 0 },
        error: `Unknown action: ${params.action}. Valid actions are: searchPosts, searchPostsAll, searchUsers, getUserTimeline`,
        executionTime: executionTime()
      };
  }
}

export default xComSearch;