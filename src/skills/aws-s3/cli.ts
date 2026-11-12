/**
 * CLI entry for the aws-s3 skill.
 * Usage: node /app/skills/aws-s3/cli.ts '{"action":"upload","key":"path/to/file.txt","content":"..."}'
 * Prints the runner-compatible JSON envelope on stdout, exits 1 on failure.
 */
import { awsS3 } from './index.ts';

async function main(): Promise<void> {
  let params: unknown;
  try {
    params = JSON.parse(process.argv[2] ?? 'throw missing');
  } catch {
    console.error(JSON.stringify({ success: false, skill: 'aws-s3', error: "Invalid JSON params. Usage: node /app/skills/aws-s3/cli.ts '<json>'" }));
    process.exit(1);
  }
  try {
    const result = await awsS3(params as any);
    console.log(JSON.stringify({ success: true, skill: 'aws-s3', result }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, skill: 'aws-s3', error: err instanceof Error ? err.message : String(err) }));
    process.exit(1);
  }
}

void main();
