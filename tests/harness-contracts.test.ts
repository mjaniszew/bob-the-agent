/**
 * bob-harness contract suite (task 23) — drift guard for repo-layer wiring
 * constants.
 *
 * Asserts the contracts that span files (compose.yaml <-> Dockerfile.hermes
 * <-> bootstrap.sh <-> bob-harness.template.yaml) so silent drift fails fast
 * in CI instead of at 3am container boot.
 *
 * Task 23 renamed the harness to bob-harness everywhere (Python package
 * bob_harness, container paths /app/bob-harness + /opt/data/bob-harness,
 * BOB_HARNESS_* env vars) and split the per-agent state into dedicated
 * folders: /opt/data/bob-harness, /opt/data/hermes, /opt/data/opencode.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

describe('container wiring contracts', () => {
  const composeText = () => read('compose.yaml');
  const dockerfileText = () => read('dockerfiles/Dockerfile.hermes');
  const bootstrapText = () => read('src/scripts/bootstrap.sh');

  test('compose exposes only the harness gateway on 8181 loopback (dashboard ports gone)', () => {
    const c = composeText();
    expect(c).toContain('127.0.0.1:8181:8181');
    expect(c).not.toMatch(/8642/);
    expect(c).not.toMatch(/9119/);
  });

  test('compose wires the full BOB_HARNESS_* env contract (dashboard-era keys gone)', () => {
    const c = composeText();
    expect(c).toContain('BOB_HARNESS_API_KEY');
    expect(c).toContain('BOB_HARNESS_ROUTER_BASE_URL');
    expect(c).toContain('BOB_HARNESS_ROUTER_MODEL');
    expect(c).toContain('BOB_HARNESS_DATA_DIR');
    expect(c).toContain('BOB_HARNESS_WORKDIR');
    expect(c).toContain('BOB_HARNESS_DISCORD_HOME_CHANNEL');
    // No legacy HARNESS_* names may remain anywhere (renamed at task 23).
    expect(c).not.toMatch(/[^_A-Z]HARNESS_/);
    expect(c).not.toMatch(/HERMES_DASHBOARD/);
    expect(c).not.toMatch(/HERMES_API_TIMEOUT/);
  });

  test('compose healthcheck probes the harness gateway tcp port (never 8642)', () => {
    const c = composeText();
    expect(c).toMatch(/echo > \/dev\/tcp\/localhost\/8181/);
    expect(c).not.toMatch(/8642/);
  });

  test('Dockerfile ships the bob-harness package + venv and keeps the opencode line', () => {
    const d = dockerfileText();
    expect(d).toMatch(/COPY \.\/src\/bob-harness\/ \/app\/bob-harness\//);
    expect(d).toContain('uv venv /opt/bob-harness-venv');
    // No pre-rename harness paths may remain in the build.
    expect(d).not.toMatch(/\/app\/harness|\/opt\/harness-venv|02-harness-only/);
    // The coding engine stays installed in the shared image.
    expect(d).toMatch(/npm install -g opencode-ai@1\.18\.32/);
    expect(d).not.toMatch(/^\s*ENTRYPOINT/im);
  });

  test('bootstrap execs the bob-harness long-runner as its final act', () => {
    const b = bootstrapText();
    expect(b).toMatch(/PROFILE_NAMES="researcher simple"/);
    expect(b).toMatch(/exec \/opt\/bob-harness-venv\/bin\/python -m bob_harness\.supervisor/);
    expect(b).not.toContain('exec hermes gateway run');
    expect(b).not.toMatch(/supervisord/);
    // Per-profile Bot Chat provisioning survives the gateway->harness rework.
    expect(b).toContain('Bot Chat');
    // Harness state root + first-boot seeds live in the dedicated folder.
    expect(b).toContain('/opt/data/bob-harness');
  });
});

describe('bob-harness config + adapter contracts', () => {
  const harnessConfig = () => yaml.load(read('src/bob-harness/bob-harness.template.yaml')) as any;

  test('gateway listens on 8181 (never 8080)', () => {
    expect(harnessConfig().server).toEqual({ host: '0.0.0.0', port: 8181 });
  });

  test('hermes adapter per-profile timeouts', () => {
    expect(harnessConfig().agents.hermes.profile_timeouts)
      .toEqual({ simple: 900, researcher: 3600 });
  });

  test('opencode adapter timeout covers coder-length tasks (7200)', () => {
    expect(harnessConfig().agents.opencode.timeout_s).toBe(7200);
  });

  test('timeout map (delegation parity table): simple 900 / researcher 3600 / coder 7200 via opencode', () => {
    // The parity table's coder row rides the opencode adapter, not a hermes
    // profile — coder stops being a hermes profile at R12.
    const agents = harnessConfig().agents;
    expect(agents.hermes.profile_timeouts).toEqual({ simple: 900, researcher: 3600 });
    expect(agents.opencode.timeout_s).toBe(7200);
  });

  test('router defaults bind the ollama-compatible endpoint and flash model', () => {
    const router = harnessConfig().router;
    expect(router.base_url).toBe('http://ollama:11434/v1');
    expect(router.model).toBe('glm-5.3-flash:cloud');
  });

  test('storage data_dir is the dedicated bob-harness folder on the agent bind', () => {
    expect(harnessConfig().storage.data_dir).toBe('/opt/data/bob-harness');
  });
});

describe('bob-harness long-runner contract (supervisord removed at task 22)', () => {
  test('the bootstrap final exec is the bob-harness supervisor (no supervisord layer)', () => {
    const b = bootstrapText();
    expect(b).toMatch(/^log "starting bob-harness \(python -m bob_harness\.supervisor\)"/m);
    expect(b).toMatch(/^export PYTHONPATH="\/app\/bob-harness"$/m);
    expect(b).toMatch(/^exec \/opt\/bob-harness-venv\/bin\/python -m bob_harness\.supervisor$/m);
  });

  test('pre-rename layouts are gone and cannot drift back', () => {
    // supervisord.conf was deleted at task 21/22; the old package dir must not
    // resurrect after the task 23 rename.
    expect(fs.existsSync(path.join(ROOT, 'src/harness/supervisord.conf'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/harness'))).toBe(false);
    expect(read('src/scripts/bootstrap.sh')).not.toMatch(/\/app\/harness|opt\/harness-venv|harness\.template\.yaml/);
  });
});

describe('main profile removed', () => {
  test('src/agents/main is gone (the harness long-runner replaces it)', () => {
    expect(fs.existsSync(path.join(ROOT, 'src/agents/main'))).toBe(false);
  });

  test('researcher and simple still ship SOUL.md + hermes.partial.yml', () => {
    for (const name of ['researcher', 'simple']) {
      expect(fs.existsSync(path.join(ROOT, `src/agents/${name}/SOUL.md`))).toBe(true);
      expect(fs.existsSync(path.join(ROOT, `src/agents/${name}/hermes.partial.yml`))).toBe(true);
    }
  });
});

describe('BOB_HARNESS_* env names + per-agent folder split (single source of truth)', () => {
  test('compose agent env exposes exactly the seven BOB_HARNESS_ keys', () => {
    const compose = yaml.load(read('compose.yaml')) as any;
    const env = compose.services.agent.environment;
    const harnessKeys = Object.keys(env).filter((k) => k.startsWith('BOB_HARNESS_')).sort();
    expect(harnessKeys).toEqual([
      'BOB_HARNESS_API_KEY',
      'BOB_HARNESS_CONFIG',
      'BOB_HARNESS_DATA_DIR',
      'BOB_HARNESS_DISCORD_HOME_CHANNEL',
      'BOB_HARNESS_ROUTER_BASE_URL',
      'BOB_HARNESS_ROUTER_MODEL',
      'BOB_HARNESS_WORKDIR',
    ]);
    // BOB_HARNESS_CONFIG must point at the config bootstrap.sh seeds onto the
    // volume (bootstrap.sh seeds /opt/data/bob-harness/config.yaml) — without
    // it load_config() finds no ./config.yaml in the CWD (/app) and silently
    // falls back to built-in defaults, discarding every YAML-only setting
    // (e.g. logfire.enabled).
    expect(env.BOB_HARNESS_CONFIG).toBe('/opt/data/bob-harness/config.yaml');
    // No legacy HARNESS_* names and no extra BOB_HARNESS_* keys beyond the
    // seven above, and no dashboard-era HERMES_ keys in the agent env.
    const banned = Object.keys(env).filter(
      (k) => /^(?!BOB_HARNESS_(API_KEY|CONFIG|DATA_DIR|DISCORD_HOME_CHANNEL|ROUTER_BASE_URL|ROUTER_MODEL|WORKDIR)$)BOB_HARNESS_/.test(k) ||
             (/(?<![A-Z_])HARNESS_/.test(k)) ||
             /^HERMES_(DASHBOARD|API_TIMEOUT)/.test(k));
    expect(banned).toEqual([]);
  });

  test('each agent owns a dedicated folder under /opt/data (task 23 folder split)', () => {
    const env = (yaml.load(read('compose.yaml')) as any).services.agent.environment;
    // Hermes state: profiles live under the dedicated hermes folder.
    expect(env.HERMES_HOME).toBe('/opt/data/hermes');
    // OpenCode: config + XDG state live under the dedicated opencode folder.
    expect(env.OPENCODE_CONFIG).toBe('/opt/data/opencode/opencode.jsonc');
    expect(env.XDG_CONFIG_HOME).toBe('/opt/data/opencode/config');
    expect(env.XDG_DATA_HOME).toBe('/opt/data/opencode/data');
    expect(env.XDG_STATE_HOME).toBe('/opt/data/opencode/state');
    expect(env.XDG_CACHE_HOME).toBe('/opt/data/opencode/cache');
    // Harness state pin (compose-level override of the config default).
    expect(env.BOB_HARNESS_DATA_DIR).toBe('/opt/data/bob-harness');
  });

  // NOTE: .env.template (HARNESS_* defaults) is authored by the orchestrator
  // and lives beside the .env* files — AGENTS.md forbids reading .env* files,
  // so its contract is intentionally NOT asserted here.
});
