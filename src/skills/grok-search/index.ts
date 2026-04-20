/**
 * Grok Search Skill Implementation
 * Searches X.com (Twitter) posts, users, and timelines via xAI Responses API with x_search tool
 */

const XAI_API_BASE = 'https://api.x.ai/v1';
const DEFAULT_MODEL = 'grok-4.1-fast';

// Action types - mirrors x-com skill for consistency
type GrokSearchAction = 'searchPosts' | 'searchPostsAll' | 'searchUsers' | 'getUserTimeline';

// Input parameters interface
interface GrokSearchParams {
  action: GrokSearchAction;
  query: string;
  username?: string;
  allowedHandles?: string[];
  excludedHandles?: string[];
  fromDate?: string;
  toDate?: string;
  enableImageUnderstanding?: boolean;
  enableVideoUnderstanding?: boolean;
  model?: string;
}

// Response interfaces
interface GrokSearchCitation {
  url: string;
  title: string;
  snippet: string;
  source: string;
}

interface GrokSearchUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  xSearchCalls: number;
}

interface GrokSearchResult {
  success: boolean;
  action: string;
  output: string;
  citations: GrokSearchCitation[];
  usage: GrokSearchUsage;
  error?: string;
  executionTime?: number;
}

/**
 * Get the API key from environment variables
 * Prefers XAI_SEARCH_API_KEY to isolate search costs from OpenClaw's XAI_API_KEY
 */
function getApiKey(): string | null {
  return process.env.XAI_SEARCH_API_KEY || process.env.XAI_API_KEY || null;
}

/**
 * Build a natural-language prompt for the Grok model based on action type
 */
function buildPrompt(params: GrokSearchParams): string {
  switch (params.action) {
    case 'searchPosts':
      return `Search for recent X (Twitter) posts about: ${params.query}. Focus on posts from the last 7 days. Provide the most relevant and recent results.`;
    case 'searchPostsAll':
      return `Search for X (Twitter) posts about: ${params.query}. Include posts from any time period, including older posts if relevant.`;
    case 'searchUsers':
      return `Search for X (Twitter) users matching: ${params.query}. Return their usernames, display names, descriptions, and follower counts.`;
    case 'getUserTimeline': {
      const handle = params.username?.replace('@', '') || params.allowedHandles?.[0];
      if (!handle) return '';
      return `Show recent posts from @${handle} on X (Twitter). Include their most recent tweets and replies.`;
    }
    default:
      return params.query;
  }
}

/**
 * Build the x_search tool configuration based on parameters
 */
function buildXSearchTool(params: GrokSearchParams): Record<string, any> {
  const tool: Record<string, any> = { type: 'x_search' };

  // Handle filtering: for getUserTimeline, force username into allowed_x_handles
  if (params.action === 'getUserTimeline' && params.username) {
    tool.allowed_x_handles = [params.username.replace('@', '')];
  } else if (params.allowedHandles?.length) {
    tool.allowed_x_handles = params.allowedHandles.slice(0, 10);
  }

  if (params.excludedHandles?.length) {
    tool.excluded_x_handles = params.excludedHandles.slice(0, 10);
  }

  // Date range handling
  if (params.fromDate) {
    tool.from_date = params.fromDate;
  } else if (params.action === 'searchPosts') {
    // Default: constrain recent posts to last 7 days
    const sevenDaysAgo = new Date();
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
    tool.from_date = sevenDaysAgo.toISOString().split('T')[0];
  }

  if (params.toDate) {
    tool.to_date = params.toDate;
  }

  // Media understanding options
  if (params.enableImageUnderstanding) {
    tool.enable_image_understanding = true;
  }
  if (params.enableVideoUnderstanding) {
    tool.enable_video_understanding = true;
  }

  return tool;
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
  if (error.status === 403) {
    return 'Forbidden: Access denied with current API key.';
  }
  if (error.status === 404) {
    return 'Not found: The requested resource does not exist.';
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
 * Main Grok Search function
 * Sends a prompt to the xAI Responses API with the x_search tool enabled
 */
export async function grokSearch(params: GrokSearchParams): Promise<GrokSearchResult> {
  const startTime = Date.now();

  // Check API key
  const apiKey = getApiKey();
  if (!apiKey) {
    return {
      success: false,
      action: params.action,
      output: '',
      citations: [],
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0, xSearchCalls: 0 },
      error: 'XAI_SEARCH_API_KEY or XAI_API_KEY environment variable is required. Use XAI_SEARCH_API_KEY (recommended) to isolate search costs.',
      executionTime: Date.now() - startTime
    };
  }

  // Validate getUserTimeline requires username
  if (params.action === 'getUserTimeline' && !params.username && !params.allowedHandles?.length) {
    return {
      success: false,
      action: params.action,
      output: '',
      citations: [],
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0, xSearchCalls: 0 },
      error: 'username or allowedHandles is required for getUserTimeline action',
      executionTime: Date.now() - startTime
    };
  }

  const prompt = buildPrompt(params);
  const xSearchTool = buildXSearchTool(params);
  const model = params.model || DEFAULT_MODEL;

  try {
    const response = await fetch(`${XAI_API_BASE}/responses`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model,
        input: [{ role: 'user', content: prompt }],
        tools: [xSearchTool]
      })
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({})) as any;
      throw {
        status: response.status,
        statusText: response.statusText,
        error: errorData
      };
    }

    const data = await response.json() as any;

    // Extract output text from response
    let outputText = '';
    for (const item of data.output || []) {
      if (item.type === 'message' && item.content) {
        for (const content of item.content) {
          if (content.type === 'output_text') {
            outputText += content.text;
          }
        }
      }
    }

    // Extract citations
    const citations: GrokSearchCitation[] = (data.citations || []).map((c: any) => ({
      url: c.url || '',
      title: c.title || '',
      snippet: c.snippet || '',
      source: c.source || 'x'
    }));

    // Extract usage metrics
    const usage: GrokSearchUsage = {
      inputTokens: data.usage?.input_tokens || 0,
      outputTokens: data.usage?.output_tokens || 0,
      totalTokens: data.usage?.total_tokens || 0,
      xSearchCalls: data.server_side_tool_usage?.x_search_calls || 0
    };

    return {
      success: true,
      action: params.action,
      output: outputText,
      citations,
      usage,
      executionTime: Date.now() - startTime
    };
  } catch (error: any) {
    return {
      success: false,
      action: params.action,
      output: '',
      citations: [],
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0, xSearchCalls: 0 },
      error: formatError(error),
      executionTime: Date.now() - startTime
    };
  }
}

export default grokSearch;