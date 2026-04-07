# Skills

This file describes which skills should be used by agent to perform various tasks and gives some context how they should be implemented or included in agent if already available.

## Current Implementation Status

The skills in `src/skills/` are TypeScript implementations for the agent's capability layer.

**Implemented Skills (TypeScript):**
| Skill | Status | Description |
|-------|--------|-------------|
| data-extraction | ✅ Implemented | Extract data from files and URLs |
| math-operations | ✅ Implemented | Mathematical calculations |
| document-creation | ✅ Implemented | PDF and DOCX generation |
| notifications | ✅ Implemented | Discord, email notifications |
| scheduling | ✅ Implemented | Cron-based task scheduling |
| grok-search | ✅ Implemented | X.AI Grok search for X.com (Twitter) |
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
| WebSearch | ⚠️ Disabled | Uses Grok tokens - prefer SearXNG |

**Environment Variables Required:**
- `XAI_SEARCH_API_KEY` - For X.com API access (x-com skill) and Grok Search (recommended, separate from XAI_API_KEY)
- `XAI_API_KEY` - Fallback for X.com and Grok search if XAI_SEARCH_API_KEY not set
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
2. Cost-effective alternative to Grok for X.com searches
3. Uses `XAI_SEARCH_API_KEY` (separate from `XAI_API_KEY` to avoid token consumption)
4. Actions:
   - `searchPosts` - Search recent posts (last 7 days)
   - `searchPostsAll` - Search full archive (requires elevated access)
   - `searchUsers` - Search users by query
   - `getUserTimeline` - Get user's tweets
5. Supports pagination with `nextToken` for large result sets
6. Query operators: `from:user`, `#hashtag`, `has:images`, `lang:en`, etc.

## Grok Search

1. Alternative X.com (Twitter) search using x.AI's Grok API
2. Use when Grok's AI analysis capabilities are needed for X.com results
3. Uses `XAI_SEARCH_API_KEY` (separate from `XAI_API_KEY` to avoid token consumption)
4. For most X.com searches, prefer the `x-com` skill as it's more cost-effective

## Data Extraction and Deep Analysis
1. Agent should be able to extract data from any source, including web search results, pdf and other documents
2. Agent should be able to analyze fairly large quantity of data, and extract information from it
3. Agent should be able to select specific data models that fits the purpose of particular tasks eg. economic data analysis, technical data analysis, etc.
4. Agent should be able to cross-check that data with other data sources already stored in files or database

## Math operations
1. Agent should be able to use proper and accurate math operations to perform calculations and complex data analysis
2. Agent should be able to choose separate math models depending on task to perform better calculations

## Documents creation
1. Agent should be able to create documents in various formats, including pdf, docx
2. Documents should be possible to be created in various languages, including English, Polish
3. Documents should be possible to be created in varius structures eg. serving purposes of reports, articles, economic analysis, etc.

## Notifications
1. Agent should have possibility to send notifications to user when task is being performed or in other cases defined in task by user
2. Notifications can be: push notifications, Discord bot notifications, email notifications via amazon ses etc. Type of notification should be also be decided based on task definition provided by user

## Scheduling
1. Agent should be able to schedulre running particular actions based on task defined by user
2. Agent should be able to provide possibility to manage scheduled tasks: add, remove, list etc. Managment can be done via CLI
3. Schedule managemnt should be possible via CLI eg. after connecting to docker container via ssh, or via Discord bot

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