---
name: data-extraction
description: Use this skill to extract structured data from web pages and documents. It can extract text, tables, and other content from URLs or files.
---

# Data Extraction Skill

Use this skill to extract structured data from web pages and documents. It can extract text, tables, and other content from URLs or files.

## When to Use

Use this skill when:
- You need to extract data from a web page
- You need to parse tables from HTML
- You need to extract text from documents
- You need structured output from unstructured sources

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill data-extraction --params '{"source":{"type":"url","value":"https://example.com"}}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| source | object | Yes | - | Source to extract from |
| source.type | string | Yes | - | "url" or "file" |
| source.value | string | Yes | - | URL or file path |
| outputFormat | string | No | "json" | Output format: "json", "csv", or "markdown" |
| extract | array | No | ["text"] | What to extract: "text", "tables", "images" |

## Examples

### Extract from URL
```bash
node /app/scripts/skill-runner.mjs --skill data-extraction --params '{"source":{"type":"url","value":"https://news.ycombinator.com"},"outputFormat":"markdown"}'
```

### Extract Tables Only
```bash
node /app/scripts/skill-runner.mjs --skill data-extraction --params '{"source":{"type":"url","value":"https://example.com/data"},"extract":["tables"],"outputFormat":"csv"}'
```

## Output

Returns JSON with:
- `source`: Source URL or file
- `format`: Output format used
- `data`: Extracted and formatted data
- `confidence`: Confidence score (0-1)
- `metadata`: Extraction metadata (wordCount, pageCount, etc.)

## Notes

- PDF extraction requires pdf-parse library (not included by default)
- DOCX extraction requires mammoth library (not included by default)
- URL extraction works with basic HTTP fetching