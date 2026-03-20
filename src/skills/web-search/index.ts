/**
 * Web Search Skill Implementation
 * Searches the web using multiple sources and aggregates results
 */

interface SearchResult {
  source: string;
  url: string;
  title: string;
  snippet: string;
  content?: string;
  rank?: number;
}

interface WebSearchParams {
  query: string;
  sources?: ('duckduckgo' | 'google' | 'bing')[];
  deep?: boolean;
  maxResults?: number;
}

interface WebSearchResult {
  query: string;
  results: SearchResult[];
  totalFound: number;
  executionTime: number;
}

// DuckDuckGo search (no API key required)
async function searchDuckDuckGo(query: string, maxResults: number): Promise<SearchResult[]> {
  const results: SearchResult[] = [];

  try {
    // Use DuckDuckGo Instant Answer API
    const response = await fetch(
      `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1`
    );

    if (!response.ok) {
      throw new Error(`DuckDuckGo API error: ${response.status}`);
    }

    const data = await response.json();

    // Extract related topics
    if (data.RelatedTopics) {
      for (const topic of data.RelatedTopics.slice(0, maxResults)) {
        if (topic.FirstURL && topic.Text) {
          results.push({
            source: 'duckduckgo',
            url: topic.FirstURL,
            title: topic.Text.split(' - ')[0] || topic.Text,
            snippet: topic.Text
          });
        }
      }
    }

    // Add abstract if available
    if (data.Abstract && data.AbstractURL) {
      results.unshift({
        source: 'duckduckgo',
        url: data.AbstractURL,
        title: data.Heading || 'Summary',
        snippet: data.Abstract,
        content: data.Abstract
      });
    }

  } catch (error) {
    console.error('DuckDuckGo search error:', error);
  }

  return results;
}

// Google Custom Search (requires API key)
async function searchGoogle(query: string, maxResults: number): Promise<SearchResult[]> {
  const results: SearchResult[] = [];
  const apiKey = process.env.GOOGLE_API_KEY;
  const searchEngineId = process.env.GOOGLE_SEARCH_ENGINE_ID;

  if (!apiKey || !searchEngineId) {
    console.log('Google search not configured (missing API key or search engine ID)');
    return results;
  }

  try {
    const response = await fetch(
      `https://www.googleapis.com/customsearch/v1?key=${apiKey}&cx=${searchEngineId}&q=${encodeURIComponent(query)}&num=${maxResults}`
    );

    if (!response.ok) {
      throw new Error(`Google API error: ${response.status}`);
    }

    const data = await response.json();

    if (data.items) {
      for (const item of data.items) {
        results.push({
          source: 'google',
          url: item.link,
          title: item.title,
          snippet: item.snippet,
          rank: item.rank
        });
      }
    }

  } catch (error) {
    console.error('Google search error:', error);
  }

  return results;
}

// Bing Web Search (requires API key)
async function searchBing(query: string, maxResults: number): Promise<SearchResult[]> {
  const results: SearchResult[] = [];
  const apiKey = process.env.BING_API_KEY;

  if (!apiKey) {
    console.log('Bing search not configured (missing API key)');
    return results;
  }

  try {
    const response = await fetch(
      `https://api.bing.microsoft.com/v7.0/search?q=${encodeURIComponent(query)}&count=${maxResults}`,
      {
        headers: {
          'Ocp-Apim-Subscription-Key': apiKey
        }
      }
    );

    if (!response.ok) {
      throw new Error(`Bing API error: ${response.status}`);
    }

    const data = await response.json();

    if (data.webPages?.value) {
      for (const page of data.webPages.value) {
        results.push({
          source: 'bing',
          url: page.url,
          title: page.name,
          snippet: page.snippet
        });
      }
    }

  } catch (error) {
    console.error('Bing search error:', error);
  }

  return results;
}

// Deep search - fetch full content from URLs
async function deepSearch(results: SearchResult[]): Promise<SearchResult[]> {
  const deepResults: SearchResult[] = [];

  for (const result of results) {
    try {
      const response = await fetch(result.url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; MiniAgent/1.0)'
        }
      });

      if (response.ok) {
        const html = await response.text();

        // Extract text content (basic implementation)
        const textContent = html
          .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
          .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
          .replace(/<[^>]+>/g, ' ')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 5000); // Limit content length

        deepResults.push({
          ...result,
          content: textContent
        });
      } else {
        deepResults.push(result);
      }
    } catch (error) {
      deepResults.push(result);
    }
  }

  return deepResults;
}

// Main search function
export async function webSearch(params: WebSearchParams): Promise<WebSearchResult> {
  const startTime = Date.now();
  const {
    query,
    sources = ['duckduckgo'],
    deep = false,
    maxResults = 10
  } = params;

  const allResults: SearchResult[] = [];

  // Run searches in parallel
  const searchPromises = sources.map(async (source) => {
    switch (source) {
      case 'duckduckgo':
        return searchDuckDuckGo(query, maxResults);
      case 'google':
        return searchGoogle(query, maxResults);
      case 'bing':
        return searchBing(query, maxResults);
      default:
        return [];
    }
  });

  const resultsArray = await Promise.all(searchPromises);
  for (const results of resultsArray) {
    allResults.push(...results);
  }

  // Deduplicate by URL
  const uniqueResults = allResults.filter((result, index, self) =>
    index === self.findIndex(r => r.url === result.url)
  );

  // Sort by source priority and rank
  uniqueResults.sort((a, b) => {
    const sourcePriority: Record<string, number> = { google: 0, duckduckgo: 1, bing: 2 };
    return (sourcePriority[a.source] || 99) - (sourcePriority[b.source] || 99);
  });

  // Limit results
  const limitedResults = uniqueResults.slice(0, maxResults);

  // Deep search if requested
  const finalResults = deep ? await deepSearch(limitedResults) : limitedResults;

  return {
    query,
    results: finalResults,
    totalFound: finalResults.length,
    executionTime: Date.now() - startTime
  };
}

export default webSearch;