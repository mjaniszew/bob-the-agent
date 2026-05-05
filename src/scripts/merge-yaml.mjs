#!/usr/bin/env node
/**
 * YAML Deep Merge Utility
 *
 * Merges a template YAML with a partial YAML. Partial values override template.
 * Objects are deep-merged; arrays are replaced entirely (not concatenated).
 *
 * Usage: node merge-yaml.mjs <template_file> <partial_file> <output_file>
 *
 * Exit codes:
 *   0 - Success
 *   1 - Invalid arguments, file not found, or write error
 *   2 - YAML parsing error
 */

import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { dirname } from 'path';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const yaml = require('js-yaml');

function deepMerge(template, partial) {
  if (partial === null || partial === undefined) {
    return null;
  }
  if (typeof partial !== 'object' || Array.isArray(partial)) {
    return partial;
  }
  if (typeof template !== 'object' || template === null || Array.isArray(template)) {
    return partial;
  }

  const result = {};

  for (const key of Object.keys(template)) {
    if (key in partial) {
      const partialVal = partial[key];
      const templateVal = template[key];
      if (
        typeof partialVal === 'object' && partialVal !== null && !Array.isArray(partialVal) &&
        typeof templateVal === 'object' && templateVal !== null && !Array.isArray(templateVal)
      ) {
        result[key] = deepMerge(templateVal, partialVal);
      } else {
        result[key] = partialVal;
      }
    } else {
      result[key] = template[key];
    }
  }

  for (const key of Object.keys(partial)) {
    if (!(key in template)) {
      result[key] = partial[key];
    }
  }

  return result;
}

function main() {
  const args = process.argv.slice(2);

  if (args.length < 3) {
    console.error('Usage: node merge-yaml.mjs <template_file> <partial_file> <output_file>');
    console.error('');
    console.error('Arguments:');
    console.error('  template_file  Path to the base template YAML file');
    console.error('  partial_file   Path to the partial override YAML file');
    console.error('  output_file    Path for the merged output YAML file');
    process.exit(1);
  }

  const [templatePath, partialPath, outputPath] = args;

  // Read and parse template
  let templateContent;
  try {
    templateContent = readFileSync(templatePath, 'utf8');
  } catch (err) {
    console.error(`Error: Cannot read template file: ${templatePath}`);
    console.error(`  ${err.message}`);
    process.exit(1);
  }

  let templateObj;
  try {
    templateObj = yaml.load(templateContent, { filename: templatePath });
  } catch (err) {
    console.error(`Error: Invalid YAML in template: ${err.message}`);
    process.exit(2);
  }

  // Read and parse partial
  let partialContent;
  try {
    partialContent = readFileSync(partialPath, 'utf8');
  } catch (err) {
    console.error(`Error: Cannot read partial file: ${partialPath}`);
    console.error(`  ${err.message}`);
    process.exit(1);
  }

  let partialObj;
  try {
    partialObj = yaml.load(partialContent, { filename: partialPath }) || {};
  } catch (err) {
    console.error(`Error: Invalid YAML in partial: ${err.message}`);
    process.exit(2);
  }

  // Handle empty partial
  if (!partialObj || (typeof partialObj === 'object' && Object.keys(partialObj).length === 0)) {
    console.log('Partial is empty; using template as-is.');
    partialObj = {};
  }

  // Deep merge
  const merged = deepMerge(templateObj, partialObj);

  // Ensure output directory exists
  mkdirSync(dirname(outputPath), { recursive: true });

  // Write output
  const outputYaml = yaml.dump(merged, {
    lineWidth: -1,
    noRefs: true,
    quotingType: "'",
    forceQuotes: false,
  });

  try {
    writeFileSync(outputPath, outputYaml, 'utf8');
  } catch (err) {
    console.error(`Error: Cannot write output file: ${outputPath}`);
    console.error(`  ${err.message}`);
    process.exit(1);
  }

  console.log(`Merged config written to: ${outputPath}`);
}

main();