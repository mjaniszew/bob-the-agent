# Skills

This file describes which skills should be used by agent to perform various tasks and gives some context how they should be implemented or included in agent if already available.

## Current Implementation Status

The skills in `src/skills/` are standalone, directly invoked implementations of the agent's capability layer. Every TypeScript skill carries its own `cli.ts` argv entry plus `package.json` and runs natively under the image's Node via type stripping (no tsc build, no dist) — the agent invokes each skill exactly as documented in its SKILL.md: `node /app/skills/<skill>/cli.ts '<json>'`; the Python searxng-web-search skill is invoked as `python3 /app/skills/searxng-web-search/scripts/searxng_search.py`. There is no central skill runner and no build step — per-skill dependencies are installed during the Docker build from each manifest (package.json / requirements.txt; searxng-web-search is stdlib-only).

**Implemented Skills (TypeScript):**
| Skill | Status | Description |
|-------|--------|-------------|
| data-extraction | ✅ Implemented | Extract data from files and URLs |
| math-operations | ✅ Implemented | Mathematical calculations |
| x-com | ✅ Implemented | Direct X.com API search for posts, users, timelines |
| grok-search | ✅ Implemented | X.com search via xAI Grok x_search tool (fallback for x-com) |
| aws-s3 | ✅ Implemented | S3 upload and URL generation |
| searxng-web-search | ✅ Implemented | Web search via SearXNG (free, no tokens) |

**Environment Variables Required:**
- `USER_X_COM_API_TOKEN` - For X.com API access (x-com skill)
- `XAI_API_KEY` - Legacy fallback consumed inside the x-com and grok-search skills only (not exposed via compose)
- `USER_XAI_SEARCH_API_KEY` - For Grok search API access (grok-search skill, separate from XAI_API_KEY)
- `USER_AWS_S3_ACCESS_KEY_ID`, `USER_AWS_S3_SECRET_ACCESS_KEY`, `USER_AWS_S3_BUCKET`, `USER_AWS_S3_REGION` - For AWS S3

---

## SearXNG Web Search

1. SearXNG is the primary web search engine - free, no tokens consumed
2. Implemented as the `searxng-web-search` skill (`src/skills/searxng-web-search/`)
3. Runs as a Docker container alongside the agent
4. No API key required - privacy-respecting metasearch

## X.com Search (x-com skill)

1. Direct X.com API access for posts, users, and timelines
2. Uses `USER_X_COM_API_TOKEN` (separate from `XAI_API_KEY` to avoid token consumption)
4. Actions:
   - `searchPosts` - Search recent posts (last 7 days)
   - `searchPostsAll` - Search full archive (requires elevated access)
   - `searchUsers` - Search users by query
   - `getUserTimeline` - Get user's tweets
5. Supports pagination with `nextToken` for large result sets
6. Query operators: `from:user`, `#hashtag`, `has:images`, `lang:en`, etc.

## Grok Search (grok-search skill)

1. X.com search via xAI Grok's x_search tool through the Responses API
2. Uses `USER_XAI_SEARCH_API_KEY` (separate from `XAI_API_KEY` to avoid OpenClaw auto-consumption)
3. Fallback when x-com skill fails or is unavailable
4. Actions:
   - `searchPosts` - Search recent posts
   - `searchPostsAll` - Search full archive
   - `searchUsers` - Search users by query
   - `getUserTimeline` - Get user's tweets
5. Returns AI-synthesized results with citations (URLs, titles, snippets)
6. Supports handle filtering (`allowedHandles`, `excludedHandles`), date ranges, image/video understanding
7. Uses cost-effective `grok-4-1-fast-non-reasoning` model by default

## Data Extraction and Deep Analysis
1. Agent should be able to extract data from any source, including web search results, pdf and other documents
2. Agent should be able to analyze fairly large quantity of data, and extract information from it
3. Agent should be able to select specific data models that fits the purpose of particular tasks eg. economic data analysis, technical data analysis, etc.
4. Agent should be able to cross-check that data with other data sources already stored in files or database

## Math operations
1. Agent should be able to use proper and accurate math operations to perform calculations and complex data analysis
2. Agent should be able to choose separate math models depending on task to perform better calculations

## AWS S3
1. Agent should be able to upload files to AWS S3 buckets
2. Should generate presigned URLs for time-limited access to private objects
3. Should generate public URLs for objects in public buckets (no credentials required)
4. Should support both direct content upload and file path upload
5. Requires AWS credentials and bucket configuration via environment variables