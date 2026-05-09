# Skills

This file describes which skills should be used by agent to perform various tasks and gives some context how they should be implemented or included in agent if already available.

## Current Implementation Status

The skills in `src/skills/` are TypeScript implementations for the agent's capability layer.

**Implemented Skills (TypeScript):**
| Skill | Status | Description |
|-------|--------|-------------|
| data-extraction | ✅ Implemented | Extract data from files and URLs |
| math-operations | ✅ Implemented | Mathematical calculations |
| x-com | ✅ Implemented | Direct X.com API search for posts, users, timelines |
| grok-search | ✅ Implemented | X.com search via xAI Grok x_search tool (fallback for x-com) |
| aws-s3 | ✅ Implemented | S3 upload and URL generation |
| agent-to-agent | ✅ Implemented | Inter-agent messaging via NATS |
| searxng-search | ✅ Implemented | Web search via SearXNG (free, no tokens) |

**Implemented Skills (Python):**
| Skill | Status | Description |
|-------|--------|-------------|
| agent-to-agent | ✅ Implemented | Agent to agent communication using NATS |

**Environment Variables Required:**
- `USER_X_COM_API_TOKEN` - For X.com API access (x-com skill)
- `XAI_API_KEY` - Fallback for X.com search if USER_X_COM_API_TOKEN not set
- `USER_XAI_SEARCH_API_KEY` - For Grok search API access (grok-search skill, separate from XAI_API_KEY)
- `SEARXNG_SECRET_KEY` - For SearXNG configuration
- `USER_AWS_S3_ACCESS_KEY_ID`, `USER_AWS_S3_SECRET_ACCESS_KEY`, `USER_AWS_S3_BUCKET`, `USER_AWS_S3_REGION` - For AWS S3
- `PLAYWRIGHT_CLI` - Installed in PATH for Playwright skill
- `NATS_URL` - NATS server URL for inter-agent messaging (default: nats://nats:4222)

---

## SearXNG Web Search

1. SearXNG is the primary web search engine - free, no tokens consumed
2. Available via OpenClaw's built-in `searxng-search` tool
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

## Agent-to-Agent (NATS Inter-Agent Messaging)
1. Agents communicate across containers via NATS messaging using the `agent-to-agent` skill
2. Enables cross-container task delegation between specialized agents
3. Each agent runs a `register-nats.py` background listener that subscribes to NATS subjects
4. Message routing uses subjects: `agent.{target_id}.tasks` and `agent.{target_id}.results`
5. Actions:
   - `send_task` — Send a task to a target agent with goal, context, and toolsets
   - `check_messages` — Check for incoming task/result messages for the current agent
   - `send_result` — Send a task result back to the originating agent
6. Available target agents: `researcher` (deep research), `simple` (lightweight tasks)
7. Uses `nats-py` v2.14.0 Python library for NATS communication
8. Requires NATS server running (configured in Docker Compose)