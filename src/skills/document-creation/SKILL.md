---
name: document_creation
description: Create documents in PDF or DOCX format with customizable templates
version: 1.0.0
trigger: "create document|generate pdf|create pdf|make document|write document"
tools: [shell, filesystem]
---

# Document Creation Skill

Use this skill to create documents in various formats with customizable templates. Supports PDF and DOCX formats.

## When to Use

Use this skill when:
- You need to generate a report
- You need to create a formal document
- You need to export content to PDF or DOCX
- You need to create formatted documentation

## Usage

Call the skill-runner with:

```bash
node /app/cli/skill-runner.js --skill document-creation --params '{"format":"pdf","template":"report","language":"en","content":{"title":"My Report"}}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| format | string | Yes | - | Output format: "pdf" or "docx" |
| template | string | No | "report" | Template: "report", "article", "analysis", "letter", "custom" |
| language | string | No | "en" | Language: "en" or "pl" |
| content | object | Yes | - | Document content structure |
| content.title | string | Yes | - | Document title |
| content.author | string | No | - | Author name |
| content.date | string | No | - | Date string |
| content.sections | array | Yes | - | Array of document sections |
| output.filename | string | No | auto | Output filename |
| output.directory | string | No | "/app/results" | Output directory |

## Content Structure

Each section in `content.sections`:
```json
{
  "heading": "Section Title",
  "content": "Section content text...",
  "subsections": [
    {
      "heading": "Subsection Title",
      "content": "Subsection content..."
    }
  ]
}
```

## Examples

### Create a Report
```bash
node /app/cli/skill-runner.js --skill document-creation --params '{
  "format": "pdf",
  "template": "report",
  "language": "en",
  "content": {
    "title": "Monthly Report",
    "author": "AI Agent",
    "sections": [
      {"heading": "Summary", "content": "This is the summary section."},
      {"heading": "Details", "content": "Detailed information here."}
    ]
  }
}'
```

### Create an Article
```bash
node /app/cli/skill-runner.js --skill document-creation --params '{
  "format": "docx",
  "template": "article",
  "content": {
    "title": "My Article",
    "author": "Author Name",
    "sections": [{"heading": "Introduction", "content": "Article introduction..."}]
  }
}'
```

## Output

Returns JSON with:
- `success`: Boolean indicating success
- `filePath`: Full path to created file
- `fileName`: File name
- `fileSize`: File size in bytes
- `format`: Output format
- `createdAt`: Creation timestamp

## Notes

- PDF generation requires pdfkit library
- DOCX generation requires docx library
- Files are saved to /app/results by default