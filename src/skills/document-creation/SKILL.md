---
name: document-creation
description: Use this skill to create documents in various formats with customizable templates. Supports PDF and DOCX formats. Use this skill when: You need to generate a report, You need to create a formal document, You need to export content to PDF or DOCX, You need to create formatted documentation.
---

# Document Creation Skill

Use this skill to create documents in various formats with customizable templates. Supports PDF and DOCX formats.

## When to Use

Use this skill when:
- You need to generate a report
- You need to create a formal document
- You need to export content to PDF or DOCX
- You need to create formatted documentation
- You need to include tables in your documents

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill document-creation --params '{"format":"pdf","template":"report","language":"en","content":{"title":"My Report"}}'
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
  ],
  "tables": [
    {
      "headers": ["Column A", "Column B", "Column C"],
      "rows": [
        ["Row 1 Cell 1", "Row 1 Cell 2", "Row 1 Cell 3"],
        ["Row 2 Cell 1", "Row 2 Cell 2", "Row 2 Cell 3"]
      ],
      "widths": [100, "*", "*"]
    }
  ]
}
```

## Tables

Tables can be added to any section. Each table requires:
- `headers`: Array of column header strings
- `rows`: Array of row arrays, each containing cell values as strings
- `widths`: (optional) Array of column widths - can be numbers (fixed width) or "*" (auto-fill remaining space)

Example with tables:
```json
{
  "format": "pdf",
  "template": "report",
  "language": "en",
  "content": {
    "title": "Sales Report",
    "author": "Analytics Team",
    "sections": [
      {
        "heading": "Q1 Results",
        "content": "Quarterly sales breakdown by region:",
        "tables": [
          {
            "headers": ["Region", "Sales", "Growth"],
            "rows": [
              ["North", "$1.2M", "+15%"],
              ["South", "$980K", "+8%"],
              ["East", "$1.5M", "+22%"],
              ["West", "$1.1M", "+12%"]
            ],
            "widths": [100, 80, 80]
          }
        ]
      }
    ]
  }
}
```

### Text Overflow Handling

Table cells automatically handle long text:
- Text wraps to multiple lines within the cell
- Column widths can be fixed (number) or flexible ("*")
- Use "*" for columns that should fill remaining space

## Templates

### Report Template
Formal document with author and date metadata at the top. Suitable for business reports.

### Article Template
Clean layout with author attribution. Suitable for blog posts and articles.

### Analysis Template
Professional analysis format with metadata block. Suitable for research and analysis documents.

### Letter Template
Formal letter format with date and signature block. Suitable for correspondence.

### Custom Template
Minimal formatting for custom documents.

## Examples

### Create a Report
```bash
node /app/scripts/skill-runner.mjs --skill document-creation --params '{
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

### Create a Report with Tables
```bash
node /app/scripts/skill-runner.mjs --skill document-creation --params '{
  "format": "pdf",
  "template": "report",
  "language": "en",
  "content": {
    "title": "Data Analysis Report",
    "author": "Data Team",
    "sections": [
      {
        "heading": "Results",
        "content": "See table below:",
        "tables": [
          {
            "headers": ["Metric", "Value", "Change"],
            "rows": [
              ["Revenue", "$50,000", "+10%"],
              ["Users", "1,200", "+5%"],
              ["Conversion", "3.2%", "-0.5%"]
            ]
          }
        ]
      }
    ]
  }
}'
```

### Create an Article
```bash
node /app/scripts/skill-runner.mjs --skill document-creation --params '{
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

- PDF generation uses pdfmake library with automatic text wrapping and table support
- Table cells handle long text automatically with proper text wrapping
- DOCX generation is a placeholder and requires additional implementation
- Files are saved to /app/results by default