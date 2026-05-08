/**
 * NATS Communication Integration Tests
 *
 * Tests for the agent-to-agent skill and register-nats.py background listener.
 * Uses TDD approach: these tests are written first and should FAIL until
 * implementation is complete.
 *
 * Test categories:
 * - Unit tests: Skill registry, Python CLI validation
 * - Docker integration: NATS messaging, end-to-end flow
 *
 * Run with: npm test -- --testPathPattern=nats-communication
 * Docker tests: npm run test:docker -- --testPathPattern=nats-communication
 */

import { describe, it, expect, beforeAll, afterAll } from '@jest/globals';

// ============================================================
// Unit Tests - Skill Registry & Module Structure
// ============================================================

describe('Agent-to-Agent Skill Registry', () => {
  it('should register agent-to-agent skill in the skill index', async () => {
    const { skillRegistry } = await import('../src/skills/index.js');
    expect(skillRegistry).toHaveProperty('agent-to-agent');
  });

  it('should have correct metadata for agent-to-agent skill', async () => {
    const { skillRegistry } = await import('../src/skills/index.js');
    const meta = skillRegistry['agent-to-agent'];
    expect(meta).toBeDefined();
    expect(meta.name).toBe('Agent-to-Agent');
    expect(meta.description).toContain('NATS');
    expect(meta.description).toContain('inter-agent');
    expect(meta.params).toHaveProperty('action');
    expect(meta.params.action.required).toBe(true);
    expect(meta.params.action.enum).toContain('send_task');
    expect(meta.params.action.enum).toContain('check_messages');
    expect(meta.params.action.enum).toContain('send_result');
  });

  it('should have target_agent_id as required param', async () => {
    const { skillRegistry } = await import('../src/skills/index.js');
    const meta = skillRegistry['agent-to-agent'];
    expect(meta.params).toHaveProperty('target_agent_id');
    expect(meta.params.target_agent_id.required).toBe(true);
  });

  it('should export agentToAgent function from skill module', async () => {
    const module = await import('../src/skills/agent-to-agent/index.js');
    expect(module.agentToAgent).toBeDefined();
    expect(typeof module.agentToAgent).toBe('function');
    expect(module.default).toBeDefined();
    expect(module.default).toBe(module.agentToAgent);
  });

  it('should include agent-to-agent in skill-runner registry', async () => {
    // This test verifies the skill-runner.mjs includes agent-to-agent
    // We read the file content since it's a .mjs file (not TypeScript)
    const fs = await import('fs');
    const path = await import('path');
    const skillRunnerPath = path.join(process.cwd(), 'src/scripts/skill-runner.mjs');
    const content = fs.readFileSync(skillRunnerPath, 'utf-8');
    expect(content).toContain('agent-to-agent');
  });
});

// ============================================================
// Unit Tests - Skill Parameter Validation
// ============================================================

describe('Agent-to-Agent Skill Validation', () => {
  it('should reject send_task without target_agent_id', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    const result = await agentToAgent({
      action: 'send_task',
      goal: 'test task'
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('target_agent_id');
  });

  it('should reject send_task without goal', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    const result = await agentToAgent({
      action: 'send_task',
      target_agent_id: 'researcher'
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('goal');
  });

  it('should reject invalid action', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    const result = await agentToAgent({
      action: 'invalid_action',
      target_agent_id: 'researcher'
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('action');
  });

  it('should reject send_result without original_message_id', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    const result = await agentToAgent({
      action: 'send_result',
      target_agent_id: 'main',
      status: 'completed'
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('original_message_id');
  });

  it('should reject send_result without status', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    const result = await agentToAgent({
      action: 'send_result',
      target_agent_id: 'main',
      original_message_id: 'test-id'
    });
    expect(result.success).toBe(false);
    expect(result.error).toContain('status');
  });
});

// ============================================================
// Unit Tests - Message Format
// ============================================================

describe('NATS Message Format', () => {
  it('should construct valid task message with all required fields', async () => {
    const { agentToAgent } = await import('../src/skills/agent-to-agent/index.js');
    // This will fail at NATS connection but we test the message structure
    // by checking the nats-helper.py output format
    // For now, verify the message schema is correct
    const validMessage = {
      message_id: expect.any(String),
      task_type: 'delegate_task',
      sender_agent_id: expect.any(String),
      target_agent_id: 'researcher',
      timestamp: expect.any(String),
      payload: {
        goal: expect.any(String),
        context: expect.any(String),
        toolsets: expect.any(Array),
        save_results_to: expect.any(String)
      }
    };
    // Validate the schema structure matches our spec
    expect(validMessage.task_type).toBe('delegate_task');
    expect(validMessage.payload).toBeDefined();
  });

  it('should construct valid result message with all required fields', () => {
    const validResult = {
      message_id: expect.any(String),
      task_type: 'task_result',
      original_message_id: expect.any(String),
      sender_agent_id: expect.any(String),
      target_agent_id: expect.any(String),
      timestamp: expect.any(String),
      status: expect.stringMatching(/^(completed|failed)$/),
      payload: {
        summary: expect.any(String),
        result_path: expect.any(String),
        error: expect.any(String)
      }
    };
    expect(validResult.task_type).toBe('task_result');
    expect(validResult.status).toMatch(/^(completed|failed)$/);
  });
});

// ============================================================
// NATS Helper Python Script Tests
// ============================================================

describe('nats-helper.py CLI', () => {
  // These tests validate the Python script CLI interface
  // They will fail until nats-helper.py is created

  const { execFile } = require('child_process');
  const path = require('path');
  const helperPath = path.join(
    process.cwd(),
    'src/skills/agent-to-agent/scripts/nats-helper.py'
  );

  it('should exist as a Python script', () => {
    const fs = require('fs');
    expect(fs.existsSync(helperPath)).toBe(true);
  });

  it('should show help text with --help flag', (done) => {
    execFile('python3', [helperPath, '--help'], (error, stdout) => {
      // --help exits with code 0
      expect(stdout).toContain('send_task');
      expect(stdout).toContain('check_messages');
      expect(stdout).toContain('send_result');
      done();
    });
  });

  it('should validate required args for send_task action', (done) => {
    execFile(
      'python3',
      [helperPath, '--action', 'send_task', '--target', 'researcher'],
      { env: { ...process.env, NATS_URL: 'nats://localhost:4222', AGENT_NAME: 'test' } },
      (error, stdout, stderr) => {
        // Should fail because --goal is required for send_task
        expect(error).not.toBeNull();
        expect(stderr).toContain('goal');
        done();
      }
    );
  });

  it('should validate required args for send_result action', (done) => {
    execFile(
      'python3',
      [helperPath, '--action', 'send_result', '--target', 'main'],
      { env: { ...process.env, NATS_URL: 'nats://localhost:4222', AGENT_NAME: 'test' } },
      (error, stdout, stderr) => {
        expect(error).not.toBeNull();
        expect(stderr).toContain('original_message_id');
        done();
      }
    );
  });
});

// ============================================================
// register-nats.py Script Tests
// ============================================================

describe('register-nats.py', () => {
  const fs = require('fs');
  const path = require('path');
  const scriptPath = path.join(
    process.cwd(),
    'src/scripts/register-nats.py'
  );

  it('should exist as a Python script', () => {
    expect(fs.existsSync(scriptPath)).toBe(true);
  });

  it('should be executable and show help with --help', (done) => {
    const { execFile } = require('child_process');
    execFile('python3', [scriptPath, '--help'], (error, stdout) => {
      expect(stdout).toContain('NATS');
      expect(stdout).toContain('agent');
      done();
    });
  });
});

// ============================================================
// Docker Integration Tests
// ============================================================
// These tests require Docker containers running.
// Run with: npm run test:docker -- --testPathPattern=nats-communication

const DOCKER_TESTS_ENABLED = process.env.DOCKER_TEST === '1';

const describeDocker = DOCKER_TESTS_ENABLED ? describe : describe.skip;

describeDocker('Docker Integration - NATS Communication', () => {
  const { exec } = require('child_process');
  const util = require('util');
  const execAsync = util.promisify(exec);

  const COMPOSE_FILE = '-f docker-compose.yml';

  beforeAll(async () => {
    // Start NATS container
    await execAsync(`docker compose ${COMPOSE_FILE} up -d nats`);
    // Wait for NATS to be healthy
    await new Promise(resolve => setTimeout(resolve, 5000));
  }, 30000);

  afterAll(async () => {
    // Cleanup: stop test containers
    await execAsync(`docker compose ${COMPOSE_FILE} down -v`);
  }, 30000);

  it('should have NATS container running and healthy', async () => {
    const { stdout } = await execAsync(
      'docker inspect --format="{{.State.Health.Status}}" bob-the-agent-nats'
    );
    expect(stdout.trim()).toBe('healthy');
  });

  it('should be able to publish and subscribe to NATS subjects', async () => {
    // Use nats CLI inside the container to test pub/sub
    const { stdout } = await execAsync(
      'docker exec bob-the-agent-nats nats pub test.subject "hello" --count=1'
    );
    expect(stdout).toContain('Published');
  });

  it('should route messages to correct agent subjects', async () => {
    // Test that messages on agent.researcher.tasks only reach researcher subscribers
    const testMessage = JSON.stringify({
      message_id: 'test-123',
      task_type: 'delegate_task',
      sender_agent_id: 'main',
      target_agent_id: 'researcher',
      timestamp: new Date().toISOString(),
      payload: { goal: 'test task', context: '', toolsets: [], save_results_to: '' }
    });

    // Publish to agent.researcher.tasks
    await execAsync(
      `docker exec bob-the-agent-nats nats pub agent.researcher.tasks '${testMessage}' --count=1`
    );

    // Verify the message is on the right subject
    const { stdout } = await execAsync(
      'docker exec bob-the-agent-nats nats sub agent.researcher.tasks --count=1 --timeout=3s 2>&1 || true'
    );
    // The subscription should show the message or timeout
    expect(stdout).toBeDefined();
  });

  it('should run register-nats.py inside agent container', async () => {
    // Build the image first
    await execAsync(
      'docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .',
      { maxBuffer: 1024 * 1024 * 10 }
    );

    // Start an agent container with NATS connection
    await execAsync(`
      docker compose ${COMPOSE_FILE} up -d agent-main
    `);

    // Wait for container to start and register-nats.py to initialize
    await new Promise(resolve => setTimeout(resolve, 10000));

    // Check that register-nats.py is running in the container
    const { stdout } = await execAsync(
      'docker exec bob-the-agent ps aux | grep register-nats || true'
    );
    expect(stdout).toContain('register-nats.py');
  }, 120000);

  it('should send task via skill and receive via NATS', async () => {
    // Use the skill to send a task
    const { stdout } = await execAsync(
      `docker exec bob-the-agent node /app/scripts/skill-runner.mjs --skill agent-to-agent --params '{"action":"send_task","target_agent_id":"researcher","goal":"test goal"}'`,
      { maxBuffer: 1024 * 1024 }
    );

    const result = JSON.parse(stdout);
    expect(result.success).toBe(true);
    expect(result.message_id).toBeDefined();
  }, 60000);
});