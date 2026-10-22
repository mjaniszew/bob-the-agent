/**
 * Skills invocation contract (task 21: kill the skill-runner).
 *
 * Groups 1-2 (plan section D): per-skill CLI entry + per-skill package.json
 * manifests, and the SKILL.md invocation contract (direct commands, no
 * skill-runner mentions). Groups 1-2 are written red-first: cli.ts and the
 * per-skill package.json files do not exist yet, and SKILL.mds still teach
 * the runner commands.
 */
import { readFileSync, existsSync, readdirSync } from 'fs';
import { join, relative } from 'path';

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

describe('runner absence contract', () => {
  const walk = (dir: string, skipDirs: string[] = []): string[] => {
    const out: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (skipDirs.includes(entry.name)) continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) out.push(...walk(full, skipDirs));
      else out.push(full);
    }
    return out;
  };

  it('src/scripts/skill-runner.mjs has been deleted', () => {
    expect(existsSync(join(REPO_ROOT, 'src', 'scripts', 'skill-runner.mjs'))).toBe(false);
  });

  it('no "skill-runner" string under src/, the root md docs, or docs/ (except plans/tasks)', () => {
    // src/ recursive walk, skipping any node_modules
    const srcFiles = walk(join(REPO_ROOT, 'src'), ['node_modules']);
    expect(srcFiles.length).toBeGreaterThan(0);
    for (const f of srcFiles) {
      expect(readFileSync(f, 'utf8')).not.toMatch(/skill-runner/);
    }
    // root md docs
    for (const md of ['README.md', 'CLAUDE.md', 'AGENTS.md']) {
      const p = join(REPO_ROOT, md);
      expect(existsSync(p)).toBe(true);
      expect(readFileSync(p, 'utf8')).not.toMatch(/skill-runner/);
    }
    // every file under docs/, except docs/plans/** and docs/tasks/**
    const docsFiles = walk(join(REPO_ROOT, 'docs'))
      .map((f) => relative(REPO_ROOT, f))
      .filter((rel) => !/^docs[/\\](plans|tasks)[/\\]/.test(rel));
    expect(docsFiles.length).toBeGreaterThan(0);
    for (const rel of docsFiles) {
      expect(readFileSync(join(REPO_ROOT, rel), 'utf8')).not.toMatch(/skill-runner/);
    }
  });

  it('root package.json has no build:skills script key', () => {
    const pkg = JSON.parse(readFileSync(join(REPO_ROOT, 'package.json'), 'utf8'));
    expect(Object.keys(pkg.scripts)).not.toContain('build:skills');
  });

  it('Dockerfile.hermes runs no npm build under /app/skills', () => {
    const df = readFileSync(join(REPO_ROOT, 'dockerfiles', 'Dockerfile.hermes'), 'utf8');
    expect(df).not.toContain('WORKDIR /app/skills');
    expect(df).not.toContain('npm run build');
  });
});

describe('Dockerfile per-skill install contract', () => {
  const dockerfile = () => readFileSync(join(REPO_ROOT, 'dockerfiles', 'Dockerfile.hermes'), 'utf8');

  it('installs aws-s3 runtime deps with a pruned npm ci', () => {
    expect(dockerfile()).toMatch(/\/app\/skills\/aws-s3[^\n]*npm ci --omit=dev/);
  });

  it('uses a BuildKit npm cache mount for fast rebuilds', () => {
    expect(dockerfile()).toContain('--mount=type=cache,target=/root/.npm');
  });

  it('has no tsc build layer for skills (no WORKDIR/npm build/dist refs)', () => {
    const df = dockerfile();
    expect(df).not.toContain('WORKDIR /app/skills');
    expect(df).not.toContain('npm run build');
    expect(df).not.toContain('/app/skills/dist');
  });

  it('makes per-skill scripts executable, keeping script perms and the js-yaml layer', () => {
    const df = dockerfile();
    expect(df).toContain('chmod +x /app/scripts/*');
    expect(df).toContain('chmod -R +x /app/skills/*/scripts/');
    expect(df).toContain('npm install js-yaml@4.1.1');
  });

  it('reverts to /app as the final WORKDIR', () => {
    const works = dockerfile().match(/^\s*WORKDIR\s+(.+)$/gm) ?? [];
    expect(works.length).toBeGreaterThan(0);
    expect(works[works.length - 1].trim()).toBe('WORKDIR /app');
  });
});
