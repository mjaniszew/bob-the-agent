import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.join(__dirname, '..');
const soul = (name: string) =>
  fs.readFileSync(path.join(ROOT, `src/agents/${name}/SOUL.md`), 'utf8');

describe('SOUL.md single-container wording', () => {
  for (const name of ['researcher', 'simple']) {
    test(`${name}: no NATS / container-era references remain`, () => {
      const s = soul(name);
      expect(s).not.toMatch(/NATS/i);
      expect(s).not.toMatch(/agent-to-agent/);
      expect(s).not.toMatch(/separate containers?/i);
      expect(s).not.toMatch(/register-nats/);
    });
  }

  test('researcher and simple know they are profiles the harness delegates to', () => {
    // Identity wording: each surviving SOUL names its own profile.
    for (const name of ['researcher', 'simple']) {
      expect(soul(name)).toContain(`You run as the \`${name}\` profile`);
      // They still reference the delegate-profile skill — the mechanism the
      // harness (and sibling profiles) use to hand them work. No
      // "delegated to by main" phrase is asserted: main was deleted in R8.
      expect(soul(name)).toContain('delegate-profile');
    }
  });

  test('simple is the terminal executor (no blanket delegation license)', () => {
    expect(soul('simple')).toMatch(/terminal executor/);
    expect(soul('simple')).toMatch(/you do not delegate/i);
  });

  test('main profile is gone (the harness long-runner replaces it)', () => {
    // Guard: R8 deleted src/agents/main — if it ever reappears, this suite
    // reminds you to restore its SOUL coverage deliberately.
    expect(fs.existsSync(path.join(ROOT, 'src/agents/main'))).toBe(false);
  });
});
