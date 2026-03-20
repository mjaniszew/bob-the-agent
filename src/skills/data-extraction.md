# Data Extraction Skill

Extracts structured data from various file formats and web sources.

## Features
- PDF parsing with OCR support
- Web scraping with JavaScript rendering
- DOCX parsing
- Cross-checking against multiple sources
- Structured output in JSON/CSV formats

## Usage
```yaml
skill: data-extraction
parameters:
  source:
    type: "url" | "file"
    value: "https://example.com/document.pdf"
  output_format: "json" | "csv" | "markdown"
  extract:
    - tables
    - text
    - images
```

## Supported Formats
- PDF (with OCR)
- HTML/Web pages
- DOCX
- Images (PNG, JPG)
- CSV/Excel files

## Output
Returns extracted data in specified format with:
- Structured data objects
- Source attribution
- Confidence scores