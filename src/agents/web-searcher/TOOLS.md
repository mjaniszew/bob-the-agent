# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### SearXNG Web Search (searxng-search)
- Primary web search tool - FREE, no tokens consumed
- Privacy-respecting metasearch engine
- Use for ALL general web searches
- Runs as Docker container alongside agent

### X.com Search Skill (x-com)
- Direct X.com (Twitter) API access - cost-effective alternative to Grok
- Uses XAI_SEARCH_API_KEY (separate from XAI_API_KEY)
- Search recent posts (last 7 days) and full archive
- Search users by query
- Retrieve user timelines
- Supports pagination for large result sets
- Use for X.com/Twitter specific searches
- For general web search, use SearXNG instead

### Grok Search Skill
- Specialized for X.com (Twitter) search using x.AI Grok API
- Uses XAI_SEARCH_API_KEY (separate from main XAI_API_KEY)
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
- Upload files to AWS S3 cloud storage
- Generate presigned URLs for time-limited file access
- Use for persisting generated artifacts and reports
- Useful for sharing files with external users
- Requires AWS credentials configured in environment

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.