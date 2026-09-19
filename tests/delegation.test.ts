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

    test('starts a task with a running marker and a wrapper that clears it', async () => {
      const childProcess = require('child_process');
      const spawnSpy = jest.spyOn(childProcess, 'spawn')
        .mockReturnValue({ unref: jest.fn(), pid: 4242 } as any);
      const res = await registry[SKILL]({
        action: 'send_task_background', target_agent_id: 'simple', goal: 'do it',
      });
      expect(res.status).toBe('started');
      expect(fs.existsSync(path.join(tmp, `${res.task_id}.running`))).toBe(true);
      const call = spawnSpy.mock.calls[0];
      expect(call[0]).toBe('bash');
      expect(call[1][1]).toMatch(/rm -f/);
      expect(call[1][1]).toMatch(/hermes -p 'simple' chat --oneshot/);
      spawnSpy.mockRestore();
    });

    test('check_task reports running while marker exists, finished after removal', async () => {
      const id = 'simple-123';
      fs.writeFileSync(path.join(tmp, `${id}.log`), 'partial output\n');
      fs.writeFileSync(path.join(tmp, `${id}.running`), 'running');
      let res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('running');
      fs.rmSync(path.join(tmp, `${id}.running`));
      res = await registry[SKILL]({ action: 'check_task', task_id: id });
      expect(res.status).toBe('finished');
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