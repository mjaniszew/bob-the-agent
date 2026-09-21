import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const composeText = () => read('compose.yaml');
const dockerfileText = () => read('dockerfiles/Dockerfile.hermes');
const dockerAvailable = process.env.DOCKER_TESTS === '1';
// Tests that drive REAL model inference (minutes of runtime on CPU-only
// ollama) are additionally gated behind OLLAMA_LIVE_TESTS=1. User decision
// 2026-09-21: local CPU inference made this verification hang/timeout, so it
// is skipped in the standard suite; the user verifies inference separately
// (later, against cloud models).
const liveInference = process.env.OLLAMA_LIVE_TESTS === '1';
const testLive = liveInference ? test : test.skip;

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
  // Slice out just the `agent:` service block. Several asserted keys
  // (memory: 4G, 8642) also appear in infra services — whole-file regexes
  // stayed green even if the agent block were gutted (Task 4 quality review).
  const agentBlock = () => {
    const m = composeText().match(/^  agent:\n([\s\S]*?)(?=^  \w|^#|^\w)/m);
    if (!m) throw new Error('agent service block not found in compose.yaml');
    return m[1];
  };

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
    const b = agentBlock();
    expect(b).toMatch(/\.\/volumes\/agent:\/opt\/data/);
    const c = composeText();
    expect(c).not.toMatch(/agent-main:\/opt\/data/);
    expect(c).not.toMatch(/agent-researcher:\/opt\/data/);
    expect(c).not.toMatch(/agent-simple:\/opt\/data/);
    expect(c).not.toMatch(/agent-coder:\/opt\/data/);
  });

  test('agent keeps results and projects mounts', () => {
    const b = agentBlock();
    expect(b).toMatch(/\.\/volumes\/results:\/app\/results/);
    expect(b).toMatch(/\.\/volumes\/projects:\/app\/projects/);
  });

  test('agent resource limits: 2 CPUs reserved and capped, 4G reserved / 8G limit', () => {
    const b = agentBlock();
    expect(b).toMatch(/reservations:\s*\n\s+cpus: 2\s*\n\s+memory: 4G/);
    expect(b).toMatch(/limits:\s*\n\s+cpus: 2\s*\n\s+memory: 8G/);
  });

  test('agent runs bootstrap.sh as command and exposes 8642 on loopback only', () => {
    const b = agentBlock();
    expect(b).toMatch(/command: \["\/app\/scripts\/bootstrap\.sh"\]/);
    // Loopback-only (user decision, 2026-09-19): the single gateway fronts ALL
    // profiles behind approvals.mode: off and its auth is unverified; nothing
    // documented needs LAN exposure (health checks and tests use localhost,
    // Discord connects outbound). Remote access via SSH tunnel if ever needed.
    expect(b).toMatch(/127\.0\.0\.1:8642:8642/);
  });

  test('agent no longer sets HERMES_YOLO_MODE (denylisted in v0.21)', () => {
    expect(composeText()).not.toMatch(/HERMES_YOLO_MODE/);
  });

  test('agent sets OPENCODE_CONFIG at compose level (docker exec can verify it)', () => {
    // Under /opt/data — the CMD is dropped to the `hermes` user by the image's
    // main-wrapper (s6-setuidgid), so /root/.config is unwritable.
    expect(agentBlock()).toMatch(/OPENCODE_CONFIG: \/opt\/data\/\.config\/opencode\/opencode\.jsonc/);
  });

  test('agent depends on ollama and searxng, not nats', () => {
    expect(agentBlock()).toMatch(/depends_on:\s*\n\s+ollama:\s*\n\s+condition: service_healthy\s*\n\s+searxng:/);
    expect(composeText()).not.toMatch(/nats:/);
  });
});

// Runtime tests against the real stack — executed in Task 9 with DOCKER_TESTS=1.
describe('running stack (DOCKER_TESTS=1 only)', () => {
  const maybe = dockerAvailable ? describe : describe.skip;

  maybe('single-container runtime', () => {
    test('agent container becomes healthy', () => {
      // Poll ~130s: start_period alone is 120s (bootstrap provisions three
      // profiles before the gateway listens), so a 12x5s window spuriously
      // failed on any first boot in the 60-120s band (Task 4 quality review).
      for (let i = 0; i < 26; i++) {
        let s: string;
        try {
          s = require('child_process').execSync(
            'docker inspect --format "{{.State.Health.Status}}" bob-the-agent',
            { encoding: 'utf8', stdio: 'pipe' }).trim();
        } catch {
          throw new Error('bob-the-agent container not found — did the stack come up?');
        }
        if (s === 'healthy') return;
        require('child_process').execSync('sleep 5');
      }
      throw new Error('bob-the-agent never became healthy within ~130s (start_period is 120s)');
    }, 300_000);

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

    testLive('in-process delegation smoke test (gateway multiplexes while -p simple runs)', () => {
      // Deterministic assertion for a persisted test: the one-shot run exits 0
      // and returns non-empty output. The strict "pong" word check stays in the
      // manual Task 9 Step 4 gate — LLM replies are inherently non-deterministic.
      // 900s (Task 9): the default qwen3.5:2b-q4_K_M runs on the ollama
      // container with CPU-only inference — a single one-shot with thinking
      // took ~11 minutes on the Task 9 host, far above the previous 300s.
      const out = require('child_process').execSync(
        'docker exec bob-the-agent hermes -p simple chat --oneshot -q "Reply with the single word: pong"',
        { encoding: 'utf8', timeout: 900_000, stdio: 'pipe' });
      expect(out.trim().length).toBeGreaterThan(0);
    }, 930_000); // jest headroom above execSync's 900s so a hung docker exec
                 // surfaces execSync's clearer timeout error, not jest's
  });
});
