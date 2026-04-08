# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### playwright Skill
- Primary tool for document creation - use browser to save contents as PDF
- Create PDFs from web content or HTML
- Run `playwright-cli --help` for available commands
- Use for generating reports and formatted documents

### aws-s3 Skill
- Upload created documents to S3 for persistent storage
- Generate shareable presigned URLs for document access
- Use for distributing generated reports and files
- Requires AWS credentials configured in environment

## Document Tips

- Include metadata: author, date, version
- Use consistent formatting throughout
- Structure content with headers and sections
- Review for errors before finalizing

## Supported Formats

| Format | Extension | Best For |
|--------|-----------|----------|
| PDF | .pdf | Final documents, presentations |
| DOCX | .docx | Editable documents, reports |
| Markdown | .md | Technical documentation |
| Plain text | .txt | Simple notes, logs |

## Language Support

- Default language: English
- Specify language in document request
- Include character encoding for special characters

---

Add whatever helps you do your job. This is your cheat sheet.