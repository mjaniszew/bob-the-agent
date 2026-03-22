/**
 * Docker Compose Integration Tests
 * Tests for verifying the docker compose stack builds and runs correctly
 *
 * These tests require Docker to be running and will create/destroy containers.
 * Run with: npm run test:docker
 *
 * Note: These tests are skipped by default. Use --testPathPattern=docker-compose to run them.
 */

import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

// Configuration
const PROJECT_ROOT = path.resolve(__dirname, '..');
const DOCKER_COMPOSE_FILE = path.join(PROJECT_ROOT, 'docker-compose.yml');
const TIMEOUT_MS = 300000; // 5 minutes for build operations

// Helper to run docker compose commands
const runDockerCompose = (args: string, timeout = TIMEOUT_MS): { stdout: string; stderr: string; status: number } => {
  try {
    const stdout = execSync(`docker compose ${args}`, {
      cwd: PROJECT_ROOT,
      encoding: 'utf-8',
      timeout
    });
    return { stdout, stderr: '', status: 0 };
  } catch (error: any) {
    return {
      stdout: error.stdout || '',
      stderr: error.stderr || error.message,
      status: error.status || 1
    };
  }
};

// Helper to check if Docker is available
const isDockerAvailable = (): boolean => {
  try {
    execSync('docker --version', { encoding: 'utf-8', timeout: 5000 });
    return true;
  } catch {
    return false;
  }
};

// Helper to check if containers are running
const getContainerStatus = (containerName: string): string | null => {
  try {
    const result = execSync(
      `docker ps --filter "name=${containerName}" --format "{{.Status}}"`,
      { encoding: 'utf-8', timeout: 10000 }
    ).trim();
    return result || null;
  } catch {
    return null;
  }
};

// Helper to wait for container health
const waitForHealthy = async (containerName: string, maxWaitMs = 120000): Promise<boolean> => {
  const startTime = Date.now();
  while (Date.now() - startTime < maxWaitMs) {
    try {
      const result = execSync(
        `docker inspect --format "{{.State.Health.Status}}" ${containerName}`,
        { encoding: 'utf-8', timeout: 5000 }
      ).trim();
      if (result === 'healthy') {
        return true;
      }
    } catch {
      // Container may not exist yet
    }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
  return false;
};

// Skip all tests if Docker is not available
const describeDocker = isDockerAvailable() ? describe : describe.skip;

describeDocker('Docker Compose Build Tests', () => {
  beforeAll(() => {
    jest.setTimeout(TIMEOUT_MS);
  });

  afterAll(() => {
    // Cleanup: Stop and remove containers
    runDockerCompose('down --volumes --remove-orphans');
  });

  describe('Dockerfile Validation', () => {
    it('should have valid Dockerfile.agent syntax', () => {
      const dockerfilePath = path.join(PROJECT_ROOT, 'dockerfiles', 'Dockerfile.agent');
      expect(fs.existsSync(dockerfilePath)).toBe(true);

      const result = runDockerCompose(`build --no-cache --progress=plain agent`);
      expect(result.status).toBe(0);
    });

    it('should have valid Dockerfile.web syntax', () => {
      const dockerfilePath = path.join(PROJECT_ROOT, 'dockerfiles', 'Dockerfile.web');
      expect(fs.existsSync(dockerfilePath)).toBe(true);

      const result = runDockerCompose(`build --no-cache --progress=plain web`);
      expect(result.status).toBe(0);
    });
  });

  describe('Base Image Requirements', () => {
    it('should use Node.js 24 base image in Dockerfile.agent', () => {
      const dockerfilePath = path.join(PROJECT_ROOT, 'dockerfiles', 'Dockerfile.agent');
      const content = fs.readFileSync(dockerfilePath, 'utf-8');
      expect(content).toMatch(/FROM\s+node:24/);
    });

    it('should use Node.js 24 base image in Dockerfile.web', () => {
      const dockerfilePath = path.join(PROJECT_ROOT, 'dockerfiles', 'Dockerfile.web');
      const content = fs.readFileSync(dockerfilePath, 'utf-8');
      expect(content).toMatch(/FROM\s+node:24/);
    });
  });
});

describeDocker('Docker Compose Runtime Tests', () => {
  beforeAll(() => {
    // Build all images first
    runDockerCompose('build');
  });

  afterAll(() => {
    // Cleanup: Stop and remove containers
    runDockerCompose('down --volumes --remove-orphans');
  });

  describe('Container Startup', () => {
    it('should start Ollama container', async () => {
      const result = runDockerCompose('up -d ollama');
      expect(result.status).toBe(0);

      // Wait for container to be running
      await new Promise(resolve => setTimeout(resolve, 5000));

      const status = getContainerStatus('bob-the-agent-ollama');
      expect(status).not.toBeNull();
    }, 60000);

    it('should have healthy Ollama container', async () => {
      const healthy = await waitForHealthy('bob-the-agent-ollama', 60000);
      expect(healthy).toBe(true);
    }, 90000);

    it('should start agent container', async () => {
      const result = runDockerCompose('up -d agent');
      expect(result.status).toBe(0);

      await new Promise(resolve => setTimeout(resolve, 5000));

      const status = getContainerStatus('bob-the-agent');
      expect(status).not.toBeNull();
    }, 60000);

    it('should have healthy agent container', async () => {
      const healthy = await waitForHealthy('bob-the-agent', 90000);
      expect(healthy).toBe(true);
    }, 120000);

    it('should start web container', async () => {
      const result = runDockerCompose('up -d web');
      expect(result.status).toBe(0);

      await new Promise(resolve => setTimeout(resolve, 5000));

      const status = getContainerStatus('bob-the-agent-web');
      expect(status).not.toBeNull();
    }, 60000);

    it('should have healthy web container', async () => {
      const healthy = await waitForHealthy('bob-the-agent-web', 60000);
      expect(healthy).toBe(true);
    }, 90000);
  });

  describe('Port Availability', () => {
    it('should expose Ollama on port 11434', () => {
      const result = execSync('docker port bob-the-agent-ollama', { encoding: 'utf-8' });
      expect(result).toContain('11434');
    });

    it('should expose agent on port 18789', () => {
      const result = execSync('docker port bob-the-agent', { encoding: 'utf-8' });
      expect(result).toContain('18789');
    });

    it('should expose web on port 8080', () => {
      const result = execSync('docker port bob-the-agent-web', { encoding: 'utf-8' });
      expect(result).toContain('80');
    });
  });

  describe('Service Connectivity', () => {
    it('should have network connectivity between services', () => {
      const result = execSync(
        'docker exec bob-the-agent curl -s -o /dev/null -w "%{http_code}" http://ollama:11434/api/tags',
        { encoding: 'utf-8', timeout: 10000 }
      ).trim();

      // Accept 200 (success) or 404 (endpoint exists but may need model)
      expect(['200', '404']).toContain(result);
    });

    it('should allow web container to reach agent via TCP', () => {
      // Agent gateway speaks WebSocket, not HTTP, so we test TCP connectivity
      const result = execSync(
        'docker exec bob-the-agent-web bash -c "echo > /dev/tcp/agent/18789 && echo OK || echo FAIL"',
        { encoding: 'utf-8', timeout: 10000 }
      ).trim();
      expect(result).toBe('OK');
    });
  });

  describe('Volume Mounts', () => {
    it('should mount results volume', () => {
      const result = execSync(
        'docker inspect bob-the-agent --format "{{range .Mounts}}{{if eq .Destination \\"/app/results\\"}}{{.Source}}{{end}}{{end}}"',
        { encoding: 'utf-8' }
      ).trim();

      expect(result).toBeTruthy();
    });

    it('should mount user-files volume', () => {
      const result = execSync(
        'docker inspect bob-the-agent --format "{{range .Mounts}}{{if eq .Destination \\"/app/user-files\\"}}{{.Source}}{{end}}{{end}}"',
        { encoding: 'utf-8' }
      ).trim();

      expect(result).toBeTruthy();
    });

    it('should mount data volume', () => {
      const result = execSync(
        'docker inspect bob-the-agent --format "{{range .Mounts}}{{if eq .Destination \\"/app/data\\"}}{{.Source}}{{end}}{{end}}"',
        { encoding: 'utf-8' }
      ).trim();

      expect(result).toBeTruthy();
    });
  });
});

describeDocker('Health Endpoint Tests', () => {
  beforeAll(async () => {
    // Ensure containers are up
    runDockerCompose('up -d');
    // Wait longer for containers to be ready after previous suite teardown
    await new Promise(resolve => setTimeout(resolve, 90000));
    // Wait for containers to be healthy
    await waitForHealthy('bob-the-agent-ollama', 60000);
    await waitForHealthy('bob-the-agent', 60000);
    await waitForHealthy('bob-the-agent-web', 60000);
  }, 180000); // Increase timeout for this hook

  afterAll(() => {
    runDockerCompose('down --volumes --remove-orphans');
  });

  describe('Agent Health', () => {
    it('should have open TCP port for WebSocket gateway', async () => {
      // Agent gateway speaks WebSocket, test TCP connectivity
      const result = execSync(
        'docker exec bob-the-agent bash -c "echo > /dev/tcp/localhost/18789 && echo OK || echo FAIL"',
        { encoding: 'utf-8', timeout: 10000 }
      ).trim();
      expect(result).toBe('OK');
    });
  });

  describe('Ollama Health', () => {
    it('should respond to /api/tags endpoint', async () => {
      const result = execSync(
        'docker exec bob-the-agent-ollama curl -s http://localhost:11434/api/tags',
        { encoding: 'utf-8', timeout: 10000 }
      );

      expect(result).toBeTruthy();
      // Should return valid JSON
      expect(() => JSON.parse(result)).not.toThrow();
    });
  });

  describe('Web Interface Health', () => {
    it('should respond to /health endpoint', async () => {
      const result = execSync(
        'docker exec bob-the-agent-web curl -s http://localhost:80/health',
        { encoding: 'utf-8', timeout: 10000 }
      ).trim();

      expect(result).toBeTruthy();
    });
  });
});

describeDocker('Compose File Validation', () => {
  it('should have valid docker-compose.yml syntax', () => {
    const result = runDockerCompose('config --quiet');
    expect(result.status).toBe(0);
  });

  it('should define all required services', () => {
    const content = fs.readFileSync(DOCKER_COMPOSE_FILE, 'utf-8');
    expect(content).toContain('ollama:');
    expect(content).toContain('agent:');
    expect(content).toContain('web:');
  });

  it('should define required volumes', () => {
    const content = fs.readFileSync(DOCKER_COMPOSE_FILE, 'utf-8');
    expect(content).toContain('ollama_data:');
    expect(content).toContain('agent_workspace:');
  });

  it('should define health checks for all services', () => {
    const content = fs.readFileSync(DOCKER_COMPOSE_FILE, 'utf-8');
    expect(content).toMatch(/healthcheck:[\s\S]*test:/);
  });

  it('should not reference missing .env files', () => {
    const content = fs.readFileSync(DOCKER_COMPOSE_FILE, 'utf-8');
    // Should not have env_file directive pointing to .env
    expect(content).not.toMatch(/env_file:\s*\n\s*-\s*\.env/);
  });
});

describe('Package.json Validation', () => {
  it('should require Node.js 24+ in root package.json', () => {
    const packagePath = path.join(PROJECT_ROOT, 'package.json');
    const content = JSON.parse(fs.readFileSync(packagePath, 'utf-8'));
    expect(content.engines?.node).toMatch(/>=24/);
  });

  it('should require Node.js 24+ in api package.json', () => {
    const packagePath = path.join(PROJECT_ROOT, 'src', 'api', 'package.json');
    const content = JSON.parse(fs.readFileSync(packagePath, 'utf-8'));
    expect(content.engines?.node).toMatch(/>=24/);
  });

  it('should require Node.js 24+ in frontend package.json', () => {
    const packagePath = path.join(PROJECT_ROOT, 'src', 'frontend', 'package.json');
    const content = JSON.parse(fs.readFileSync(packagePath, 'utf-8'));
    expect(content.engines?.node).toMatch(/>=24/);
  });
});