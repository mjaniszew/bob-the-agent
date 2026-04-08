# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### SearXNG Web Search (searxng-search)
- Primary web search tool - FREE, no tokens consumed
- Privacy-respecting metasearch engine
- Use for ALL web searches
- Runs as Docker container alongside agent

### X.com Search Skill (x-com)
- Direct X.com (Twitter) API access - cost-effective alternative to Grok
- Uses X_COM_API_TOKEN (separate from XAI_API_KEY)
- Search recent posts (last 7 days) and full archive
- Search users by query
- Retrieve user timelines
- Supports pagination for large result sets
- Use for X.com/Twitter specific searches
- For general web search, use SearXNG instead

### Grok Search Skill
- Specialized for X.com (Twitter) search using x.AI Grok API
- Uses X_COM_API_TOKEN (separate from main XAI_API_KEY)
- Real-time discussions and trends from Twitter
- Alternative to x-com when Grok's AI analysis is needed
- NOT for general web search - use SearXNG instead

### Playwright Skill
- Headless browser for website interaction and automation
- Take screenshots for visual evidence
- Use for JavaScript-heavy sites that WebFetch cannot parse
- When specifically asked to use playwright
- Run `playwright-cli --help` for available commands

### aws-s3 Skill
- Upload search results or extracted data to S3
- Generate shareable presigned URLs for findings
- Use for persisting research artifacts
- Requires AWS credentials configured in environment

## Search Tips

- Use SearXNG (searxng-search tool) for ALL general web searches
- Use X.com Search (x-com) for direct Twitter/X searches - more cost-effective
- Use Grok Search when you need Grok's AI analysis for X.com results
- Use `site:` operator to search within specific domains
- Use quotes for exact phrase matching
- Include year for time-sensitive queries
- Combine operators for precision: `site:docs.example.com "API reference" 2026`

---
Add whatever helps you do your job. This is your cheat sheet.