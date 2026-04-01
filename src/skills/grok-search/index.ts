/**
 * Grok Search Skill Implementation
 * Searches web and X.com (Twitter) using x.AI's Grok API
 */

interface GrokSearchResult {
  source: 'web' | 'x';
  url: string;
  title: string;
  content: string;
  metadata?: Record<string, unknown>;
}

interface GrokWebSearchParams {
  query: string;
  allowedDomains?: string[];
  excludedDomains?: string[];
  enableImageUnderstanding?: boolean;
  maxResults?: number;
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

interface GrokSearchParams {
  query: string;
  mode: 'web' | 'x' | 'both';
  allowedDomains?: string[];
  excludedDomains?: string[];
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
 * Search the web using x.AI's web-search tool
 */
async function grokWebSearch(params: GrokWebSearchParams): Promise<SearchResult> {
  const apiKey = process.env.XAI_API_KEY;

  if (!apiKey) {
    return { results: [], error: 'XAI_API_KEY environment variable is not set' };
  }

  const requestBody: Record<string, unknown> = {
    query: params.query,
    max_results: params.maxResults || 10
  };

  if (params.allowedDomains && params.allowedDomains.length > 0) {
    requestBody.allowed_domains = params.allowedDomains;
  }

  if (params.excludedDomains && params.excludedDomains.length > 0) {
    requestBody.excluded_domains = params.excludedDomains;
  }

  if (params.enableImageUnderstanding) {
    requestBody.enable_image_understanding = true;
  }

  try {
    const response = await fetch(`${XAI_API_BASE}/web-search`, {
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
      console.error('Grok Web Search API error:', response.status, errorData);
      return { results: [], error: errorMessage };
    }

    const data = await response.json() as GrokSearchResponse;

    return {
      results: (data.results || []).map(result => ({
        source: 'web' as const,
        url: result.url,
        title: result.title,
        content: result.content,
        metadata: result.metadata
      }))
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    console.error('Grok Web Search error:', error);
    return { results: [], error: errorMessage };
  }
}

/**
 * Search X.com (Twitter) using x.AI's x_search tool
 */
async function grokXSearch(params: GrokXSearchParams): Promise<SearchResult> {
  const apiKey = process.env.XAI_API_KEY;

  if (!apiKey) {
    return { results: [], error: 'XAI_API_KEY environment variable is not set' };
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
  mode: string;
  results: GrokSearchResult[];
  totalFound: number;
  executionTime: number;
  error?: string;
}

/**
 * Main Grok Search function
 * Searches web and/or X.com (Twitter) using x.AI's Grok API
 */
export async function grokSearch(params: GrokSearchParams): Promise<GrokSearchOutput> {
  const startTime = Date.now();
  const apiKey = process.env.XAI_API_KEY;

  console.log("Doing Grok Search");

  if (!apiKey) {
    return {
      query: params.query,
      mode: params.mode,
      results: [],
      totalFound: 0,
      executionTime: Date.now() - startTime,
      error: 'XAI_API_KEY environment variable is not set. Please configure your x.AI API key.'
    };
  }

  const allResults: GrokSearchResult[] = [];
  const errors: string[] = [];
  const maxResults = params.maxResults || 10;

  try {
    if (params.mode === 'web' || params.mode === 'both') {
      const webResult = await grokWebSearch({
        query: params.query,
        allowedDomains: params.allowedDomains,
        excludedDomains: params.excludedDomains,
        enableImageUnderstanding: params.enableImageUnderstanding,
        maxResults
      });
      allResults.push(...webResult.results);
      if (webResult.error) {
        errors.push(webResult.error);
      }
    }

    if (params.mode === 'x' || params.mode === 'both') {
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
      allResults.push(...xResult.results);
      if (xResult.error) {
        errors.push(xResult.error);
      }
    }

    return {
      query: params.query,
      mode: params.mode,
      results: allResults.slice(0, maxResults),
      totalFound: allResults.length,
      executionTime: Date.now() - startTime,
      error: errors.length > 0 ? errors.join('; ') : undefined
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    return {
      query: params.query,
      mode: params.mode,
      results: [],
      totalFound: 0,
      executionTime: Date.now() - startTime,
      error: `Network error: ${errorMessage}`
    };
  }
}

export default grokSearch;