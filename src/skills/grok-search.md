# Grok Search Skill

Searches X.com (Twitter) using x.AI's Grok API.

**NOTE:** This skill is for X.com (Twitter) search ONLY. For web search, use SearXNG via OpenClaw's `searxng-search` tool (free, no tokens).

## Features

- **X.com Search**: Search Twitter/X posts with advanced filtering options
- **Handle Filtering**: Filter X.com results by specific user handles
- **Date Range**: Filter X.com results by date range
- **Media Understanding**: Optional image and video understanding capabilities

## Usage

### Basic X.com Search
```yaml
skill: grok-search
parameters:
  query: "search term"
  max_results: 10
```

### X.com Search with Handle Filtering
```yaml
skill: grok-search
parameters:
  query: "search term"
  allowed_x_handles:
    - "elonmusk"
    - "openai"
  from_date: "2026-01-01"
  to_date: "2026-04-01"
  enable_video_understanding: true
  max_results: 10
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | Yes | - | Search query string |
| allowed_x_handles | string[] | No | - | X.com handles to include |
| excluded_x_handles | string[] | No | - | X.com handles to exclude |
| from_date | string | No | - | Start date for results (YYYY-MM-DD) |
| to_date | string | No | - | End date for results (YYYY-MM-DD) |
| enable_image_understanding | boolean | No | false | Enable image understanding in results |
| enable_video_understanding | boolean | No | false | Enable video understanding in X.com results |
| max_results | number | No | 10 | Maximum number of results |

## Output

Returns structured search results with:
- source: "x" (always X.com)
- url: Result URL
- title: Result title
- content: Result snippet/content
- metadata: Additional metadata (optional)

## Configuration

Set the x.AI API key in environment variables:
- XAI_SEARCH_API_KEY: Recommended, separate from main XAI_API_KEY
- XAI_API_KEY: Fallback if XAI_SEARCH_API_KEY not set

Get your API key from: https://console.x.ai/

## Why Separate API Key?

Using `XAI_SEARCH_API_KEY` instead of `XAI_API_KEY` prevents OpenClaw from consuming tokens when using its built-in web search. SearXNG is the preferred web search method (free), while Grok is used only for X.com search.

## Pricing

- X Search: $5 per 1,000 calls

See x.AI documentation for current pricing: https://docs.x.ai/docs/models

## Error Handling

The skill gracefully handles:
- Missing API key (returns error message)
- API rate limits (returns error message)
- Network errors (returns error message)
- Invalid responses (returns empty results)

## Notes

- For general web search, use SearXNG via OpenClaw's `searxng-search` tool - it's free and doesn't consume tokens
- This skill is specifically for X.com (Twitter) search
- The X.com search is exclusive to Grok and provides unique capabilities not available in other search APIs