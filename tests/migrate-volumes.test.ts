import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';
import * as os from 'os';

const ROOT = path.join(__dirname, '..');
const scriptPath = path.join(ROOT, 'src/scripts/migrate-volumes.sh');

// Quote shell args — repo path may contain spaces.
const q = (p: string): string => `"${p}"`;

function makeFakeVolume(base: string, name: string) {
  const dir = path.join(base, name);
  fs.mkdirSync(path.join(dir, 'memories'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'memories', 'MEMORY.md'), `notes for ${name}\n`);
  fs.symlinkSync('MEMORY.md', path.join(dir, 'memories', 'link.md')); // must be preserved as a symlink
  fs.mkdirSync(path.join(dir, 'cron'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'cron', 'jobs'), `cron for ${name}\n`);
  fs.writeFileSync(path.join(dir, 'config.yaml'), 'old: config\n'); // must be skipped
  fs.mkdirSync(path.join(dir, 'state.db'), { recursive: true }); // must be skipped
  fs.mkdirSync(path.join(dir, 'sessions'), { recursive: true });  // must be skipped
  return dir;
}

describe('migrate-volumes.sh', () => {
  let base: string;
  beforeEach(() => {
    base = fs.mkdtempSync(path.join(os.tmpdir(), 'bob-migrate-'));
  });
  afterEach(() => fs.rmSync(base, { recursive: true, force: true }));

  test('migrates memories/cron/workspace from old agent dirs into new layout, skipping state.db/sessions', () => {
    for (const name of ['agent-main', 'agent-researcher', 'agent-simple', 'agent-coder']) {
      makeFakeVolume(base, name);
    }
    const target = path.join(base, 'agent');
    fs.mkdirSync(target, { recursive: true });

    execSync(`bash ${q(scriptPath)} ${q(base)} ${q(target)}`);

    // main -> target root
    expect(fs.existsSync(path.join(target, 'memories', 'MEMORY.md'))).toBe(true);
    expect(fs.existsSync(path.join(target, 'cron', 'jobs'))).toBe(true);
    expect(fs.existsSync(path.join(target, 'config.yaml'))).toBe(false);
    expect(fs.lstatSync(path.join(target, 'memories', 'link.md')).isSymbolicLink()).toBe(true);
    expect(fs.existsSync(path.join(target, 'state.db'))).toBe(false);
    // secondary profiles -> target/profiles/<name>
    for (const name of ['researcher', 'simple', 'coder']) {
      expect(fs.existsSync(path.join(target, 'profiles', name, 'memories', 'MEMORY.md'))).toBe(true);
      expect(fs.existsSync(path.join(target, 'profiles', name, 'cron', 'jobs'))).toBe(true);
      expect(fs.existsSync(path.join(target, 'profiles', name, 'config.yaml'))).toBe(false);
      expect(fs.existsSync(path.join(target, 'profiles', name, 'state.db'))).toBe(false);
    }
    // originals untouched
    expect(fs.existsSync(path.join(base, 'agent-main', 'memories', 'MEMORY.md'))).toBe(true);
  });

  test('is safe to re-run (does not overwrite existing files)', () => {
    for (const name of ['agent-main', 'agent-researcher', 'agent-simple', 'agent-coder']) {
      makeFakeVolume(base, name);
    }
    const target = path.join(base, 'agent');
    fs.mkdirSync(path.join(target, 'memories'), { recursive: true });
    fs.writeFileSync(path.join(target, 'memories', 'MEMORY.md'), 'existing\n');

    execSync(`bash ${q(scriptPath)} ${q(base)} ${q(target)}`);
    expect(fs.readFileSync(path.join(target, 'memories', 'MEMORY.md'), 'utf8')).toBe('existing\n');
  });

  test('fails loudly when volumes_root contains no old agent dirs', () => {
    const target = path.join(base, 'agent');
    let status = 0;
    try {
      execSync(`bash ${q(scriptPath)} ${q(base)} ${q(target)}`);
    } catch (e: any) {
      status = e.status ?? 1;
    }
    expect(status).not.toBe(0);
  });
});