---
name: grok-search
description: Use this skill to search X.com (Twitter) using x.AI's Grok API. Use this skill ONLY for X.com/Twitter searches. For general web search, use SearXNG via OpenClaw's searxng-search tool instead. Trigger word: x.com, twitter, x search, grok x.
---

# Grok Search Skill

Use this skill to search X.com (Twitter) using x.AI's Grok API. Provides real-time access to social media content.

**IMPORTANT:** This skill is for X.com (Twitter) search ONLY. For general web search, use SearXNG via OpenClaw's built-in `searxng-search` tool - it's free and doesn't consume tokens.

## When to Use

Use this skill when:
- You need to search X.com (Twitter) for posts and discussions
- You need real-time information from social media
- You need current news and trends from Twitter
- You need to filter by X handles or date ranges

## When NOT to Use

Do NOT use this skill for:
- General web search → Use SearXNG (searxng-search tool) instead
- Searching for websites, articles, documentation → Use SearXNG instead

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"your search term"}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | Yes | - | The search query |
| allowedXHandles | array | No | - | X.com handles to include |
| excludedXHandles | array | No | - | X.com handles to exclude |
| fromDate | string | No | - | Start date for results (YYYY-MM-DD) |
| toDate | string | No | - | End date for results (YYYY-MM-DD) |
| enableImageUnderstanding | boolean | No | false | Enable image understanding |
| enableVideoUnderstanding | boolean | No | false | Enable video understanding |
| maxResults | number | No | 10 | Maximum results |

## Examples

### Basic X/Twitter Search
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"breaking news","maxResults":20}'
```

### Search with Date Filter
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"query":"technology news","fromDate":"2026-01-01","maxResults":15}'
```

## Output

Returns JSON with:
- `query`: The original search query
- `results`: Array of results with source (always 'x'), url, title, content
- `totalFound`: Number of results found
- `executionTime`: Time in milliseconds
- `error`: Error message if applicable

## Environment Variables

- `XAI_SEARCH_API_KEY`: Required for Grok API access (recommended, separate from main XAI_API_KEY)
- `XAI_API_KEY`: Fallback if XAI_SEARCH_API_KEY is not set

## Why Separate API Key?

Using `XAI_SEARCH_API_KEY` instead of the main `XAI_API_KEY` prevents OpenClaw from consuming tokens for web search. SearXNG is used for web search (free), while Grok is used only for X.com search.