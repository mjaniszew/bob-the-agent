/**
 * Diagnostics API routes
 */

import express from 'express';
import { exec } from 'child_process';
import { promisify } from 'util';
import os from 'os';
import fs from 'fs';

const execAsync = promisify(exec);

export const diagnosticsRouter = express.Router();

// Middleware to verify JWT
function verifyToken(req: express.Request, res: express.Response, next: express.NextFunction) {
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Authentication required' });
  }

  next();
}

// Get full diagnostics
diagnosticsRouter.get('/', verifyToken, async (req, res) => {
  try {
    const diagnostics = {
      timestamp: new Date().toISOString(),
      system: await getSystemDiagnostics(),
      network: await getNetworkDiagnostics(),
      storage: await getStorageDiagnostics(),
      services: await getServicesDiagnostics()
    };

    res.json(diagnostics);
  } catch (error) {
    console.error('Error getting diagnostics:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Test Ollama connection
diagnosticsRouter.post('/test-ollama', verifyToken, async (req, res) => {
  try {
    const ollamaUrl = process.env.OLLAMA_BASE_URL || 'http://ollama:11434';

    const response = await fetch(`${ollamaUrl}/api/tags`, {
      method: 'GET',
      headers: { 'Accept': 'application/json' }
    });

    if (!response.ok) {
      throw new Error(`Ollama responded with status ${response.status}`);
    }

    const data = await response.json() as { models?: Array<{ name: string; size: number; modified_at: string }> };

    res.json({
      status: 'connected',
      url: ollamaUrl,
      models: data.models || []
    });
  } catch (error) {
    res.status(503).json({
      status: 'error',
      error: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Test Agent API connection
diagnosticsRouter.post('/test-agent', verifyToken, async (req, res) => {
  try {
    const agentUrl = process.env.AGENT_API_URL || 'http://localhost:18789';

    const response = await fetch(`${agentUrl}/healthz`, {
      method: 'GET'
    });

    if (!response.ok) {
      throw new Error(`Agent responded with status ${response.status}`);
    }

    res.json({
      status: 'connected',
      url: agentUrl
    });
  } catch (error) {
    res.status(503).json({
      status: 'error',
      error: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Helper functions
async function getSystemDiagnostics() {
  return {
    platform: os.platform(),
    arch: os.arch(),
    release: os.release(),
    hostname: os.hostname(),
    uptime: os.uptime(),
    loadAverage: os.loadavg(),
    cpus: os.cpus().length,
    memory: {
      total: os.totalmem(),
      free: os.freemem(),
      usage: ((os.totalmem() - os.freemem()) / os.totalmem() * 100).toFixed(2) + '%'
    },
    nodeVersion: process.version,
    processUptime: process.uptime()
  };
}

async function getNetworkDiagnostics() {
  const interfaces = os.networkInterfaces();
  const networkInfo: any = {};

  for (const [name, addrs] of Object.entries(interfaces)) {
    networkInfo[name] = addrs?.map(addr => ({
      address: addr.address,
      family: addr.family,
      internal: addr.internal
    }));
  }

  return {
    interfaces: networkInfo,
    hostname: os.hostname()
  };
}

async function getStorageDiagnostics() {
  try {
    const resultsPath = (process.env.WEB_APP_BASE_PATH || '.app') + '/results';
    const dataPath = (process.env.WEB_APP_BASE_PATH || '.app') + '/data';

    const resultsStat = fs.statSync(resultsPath);
    const dataStat = fs.statSync(dataPath);

    return {
      results: {
        path: resultsPath,
        exists: true,
        writable: isWritable(resultsPath)
      },
      data: {
        path: dataPath,
        exists: true,
        writable: isWritable(dataPath)
      }
    };
  } catch (error) {
    return {
      error: error instanceof Error ? error.message : 'Unknown error'
    };
  }
}

function isWritable(path: string): boolean {
  try {
    fs.accessSync(path, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

async function getServicesDiagnostics() {
  const services: any = {};

  // Check if running in Docker
  try {
    fs.accessSync('/.dockerenv', fs.constants.F_OK);
    services.docker = true;
  } catch {
    services.docker = false;
  }

  // Check Ollama
  try {
    const ollamaUrl = process.env.OLLAMA_BASE_URL || 'http://ollama:11434';
    const response = await fetch(`${ollamaUrl}/api/tags`, {
      method: 'GET',
      signal: AbortSignal.timeout(5000)
    });
    services.ollama = response.ok;
  } catch {
    services.ollama = false;
  }

  // Check OpenClaw gateway
  try {
    const agentUrl = process.env.AGENT_API_URL || 'http://localhost:18789';
    const response = await fetch(`${agentUrl}/healthz`, {
      method: 'GET',
      signal: AbortSignal.timeout(5000)
    });
    services.openclawGateway = response.ok;
  } catch {
    services.openclawGateway = false;
  }

  return services;
}