/**
 * CLI entry for the data-extraction skill.
 * Usage: node /app/skills/data-extraction/cli.ts '{"source":{"type":"url","value":"https://example.com"}}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { dataExtraction } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'data-extraction', error: "Invalid JSON params. Usage: node /app/skills/data-extraction/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = await dataExtraction(params as any);
    console.log(JSON.stringify({ success: true, skill: 'data-extraction', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'data-extraction', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
