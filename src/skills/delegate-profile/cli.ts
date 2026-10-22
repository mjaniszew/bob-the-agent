/**
 * CLI entry for the delegate-profile skill.
 * Usage: node /app/skills/delegate-profile/cli.ts '{"action":"send_task","target_agent_id":"researcher","goal":"..."}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { delegateProfile } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'delegate-profile', error: "Invalid JSON params. Usage: node /app/skills/delegate-profile/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = await delegateProfile(params as Record<string, unknown>);
    console.log(JSON.stringify({ success: true, skill: 'delegate-profile', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'delegate-profile', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
