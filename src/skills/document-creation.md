# Document Creation Skill

Creates documents in various formats with customizable templates.

## Features
- PDF generation
- DOCX generation with formatting
- Multi-language support (English, Polish)
- Template-based generation
- Charts and images embedding

## Usage
```yaml
skill: document-creation
parameters:
  format: "pdf" | "docx"
  template: "report" | "article" | "analysis"
  language: "en" | "pl"
  content:
    title: "Document Title"
    sections:
      - heading: "Introduction"
        content: "..."
      - heading: "Analysis"
        content: "..."
```

## Templates
- **report**: Business report with sections
- **article**: Article/blog post format
- **analysis**: Economic/technical analysis
- **letter**: Formal correspondence
- **custom**: Custom template from file

## Output
Saves document to /app/results/ directory and returns:
- File path
- File size
- Preview link