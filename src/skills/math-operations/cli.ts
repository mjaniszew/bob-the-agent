/**
 * CLI entry for the math-operations skill.
 * Usage: node /app/skills/math-operations/cli.ts '{"operation":"calculate","expression":"2+2"}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { mathOperations } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'math-operations', error: "Invalid JSON params. Usage: node /app/skills/math-operations/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = mathOperations(params as any);
    console.log(JSON.stringify({ success: true, skill: 'math-operations', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'math-operations', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
