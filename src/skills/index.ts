/**
 * Skills Registry
 * Exports all available skills for the agent
 */

export { webSearch } from './web-search/index.js';
export { dataExtraction } from './data-extraction/index.js';
export { mathOperations } from './math-operations/index.js';
export { documentCreation } from './document-creation/index.js';
export { notifications } from './notifications/index.js';
export { scheduling, listSchedules, deleteSchedule } from './scheduling/index.js';
export { grokSearch } from './grok-search/index.js';

// Skill metadata for registration
export const skillRegistry = {
  'web-search': {
    name: 'Web Search',
    description: 'Search the web using multiple sources and aggregate results',
    version: '1.0.0',
    params: {
      query: { type: 'string', required: true, description: 'Search query' },
      sources: { type: 'array', default: ['duckduckgo'], description: 'Search sources to use' },
      deep: { type: 'boolean', default: false, description: 'Enable deep search for full content' },
      maxResults: { type: 'number', default: 10, description: 'Maximum results per source' }
    }
  },
  'data-extraction': {
    name: 'Data Extraction',
    description: 'Extract structured data from various file formats and web sources',
    version: '1.0.0',
    params: {
      source: { type: 'object', required: true, description: 'Source to extract from (url or file)' },
      outputFormat: { type: 'string', enum: ['json', 'csv', 'markdown'], default: 'json' },
      extract: { type: 'array', default: ['text'], description: 'What to extract (tables, text, images)' }
    }
  },
  'math-operations': {
    name: 'Math Operations',
    description: 'Perform accurate mathematical calculations and statistical analysis',
    version: '1.0.0',
    params: {
      operation: { type: 'string', enum: ['calculate', 'statistics', 'convert'], required: true },
      expression: { type: 'string', description: 'Mathematical expression to evaluate' },
      data: { type: 'array', description: 'Data array for statistics' },
      precision: { type: 'number', default: 4, description: 'Decimal precision' }
    }
  },
  'document-creation': {
    name: 'Document Creation',
    description: 'Create documents in various formats with customizable templates',
    version: '1.0.0',
    params: {
      format: { type: 'string', enum: ['pdf', 'docx'], required: true },
      template: { type: 'string', enum: ['report', 'article', 'analysis', 'letter', 'custom'], default: 'report' },
      language: { type: 'string', enum: ['en', 'pl'], default: 'en' },
      content: { type: 'object', required: true, description: 'Document content structure' }
    }
  },
  'notifications': {
    name: 'Notifications',
    description: 'Send notifications through various channels',
    version: '1.0.0',
    params: {
      channels: { type: 'array', required: true, description: 'Notification channels' },
      message: { type: 'object', required: true, description: 'Message content' },
      retryCount: { type: 'number', default: 3, description: 'Retry attempts' }
    }
  },
  'scheduling': {
    name: 'Scheduling',
    description: 'Manage scheduled task execution',
    version: '1.0.0',
    params: {
      mode: { type: 'string', enum: ['cron', 'interval', 'once'], required: true },
      schedule: { type: 'object', required: true, description: 'Schedule configuration' },
      task: { type: 'object', required: true, description: 'Task to execute' }
    }
  },
  'grok-search': {
    name: 'Grok Search',
    description: 'Search web and X.com (Twitter) using x.AI Grok API',
    version: '1.0.0',
    params: {
      query: { type: 'string', required: true, description: 'Search query' },
      mode: { type: 'string', enum: ['web', 'x', 'both'], default: 'web', description: 'Search mode: web, x (Twitter), or both' },
      allowedDomains: { type: 'array', description: 'Domains to include in results' },
      excludedDomains: { type: 'array', description: 'Domains to exclude from results' },
      allowedXHandles: { type: 'array', description: 'X.com handles to include' },
      excludedXHandles: { type: 'array', description: 'X.com handles to exclude' },
      fromDate: { type: 'string', description: 'Start date for X.com results (YYYY-MM-DD)' },
      toDate: { type: 'string', description: 'End date for X.com results (YYYY-MM-DD)' },
      enableImageUnderstanding: { type: 'boolean', default: false, description: 'Enable image understanding' },
      enableVideoUnderstanding: { type: 'boolean', default: false, description: 'Enable video understanding for X.com' },
      maxResults: { type: 'number', default: 10, description: 'Maximum results per source' }
    }
  }
};

/**
 * Execute a skill by name
 */
export async function executeSkill(skillName: string, params: Record<string, any>): Promise<any> {
  const skill = skillRegistry[skillName as keyof typeof skillRegistry];

  if (!skill) {
    throw new Error(`Unknown skill: ${skillName}`);
  }

  // Validate required parameters
  for (const [paramName, paramDef] of Object.entries(skill.params)) {
    if ((paramDef as any).required && params[paramName] === undefined) {
      throw new Error(`Missing required parameter: ${paramName}`);
    }
  }

  // Execute the skill
  switch (skillName) {
    case 'web-search': {
      const { webSearch } = await import('./web-search/index.js');
      return webSearch(params as any);
    }
    case 'data-extraction': {
      const { dataExtraction } = await import('./data-extraction/index.js');
      return dataExtraction(params as any);
    }
    case 'math-operations': {
      const { mathOperations } = await import('./math-operations/index.js');
      return mathOperations(params as any);
    }
    case 'document-creation': {
      const { documentCreation } = await import('./document-creation/index.js');
      return documentCreation(params as any);
    }
    case 'notifications': {
      const { notifications } = await import('./notifications/index.js');
      return notifications(params as any);
    }
    case 'scheduling': {
      const { scheduling } = await import('./scheduling/index.js');
      return scheduling(params as any);
    }
    case 'grok-search': {
      const { grokSearch } = await import('./grok-search/index.js');
      return grokSearch(params as any);
    }
    default:
      throw new Error(`Skill not implemented: ${skillName}`);
  }
}

export default executeSkill;