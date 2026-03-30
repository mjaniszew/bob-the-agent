# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### WebFetch (Built-in)
- Fetch and parse web content
- Returns HTML/markdown content from URLs
- Use for fetching pages to extract data from

### data-extraction Skill
- Specialized extraction capabilities
- Structured data parsing
- Check skill documentation for supported formats

### playwright Skill
- Headless browser for website interaction and automation
- Take screenshots for visual evidence
- Use for JavaScript-heavy sites that WebFetch cannot parse
- When specifically asked to use playwright
- Run `playwright-cli --help` for available commands

## Extraction Tips

- Identify the structure before extracting
- Use CSS selectors for HTML content
- Handle pagination for large datasets
- Include metadata: source URL, timestamp, extraction date
- Validate extracted data for completeness

## Common Formats

| Format | Best For |
|--------|----------|
| JSON | API responses, structured data |
| CSV | Tables, spreadsheets |
| Markdown | Documentation, text content |

---

Add whatever helps you do your job. This is your cheat sheet.