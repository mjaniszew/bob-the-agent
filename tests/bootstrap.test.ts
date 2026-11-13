import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

const ROOT = path.join(__dirname, '..');
const scriptPath = path.join(ROOT, 'src/scripts/bootstrap.sh');
const script = () => fs.readFileSync(scriptPath, 'utf8');

// Shell-quote helper — repo checkout paths may contain spaces.
const q = (p: string): string => `"${p}"`;

afterAll(() => {
  // The atomic-merge test writes tmp/*.yaml — don't leave untracked noise
  // behind in the working tree.
  for (const f of ['atomic-in-place.yaml', 'atomic-partial.yaml']) {
    fs.rmSync(path.join(ROOT, 'tmp', f), { force: true });
  }
});

describe('bootstrap.sh', () => {
  test('script exists and is executable', () => {
    expect(fs.existsSync(scriptPath)).toBe(true);
    const mode = fs.statSync(scriptPath).mode;
    expect(mode & 0o111).not.toBe(0); // executable bit
  });

  test('passes bash syntax check', () => {
    execSync(`bash -n ${q(scriptPath)}`);
  });

  test('no main-profile provisioning remains (harness replaces main)', () => {
    // R8: the main profile (and its partial) was deleted — the harness
    // long-runner owns orchestration now, bootstrap must not provision main.
    expect(script()).not.toMatch(/generate-config\.sh "\$TEMPLATE_FILE" "\$AGENTS_DIR" main/);
    expect(script()).not.toMatch(/merge-yaml\.mjs "\$HERMES_HOME\/config\.yaml"/);
    expect(script()).not.toMatch(/AGENTS_DIR\/main/);
  });

  test('reconciles pre-existing (migrated) configs with the current partial', () => {
    // A migrated config predating the single-container refactor must still
    // gain the current partial's keys — merge partial over existing, per
    // profile dir only (there is no $HERMES_HOME top-level config anymore).
    expect(script()).toMatch(/merge-yaml\.mjs "\$dir\/config\.yaml"/);
  });

  test('in-place reconcile takes a one-time .pre-reconcile.bak and writes atomically', () => {
    const src = script();
    // Backup is created only when absent, so reboots never clobber the
    // original pre-reconcile state with already-reconciled content.
    expect(src).toMatch(/\[ -f "\$1\.pre-reconcile\.bak" \] \|\| cp "\$1" "\$1\.pre-reconcile\.bak"/);
    // merge-yaml in-place (input == output, the reconcile shape) must leave a
    // complete file behind — atomic temp+rename, not truncate-then-write.
    const io = path.join(ROOT, 'tmp', 'atomic-in-place.yaml');
    const partial = path.join(ROOT, 'tmp', 'atomic-partial.yaml');
    fs.mkdirSync(path.join(ROOT, 'tmp'), { recursive: true });
    fs.writeFileSync(io, 'a: 1\nb: 2\n');
    fs.writeFileSync(partial, 'a: 2\n');
    execSync(`node ${q(path.join(ROOT, 'src/scripts/merge-yaml.mjs'))} ${q(io)} ${q(partial)} ${q(io)}`);
    const merged = fs.readFileSync(io, 'utf8');
    expect(merged).toMatch(/a: 2/);
    expect(merged).toMatch(/b: 2/);
    // No temp debris left in the output directory.
    expect(fs.readdirSync(path.join(ROOT, 'tmp')).filter(f => f.includes('merge-yaml.tmp'))).toEqual([]);
  });

  test('pulls the template default ollama model when missing', () => {
    const src = script();
    expect(src).toMatch(/api\/pull/);
    expect(src).toMatch(/api\/tags/);
    // Model pull must be settled before the harness execs (section 4 of 5).
    const pullAt = src.indexOf('api/pull');
    const execAt = src.indexOf('exec /opt/harness-venv/bin/supervisord -c /app/harness/supervisord.conf');
    expect(pullAt).toBeGreaterThanOrEqual(0);
    expect(execAt).toBeGreaterThan(pullAt);
  });

  test('creates secondary profiles researcher, simple under profiles/', () => {
    for (const name of ['researcher', 'simple']) {
      expect(script()).toMatch(new RegExp(`profiles/\\$\\{?name\\}?`));
    }
    expect(script()).toMatch(/PROFILE_NAMES="researcher simple"/);
    // coder rides out until the R12 two-layer split — not provisioned here.
    expect(script()).not.toMatch(/PROFILE_NAMES="researcher simple coder"/);
  });

  test('supervisord exec replaces the gateway exec', () => {
    const src = script();
    // The bootstrap's final act is the harness long-runner (supervisord),
    // not the hermes gateway.
    expect(src).toMatch(/exec \/opt\/harness-venv\/bin\/supervisord -c \/app\/harness\/supervisord\.conf/);
    expect(src).not.toContain('exec hermes gateway run');
    // The ollama model pull must be settled before the supervisor takes over.
    const pullAt = src.indexOf('api/pull');
    const execAt = src.indexOf('exec /opt/harness-venv/bin/supervisord -c /app/harness/supervisord.conf');
    expect(pullAt).toBeGreaterThanOrEqual(0);
    expect(execAt).toBeGreaterThan(pullAt);
  });

  test('seeds the harness state root under $HERMES_HOME/harness (idempotent, first boot)', () => {
    const src = script();
    expect(src).toMatch(/H="\$HERMES_HOME\/harness"/);
    expect(src).toMatch(/mkdir -p "\$H\/skills" "\$H\/memory" "\$H\/logs"/);
    // Config is template-copy-once (user edits on the volume are never overwritten).
    expect(src).toMatch(/\[ -f "\$H\/config\.yaml" \] \|\| cp \/app\/harness\/harness\.template\.yaml "\$H\/config\.yaml"/);
    // Skills seed mirrors copy_skills' rm-then-fresh so the harness skill
    // index cannot go stale across boots.
    const rmAt = src.indexOf('rm -rf "$H/skills/$n"');
    const cpAt = src.indexOf('cp -r "$d" "$H/skills/$n"');
    expect(rmAt).toBeGreaterThanOrEqual(0);
    expect(cpAt).toBeGreaterThan(rmAt);
    // Memory continuity: seed MEMORY.md pre-truncated to the 2200-char budget.
    expect(src).toMatch(/head -c 2200 "\$HERMES_HOME\/memories\/MEMORY\.md" > "\$H\/memory\/MEMORY\.md"/);
    // The seed block sits after the profile loop and before the OpenCode section.
    const loopAt = src.indexOf('for name in $PROFILE_NAMES');
    const seedAt = src.indexOf('H="$HERMES_HOME/harness"');
    const opencodeAt = src.indexOf('OPENCODE_TARGET=');
    expect(loopAt).toBeGreaterThanOrEqual(0);
    expect(opencodeAt).toBeGreaterThanOrEqual(0);
    expect(seedAt).toBeGreaterThan(loopAt);
    expect(seedAt).toBeLessThan(opencodeAt);
  });

  test('generates per-profile .env with skill variables and chmod 600', () => {
    expect(script()).toMatch(/chmod 600/);
    expect(script()).toMatch(/OLLAMA_BASE_URL/);
    expect(script()).toMatch(/SEARXNG_BASE_URL/);
  });

  test('generates OpenCode config unconditionally (env-substituted template)', () => {
    expect(script()).toMatch(/opencode\.template\.jsonc/);
    expect(script()).toMatch(/OLLAMA_BASE_URL_PLACEHOLDER/);
  });

  test('creates workspaces and execs the long-running harness last', () => {
    // Line-anchored so explanatory comments don't satisfy the assertion.
    expect(script()).toMatch(/^mkdir -p \/app\/projects \/app\/results$/m);
    expect(script()).toMatch(/exec \/opt\/harness-venv\/bin\/supervisord -c \/app\/harness\/supervisord\.conf/);
  });

  test('skill copies skip node_modules and dist (volume bloat guard)', () => {
    expect(script()).toMatch(/\[ "\$name" = "node_modules" \] && continue/);
    expect(script()).toMatch(/\[ "\$name" = "dist" \] && continue/);
  });

  test('skill copies skip .venv (python skills may carry per-skill venvs)', () => {
    expect(script()).toMatch(/\[ "\$name" = "\.venv" \] && continue/);
  });

  test('copy_skills wipes the target per-skill dir before copying (fresh copy each boot)', () => {
    const src = script();
    expect(src).toMatch(/rm -rf "\$1\/skills\/\$name"/);
    const rmAt = src.indexOf('rm -rf "$1/skills/$name"');
    const cpAt = src.indexOf('cp -r "$d" "$1/skills/"');
    expect(rmAt).toBeGreaterThanOrEqual(0);
    expect(cpAt).toBeGreaterThan(rmAt);
  });

  test('old entrypoints are gone', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/hermes-entrypoint.sh'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/coder-entrypoint.sh'))).toBe(false);
  });
});
