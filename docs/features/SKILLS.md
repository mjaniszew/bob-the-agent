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
| aws-s3 | ✅ Implemented | S3 upload and URL generation |

**System Tool Skills (SKILL.md only):**
| Skill | Status | Description |
|-------|--------|-------------|
| playwright | ✅ Available | Browser automation via playwright-cli |

**Built-in OpenClaw Tools:**
| Tool | Status | Description |
|-------|--------|-------------|
| searxng-search | ✅ Available | Web search via SearXNG (free, no tokens) |

**Environment Variables Required:**
- `X_COM_API_TOKEN` - For X.com API access (x-com skill)
- `XAI_API_KEY` - Fallback for X.com search if X_COM_API_TOKEN not set
- `SEARXNG_SECRET_KEY` - For SearXNG configuration
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`, `AWS_S3_REGION` - For AWS S3
- `PLAYWRIGHT_CLI` - Installed in PATH for Playwright skill

---

## SearXNG Web Search

1. SearXNG is the primary web search engine - free, no tokens consumed
2. Available via OpenClaw's built-in `searxng-search` tool
3. Runs as a Docker container alongside the agent
4. No API key required - privacy-respecting metasearch

## X.com Search (x-com skill)

1. Direct X.com API access for posts, users, and timelines
2. Uses `X_COM_API_TOKEN` (separate from `XAI_API_KEY` to avoid token consumption)
4. Actions:
   - `searchPosts` - Search recent posts (last 7 days)
   - `searchPostsAll` - Search full archive (requires elevated access)
   - `searchUsers` - Search users by query
   - `getUserTimeline` - Get user's tweets
5. Supports pagination with `nextToken` for large result sets
6. Query operators: `from:user`, `#hashtag`, `has:images`, `lang:en`, etc.

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

## Playwright (Browser Automation)
1. Agent should be able to open and visit complex websites using playwright-cli
2. Should interact with graphical interfaces of websites, take screenshots
3. Should perform web automations, execute scripts in browser
4. This skill uses playwright-cli system tool, not TypeScript implementation
5. Requires playwright-cli to be installed and available in PATH