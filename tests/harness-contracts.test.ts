/**
 * Harness contract suite (R8) — drift guard for repo-layer wiring constants.
 *
 * Asserts the contracts that span files (compose.yaml <-> Dockerfile.hermes
 * <-> bootstrap.sh <-> harness.template.yaml <-> supervisord.conf) so silent
 * drift fails fast in CI instead of at 3am container boot.
 *
 * The coder-profile/Hermes-supervisor split lands at R12; this file only
 * carries NEW contract assertions at R8 plus the main-deletion guards that
 * had to move here once src/agents/main was deleted.
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

  test('compose wires the full HARNESS_* env contract (dashboard-era keys gone)', () => {
    const c = composeText();
    expect(c).toContain('HARNESS_API_KEY');
    expect(c).toContain('HARNESS_ROUTER_BASE_URL');
    expect(c).toContain('HARNESS_ROUTER_MODEL');
    expect(c).toContain('HARNESS_DATA_DIR');
    expect(c).toContain('HARNESS_WORKDIR');
    expect(c).toContain('HARNESS_DISCORD_HOME_CHANNEL');
    expect(c).not.toMatch(/HERMES_DASHBOARD/);
    expect(c).not.toMatch(/HERMES_API_TIMEOUT/);
  });

  test('compose healthcheck probes the harness gateway tcp port (never 8642)', () => {
    const c = composeText();
    expect(c).toMatch(/echo > \/dev\/tcp\/localhost\/8181/);
    expect(c).not.toMatch(/8642/);
  });

  test('Dockerfile ships the harness package + venv and keeps the opencode line', () => {
    const d = dockerfileText();
    expect(d).toMatch(/COPY \.\/src\/harness\/ \/app\/harness\//);
    expect(d).toContain('uv venv /opt/harness-venv');
    // The coding engine stays installed in the shared image (R12 split target).
    expect(d).toMatch(/npm install -g opencode-ai@1\.18\.32/);
    expect(d).not.toMatch(/^\s*ENTRYPOINT/im);
  });

  test('bootstrap execs supervisord as its final act (harness long-runner)', () => {
    const b = bootstrapText();
    expect(b).toMatch(/PROFILE_NAMES="researcher simple"/);
    expect(b).toMatch(/exec \/opt\/harness-venv\/bin\/supervisord -c \/app\/harness\/supervisord\.conf/);
    expect(b).not.toContain('exec hermes gateway run');
    // Per-profile Bot Chat provisioning survives the gateway->harness rework.
    expect(b).toContain('Bot Chat');
    // Harness state root + first-boot seeds live under the existing bind.
    expect(b).toContain('$HERMES_HOME/harness');
  });
});

describe('harness config + adapter contracts', () => {
  const harnessConfig = () => yaml.load(read('src/harness/harness.template.yaml')) as any;

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

  test('storage data_dir is the agent bind (no new volume)', () => {
    expect(harnessConfig().storage.data_dir).toBe('/opt/data/harness');
  });
});

describe('supervisord contract', () => {
  const confPath = path.join(ROOT, 'src/harness/supervisord.conf');

  test('supervisord.conf exists', () => {
    expect(fs.existsSync(confPath)).toBe(true);
  });

  test('runs the harness supervisor as the long-runner behind supervisorctl', () => {
    const c = read('src/harness/supervisord.conf');
    expect(c).toContain('[program:harness]');
    expect(c).toContain('python -m harness.supervisor');
    // opencode-serve exists but is NOT autostarted (on-demand coding engine).
    expect(c).toContain('autostart=false');
    expect(c).toContain('[unix_http_server]');
    expect(c).toContain('[supervisorctl]');
    expect(c).toMatch(/rpcinterface_factory/);
    // No user= key: bootstrap already runs as the `hermes` user (s6-setuidgid).
    expect(c).not.toMatch(/^user=/m);
    // supervisor's own validator accepts ONLY false/unexpected/true for
    // autorestart — never the systemd-ism "on-failure" (crashes supervisord
    // at boot). Guarded here so the value can never drift back.
    expect(c).toMatch(/^autorestart=unexpected$/m);
    expect(c).not.toMatch(/^autorestart=on-failure/m);
    // The harness long-runner must NOT be autostart=false; only opencode-serve is.
    const harnessBlock = c.split('[program:harness]')[1].split('[')[0];
    expect(harnessBlock).not.toContain('autostart=false');
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

describe('HARNESS_* env names (single source of truth)', () => {
  test('compose agent env exposes exactly the six HARNESS_ keys', () => {
    const compose = yaml.load(read('compose.yaml')) as any;
    const env = compose.services.agent.environment;
    const harnessKeys = Object.keys(env).filter((k) => k.startsWith('HARNESS_')).sort();
    expect(harnessKeys).toEqual([
      'HARNESS_API_KEY',
      'HARNESS_DATA_DIR',
      'HARNESS_DISCORD_HOME_CHANNEL',
      'HARNESS_ROUTER_BASE_URL',
      'HARNESS_ROUTER_MODEL',
      'HARNESS_WORKDIR',
    ]);
    // No extra HARNESS_* keys, and no dashboard-era HERMES_ keys in the agent env.
    const banned = Object.keys(env).filter(
      (k) => /^HARNESS_(?!API_KEY|DATA_DIR|DISCORD_HOME_CHANNEL|ROUTER_BASE_URL|ROUTER_MODEL|WORKDIR)/.test(k) ||
             /^HERMES_(DASHBOARD|API_TIMEOUT)/.test(k));
    expect(banned).toEqual([]);
  });

  // NOTE: .env.template (HARNESS_* defaults) is authored by the orchestrator
  // and lives beside the .env* files — AGENTS.md forbids reading .env* files,
  // so its contract is intentionally NOT asserted here.
});
