import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as yaml from 'js-yaml';

const PROJECT_ROOT = path.resolve(__dirname, '..');
const FIXTURES_DIR = path.join(PROJECT_ROOT, 'tests', 'fixtures', 'config');
const SCRIPTS_DIR = path.join(PROJECT_ROOT, 'src', 'scripts');
const MERGE_SCRIPT = path.join(SCRIPTS_DIR, 'merge-yaml.mjs');
const GENERATE_SCRIPT = path.join(SCRIPTS_DIR, 'generate-config.sh');
const TMP_DIR = path.join(PROJECT_ROOT, 'tmp', 'test-output');

function q(p: string): string {
  return `"${p}"`;
}

function cleanup(): void {
  fs.rmSync(TMP_DIR, { recursive: true, force: true });
}

beforeAll(() => {
  fs.mkdirSync(TMP_DIR, { recursive: true });
});

afterAll(() => {
  cleanup();
});

describe('merge-yaml.mjs', () => {
  it('should deep merge template with partial', () => {
    const template = path.join(FIXTURES_DIR, 'template.yaml');
    const partial = path.join(FIXTURES_DIR, 'agents', 'main', 'hermes.partial.yml');
    const output = path.join(TMP_DIR, 'merged.yaml');

    execSync(`node ${q(MERGE_SCRIPT)} ${q(template)} ${q(partial)} ${q(output)}`);

    const result = yaml.load(fs.readFileSync(output, 'utf8')) as any;

    // Partial overrides
    expect(result.model.default).toBe('custom-model');
    expect(result.model.provider).toBe('custom');
    expect(result.model.base_url).toBe('http://custom:11434/v1');

    // Template values preserved
    expect(result.agent.max_turns).toBe(50);
    expect(result.agent.gateway_timeout).toBe(600);
    expect(result._config_version).toBe(23);

    // Arrays replaced (not concatenated)
    expect(result.custom_providers).toHaveLength(1);
    expect(result.custom_providers[0].name).toBe('custom-provider');

    // Template arrays preserved when not in partial
    expect(result.toolsets).toEqual(['hermes-cli', 'browser']);
  });

  it('should produce template as-is when partial is empty object', () => {
    const template = path.join(FIXTURES_DIR, 'template.yaml');
    const output = path.join(TMP_DIR, 'empty-partial.yaml');

    // Create an empty partial
    const emptyPartialDir = path.join(TMP_DIR, 'empty-agent');
    fs.mkdirSync(emptyPartialDir, { recursive: true });
    fs.writeFileSync(path.join(emptyPartialDir, 'hermes.partial.yml'), '{}\n');

    execSync(`node ${q(MERGE_SCRIPT)} ${q(template)} ${q(path.join(emptyPartialDir, 'hermes.partial.yml'))} ${q(output)}`);

    const result = yaml.load(fs.readFileSync(output, 'utf8')) as any;
    const templateObj = yaml.load(fs.readFileSync(template, 'utf8')) as any;

    expect(result.model.default).toBe(templateObj.model.default);
    expect(result.custom_providers[0].name).toBe(templateObj.custom_providers[0].name);
  });

  it('should fail with error for missing template file', () => {
    expect(() => {
      execSync(`node ${q(MERGE_SCRIPT)} /nonexistent/template.yaml /nonexistent/partial.yaml /tmp/out.yaml`, {
        stdio: 'pipe',
      });
    }).toThrow();
  });
});

describe('generate-config.sh', () => {
  it('should produce merged config with all 4 parameters', () => {
    const template = path.join(FIXTURES_DIR, 'template.yaml');
    const agentsDir = path.join(FIXTURES_DIR, 'agents');
    const output = path.join(TMP_DIR, 'gen-output.yaml');

    execSync(`bash ${q(GENERATE_SCRIPT)} ${q(template)} ${q(agentsDir)} main ${q(output)}`);

    const result = yaml.load(fs.readFileSync(output, 'utf8')) as any;
    expect(result.model.default).toBe('custom-model');
    expect(result.model.provider).toBe('custom');
    expect(result._config_version).toBe(23);
  });

  it('should fall back to template when agent partial does not exist', () => {
    const template = path.join(FIXTURES_DIR, 'template.yaml');
    const agentsDir = path.join(FIXTURES_DIR, 'agents');
    const output = path.join(TMP_DIR, 'gen-fallback.yaml');

    execSync(`bash ${q(GENERATE_SCRIPT)} ${q(template)} ${q(agentsDir)} nonexistent ${q(output)}`);

    const result = yaml.load(fs.readFileSync(output, 'utf8')) as any;
    const templateObj = yaml.load(fs.readFileSync(template, 'utf8')) as any;
    expect(result.model.default).toBe(templateObj.model.default);
  });

  it('should fail when template file does not exist', () => {
    const output = path.join(TMP_DIR, 'gen-error.yaml');
    expect(() => {
      execSync(`bash ${q(GENERATE_SCRIPT)} /nonexistent/template.yaml /some/dir agent ${q(output)}`, {
        stdio: 'pipe',
      });
    }).toThrow();
  });
});

describe('real hermes config merge', () => {
  it('should correctly merge hermes.template.yaml with main/hermes.partial.yml', () => {
    const template = path.join(PROJECT_ROOT, 'src', 'config', 'hermes.template.yaml');
    const partial = path.join(PROJECT_ROOT, 'src', 'agents', 'main', 'hermes.partial.yml');
    const output = path.join(TMP_DIR, 'hermes-main-merged.yaml');

    execSync(`node ${q(MERGE_SCRIPT)} ${q(template)} ${q(partial)} ${q(output)}`);

    const result = yaml.load(fs.readFileSync(output, 'utf8')) as any;

    // Partial overrides
    expect(result.model.default).toBe('kimi-k2.6:cloud');
    expect(result.model.provider).toBe('custom');
    expect(result.model.base_url).toBe('http://ollama:11434/v1');

    // Template values preserved
    expect(result.agent.max_turns).toBe(90);
    expect(result._config_version).toBe(23);

    // Arrays replaced
    expect(result.custom_providers).toHaveLength(1);
    expect(result.custom_providers[0].name).toBe('ollama/kimi-k2.6:cloud');
  });
});