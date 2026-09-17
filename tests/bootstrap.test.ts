import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

const ROOT = path.join(__dirname, '..');
const scriptPath = path.join(ROOT, 'src/scripts/bootstrap.sh');
const script = () => fs.readFileSync(scriptPath, 'utf8');

// Shell-quote helper — repo checkout paths may contain spaces.
const q = (p: string): string => `"${p}"`;

afterAll(() => {
  // The merged-config test writes tmp/merged-main-config.yaml — don't leave
  // untracked noise behind in the working tree.
  fs.rmSync(path.join(ROOT, 'tmp', 'merged-main-config.yaml'), { force: true });
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

  test('provisions default profile config from template + main partial', () => {
    expect(script()).toMatch(/generate-config\.sh "\$TEMPLATE_FILE" "\$AGENTS_DIR" main/);
  });

  test('creates secondary profiles researcher, simple, coder under profiles/', () => {
    for (const name of ['researcher', 'simple', 'coder']) {
      expect(script()).toMatch(new RegExp(`profiles/\\$\\{?name\\}?`));
    }
    expect(script()).toMatch(/PROFILE_NAMES="researcher simple coder"/);
  });

  test('merged main config enables multiplex_profiles and bot_mode_protocol', () => {
    const out = path.join(ROOT, 'tmp', 'merged-main-config.yaml');
    require('child_process').execSync(
      `node ${q(path.join(ROOT, 'src/scripts/merge-yaml.mjs'))} ` +
      `${q(path.join(ROOT, 'src/config/hermes.template.yaml'))} ` +
      `${q(path.join(ROOT, 'src/agents/main/hermes.partial.yml'))} ${q(out)}`);
    const merged = fs.readFileSync(out, 'utf8');
    expect(merged).toMatch(/multiplex_profiles:\s*true/);
    expect(merged).toMatch(/bot_mode_protocol:\s*true/);
  });

  test('enables gateway multiplexing and bot mode protocol via main config', () => {
    // The keys live in src/agents/main/hermes.partial.yml (merged into config.yaml)
    const partial = fs.readFileSync(
      path.join(ROOT, 'src/agents/main/hermes.partial.yml'), 'utf8');
    expect(partial).toMatch(/multiplex_profiles:\s*true/);
    expect(partial).toMatch(/bot_mode_protocol:\s*true/);
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

  test('creates workspaces and execs the long-running gateway last', () => {
    // Line-anchored so explanatory comments don't satisfy the assertion.
    expect(script()).toMatch(/^mkdir -p \/app\/projects \/app\/results$/m);
    expect(script()).toMatch(/exec hermes gateway run/);
  });

  test('skill copies skip node_modules and dist (volume bloat guard)', () => {
    expect(script()).toMatch(/\[ "\$name" = "node_modules" \] && continue/);
    expect(script()).toMatch(/\[ "\$name" = "dist" \] && continue/);
  });

  test('old entrypoints are gone', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/hermes-entrypoint.sh'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/coder-entrypoint.sh'))).toBe(false);
  });
});
