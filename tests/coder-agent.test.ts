/**
 * Coder Agent Integration Tests
 *
 * Tests for the coder agent configuration, Docker compose setup,
 * and OpenCode template configuration.
 *
 * Run with: npm test -- --testPathPattern=coder-agent
 * Docker tests: DOCKER_TEST=1 npm test -- --testPathPattern=coder-agent
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

  it('should reference agent-to-agent skill', () => {
    const content = fs.readFileSync(soulPath, 'utf-8');
    expect(content).toContain('agent-to-agent');
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
  const templatePath = path.join(PROJECT_ROOT, 'src/config/opencode.template.json');

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

  it('should contain SEARXNG_BASE_URL placeholder', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    expect(content).toContain('SEARXNG_BASE_URL_PLACEHOLDER');
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

  it('should enable autoCompact', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.autoCompact).toBe(true);
  });

  it('should configure agents with glm-5.1:cloud model', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.agents.coder.model).toContain('glm-5.1:cloud');
  });

  it('should configure full tool profile', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.tools.profile).toBe('full');
  });
});

// ============================================================
// Coder Entrypoint Script Tests
// ============================================================

describe('Coder Entrypoint Script', () => {
  const scriptPath = path.join(PROJECT_ROOT, 'src/scripts/coder-entrypoint.sh');

  it('should exist', () => {
    expect(fs.existsSync(scriptPath)).toBe(true);
  });

  it('should be executable', () => {
    const stats = fs.statSync(scriptPath);
    // Check if any execute bit is set
    expect(stats.mode & 0o111).not.toBe(0);
  });

  it('should install opencode CLI', () => {
    const content = fs.readFileSync(scriptPath, 'utf-8');
    expect(content).toContain('opencode');
    expect(content).toContain('npm install -g opencode-ai');
  });

  it('should generate opencode config from template', () => {
    const content = fs.readFileSync(scriptPath, 'utf-8');
    expect(content).toContain('opencode.template.json');
    expect(content).toContain('OLLAMA_BASE_URL');
    expect(content).toContain('sed');
  });

  it('should create projects directory', () => {
    const content = fs.readFileSync(scriptPath, 'utf-8');
    expect(content).toContain('/app/projects');
    expect(content).toContain('mkdir -p');
  });

  it('should start NATS listener', () => {
    const content = fs.readFileSync(scriptPath, 'utf-8');
    expect(content).toContain('register-nats.py');
  });

  it('should exec the main command at the end', () => {
    const content = fs.readFileSync(scriptPath, 'utf-8');
    expect(content).toContain('exec "$@"');
  });
});

// ============================================================
// Docker Compose Configuration Tests
// ============================================================

describe('Docker Compose - Coder Service', () => {
  const composePath = path.join(PROJECT_ROOT, 'compose.yaml');

  it('should define coder service', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    expect(content).toContain('coder:');
  });

  it('should set AGENT_NAME to coder', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    // Simple string check since compose uses YAML anchors
    expect(content).toContain('AGENT_NAME: coder');
  });

  it('should mount projects volume', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    expect(content).toContain('./volumes/projects:/app/projects');
  });

  it('should mount agent-coder data volume', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    expect(content).toContain('./volumes/agent-coder:/opt/data');
  });

  it('should mount results volume', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    // The coder service should have the results volume
    expect(content).toContain('./volumes/results:/app/results');
  });

  it('should use coder-entrypoint.sh', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    expect(content).toContain('coder-entrypoint.sh');
  });

  it('should have increased memory limits for coding tasks', () => {
    const content = fs.readFileSync(composePath, 'utf-8');
    // The coder service should have higher memory limits than the default 4G
    expect(content).toMatch(/memory:\s*8G/);
  });
});

// ============================================================
// Agent-to-Agent Skill Tests - Coder Agent
// ============================================================

describe('Agent-to-Agent Skill - Coder Agent', () => {
  const skillPath = path.join(PROJECT_ROOT, 'src/skills/agent-to-agent/SKILL.md');

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
    expect(content).toContain('Timeout: 120 minutes');
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
// OpenCode Template Env Substitution Tests
// ============================================================

describe('OpenCode Template Env Substitution', () => {
  const templatePath = path.join(PROJECT_ROOT, 'src/config/opencode.template.json');

  it('should produce valid JSON after placeholder substitution', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const substituted = content
      .replace(/OLLAMA_BASE_URL_PLACEHOLDER/g, 'http://ollama:11434')
      .replace(/SEARXNG_BASE_URL_PLACEHOLDER/g, 'http://searxng:8888');
    expect(() => JSON.parse(substituted)).not.toThrow();
  });

  it('should have correct Ollama URL after substitution', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const substituted = content
      .replace(/OLLAMA_BASE_URL_PLACEHOLDER/g, 'http://ollama:11434')
      .replace(/SEARXNG_BASE_URL_PLACEHOLDER/g, 'http://searxng:8888');
    const config = JSON.parse(substituted);
    expect(config.provider.ollama.options.baseURL).toBe('http://ollama:11434/v1');
  });

  it('should have correct SearXNG URL after substitution', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const substituted = content
      .replace(/OLLAMA_BASE_URL_PLACEHOLDER/g, 'http://ollama:11434')
      .replace(/SEARXNG_BASE_URL_PLACEHOLDER/g, 'http://searxng:8888');
    const config = JSON.parse(substituted);
    expect(config.tools.web.search.baseURL).toBe('http://searxng:8888');
  });
});

// ============================================================
// Docker Integration Tests (skipped unless DOCKER_TEST=1)
// ============================================================

const DOCKER_TESTS_ENABLED = process.env.DOCKER_TEST === '1';
const describeDocker = DOCKER_TESTS_ENABLED ? describe : describe.skip;

describeDocker('Docker Integration - Coder Agent', () => {
  const { execSync } = require('child_process');

  beforeAll(() => {
    jest.setTimeout(300000);
  });

  afterAll(() => {
    // Cleanup
    try {
      execSync('docker compose down --volumes --remove-orphans', { cwd: PROJECT_ROOT });
    } catch { /* ignore */ }
  });

  it('should build the Docker image successfully', () => {
    const result = execSync(
      'docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .',
      { cwd: PROJECT_ROOT, encoding: 'utf-8', timeout: 300000 }
    );
    expect(result).toBeDefined();
  }, 300000);

  it('should start coder container', () => {
    execSync('docker compose up -d coder', { cwd: PROJECT_ROOT, encoding: 'utf-8', timeout: 60000 });
  }, 60000);

  it('should have OpenCode CLI installed', () => {
    const result = execSync(
      'docker exec bob-the-agent-coder which opencode',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toBeTruthy();
  }, 15000);

  it('should have OpenCode config generated', () => {
    const result = execSync(
      'docker exec bob-the-agent-coder cat /home/node/.opencode/opencode.json',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toContain('ollama');
  }, 15000);

  it('should have projects directory mounted', () => {
    const result = execSync(
      'docker exec bob-the-agent-coder ls -la /app/projects',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toBeTruthy();
  }, 15000);

  it('should have NATS listener running', () => {
    const result = execSync(
      'docker exec bob-the-agent-coder ps aux | grep register-nats',
      { encoding: 'utf-8', timeout: 10000 }
    );
    expect(result).toContain('register-nats');
  }, 15000);
});