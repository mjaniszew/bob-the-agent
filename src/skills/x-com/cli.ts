/**
 * CLI entry for the x-com skill.
 * Usage: node /app/skills/x-com/cli.ts '{"action":"searchPosts","query":"your search term"}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { xComSearch } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'x-com', error: "Invalid JSON params. Usage: node /app/skills/x-com/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = await xComSearch(params as any);
    console.log(JSON.stringify({ success: true, skill: 'x-com', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'x-com', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
