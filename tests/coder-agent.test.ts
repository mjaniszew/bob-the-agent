/**
 * Coder Agent Integration Tests
 *
 * Tests for the coder profile configuration (single-container Hermes
 * multiplexing), Docker compose setup, and OpenCode template configuration.
 *
 * Run with: npm test -- --testPathPattern=coder-agent
 * Docker tests: DOCKER_TESTS=1 npm test -- --testPathPattern=coder-agent
 */

import { describe, it, expect } from '@jest/globals';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

const PROJECT_ROOT = path.resolve(__dirname, '..');

// ============================================================
// Coder Agent SOUL.md Tests
// ============================================================

describe('Coder Agent SOUL.md', () => {
  const soulPath = path.join(PROJECT_ROOT, 'src/agents/coder/SOUL.md');

  it('should exist', () => {
    expect(fs.existsSync(soulPath)).toBe(true);
  });

  it('should contain identity section', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('Name:** Coder');
    expect(content).toContain('Software Engineering Specialist Agent');
  });

  it('should reference delegate-profile skill', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('delegate-profile');
  });

  it('should reference opencode skill', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('opencode');
  });

  it('should mention supervision protocol', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('Supervision Protocol');
  });

  it('should mention one-shot and interactive modes for OpenCode', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('opencode run');
    expect(content).toContain('background=true');
    expect(content).toContain('pty=true');
  });

  it('should mention delegating to simple agent', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('simple');
  });

  it('should mention saving results to /app/results/', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('/app/results/');
  });
});

// ============================================================
// Coder Agent Hermes Partial Config Tests
// ============================================================

describe('Coder Agent hermes.partial.yml', () => {
  const configPath = path.join(PROJECT_ROOT, 'src/agents/coder/hermes.partial.yml');

  it('should exist', () => {
    expect(fs.existsSync(configPath)).toBe(true);
  });

  it('should configure glm-5.1:cloud model', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.model.default).toBe('glm-5.1:cloud');
  });

  it('should configure Ollama provider pointing to ollama:11434', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.model.base_url).toBe('http://ollama:11434/v1');
  });

  it('should have higher max_turns for coding tasks', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.agent.max_turns).toBeGreaterThanOrEqual(120);
  });

  it('should have higher gateway_timeout for long-running coding tasks', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.agent.gateway_timeout).toBeGreaterThanOrEqual(7200);
  });

  it('should include hermes-cli toolset', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.toolsets).toContain('hermes-cli');
  });

  it('should include browser toolset', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.toolsets).toContain('browser');
  });

  it('should have custom provider for glm-5.1:cloud', () => {
    const content = fs.readFileSync(configPath, 'utf-8');
    const config = yaml.load(content) as any;
    expect(config.custom_providers).toBeDefined();
    expect(config.custom_providers[0].name).toContain('glm-5.1:cloud');
  });
});

// ============================================================
// OpenCode Template Config Tests
// ============================================================

describe('OpenCode Template Config', () => {
  const templatePath = path.join(PROJECT_ROOT, 'src/config/opencode.template.jsonc');

  it('should exist', () => {
    expect(fs.existsSync(templatePath)).toBe(true);
  });

  it('should be valid JSON', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    expect(() => JSON.parse(content)).not.toThrow();
  });

  it('should contain OLLAMA_BASE_URL placeholder', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    expect(content).toContain('OLLAMA_BASE_URL_PLACEHOLDER');
  });

  it('should configure ollama provider with openai-compatible npm package', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.provider.ollama).toBeDefined();
    expect(config.provider.ollama.npm).toBe('@ai-sdk/openai-compatible');
  });

  it('should configure glm-5.1:cloud model', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.provider.ollama.models['glm-5.1:cloud']).toBeDefined();
  });

  it('should use "agent" (not "agents") as the top-level agent key', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.agent).toBeDefined();
    expect(config.agents).toBeUndefined();
  });

  it('should configure the build agent with glm-5.1:cloud model', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.agent.build.model).toContain('glm-5.1:cloud');
  });
});

// ============================================================
// OpenCode Template Env Substitution Tests
// ============================================================

describe('OpenCode Template Env Substitution', () => {
  const templatePath = path.join(PROJECT_ROOT, 'src/config/opencode.template.jsonc');

  it('should produce valid JSON after placeholder substitution', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const substituted = content
      .replace(/OLLAMA_BASE_URL_PLACEHOLDER/g, 'http://ollama:11434');
    expect(() => JSON.parse(substituted)).not.toThrow();
  });

  it('should have correct Ollama URL after substitution', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const substituted = content
      .replace(/OLLAMA_BASE_URL_PLACEHOLDER/g, 'http://ollama:11434');
    const config = JSON.parse(substituted);
    expect(config.provider.ollama.options.baseURL).toBe('http://ollama:11434/v1');
  });
});

// ============================================================
// Bootstrap (Coder) Tests
// ============================================================

describe('Bootstrap (Coder)', () => {
  const bootstrapPath = path.join(PROJECT_ROOT, 'src/scripts/bootstrap.sh');
  const dockerfilePath = path.join(PROJECT_ROOT, 'dockerfiles/Dockerfile.hermes');

  it('should generate the OpenCode config unconditionally (no if-missing guard)', () => {
    // bootstrap.test.ts covers that the template is used; this asserts the
    // coder-path specifics: the OpenCode config write sits OUTSIDE any
    // if [ ! -f ] guard, so the coder tool layer is re-provisioned on
    // every boot (idempotent overwrite of a possibly stale config).
    const content = fs.readFileSync(bootstrapPath, 'utf-8');
    expect(content).toContain('opencode.template.jsonc');
    expect(content).not.toMatch(/if \[ ! -f "\$OPENCODE_TARGET"/);
  });

  it('should install opencode-ai@latest in the shared Dockerfile', () => {
    const content = fs.readFileSync(dockerfilePath, 'utf-8');
    expect(content).toMatch(/npm install -g opencode-ai@latest/);
  });
});

// ============================================================
// Docker Compose Configuration Tests
// ============================================================

describe('Docker Compose - Agent Service (Coder Context)', () => {
  const composePath = path.join(PROJECT_ROOT, 'compose.yaml');

  // Slice out just the `agent:` service block (same idiom as
  // tests/docker-compose.test.ts) so assertions stay scoped to the
  // single agent service that now hosts the coder profile.
  const agentBlock = () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    const m = content.match(/^  agent:\n([\s\S]*?)(?=^  \w|^#|^\w)/m);
    if (!m) throw new Error('agent service block not found in compose.yaml');
    return m[1];
  };

  it('should mount the single agent data volume', () => {
    expect(agentBlock()).toMatch(/\.\/volumes\/agent:\/opt\/data/);
  });

  it('should mount the projects volume', () => {
    expect(agentBlock()).toMatch(/\.\/volumes\/projects:\/app\/projects/);
  });

  it('should cap memory at 8G for coding tasks', () => {
    expect(agentBlock()).toMatch(/limits:\s*\n\s+cpus: 2\s*\n\s+memory: 8G/);
  });
});

// ============================================================
// Delegate-Profile Skill Tests - Coder Target
// ============================================================

describe('Delegate-Profile Skill - Coder Target', () => {
  const skillPath = path.join(PROJECT_ROOT, 'src/skills/delegate-profile/SKILL.md');

  it('should reference coder agent in skill documentation', () => {
    const content = fs.readFileSync(skillPath, 'utf-8');
    expect(content).toContain('coder');
    expect(content).toContain('target_id: coder');
  });

  it('should list coding delegation triggers', () => {
    const content = fs.readFileSync(skillPath, 'utf-8');
    expect(content).toContain('code');
    expect(content).toContain('implement');
    expect(content).toContain('refactor');
  });

  it('should specify 120 minute timeout for coder', () => {
    const content = fs.readFileSync(skillPath, 'utf-8');
    expect(content).toContain('120 minutes');
  });
});

// ============================================================
// Main SOUL.md Delegation Tests
// ============================================================

describe('Main Agent SOUL.md - Coder Delegation', () => {
  const soulPath = path.join(PROJECT_ROOT, 'src/agents/main/SOUL.md');

  it('should include coding delegation triggers', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('code');
    expect(content).toContain('architecture');
  });
});

// ============================================================
// Docker Integration Tests (skipped unless DOCKER_TESTS=1)
// ============================================================
// NOTE: the DOCKER_TESTS=1 gated suites (this file and docker-compose.test.ts)
// must be run with --runInBand or against an already-running stack: parallel
// jest workers race — docker-compose.test.ts polls health immediately and
// throws on its first failed inspect while this suite's `up -d agent` is
// still pulling images / waiting on the ollama healthcheck.
// Every gated test passes an explicit timeout (no jest.setTimeout needed).

const DOCKER_TESTS_ENABLED = process.env.DOCKER_TESTS === '1';
const describeDocker = DOCKER_TESTS_ENABLED ? describe : describe.skip;

describeDocker('Docker Integration - Coder Profile (single container)', () => {
  const { execSync } = require('child_process');

  it('should start the agent container and become healthy', () => {
    // `up -d` blocks until ollama is service_healthy; a cold-machine pull of
    // the multi-GB ollama image can exceed 120s, so allow 240s.
    execSync('docker compose up -d agent', { cwd: PROJECT_ROOT, encoding: 'utf-8', timeout: 240000 });
    // Health-poll ~130s: start_period alone is 120s (bootstrap provisions three
    // profiles before the gateway listens) — same window as docker-compose.test.ts.
    for (let i = 0; i < 26; i++) {
      let status: string;
      try {
        status = execSync(
          'docker inspect --format "{{.State.Health.Status}}" bob-the-agent',
          { encoding: 'utf-8', stdio: 'pipe' }
        ).trim();
      } catch {
        throw new Error('bob-the-agent container not found — did the stack come up?');
      }
      if (status === 'healthy') return;
      execSync('sleep 5');
    }
    throw new Error('bob-the-agent never became healthy within ~130s (start_period is 120s)');
    // 240s up + 130s poll = 370s worst case, so the previous 300s budget was too tight.
  }, 600000);

  it('should have OpenCode CLI on PATH', () => {
    const result = execSync(
      'docker exec bob-the-agent which opencode',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result.trim()).toContain('opencode');
  }, 15000);

  it('should have OpenCode config at $OPENCODE_CONFIG', () => {
    // OPENCODE_CONFIG is set at compose level so docker exec can see it
    // (bootstrap's in-process exports are invisible to docker exec).
    const result = execSync(
      'docker exec bob-the-agent sh -c \'test -f "$OPENCODE_CONFIG" && cat "$OPENCODE_CONFIG"\'',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toContain('ollama');
  }, 15000);

  it('should have coder profile config with glm-5.1:cloud model', () => {
    const result = execSync(
      'docker exec bob-the-agent cat /opt/data/profiles/coder/config.yaml',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toContain('glm-5.1:cloud');
  }, 15000);
});
