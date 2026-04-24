#!/usr/bin/env node
/**
 * Skill Runner CLI
 *
 * Executes skills from the command line.
 * Usage: node skill-runner.mjs --skill <skill-name> --params '<json-params>'
 *
 * Example:
 *   node skill-runner.mjs --skill web-search --params '{"query":"test"}'
 */

import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Skills are compiled to /app/skills/dist/ in the container
const SKILLS_DIST = process.env.APP_SKILLS_DIST || '/app/skills/dist';

// Parse command line arguments
function parseArgs() {
  const args = process.argv.slice(2);
  const params = {
    skill: null,
    params: {}
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '--skill' || arg === '-s') {
      params.skill = args[++i];
    } else if (arg === '--params' || arg === '-p') {
      try {
        params.params = JSON.parse(args[++i]);
      } catch (e) {
        console.error('Error parsing params JSON:', e.message);
        process.exit(1);
      }
    } else if (arg === '--help' || arg === '-h') {
      console.log(`
Skill Runner CLI

Usage: node skill-runner.mjs --skill <skill-name> --params '<json-params>'

Options:
  --skill, -s <name>     Skill name to execute (required)
  --params, -p <json>    JSON parameters for the skill
  --help, -h             Show this help message

Available skills:
  - web-search          Search the web using DuckDuckGo, Google, or Bing
  - x-com               Search X.com using x.AI API
  - data-extraction     Extract data from URLs and files
  - math-operations     Perform calculations and statistical analysis
  - aws-s3             Upload files to S3 and generate presigned URLs
  - grok-search        Search X.com via xAI Grok x_search tool (fallback for x-com)

Examples:
  # Web search
  node skill-runner.mjs --skill web-search --params '{"query":"AI news"}'

  # Math calculation
  node skill-runner.mjs --skill math-operations --params '{"operation":"calculate","expression":"2+2"}'

  # x.com search
  node skill-runner.mjs --skill x-com --params '{"action": "searchPosts", "query": "breaking news", "maxResults": 20}'

  # S3 file upload
  node skill-runner.mjs --skill aws-s3 --params '{"action":"upload","key":"test/file.txt","content":"Hello World"}'

  # S3 presigned URL
  node skill-runner.mjs --skill aws-s3 --params '{"action":"getUrl","key":"test/file.txt"}'

  # Grok search (X.com fallback)
  node skill-runner.mjs --skill grok-search --params '{"action":"searchPosts","query":"xAI announcements"}'
`);
      process.exit(0);
    }
  }

  if (!params.skill) {
    console.error('Error: --skill parameter is required');
    console.error('Use --help for usage information');
    process.exit(1);
  }

  return params;
}

// Skill registry with lazy loading
const skillRegistry = {
  'web-search': () => import(`${SKILLS_DIST}/web-search/index.js`).then(m => m.default),
  'x-com': () => import(`${SKILLS_DIST}/x-com/index.js`).then(m => m.default),
  'data-extraction': () => import(`${SKILLS_DIST}/data-extraction/index.js`).then(m => m.default),
  'math-operations': () => import(`${SKILLS_DIST}/math-operations/index.js`).then(m => m.default),
  'aws-s3': () => import(`${SKILLS_DIST}/aws-s3/index.js`).then(m => m.default),
  'grok-search': () => import(`${SKILLS_DIST}/grok-search/index.js`).then(m => m.default)
};

// Validate skill parameters against registry metadata
function validateParams(skillName, params) {
  // Import skill registry metadata
  return import(`${SKILLS_DIST}/index.js`).then(m => {
    const registry = m.skillRegistry;
    const skillMeta = registry[skillName];

    if (!skillMeta) {
      return { valid: false, error: `Unknown skill: ${skillName}` };
    }

    // Check required parameters
    for (const [paramName, paramDef] of Object.entries(skillMeta.params)) {
      if (paramDef.required && params[paramName] === undefined) {
        return { valid: false, error: `Missing required parameter: ${paramName}` };
      }
    }

    return { valid: true };
  });
}

// Main execution
async function main() {
  const { skill, params } = parseArgs();

  // Check if skill exists
  if (!skillRegistry[skill]) {
    const result = {
      success: false,
      error: `Unknown skill: ${skill}`,
      availableSkills: Object.keys(skillRegistry)
    };
    console.log(JSON.stringify(result, null, 2));
    process.exit(1);
  }

  try {
    // Validate parameters
    const validation = await validateParams(skill, params);
    if (!validation.valid) {
      const result = {
        success: false,
        error: validation.error,
        skill: skill
      };
      console.log(JSON.stringify(result, null, 2));
      process.exit(1);
    }

    // Load and execute skill
    const skillFn = await skillRegistry[skill]();
    const result = await skillFn(params);

    // Output result as JSON
    console.log(JSON.stringify({
      success: true,
      skill: skill,
      timestamp: new Date().toISOString(),
      result: result
    }, null, 2));

  } catch (error) {
    const result = {
      success: false,
      skill: skill,
      error: error.message,
      stack: error.stack
    };
    console.log(JSON.stringify(result, null, 2));
    process.exit(1);
  }
}

main();