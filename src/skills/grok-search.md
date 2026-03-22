# Grok Search Skill

Searches the web and X.com (Twitter) using x.AI's Grok API.

## Features

- **Web Search**: Search the web using x.AI's powerful search capabilities
- **X.com Search**: Search Twitter/X posts with advanced filtering options
- **Domain Filtering**: Restrict results to specific domains or exclude unwanted ones
- **Handle Filtering**: Filter X.com results by specific user handles
- **Date Range**: Filter X.com results by date range
- **Media Understanding**: Optional image and video understanding capabilities
- **Combined Search**: Search both web and X.com simultaneously

## Usage

### Basic Web Search
```yaml
skill: grok-search
parameters:
  query: "search term"
  mode: "web"
  max_results: 10
```

### Basic X.com Search
```yaml
skill: grok-search
parameters:
  query: "search term"
  mode: "x"
  max_results: 10
```

### Combined Search (Web + X.com)
```yaml
skill: grok-search
parameters:
  query: "search term"
  mode: "both"
  max_results: 20
```

### Web Search with Domain Filtering
```yaml
skill: grok-search
parameters:
  query: "search term"
  mode: "web"
  allowed_domains:
    - "example.com"
    - "docs.example.com"
  excluded_domains:
    - "spam.example.com"
  max_results: 10
```

### X.com Search with Handle Filtering
```yaml
skill: grok-search
parameters:
  query: "search term"
  mode: "x"
  allowed_x_handles:
    - "elonmusk"
    - "openai"
  from_date: "2024-01-01"
  to_date: "2024-12-31"
  enable_video_understanding: true
  max_results: 10
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | Yes | - | Search query string |
| mode | string | No | "web" | Search mode: "web", "x", or "both" |
| allowed_domains | string[] | No | - | Domains to include in web results |
| excluded_domains | string[] | No | - | Domains to exclude from web results |
| allowed_x_handles | string[] | No | - | X.com handles to include |
| excluded_x_handles | string[] | No | - | X.com handles to exclude |
| from_date | string | No | - | Start date for X.com results (YYYY-MM-DD) |
| to_date | string | No | - | End date for X.com results (YYYY-MM-DD) |
| enable_image_understanding | boolean | No | false | Enable image understanding in results |
| enable_video_understanding | boolean | No | false | Enable video understanding in X.com results |
| max_results | number | No | 10 | Maximum number of results per source |

## Output

Returns structured search results with:
- source: "web" or "x"
- url: Result URL
- title: Result title
- content: Result snippet/content
- metadata: Additional metadata (optional)

## Configuration

Set the x.AI API key in environment variables:
- XAI_API_KEY: Your x.AI API key

Get your API key from: https://console.x.ai/

## Pricing

- Web Search: $5 per 1,000 calls
- X Search: $5 per 1,000 calls

See x.AI documentation for current pricing: https://docs.x.ai/docs/models

## Error Handling

The skill gracefully handles:
- Missing API key (returns error message)
- API rate limits (returns error message)
- Network errors (returns error message)
- Invalid responses (returns empty results)

## Notes

- This skill is separate from the `web-search` skill which uses DuckDuckGo, Google, and Bing
- You can use both skills together for comprehensive search coverage
- The X.com search is exclusive to Grok and provides unique capabilities not available in other search APIs