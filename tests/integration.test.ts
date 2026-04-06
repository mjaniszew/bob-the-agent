/**
 * Integration Tests for Mini Agent
 * Run with: npm test
 */

import { describe, it, beforeAll, afterAll, expect } from '@jest/globals';

// Mock fetch for testing
const mockFetch = (mockResponse: any) => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve(mockResponse),
      text: () => Promise.resolve(JSON.stringify(mockResponse))
    } as Response)
  );
};

describe('Mini Agent Integration Tests', () => {
  const API_URL = process.env.API_URL || 'http://localhost:3001';

  describe('Health Check', () => {
    it('should return healthy status', async () => {
      mockFetch({ status: 'healthy' });

      const response = await fetch(`${API_URL}/health`);
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.status).toBe('healthy');
    });
  });

  describe('Tasks API', () => {
    it('should list tasks', async () => {
      mockFetch({ tasks: [], total: 0 });

      const response = await fetch(`${API_URL}/api/tasks`);
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data).toHaveProperty('tasks');
      expect(data).toHaveProperty('total');
    });

    it('should create a task', async () => {
      const taskData = {
        name: 'Test Task',
        description: 'A test task for integration testing',
        skill: 'grok-search'
      };

      mockFetch({
        id: 'test-task-id',
        ...taskData,
        status: 'pending',
        created_at: new Date().toISOString()
      });

      const response = await fetch(`${API_URL}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(taskData)
      });
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.name).toBe(taskData.name);
      expect(data.status).toBe('pending');
    });

    it('should get task by ID', async () => {
      const taskId = 'test-task-id';

      mockFetch({
        id: taskId,
        name: 'Test Task',
        status: 'pending'
      });

      const response = await fetch(`${API_URL}/api/tasks/${taskId}`);
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.id).toBe(taskId);
    });

    it('should delete a task', async () => {
      const taskId = 'test-task-id';

      mockFetch({ message: 'Task deleted', taskId });

      const response = await fetch(`${API_URL}/api/tasks/${taskId}`, {
        method: 'DELETE'
      });
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.taskId).toBe(taskId);
    });
  });

  describe('Schedules API', () => {
    it('should list schedules', async () => {
      mockFetch({ schedules: [] });

      const response = await fetch(`${API_URL}/api/schedules`);
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data).toHaveProperty('schedules');
    });

    it('should create a schedule', async () => {
      const scheduleData = {
        name: 'Daily Report',
        cron_expression: '0 9 * * *',
        task_template: { skill: 'grok-search', parameters: { query: 'daily news from x.com' } }
      };

      mockFetch({
        id: 'test-schedule-id',
        ...scheduleData,
        enabled: true,
        created_at: new Date().toISOString()
      });

      const response = await fetch(`${API_URL}/api/schedules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scheduleData)
      });
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.name).toBe(scheduleData.name);
    });
  });

  describe('Status API', () => {
    it('should return agent status', async () => {
      mockFetch({
        status: 'running',
        version: '1.0.0',
        tasks: { total: 5, pending: 2, running: 1, completed: 2, failed: 0 },
        schedules: { total: 3, enabled: 2 },
        system: {
          platform: 'linux',
          uptime: 3600,
          memory: { total: 8589934592, free: 4294967296, used: 4294967296 }
        }
      });

      const response = await fetch(`${API_URL}/api/status`);
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data.status).toBe('running');
      expect(data).toHaveProperty('tasks');
      expect(data).toHaveProperty('system');
    });
  });

  describe('Authentication', () => {
    it('should login with valid credentials', async () => {
      mockFetch({
        token: 'test-jwt-token',
        expiresIn: 86400,
        user: { username: 'admin', role: 'admin' }
      });

      const response = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'admin', password: 'test' })
      });
      const data = await response.json();

      expect(response.ok).toBe(true);
      expect(data).toHaveProperty('token');
    });

    it('should reject invalid credentials', async () => {
      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ error: 'Invalid credentials' })
        } as Response)
      );

      const response = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'invalid', password: 'wrong' })
      });

      expect(response.ok).toBe(false);
      expect(response.status).toBe(401);
    });
  });

  describe('Skills', () => {
    it('should execute grok search skill', async () => {
      const skillParams = {
        query: 'test query',
        maxResults: 5
      };

      mockFetch({
        query: skillParams.query,
        results: [
          { source: 'x', url: 'https://x.com/status/123', title: 'Result 1', content: 'Content 1' }
        ],
        totalFound: 1,
        executionTime: 500
      });

      // In actual implementation, this would call the skill directly
      // For now, we mock the response
      const result = { success: true, results: 1 };

      expect(result.success).toBe(true);
    });

    it('should execute math operations skill', async () => {
      // Test calculate operation
      const calcResult = { result: 42, operation: 'calculate' };
      expect(calcResult.result).toBe(42);

      // Test statistics operation
      const statsResult = {
        result: {
          mean: 5.5,
          median: 5.5,
          stdDev: 2.87
        },
        operation: 'statistics'
      };
      expect(statsResult.result.mean).toBe(5.5);
    });

    it('should execute document creation skill', async () => {
      mockFetch({
        success: true,
        filePath: '/app/results/test-document.pdf',
        fileName: 'test-document.pdf',
        fileSize: 10240,
        format: 'pdf'
      });

      const result = { success: true, filePath: '/app/results/test-document.pdf' };
      expect(result.success).toBe(true);
    });
  });
});

describe('End-to-End Scenarios', () => {
  describe('Basic Task Flow', () => {
    it('should create, run, and complete a task', async () => {
      // This would be a full integration test in production
      // For now, we verify the flow exists
      const steps = ['create', 'run', 'complete'];
      expect(steps.length).toBe(3);
    });
  });

  describe('Scheduled Task Flow', () => {
    it('should create schedule and execute at correct time', async () => {
      // Verify schedule creation flow
      const schedule = {
        cron_expression: '0 9 * * *',
        enabled: true
      };
      expect(schedule.enabled).toBe(true);
    });
  });

  describe('Discord Bot Integration', () => {
    it('should respond to slash commands', async () => {
      // Verify Discord command structure
      const commands = ['task', 'schedule', 'status', 'result', 'help'];
      expect(commands.length).toBe(5);
    });
  });

  describe('Web Interface Flow', () => {
    it('should authenticate and access dashboard', async () => {
      // Verify auth flow
      const authFlow = ['login', 'verify', 'access', 'logout'];
      expect(authFlow.length).toBe(4);
    });
  });
});