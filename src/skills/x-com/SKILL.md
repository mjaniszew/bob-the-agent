---
name: x-com
description: Search X.com (Twitter) directly via X API for posts, users, and timelines. More cost-effective than Grok for X.com searches.
version: 1.0.0
trigger: "x.com search|twitter search|x post|x user|tweet search|timeline|x-com"
tools: [shell]
---

# X.com Search Skill

Use this skill to search X.com (Twitter) directly via the native X API. This is a cost-effective alternative to Grok for X.com-specific searches.

**IMPORTANT:** This skill uses the native X API directly, not Grok. Use `XAI_SEARCH_API_KEY` (recommended) to avoid consuming Grok tokens.

## When to Use

Use this skill when:
- You need to search X.com (Twitter) for posts and discussions
- You need to search for X.com users by name or handle
- You need to retrieve a user's timeline (their posts)
- You need real-time or historical social media data
- You need to filter by handles, date ranges, or post attributes

## When NOT to Use

Do NOT use this skill for:
- General web search → Use SearXNG (searxng-search tool) instead
- Searching websites, articles, documentation → Use SearXNG instead
- X.com searches that require Grok's AI analysis → Use grok-search skill instead

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{"action":"searchPosts","query":"your search term"}'
```

## Actions

| Action | Description | Rate Limit (per 15min) |
|--------|-------------|------------------------|
| `searchPosts` | Search recent posts (last 7 days) | 450 requests (app) |
| `searchPostsAll` | Search full archive (pay-per-use) | 300 requests (app) |
| `searchUsers` | Search users by query | 300 requests (app) |
| `getUserTimeline` | Get user's tweets | 10,000 requests (app) |

## Parameters

### Common Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| action | string | Yes | - | Action to perform |
| query | string | Yes | - | Search query |
| maxResults | number | No | 10 | Maximum results (10-100 posts, up to 1000 users) |
| nextToken | string | No | - | Pagination token for next page |

### Posts Search Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| startTime | string | ISO date for start of time range |
| endTime | string | ISO date for end of time range |
| sinceId | string | Return posts newer than this ID |
| untilId | string | Return posts older than this ID |
| tweetFields | string[] | Fields to include (created_at, public_metrics, etc.) |

### User Search Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| userFields | string[] | User fields to include (description, public_metrics, etc.) |

### User Timeline Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| userId | string | User ID to get timeline for |
| username | string | Username (alternative to userId) |

## Query Operators

Use these operators in your search query for more precise results:

### Keywords
- `word` - Match posts containing the word
- `"phrase"` - Match exact phrase
- `#hashtag` - Match posts with hashtag
- `@username` - Match posts mentioning user

### User Filters
- `from:user` - Posts by a specific user
- `to:user` - Replies to a specific user
- `retweets_of:user` - Retweets of a user's posts

### Content Filters
- `has:images` - Posts with images
- `has:videos` - Posts with videos
- `has:links` - Posts with links
- `has:mentions` - Posts mentioning someone

### Language & Exclusions
- `lang:en` - Posts in a specific language
- `-is:retweet` - Exclude retweets
- `-is:reply` - Exclude replies
- `-is:quote` - Exclude quote tweets
- `is:verified` - Posts by verified users

## Examples

### Search Recent Posts
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPosts",
  "query": "breaking news",
  "maxResults": 20
}'
```

### Search with Date Filter
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPosts",
  "query": "technology trends",
  "startTime": "2026-01-01T00:00:00Z",
  "endTime": "2026-03-31T23:59:59Z",
  "maxResults": 50
}'
```

### Search Posts from Specific User
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPosts",
  "query": "from:elonmusk AI",
  "maxResults": 10
}'
```

### Search Full Archive
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPostsAll",
  "query": "historical events",
  "maxResults": 100
}'
```

### Search Users
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchUsers",
  "query": "developer",
  "maxResults": 50
}'
```

### Get User Timeline by Username
```bash
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "getUserTimeline",
  "query": "",
  "username": "elonmusk",
  "maxResults": 20
}'
```

### Pagination
```bash
# First request returns nextToken in meta
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPosts",
  "query": "test",
  "maxResults": 10
}'

# Use nextToken for next page
node /app/scripts/skill-runner.mjs --skill x-com --params '{
  "action": "searchPosts",
  "query": "test",
  "maxResults": 10,
  "nextToken": "b26v89c19zqg8o3fobd8v73egzbdt3qao235oql"
}'
```

## Output

Returns JSON with:

```json
{
  "success": true,
  "action": "searchPosts",
  "data": [
    {
      "id": "123456789",
      "text": "Post content...",
      "author_id": "987654321",
      "created_at": "2026-04-07T10:00:00Z",
      "public_metrics": {
        "like_count": 10,
        "retweet_count": 5,
        "reply_count": 2
      }
    }
  ],
  "meta": {
    "resultCount": 1,
    "nextToken": "b26v89c19zqg8o3fobd8v73egzbdt3qao235oql"
  },
  "executionTime": 250
}
```

## Environment Variables

- `XAI_SEARCH_API_KEY` - Required for X API access (recommended, separate from XAI_API_KEY)
- `XAI_API_KEY` - Fallback if XAI_SEARCH_API_KEY is not set

## Why Separate API Key?

Using `XAI_SEARCH_API_KEY` instead of the main `XAI_API_KEY`:
- Prevents token consumption from Grok
- Keeps search costs separate from AI model usage
- Better cost tracking and management

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| 401 Unauthorized | Invalid API key | Check XAI_SEARCH_API_KEY environment variable |
| 429 Rate Limited | Too many requests | Wait before making more requests |
| 403 Forbidden | Insufficient access | Upgrade X API access level |
| 404 Not Found | Resource doesn't exist | Check user ID or query parameters |

## Notes

- Recent search covers last 7 days only
- Full archive search requires elevated X API access
- Use pagination for large result sets
- Rate limits reset every 15 minutes
- Consider caching results for frequently accessed data