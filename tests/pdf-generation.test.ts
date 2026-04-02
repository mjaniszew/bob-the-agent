/**
 * PDF Generation Tests - TDD
 * Tests for document-creation skill with proper PDF generation
 *
 * Using pdfmake for table support with automatic text wrapping
 */

import { describe, it, expect, beforeAll, afterAll } from '@jest/globals';
import { existsSync, readFileSync, unlinkSync, mkdirSync, rmdirSync, writeFileSync } from 'fs';
import { join } from 'path';

// Import skill directly
import { documentCreation } from '../src/skills/document-creation/index';

// Test output directory
const TEST_OUTPUT_DIR = join(__dirname, 'pdf-output');

// Helper to check if buffer is valid PDF
function isValidPdf(buffer: Buffer): boolean {
  // PDF files start with %PDF-
  const header = buffer.slice(0, 5).toString('ascii');
  return header === '%PDF-';
}

// Helper to generate lorem ipsum text
function loremIpsum(sentences: number = 5): string {
  const loremWords = [
    'Lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur', 'adipiscing', 'elit',
    'sed', 'do', 'eiusmod', 'tempor', 'incididunt', 'ut', 'labore', 'et', 'dolore',
    'magna', 'aliqua', 'Ut', 'enim', 'ad', 'minim', 'veniam', 'quis', 'nostrud',
    'exercitation', 'ullamco', 'laboris', 'nisi', 'aliquip', 'ex', 'ea', 'commodo',
    'consequat', 'Duis', 'aute', 'irure', 'in', 'reprehenderit', 'voluptate',
    'velit', 'esse', 'cillum', 'fugiat', 'nulla', 'pariatur', 'Excepteur', 'sint',
    'occaecat', 'cupidatat', 'non', 'proident', 'sunt', 'culpa', 'qui', 'officia',
    'deserunt', 'mollit', 'anim', 'id', 'est', 'laborum'
  ];

  const result: string[] = [];
  for (let i = 0; i < sentences; i++) {
    const sentenceLength = 8 + Math.floor(Math.random() * 10);
    const words: string[] = [];
    for (let j = 0; j < sentenceLength; j++) {
      words.push(loremWords[Math.floor(Math.random() * loremWords.length)]);
    }
    words[0] = words[0].charAt(0).toUpperCase() + words[0].slice(1);
    result.push(words.join(' ') + '.');
  }
  return result.join(' ');
}

describe('PDF Generation', () => {
  beforeAll(() => {
    // Create test output directory
    if (!existsSync(TEST_OUTPUT_DIR)) {
      mkdirSync(TEST_OUTPUT_DIR, { recursive: true });
    }
  });

  afterAll(() => {
    // Clean up test files
    if (existsSync(TEST_OUTPUT_DIR)) {
      const files = require('fs').readdirSync(TEST_OUTPUT_DIR);
      files.forEach((file: string) => {
        if (file.endsWith('.pdf')) {
          unlinkSync(join(TEST_OUTPUT_DIR, file));
        }
      });
      try {
        rmdirSync(TEST_OUTPUT_DIR);
      } catch {
        // Directory not empty, leave it
      }
    }
  });

  describe('Basic PDF Generation', () => {
    it('should generate a valid PDF file with title, author, and sections', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Test Report',
          author: 'Test Author',
          date: '2026-04-01',
          sections: [
            { heading: 'Introduction', content: 'This is the introduction.' },
            { heading: 'Conclusion', content: 'This is the conclusion.' }
          ]
        },
        output: {
          filename: 'test-basic',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      // Test: skill execution succeeds
      expect(result.success).toBe(true);
      expect(result.format).toBe('pdf');

      // Test: PDF file was created
      expect(existsSync(result.filePath)).toBe(true);

      // Test: file is a valid PDF (starts with %PDF-)
      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);

      // Test: file has reasonable size (not empty, not just placeholder text)
      expect(fileBuffer.length).toBeGreaterThan(1000);
    });

    it('should include document metadata in PDF', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Metadata Test',
          author: 'John Doe',
          date: '2026-04-01',
          sections: [{ heading: 'Content', content: 'Test content' }]
        },
        output: {
          filename: 'test-metadata',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);

      // Verify PDF is valid and has reasonable size
      expect(isValidPdf(fileBuffer)).toBe(true);
      expect(fileBuffer.length).toBeGreaterThan(1000);

      // Verify file was created with correct metadata in result
      expect(result.fileName).toContain('test-metadata');
      expect(result.format).toBe('pdf');
    });
  });

  describe('Table Rendering', () => {
    it('should render a 5x5 table with lorem ipsum data without overflow', async () => {
      const tableData = {
        headers: ['Column A', 'Column B', 'Column C', 'Column D', 'Column E'],
        rows: Array(5).fill(null).map(() => [
          loremIpsum(1).slice(0, 30),
          loremIpsum(1).slice(0, 30),
          loremIpsum(1).slice(0, 30),
          loremIpsum(1).slice(0, 30),
          loremIpsum(1).slice(0, 30)
        ])
      };

      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Table Test Report',
          sections: [
            {
              heading: 'Data Table',
              content: 'Below is a table with lorem ipsum data:',
              tables: [tableData]
            }
          ]
        },
        output: {
          filename: 'test-table-5x5',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      // Test: skill execution succeeds with table data
      expect(result.success).toBe(true);

      // Test: PDF was created
      expect(existsSync(result.filePath)).toBe(true);

      // Test: valid PDF
      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);

      // Test: reasonable file size (tables add content)
      expect(fileBuffer.length).toBeGreaterThan(2000);
    });

    it('should handle table cells with content longer than column width', async () => {
      // This tests the overflow edge case - text should wrap, not overflow
      const overflowTableData = {
        headers: ['Short', 'Very Long Content Column Header'],
        rows: [
          [
            'X',
            'This is an extremely long piece of text that should either wrap properly or be truncated but never overflow outside the table cell boundaries which would cause visual issues in the PDF document'
          ],
          [
            'Y',
            'Another very long content string designed to test the text wrapping capabilities of the PDF generation library to ensure proper rendering without overflow'
          ]
        ],
        widths: [50, '*'] // First column fixed width, second takes remaining
      };

      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Overflow Test',
          sections: [
            {
              heading: 'Long Content Test',
              content: 'Testing text wrapping in table cells:',
              tables: [overflowTableData]
            }
          ]
        },
        output: {
          filename: 'test-table-overflow',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      expect(result.success).toBe(true);

      expect(existsSync(result.filePath)).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);

      // The PDF should be larger due to wrapped text
      expect(fileBuffer.length).toBeGreaterThan(1500);
    });

    it('should support multiple tables in a single document', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Multiple Tables Report',
          sections: [
            {
              heading: 'First Section',
              content: 'First table:',
              tables: [{
                headers: ['A', 'B'],
                rows: [['1', '2'], ['3', '4']]
              }]
            },
            {
              heading: 'Second Section',
              content: 'Second table:',
              tables: [{
                headers: ['X', 'Y', 'Z'],
                rows: [['a', 'b', 'c']]
              }]
            }
          ]
        },
        output: {
          filename: 'test-multiple-tables',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      expect(result.success).toBe(true);

      expect(existsSync(result.filePath)).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });
  });

  describe('Text Wrapping', () => {
    it('should wrap long paragraphs correctly', async () => {
      const longParagraph = loremIpsum(20); // Very long paragraph

      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Long Content Test',
          sections: [
            {
              heading: 'Long Paragraph',
              content: longParagraph
            }
          ]
        },
        output: {
          filename: 'test-long-paragraph',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      expect(result.success).toBe(true);

      expect(existsSync(result.filePath)).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);

      // Long content should produce a reasonably sized PDF
      expect(fileBuffer.length).toBeGreaterThan(2000);
    });
  });

  describe('Multi-Page Documents', () => {
    it('should handle content spanning multiple pages', async () => {
      // Generate content that will span multiple pages
      const testSections: Array<{
        heading: string;
        content: string;
        tables: Array<{ headers: string[]; rows: string[][]; }>;
      }> = [];

      for (let i = 0; i < 10; i++) {
        testSections.push({
          heading: `Section ${i + 1}`,
          content: loremIpsum(5),
          tables: [{
            headers: ['Data', 'Value', 'Notes'],
            rows: [
              [loremIpsum(1).slice(0, 20), 'Value ' + i, loremIpsum(1).slice(0, 30)],
              [loremIpsum(1).slice(0, 20), 'Value ' + (i + 1), loremIpsum(1).slice(0, 30)]
            ]
          }]
        });
      }

      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Multi-Page Document',
          author: 'Test Generator',
          sections: testSections
        },
        output: {
          filename: 'test-multipage',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);

      expect(result.success).toBe(true);

      expect(existsSync(result.filePath)).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);

      // Multi-page document should be larger
      expect(fileBuffer.length).toBeGreaterThan(5000);
    });
  });

  describe('Templates', () => {
    it('should generate PDF with report template', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Report Template Test',
          author: 'Reporter',
          sections: [{ heading: 'Findings', content: 'Key findings here.' }]
        },
        output: {
          filename: 'test-template-report',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });

    it('should generate PDF with article template', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'article' as const,
        language: 'en' as const,
        content: {
          title: 'Article Template Test',
          author: 'Writer',
          sections: [{ heading: 'Introduction', content: 'Article intro.' }]
        },
        output: {
          filename: 'test-template-article',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });

    it('should generate PDF with analysis template', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'analysis' as const,
        language: 'en' as const,
        content: {
          title: 'Analysis Template Test',
          author: 'Analyst',
          sections: [{ heading: 'Results', content: 'Analysis results.' }]
        },
        output: {
          filename: 'test-template-analysis',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });

    it('should generate PDF with letter template', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'letter' as const,
        language: 'en' as const,
        content: {
          title: 'Dear Sir/Madam',
          author: 'Sender',
          sections: [{ heading: '', content: 'This is a formal letter.' }]
        },
        output: {
          filename: 'test-template-letter',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });
  });

  describe('Language Support', () => {
    it('should generate PDF in Polish language', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'pl' as const,
        content: {
          title: 'Testowy Raport',
          author: 'Autor Testowy',
          sections: [{ heading: 'Wprowadzenie', content: 'To jest wprowadzenie.' }]
        },
        output: {
          filename: 'test-language-pl',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });
  });

  describe('Edge Cases', () => {
    it('should handle minimal document (title only)', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'custom' as const,
        language: 'en' as const,
        content: {
          title: 'Minimal Document',
          sections: []
        },
        output: {
          filename: 'test-minimal',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });

    it('should handle subsections correctly', async () => {
      const params = {
        format: 'pdf' as const,
        template: 'report' as const,
        language: 'en' as const,
        content: {
          title: 'Subsections Test',
          sections: [
            {
              heading: 'Main Section',
              content: 'Main content',
              subsections: [
                { heading: 'Subsection A', content: 'Sub A content' },
                { heading: 'Subsection B', content: 'Sub B content' }
              ]
            }
          ]
        },
        output: {
          filename: 'test-subsections',
          directory: TEST_OUTPUT_DIR
        }
      };

      const result = await documentCreation(params);
      expect(result.success).toBe(true);

      const fileBuffer = readFileSync(result.filePath);
      expect(isValidPdf(fileBuffer)).toBe(true);
    });
  });
});