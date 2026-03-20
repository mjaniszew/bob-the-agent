# Web Search Skill

Searches the web using multiple sources and aggregates results.

## Features
- Multi-source search (Google, Bing, DuckDuckGo)
- Parallel execution for faster results
- Deep search capability for comprehensive coverage
- Result caching for repeated queries

## Usage
```yaml
skill: web-search
parameters:
  query: "search term"
  sources:
    - google
    - bing
    - duckduckgo
  deep: false
  max_results: 10
```

## Output
Returns structured search results with:
- Source URL
- Title
- Snippet
- Full content (if deep search enabled)

## Configuration
Set API keys in environment variables:
- GOOGLE_API_KEY (optional)
- BING_API_KEY (optional)
- Default: DuckDuckGo (no API key required)