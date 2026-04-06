/**
 * Grok Search Skill Implementation
 * Searches X.com (Twitter) using x.AI's Grok API
 *
 * Note: Web search functionality has been moved to SearXNG.
 * This skill is now dedicated to X.com (Twitter) search only.
 */

interface GrokSearchResult {
  source: 'x';
  url: string;
  title: string;
  content: string;
  metadata?: Record<string, unknown>;
}

interface GrokXSearchParams {
  query: string;
  allowedXHandles?: string[];
  excludedXHandles?: string[];
  fromDate?: string;
  toDate?: string;
  enableImageUnderstanding?: boolean;
  enableVideoUnderstanding?: boolean;
  maxResults?: number;
}

interface GrokSearchResponse {
  results: Array<{
    url: string;
    title: string;
    content: string;
    metadata?: Record<string, unknown>;
  }>;
}

interface SearchResult {
  results: GrokSearchResult[];
  error?: string;
}

const XAI_API_BASE = 'https://api.x.ai/v1';

/**
 * Search X.com (Twitter) using x.AI's x_search tool
 * Uses XAI_SEARCH_API_KEY (separate from main XAI_API_KEY to avoid token consumption)
 */
async function grokXSearch(params: GrokXSearchParams): Promise<SearchResult> {
  // Use XAI_SEARCH_API_KEY for search (separate from main XAI_API_KEY)
  const apiKey = process.env.XAI_SEARCH_API_KEY || process.env.XAI_API_KEY;

  if (!apiKey) {
    return { results: [], error: 'XAI_SEARCH_API_KEY or XAI_API_KEY environment variable is not set. Use XAI_SEARCH_API_KEY (recommended) to avoid consuming tokens for web search via OpenClaw.' };
  }

  const requestBody: Record<string, unknown> = {
    query: params.query,
    max_results: params.maxResults || 10
  };

  if (params.allowedXHandles && params.allowedXHandles.length > 0) {
    requestBody.allowed_x_handles = params.allowedXHandles;
  }

  if (params.excludedXHandles && params.excludedXHandles.length > 0) {
    requestBody.excluded_x_handles = params.excludedXHandles;
  }

  if (params.fromDate) {
    requestBody.from_date = params.fromDate;
  }

  if (params.toDate) {
    requestBody.to_date = params.toDate;
  }

  if (params.enableImageUnderstanding) {
    requestBody.enable_image_understanding = true;
  }

  if (params.enableVideoUnderstanding) {
    requestBody.enable_video_understanding = true;
  }

  try {
    const response = await fetch(`${XAI_API_BASE}/x_search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`
      },
      body: JSON.stringify(requestBody)
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = (errorData as any).error?.message || `API error: ${response.status}`;
      console.error('Grok X Search API error:', response.status, errorData);
      return { results: [], error: errorMessage };
    }

    const data = await response.json() as GrokSearchResponse;

    return {
      results: (data.results || []).map(result => ({
        source: 'x' as const,
        url: result.url,
        title: result.title,
        content: result.content,
        metadata: result.metadata
      }))
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    console.error('Grok X Search error:', error);
    return { results: [], error: errorMessage };
  }
}

interface GrokSearchOutput {
  query: string;
  results: GrokSearchResult[];
  totalFound: number;
  executionTime: number;
  error?: string;
}

/**
 * Main Grok Search function
 * Searches X.com (Twitter) using x.AI's Grok API
 *
 * Note: For web search, use SearXNG instead (free, no token consumption).
 * This skill is dedicated to X.com (Twitter) search.
 */
export async function grokSearch(params: { query: string; allowedXHandles?: string[]; excludedXHandles?: string[]; fromDate?: string; toDate?: string; enableImageUnderstanding?: boolean; enableVideoUnderstanding?: boolean; maxResults?: number }): Promise<GrokSearchOutput> {
  const startTime = Date.now();
  const apiKey = process.env.XAI_SEARCH_API_KEY || process.env.XAI_API_KEY;

  console.log("Doing Grok X Search");

  if (!apiKey) {
    return {
      query: params.query,
      results: [],
      totalFound: 0,
      executionTime: Date.now() - startTime,
      error: 'XAI_SEARCH_API_KEY or XAI_API_KEY environment variable is not set. Use XAI_SEARCH_API_KEY (recommended) to avoid consuming tokens for web search via OpenClaw.'
    };
  }

  const maxResults = params.maxResults || 10;

  try {
    const xResult = await grokXSearch({
      query: params.query,
      allowedXHandles: params.allowedXHandles,
      excludedXHandles: params.excludedXHandles,
      fromDate: params.fromDate,
      toDate: params.toDate,
      enableImageUnderstanding: params.enableImageUnderstanding,
      enableVideoUnderstanding: params.enableVideoUnderstanding,
      maxResults
    });

    return {
      query: params.query,
      results: xResult.results.slice(0, maxResults),
      totalFound: xResult.results.length,
      executionTime: Date.now() - startTime,
      error: xResult.error
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    return {
      query: params.query,
      results: [],
      totalFound: 0,
      executionTime: Date.now() - startTime,
      error: `Network error: ${errorMessage}`
    };
  }
}

export default grokSearch;