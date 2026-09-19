import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.join(__dirname, '..');
const soul = (name: string) =>
  fs.readFileSync(path.join(ROOT, `src/agents/${name}/SOUL.md`), 'utf8');

describe('SOUL.md single-container wording', () => {
  for (const name of ['main', 'researcher', 'simple', 'coder']) {
    test(`${name}: no NATS / container-era references remain`, () => {
      const s = soul(name);
      expect(s).not.toMatch(/NATS/i);
      expect(s).not.toMatch(/agent-to-agent/);
      expect(s).not.toMatch(/separate containers?/i);
      expect(s).not.toMatch(/register-nats/);
    });
  }

  test('main teaches the real delegate-profile contract', () => {
    const s = soul('main');
    expect(s).toMatch(/delegate-profile/);
    expect(s).toMatch(/send_task_background/);
    expect(s).toMatch(/check_task/);
    expect(s).toMatch(/simple 15m, researcher 60m, coder 120m/);
    expect(s).toMatch(/your own session/);
  });

  test('researcher, simple, coder know they are profiles delegated to by main', () => {
    for (const name of ['researcher', 'simple', 'coder']) {
      expect(soul(name)).toContain(`You run as the \`${name}\` profile`);
    }
  });

  test('simple is the terminal executor (no blanket delegation license)', () => {
    expect(soul('simple')).toMatch(/terminal executor/);
    expect(soul('simple')).toMatch(/you do not delegate/i);
  });

  test('coder keeps OpenCode two-layer instructions', () => {
    const s = soul('coder');
    expect(s).toMatch(/opencode/i);
    expect(s).toMatch(/terminal/);
    expect(s).toMatch(/\/app\/projects/);
  });
});