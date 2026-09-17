import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const composeText = () => read('compose.yaml');
const dockerfileText = () => read('dockerfiles/Dockerfile.hermes');
const dockerAvailable = process.env.DOCKER_TESTS === '1';

describe('Dockerfile.hermes', () => {
  test('pins the newest Hermes base image (>= v0.21.2 required for profile isolation fixes)', () => {
    expect(dockerfileText()).toMatch(/FROM nousresearch\/hermes-agent:v2026\.9\.14/);
  });

  test('does not override the image entrypoint (s6-overlay must stay PID 1)', () => {
    // \s* + case-insensitive: Docker allows leading whitespace and lowercase
    // keywords; still line-anchored so explanatory comments do not match.
    expect(dockerfileText()).not.toMatch(/^\s*ENTRYPOINT/im);
  });

  test('installs OpenCode CLI in the shared image (coder two-layer, single container)', () => {
    expect(dockerfileText()).toMatch(/npm install -g opencode-ai@latest/);
  });

  test('pre-creates workspace dirs chowned to hermes (CMD drops to hermes user)', () => {
    expect(dockerfileText()).toMatch(/mkdir -p \/app\/projects \/app\/results/);
    expect(dockerfileText()).toMatch(/chown hermes:hermes \/app\/projects \/app\/results/);
  });

  test('does not install the NATS python client', () => {
    expect(dockerfileText()).not.toMatch(/nats-py/);
  });

  test('no coder-specific entrypoint remains', () => {
    expect(dockerfileText()).not.toMatch(/coder-entrypoint\.sh/);
    expect(dockerfileText()).not.toMatch(/hermes-entrypoint\.sh/);
  });
});

describe('compose.yaml infrastructure services', () => {
  test('keeps ollama, searxng and valkey services unchanged', () => {
    const c = composeText();
    expect(c).toMatch(/ollama:\s*\n/);
    expect(c).toMatch(/searxng:\s*\n/);
    expect(c).toMatch(/valkey:\s*\n/);
  });
});

describe('compose.yaml single-agent architecture', () => {
  test('has exactly one agent service named "agent"', () => {
    const c = composeText();
    expect(c).toMatch(/^  agent:\s*$/m);
    expect(c).not.toMatch(/^  agent-main:\s*$/m);
    expect(c).not.toMatch(/^  researcher:\s*$/m);
    expect(c).not.toMatch(/^  simple-agent:\s*$/m);
    expect(c).not.toMatch(/^  coder:\s*$/m);
  });

  test('has no NATS service and no NATS url anywhere', () => {
    const c = composeText();
    expect(c).not.toMatch(/^  nats:\s*$/m);
    expect(c).not.toMatch(/NATS_URL/);
    expect(c).not.toMatch(/nats:/);
  });

  test('no per-service AGENT_NAME env pattern remains', () => {
    expect(composeText()).not.toMatch(/AGENT_NAME/);
  });

  test('agent service mounts single volume ./volumes/agent at /opt/data', () => {
    const c = composeText();
    expect(c).toMatch(/\.\/volumes\/agent:\/opt\/data/);
    expect(c).not.toMatch(/agent-main:\/opt\/data/);
    expect(c).not.toMatch(/agent-researcher:\/opt\/data/);
    expect(c).not.toMatch(/agent-simple:\/opt\/data/);
    expect(c).not.toMatch(/agent-coder:\/opt\/data/);
  });

  test('agent keeps results and projects mounts', () => {
    const c = composeText();
    expect(c).toMatch(/\.\/volumes\/results:\/app\/results/);
    expect(c).toMatch(/\.\/volumes\/projects:\/app\/projects/);
  });

  test('agent resource limits at least match old coder service (4G / 8G)', () => {
    const c = composeText();
    expect(c).toMatch(/memory: 4G/);
    expect(c).toMatch(/memory: 8G/);
  });

  test('agent runs bootstrap.sh as command and exposes 8642', () => {
    const c = composeText();
    expect(c).toMatch(/command: \["\/app\/scripts\/bootstrap\.sh"\]/);
    expect(c).toMatch(/8642:8642/);
  });

  test('agent no longer sets HERMES_YOLO_MODE (denylisted in v0.21)', () => {
    expect(composeText()).not.toMatch(/HERMES_YOLO_MODE/);
  });

  test('agent sets OPENCODE_CONFIG at compose level (docker exec can verify it)', () => {
    // Under /opt/data — the CMD is dropped to the `hermes` user by the image's
    // main-wrapper (s6-setuidgid), so /root/.config is unwritable.
    expect(composeText()).toMatch(/OPENCODE_CONFIG: \/opt\/data\/\.config\/opencode\/opencode\.jsonc/);
  });

  test('agent depends on ollama and searxng, not nats', () => {
    const c = composeText();
    expect(c).toMatch(/depends_on:\s*\n\s+ollama:\s*\n\s+condition: service_healthy\s*\n\s+searxng:/);
    expect(c).not.toMatch(/nats:/);
  });
});

// Runtime tests against the real stack — executed in Task 9 with DOCKER_TESTS=1.
describe('running stack (DOCKER_TESTS=1 only)', () => {
  const maybe = dockerAvailable ? describe : describe.skip;

  maybe('single-container runtime', () => {
    test('agent container becomes healthy', () => {
      for (let i = 0; i < 12; i++) {
        const s = require('child_process').execSync(
          'docker inspect --format "{{.State.Health.Status}}" bob-the-agent',
          { encoding: 'utf8' }).trim();
        if (s === 'healthy') return;
        require('child_process').execSync('sleep 5');
      }
      throw new Error('bob-the-agent never became healthy');
    }, 120_000);

    test('multiplexed gateway port 8642 reachable from host', () => {
      require('child_process').execSync('bash -c "echo > /dev/tcp/localhost/8642"');
    });

    test('profiles researcher/simple/coder provisioned in /opt/data', () => {
      const out = require('child_process').execSync(
        'docker exec bob-the-agent ls /opt/data/profiles', { encoding: 'utf8' });
      expect(out).toMatch(/researcher/);
      expect(out).toMatch(/simple/);
      expect(out).toMatch(/coder/);
    });

    test('in-process delegation smoke test (gateway multiplexes while -p simple runs)', () => {
      // Deterministic assertion for a persisted test: the one-shot run exits 0
      // and returns non-empty output. The strict "pong" word check stays in the
      // manual Task 9 Step 4 gate — LLM replies are inherently non-deterministic.
      const out = require('child_process').execSync(
        'docker exec bob-the-agent hermes -p simple chat --oneshot -q "Reply with the single word: pong"',
        { encoding: 'utf8', timeout: 300_000, stdio: 'pipe' });
      expect(out.trim().length).toBeGreaterThan(0);
    }, 300_000);
  });
});