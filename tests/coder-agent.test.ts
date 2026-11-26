/**
 * Coder Agent Deletion + OpenCode Template Tests
 *
 * The coder Hermes profile (src/agents/coder) was removed in the harness PoC
 * final phase (R8): the harness invokes OpenCode directly. This file is the
 * coder-deletion contract plus the OpenCode template/bootstrap contract
 * (unconditionally re-provisioned template + pinned opencode-ai install),
 * plus a negative-existence guard so no test resurrects the deleted profile
 * by reading its deleted artifacts.
 *
 * Compose facts (agent volume/mounts, resource limits) and DOCKER_TESTS=1
 * gated runtime coverage live in tests/docker-compose.test.ts — they were
 * deduplicated out of this file in the R12c review round.
 *
 * Run with: npm test -- --testPathPattern=coder-agent
 */

import { describe, it, expect } from '@jest/globals';
import * as fs from 'fs';
import * as path from 'path';

const PROJECT_ROOT = path.resolve(__dirname, '..');

// ============================================================
// Coder Agent Deletion Contract
// ============================================================

describe('Coder Agent (removed)', () => {
  it('src/agents/coder is gone (the harness invokes OpenCode directly)', () => {
    // Guard: if the in-container coder profile ever reappears, restore its
    // SOUL/partial coverage deliberately instead of reviving stale tests.
    expect(fs.existsSync(path.join(PROJECT_ROOT, 'src/agents/coder'))).toBe(false);
  });

  it('src/agents/main is gone (R8 deletion; the harness long-runner replaces it)', () => {
    expect(fs.existsSync(path.join(PROJECT_ROOT, 'src/agents/main'))).toBe(false);
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

  it('should configure qwen3.5:2b-q4_K_M model', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.provider.ollama.models['qwen3.5:2b-q4_K_M']).toBeDefined();
  });

  it('should use "agent" (not "agents") as the top-level agent key', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.agent).toBeDefined();
    expect(config.agents).toBeUndefined();
  });

  it('should configure the build agent with the glm-5.3-flash:cloud model', () => {
    const content = fs.readFileSync(templatePath, 'utf-8');
    const config = JSON.parse(content);
    expect(config.agent.build.model).toBe('ollama/glm-5.3-flash:cloud');
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
// Bootstrap (OpenCode layer) Tests
// ============================================================

describe('Bootstrap (OpenCode layer)', () => {
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

  it('should install opencode-ai@1.18.32 in the shared Dockerfile', () => {
    const content = fs.readFileSync(dockerfilePath, 'utf-8');
    expect(content).toMatch(/npm install -g opencode-ai@1\.18\.32/);
  });
});

// ============================================================
// Deleted-profile negative-existence guard (R12c review round)
// ============================================================
// The coder profile was deleted in R8; no test may resurrect it,
// including by reading deleted artifacts (the pre-R12c gated
// suite exec'd `cat` of that deleted profile's config.yaml).
// The needle is built by joining fragments instead of written
// literally so the guard never matches its own source text and
// the scan genuinely covers every test file, this one included.
describe("deleted coder profile artifacts are never read by tests", () => {
  it("no test file reads the deleted coder profile config path", () => {
    const needle = ["profiles", "coder"].join("/");
    const testsDir = path.join(PROJECT_ROOT, "tests");
    const offenders = fs.readdirSync(testsDir)
      .filter((f) => f.endsWith(".test.ts"))
      .filter((f) =>
        fs.readFileSync(path.join(testsDir, f), "utf-8")
          .includes(needle)
      );
    expect(offenders).toEqual([]);
  });
});


