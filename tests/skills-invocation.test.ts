/**
 * Skills invocation contract (task 21: kill the skill-runner).
 *
 * Groups 1-2 (plan section D): per-skill CLI entry + per-skill package.json
 * manifests, and the SKILL.md invocation contract (direct commands, no
 * skill-runner mentions). Groups 1-2 are written red-first: cli.ts and the
 * per-skill package.json files do not exist yet, and SKILL.mds still teach
 * the runner commands.
 */
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

const REPO_ROOT = join(__dirname, '..');
const skillsDir = (...p: string[]) => join(REPO_ROOT, 'src', 'skills', ...p);

const TS_SKILLS = [
  'aws-s3',
  'data-extraction',
  'delegate-profile',
  'grok-search',
  'math-operations',
  'x-com'
] as const;

describe('per-skill CLI entry contract (TS skills)', () => {
  it.each(TS_SKILLS)('%s: cli.ts exists, parses argv JSON, imports the skill function', (skill) => {
    const cliPath = skillsDir(skill, 'cli.ts');
    expect(existsSync(cliPath)).toBe(true);
    const cli = readFileSync(cliPath, 'utf8');
    expect(cli).toContain('JSON.parse(process.argv');
    // imports the exported skill function via relative .ts import
    expect(cli).toMatch(/import\s+.*from\s+['"]\.\/index\.ts['"]/);
  });

  it.each(TS_SKILLS)('%s: package.json exists with type module', (skill) => {
    const pkgPath = skillsDir(skill, 'package.json');
    expect(existsSync(pkgPath)).toBe(true);
    const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
    expect(pkg.type).toBe('module');
  });

  it('aws-s3: package.json declares AWS SDK deps and package-lock.json exists', () => {
    const pkg = JSON.parse(readFileSync(skillsDir('aws-s3', 'package.json'), 'utf8'));
    expect(pkg.dependencies).toBeTruthy();
    expect(pkg.dependencies['@aws-sdk/client-s3']).toBeTruthy();
    expect(pkg.dependencies['@aws-sdk/s3-request-presigner']).toBeTruthy();
    expect(existsSync(skillsDir('aws-s3', 'package-lock.json'))).toBe(true);
  });
});

describe('SKILL.md invocation contract', () => {
  it.each([...TS_SKILLS])('%s: SKILL.md teaches the direct cli.ts command, no skill-runner', (skill) => {
    const md = readFileSync(skillsDir(skill, 'SKILL.md'), 'utf8');
    expect(md).toContain(`node /app/skills/${skill}/cli.ts`);
    expect(md).not.toContain('skill-runner');
  });

  it('searxng-web-search: SKILL.md teaches the direct python3 command, no skill-runner', () => {
    const md = readFileSync(skillsDir('searxng-web-search', 'SKILL.md'), 'utf8');
    expect(md).toContain('python3 /app/skills/searxng-web-search/scripts/searxng_search.py');
    expect(md).not.toContain('skill-runner');
    // the python entrypoint exists
    expect(existsSync(skillsDir('searxng-web-search', 'scripts', 'searxng_search.py'))).toBe(true);
  });
});
