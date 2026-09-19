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

  test('main mandates delegate-profile with delegation-first protocol', () => {
    expect(soul('main')).toMatch(/delegate-profile/);
    expect(soul('main')).toMatch(/delegation/i);
  });

  test('researcher, simple, coder know they are profiles delegated to by main', () => {
    for (const name of ['researcher', 'simple', 'coder']) {
      expect(soul(name)).toMatch(/profile/i);
    }
  });

  test('coder keeps OpenCode two-layer instructions', () => {
    const s = soul('coder');
    expect(s).toMatch(/opencode/i);
    expect(s).toMatch(/terminal/);
    expect(s).toMatch(/\/app\/projects/);
  });
});