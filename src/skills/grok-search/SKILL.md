---
name: grok-search
description: Use this skill to search X.com (Twitter) via xAI Grok's x_search tool. Fallback when x-com fails or when requested. Trigger words: grok search|xai search|grok x search
---

# Grok Search Skill

Use this skill to search X.com (Twitter) via xAI Grok's x_search tool using the xAI Responses API. This is the fallback skill for X.com searches when the native X API (x-com skill) is unavailable or fails, and when specifically requested.

**IMPORTANT:** This skill uses the xAI Responses API with `x_search` tool. Use `XAI_SEARCH_API_KEY` (separate from `XAI_API_KEY`) to isolate search costs from general AI usage.

## When to Use

Use this skill when:
- The x-com skill fails or is unavailable
- The user specifically requests Grok search
- You need AI-synthesized search results with citations from X.com
- You need semantic or intelligent search that understands context
- You need to search X.com and want results summarized by an AI model

## When NOT to Use

Do NOT use this skill for:
- General web search → Use SearXNG (searxng-search tool) instead
- Direct X API access is available and working → Use x-com skill instead (cheaper)

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{"action":"searchPosts","query":"your search term"}'
```

## Actions

| Action | Description |
|--------|-------------|
| `searchPosts` | Search recent posts (last 7 days) |
| `searchPostsAll` | Search posts from any time period |
| `searchUsers` | Search users by query |
| `getUserTimeline` | Get a user's posts/timeline |

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| action | string | Yes | - | Action to perform (searchPosts, searchPostsAll, searchUsers, getUserTimeline) |
| query | string | Yes | - | Search query |
| username | string | Conditional | - | Username for getUserTimeline action |
| allowedHandles | string[] | No | - | Only include posts from these handles |
| excludedHandles | string[] | No | - | Exclude posts from these handles |
| fromDate | string | No | - | ISO date for start of time range |
| toDate | string | No | - | ISO date for end of time range |
| enableImageUnderstanding | boolean | No | false | Enable AI understanding of images in posts |
| enableVideoUnderstanding | boolean | No | false | Enable AI understanding of videos in posts |
| model | string | No | grok-4.1-fast | xAI model to use for the response |

## Examples

### Search Recent Posts
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "searchPosts",
  "query": "breaking news"
}'
```

### Search with Handle Filters
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "searchPosts",
  "query": "AI developments",
  "allowedHandles": ["elonmusk", "sama"],
  "fromDate": "2026-01-01T00:00:00Z"
}'
```

### Search All Time Periods
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "searchPostsAll",
  "query": "historical events on X"
}'
```

### Search Users
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "searchUsers",
  "query": "developer"
}'
```

### Get User Timeline
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "getUserTimeline",
  "query": "",
  "username": "elonmusk"
}'
```

### Search with Image Understanding
```bash
node /app/scripts/skill-runner.mjs --skill grok-search --params '{
  "action": "searchPosts",
  "query": "photos from the event",
  "enableImageUnderstanding": true
}'
```

## Output

Returns JSON with AI-synthesized results including citations:

```json
{
  "success": true,
  "action": "searchPosts",
  "data": {
    "summary": "AI-synthesized summary of search results...",
    "citations": [
      {
        "text": "Relevant excerpt from a post...",
        "url": "https://x.com/user/status/123456789",
        "author": "username"
      }
    ]
  },
  "executionTime": 1500
}
```

## Environment Variables

- `XAI_SEARCH_API_KEY` - Required for xAI search API access (separate from XAI_API_KEY to isolate search costs)
- `XAI_API_KEY` - Fallback if XAI_SEARCH_API_KEY is not set

## Why Separate API Key?

Using `XAI_SEARCH_API_KEY` instead of the main `XAI_API_KEY`:
- Keeps search costs separate from AI model usage
- Better cost tracking and management
- X Search calls are billed separately ($5/1K calls) and can rack up costs quickly
- Dedicated API access for Grok search operations

## Cost Notes

| Resource | Cost |
|----------|------|
| grok-4.1-fast (default model) | $0.20/M input tokens, $0.50/M output tokens |
| X Search tool | $5.00 per 1,000 calls |

Compared to the x-com skill (native X API), Grok search is more expensive per call but provides AI-synthesized results with semantic understanding. Use the x-com skill for straightforward searches and reserve Grok search for when you need intelligent analysis or when x-com is unavailable.

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| 401 Unauthorized | Invalid API key | Check XAI_SEARCH_API_KEY environment variable |
| 429 Rate Limited | Too many requests | Wait before making more requests |
| 403 Forbidden | Insufficient access | Verify API key has xAI Responses API access |
| 500 Internal Server Error | xAI service issue | Retry after a brief wait |
| Timeout | Slow response from xAI | Reduce query complexity or retry |

## Notes

- This skill is a fallback for the x-com skill. Prefer x-com for direct X API access when available (cheaper, more structured data).
- Grok search returns AI-synthesized results with citations rather than raw API data.
- The `searchPosts` action covers the last 7 days; use `searchPostsAll` for any time period.
- Image and video understanding add processing time and cost.
- The default model `grok-4.1-fast` balances speed and quality; you can specify a different model if needed.