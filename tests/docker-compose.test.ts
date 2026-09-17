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

// The single-agent-service assertions are added in Task 4 (compose refactor),
// deliberately, so this file evolves TDD-style per task.