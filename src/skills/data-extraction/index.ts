/**
 * Data Extraction Skill Implementation
 * Extracts structured data from various file formats and web sources
 */

interface ExtractionResult {
  source: string;
  format: string;
  data: any;
  confidence: number;
  metadata: {
    extractedAt: string;
    pageCount?: number;
    wordCount?: number;
  };
}

interface DataExtractionParams {
  source: {
    type: 'url' | 'file';
    value: string;
  };
  outputFormat?: 'json' | 'csv' | 'markdown';
  extract?: ('tables' | 'text' | 'images')[];
}

// Extract from URL (web scraping)
async function extractFromUrl(url: string): Promise<{ text: string; tables: any[] }> {
  const response = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; MiniAgent/1.0)'
    }
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch URL: ${response.status}`);
  }

  const html = await response.text();

  // Basic HTML parsing (in production, use a proper parser)
  const text = html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();

  // Extract tables (basic implementation)
  const tables: any[] = [];
  const tableRegex = /<table[^>]*>([\s\S]*?)<\/table>/gi;
  let tableMatch;

  while ((tableMatch = tableRegex.exec(html)) !== null) {
    const tableHtml = tableMatch[1];
    const rows: string[][] = [];

    const rowRegex = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
    let rowMatch;

    while ((rowMatch = rowRegex.exec(tableHtml)) !== null) {
      const cells: string[] = [];
      const cellRegex = /<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi;
      let cellMatch;

      while ((cellMatch = cellRegex.exec(rowMatch[1])) !== null) {
        cells.push(cellMatch[1].trim());
      }

      if (cells.length > 0) {
        rows.push(cells);
      }
    }

    if (rows.length > 0) {
      tables.push(rows);
    }
  }

  return { text, tables };
}

// Extract from PDF (requires pdf-parse library)
async function extractFromPdf(buffer: Buffer): Promise<{ text: string; pageCount: number }> {
  // In production, use pdf-parse library
  // For now, return placeholder
  console.log('PDF extraction requires pdf-parse library');

  return {
    text: '[PDF content extraction requires pdf-parse library to be installed]',
    pageCount: 1
  };
}

// Extract from DOCX (requires mammoth library)
async function extractFromDocx(buffer: Buffer): Promise<{ text: string; paragraphs: number }> {
  // In production, use mammoth library
  // For now, return placeholder
  console.log('DOCX extraction requires mammoth library');

  return {
    text: '[DOCX content extraction requires mammoth library to be installed]',
    paragraphs: 1
  };
}

// Format output
function formatOutput(
  data: any,
  format: 'json' | 'csv' | 'markdown'
): string {
  switch (format) {
    case 'json':
      return JSON.stringify(data, null, 2);

    case 'csv':
      if (Array.isArray(data)) {
        if (data.length === 0) return '';
        const headers = Object.keys(data[0]);
        const rows = data.map(row => headers.map(h => row[h]).join(','));
        return [headers.join(','), ...rows].join('\n');
      }
      return JSON.stringify(data);

    case 'markdown':
      if (typeof data === 'string') {
        return data;
      }
      if (Array.isArray(data)) {
        return data.map(item => `- ${JSON.stringify(item)}`).join('\n');
      }
      return '```json\n' + JSON.stringify(data, null, 2) + '\n```';

    default:
      return JSON.stringify(data, null, 2);
  }
}

// Main extraction function
export async function dataExtraction(params: DataExtractionParams): Promise<ExtractionResult> {
  const {
    source,
    outputFormat = 'json',
    extract = ['text']
  } = params;

  const startTime = Date.now();
  let extractedData: any = {};
  let metadata: any = {
    extractedAt: new Date().toISOString()
  };

  try {
    if (source.type === 'url') {
      const { text, tables } = await extractFromUrl(source.value);

      if (extract.includes('text')) {
        extractedData.text = text;
        metadata.wordCount = text.split(/\s+/).length;
      }

      if (extract.includes('tables')) {
        extractedData.tables = tables;
      }

      metadata.source = source.value;
      metadata.format = 'html';

    } else if (source.type === 'file') {
      // Determine file type from extension
      const ext = source.value.split('.').pop()?.toLowerCase();

      if (ext === 'pdf') {
        const result = await extractFromPdf(Buffer.from('')); // Placeholder
        extractedData.text = result.text;
        metadata.pageCount = result.pageCount;
        metadata.format = 'pdf';

      } else if (ext === 'docx') {
        const result = await extractFromDocx(Buffer.from('')); // Placeholder
        extractedData.text = result.text;
        metadata.paragraphs = result.paragraphs;
        metadata.format = 'docx';

      } else {
        throw new Error(`Unsupported file format: ${ext}`);
      }
    }

    const formattedOutput = formatOutput(extractedData, outputFormat);

    return {
      source: source.value,
      format: outputFormat,
      data: formattedOutput,
      confidence: 0.85, // Placeholder confidence score
      metadata
    };

  } catch (error) {
    throw new Error(`Data extraction failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

export default dataExtraction;