import * as registry from '../src/skills/index';

const SKILL = 'delegateProfile';

describe('delegate-profile skill', () => {
  test('is registered', () => {
    expect(registry).toHaveProperty(SKILL);
  });

  test('rejects unknown actions', async () => {
    await expect(registry[SKILL]({ action: 'bogus' })).rejects.toThrow(/action/i);
  });

  test('rejects send_task without target/goal', async () => {
    await expect(registry[SKILL]({ action: 'send_task' })).rejects.toThrow(/target/i);
    await expect(
      registry[SKILL]({ action: 'send_task', target_agent_id: 'simple' })
    ).rejects.toThrow(/goal/i);
  });

  test('rejects unknown delegation target', async () => {
    await expect(
      registry[SKILL]({ action: 'send_task', target_agent_id: 'unknown', goal: 'x' })
    ).rejects.toThrow(/unknown target/i);
  });

  test('enforces per-target timeout table (simple 15m, researcher 60m, coder 120m)', () => {
    const timeouts = (registry[SKILL] as any).targetTimeouts;
    expect(timeouts.simple).toBe(15 * 60 * 1000);
    expect(timeouts.researcher).toBe(60 * 60 * 1000);
    expect(timeouts.coder).toBe(120 * 60 * 1000);
  });

  describe('send_task_background / check_task', () => {
    const fs = require('fs');
    const path = require('path');
    const os = require('os');
    let tmp: string;

    beforeEach(() => {
      tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bob-deleg-'));
      process.env.DELEGATION_LOG_DIR = tmp;
    });
    afterEach(() => {
      fs.rmSync(tmp, { recursive: true, force: true });
      delete process.env.DELEGATION_LOG_DIR;
    });

    test('starts a task with a JSON marker and a wrapper that times out, logs exit code, and clears the marker', async () => {
      const childProcess = require('child_process');
      const spawnSpy = jest.spyOn(childProcess, 'spawn')
        .mockReturnValue({ unref: jest.fn(), on: jest.fn(), pid: 4242 } as any);
      const res = await registry[SKILL]({
        action: 'send_task_background', target_agent_id: 'simple', goal: 'do it',
      });
      expect(res.status).toBe('started');
      const markerFile = path.join(tmp, `${res.task_id}.running`);
      expect(fs.existsSync(markerFile)).toBe(true);
      const marker = JSON.parse(fs.readFileSync(markerFile, 'utf8'));
      expect(marker.target).toBe('simple');
      expect(marker.startedAt).toBeGreaterThan(0);
      const call = spawnSpy.mock.calls[0];
      expect(call[0]).toBe('bash');
      // call[1] = ['-c', wrapped] — the wrapper is args[1] (spawn('bash', ['-c', wrapped]))
      expect(call[1][1]).toMatch(/timeout 900 hermes -p 'simple' chat --oneshot/);
      expect(call[1][1]).toMatch(/echo "EXIT:\$rc" >>/);
      expect(call[1][1]).toMatch(/rm -f/);
      spawnSpy.mockRestore();
    });

    test('send_task_background rejects unknown target like the foreground path', async () => {
      await expect(
        registry[SKILL]({ action: 'send_task_background', target_agent_id: 'nope', goal: 'x' })
      ).rejects.toThrow(/unknown target/);
    });

    test('check_task reports running while a fresh marker exists', async () => {
      const id = 'simple-123-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial output\n');
      fs.writeFileSync(path.join(tmp, `${id}.running`),
        JSON.stringify({ target: 'simple', startedAt: Date.now() }));
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('running');
    });

    test('check_task reports failed with exit code from the sentinel', async () => {
      const id = 'simple-456-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'boom\nEXIT:5\n');
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('failed');
      expect(res.exit_code).toBe(5);
    });

    test('check_task reports finished with exit_code 0 on success sentinel', async () => {
      const id = 'simple-789-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'all done\nEXIT:0\n');
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('finished');
      expect(res.exit_code).toBe(0);
    });

    test('check_task reports finished with unknown outcome when the marker is gone without a sentinel', async () => {
      const id = 'simple-111-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial output\n');
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('finished');
    });

    test('check_task flags stale markers (orphaned by a restart) as failed', async () => {
      const id = 'simple-222-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial output\n');
      fs.writeFileSync(path.join(tmp, `${id}.running`),
        JSON.stringify({ target: 'simple', startedAt: Date.now() - 30 * 60 * 1000 }));
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('failed');
      expect(res.stale).toBe(true);
    });

    test('check_task treats a plain legacy marker as running', async () => {
      const id = 'simple-333-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial output\n');
      fs.writeFileSync(path.join(tmp, `${id}.running`), 'running');
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('running');
    });

    test('check_task rejects malformed task_id (path traversal) and missing logs', async () => {
      await expect(
        registry[SKILL]({ action: 'check_task', task_id: '../../etc/passwd' })
      ).rejects.toThrow(/invalid task_id/);
      await expect(
        registry[SKILL]({ action: 'check_task', task_id: 'simple-444-abcdef' })
      ).rejects.toThrow(/no log/);
    });

    test('check_task truncates the log tail to ~4000 chars without loading the whole file', async () => {
      const id = 'simple-555-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'x'.repeat(20 * 1024));
      const res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.log_tail.length).toBeLessThanOrEqual(4000);
    });

    test('sweeps delegation logs older than 7 days on background start', async () => {
      const childProcess = require('child_process');
      const spawnSpy = jest.spyOn(childProcess, 'spawn')
        .mockReturnValue({ unref: jest.fn(), on: jest.fn(), pid: 4242 } as any);
      const old = path.join(tmp, 'simple-999-abcdef.log');
      fs.writeFileSync(old, 'old');
      const oneWeekAgo = (Date.now() - 8 * 24 * 60 * 60 * 1000) / 1000;
      fs.utimesSync(old, oneWeekAgo, oneWeekAgo);
      await registry[SKILL]({ action: 'send_task_background', target_agent_id: 'simple', goal: 'do it' });
      expect(fs.existsSync(old)).toBe(false);
      spawnSpy.mockRestore();
    });
  });

  // Regression for Task 5 quality review issue 1: check_task must pass the
  // skillRegistry metadata validation (skill-runner.mjs validateParams and
  // executeSkill) with ONLY task_id — the old metadata marked target_agent_id
  // as required, breaking the documented polling flow on every invocation.
  describe('executeSkill integration (validation layer)', () => {
    const fs = require('fs');
    const path = require('path');
    const os = require('os');
    const { executeSkill } = require('../src/skills/index');
    let tmp: string;

    beforeEach(() => {
      tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bob-deleg-x-'));
      process.env.DELEGATION_LOG_DIR = tmp;
    });
    afterEach(() => {
      fs.rmSync(tmp, { recursive: true, force: true });
      delete process.env.DELEGATION_LOG_DIR;
    });

    test('check_task passes the metadata validation layer with only task_id', async () => {
      const id = 'simple-666-abcdef';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial\n');
      const res = await executeSkill('delegate-profile', { action: 'check_task', task_id: id });
      expect(res.status).toBe('finished');
    });

    test('send_task unknown target still rejects through the validation layer', async () => {
      await expect(
        executeSkill('delegate-profile', { action: 'send_task', target_agent_id: 'nope', goal: 'x' })
      ).rejects.toThrow(/unknown target/);
    });
  });

  test('NATS artifacts are gone', () => {
    const fs = require('fs');
    const path = require('path');
    const ROOT = path.join(__dirname, '..');
    expect(fs.existsSync(path.join(ROOT, 'src/skills/agent-to-agent'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/register-nats.py'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'src/scripts/hermes-cmd.sh'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'tests/nats-communication.test.ts'))).toBe(false);
    expect(fs.readFileSync(path.join(ROOT, 'src/scripts/skill-runner.mjs'), 'utf8'))
      .not.toMatch(/agent-to-agent/);
    expect(fs.readFileSync(path.join(ROOT, 'src/scripts/skill-runner.mjs'), 'utf8'))
      .toMatch(/delegate-profile/);
  });
});