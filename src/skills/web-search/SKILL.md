---
name: web-search
description: Use this skill when you need to find information on the web using classic way, not through AI. It supports multiple search engines and can optionally fetch full content from result pages. Try to use it as a first choic above anything else, unless specifically asked to use particular tool like x.ai. Trigger word: search, web search, google, bing, duckduckgo.
---

# Web Search Skill

Use this skill when you need to find information on the web. It supports multiple search engines and can optionally fetch full content from result pages.

## When to Use

Use this skill when:
- You need to find current information on the web
- You need to research a topic
- You need to verify facts from multiple sources
- You need to find specific URLs or resources

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill web-search --params '{"query":"your search term"}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | Yes | - | The search query |
| sources | array | No | ["duckduckgo"] | Search sources: "duckduckgo", "google", "bing" |
| deep | boolean | No | false | Enable deep search to fetch full content from result pages |
| maxResults | number | No | 10 | Maximum number of results per source |

## Examples

### Basic Search
```bash
node /app/scripts/skill-runner.mjs --skill web-search --params '{"query":"latest AI news"}'
```

### Multi-source Search
```bash
node /app/scripts/skill-runner.mjs --skill web-search --params '{"query":"TypeScript best practices","sources":["google","duckduckgo"],"maxResults":20}'
```

### Deep Search
```bash
node /app/scripts/skill-runner.mjs --skill web-search --params '{"query":"detailed tutorial","deep":true,"maxResults":5}'
```

## Output

Returns JSON with:
- `query`: The original search query
- `results`: Array of search results with url, title, snippet, and optionally content
- `totalFound`: Number of results found
- `executionTime`: Time in milliseconds

## Environment Variables

- `GOOGLE_API_KEY`: Required for Google search
- `GOOGLE_SEARCH_ENGINE_ID`: Required for Google search
- `BING_API_KEY`: Required for Bing search
- DuckDuckGo requires no API key (default)