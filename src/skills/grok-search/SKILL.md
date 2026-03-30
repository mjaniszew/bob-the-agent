---
name: grok-search
description: Search web and X.com (Twitter) using x.AI's Grok API for real-time information
---

# Grok Search Skill

Use this skill to search the web and X.com (Twitter) using x.AI's Grok API. Provides real-time access to information including social media content.

## When to Use

Use this skill when:
- When specifically asked to search the web using grok
- You need real-time information from social media (X/Twitter)
- You need current news and trends
- You want to search both web and social media together
- DuckDuckGo/Google/Bing are not sufficient for your needs

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"your search term","mode":"web"}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | Yes | - | The search query |
| mode | string | No | "web" | Search mode: "web", "x", or "both" |
| allowedDomains | array | No | - | Domains to include in results |
| excludedDomains | array | No | - | Domains to exclude from results |
| allowedXHandles | array | No | - | X.com handles to include |
| excludedXHandles | array | No | - | X.com handles to exclude |
| fromDate | string | No | - | Start date for X.com results (YYYY-MM-DD) |
| toDate | string | No | - | End date for X.com results (YYYY-MM-DD) |
| enableImageUnderstanding | boolean | No | false | Enable image understanding |
| enableVideoUnderstanding | boolean | No | false | Enable video understanding for X.com |
| maxResults | number | No | 10 | Maximum results per source |

## Examples

### Web Search
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"AI developments 2026","mode":"web"}'
```

### X/Twitter Search
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"breaking news","mode":"x","maxResults":20}'
```

### Combined Search with Filters
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"technology news","mode":"both","fromDate":"2026-01-01","maxResults":15}'
```

## Output

Returns JSON with:
- `query`: The original search query
- `mode`: Search mode used
- `results`: Array of results with source, url, title, content
- `totalFound`: Number of results found
- `executionTime`: Time in milliseconds
- `error`: Error message if applicable

## Environment Variables

- `XAI_API_KEY`: Required for Grok API access