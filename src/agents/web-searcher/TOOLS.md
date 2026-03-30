# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### WebSearch (Built-in)
- General web search capability
- Returns search results with snippets and URLs
- Use for current events, documentation, general queries

### web-search Skill
- Structured search with specific requirements
- May have additional filtering options
- Check skill documentation for capabilities

### grok-search Skill
- Specialized for X/Twitter content, and web search using Grok
- Real-time discussions and trends
- Use when you need social media context

### playwright Skill
- Headless browser for website interaction and automation
- Take screenshots for visual evidence
- Use for JavaScript-heavy sites that WebFetch cannot parse
- When specifically asked to use playwright
- Run `playwright-cli --help` for available commands

## Search Tips

- Use `site:` operator to search within specific domains
- Use quotes for exact phrase matching
- Include year for time-sensitive queries
- Combine operators for precision: `site:docs.example.com "API reference" 2026`

---

Add whatever helps you do your job. This is your cheat sheet.