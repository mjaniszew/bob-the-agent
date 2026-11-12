/**
 * CLI entry for the grok-search skill.
 * Usage: node /app/skills/grok-search/cli.ts '{"action":"searchPosts","query":"your search term"}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { grokSearch } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'grok-search', error: "Invalid JSON params. Usage: node /app/skills/grok-search/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = await grokSearch(params as any);
    console.log(JSON.stringify({ success: true, skill: 'grok-search', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'grok-search', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
