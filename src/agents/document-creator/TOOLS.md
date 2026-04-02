# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Your Primary Tools

### document-creation Skill
- Create PDF, DOCX, and other document formats
- Support for multiple languages
- Template support for consistent formatting
- Check skill documentation for available options

### playwright Skill
- Use as a fallback for pdf creation, use browser to save contents as pdf
- Run `playwright-cli --help` for available commands

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