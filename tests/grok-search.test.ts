/**
 * Grok Search Skill Tests
 * Tests for X.com search via xAI Responses API with x_search tool
 */

import { describe, it, expect, beforeAll, afterAll, jest } from '@jest/globals';

// Mock the problematic modules before they're imported
jest.mock('../../src/api/src/database', () => ({
  getDatabase: jest.fn(() => ({}))
}), { virtual: true });

jest.mock('../../src/api/src/websocket', () => ({
  eventBus: { emit: jest.fn(), on: jest.fn() }
}), { virtual: true });

// Mock environment variables
const originalEnv = process.env;

beforeAll(() => {
  process.env = { ...originalEnv };
});

afterAll(() => {
  process.env = originalEnv;
});

describe('Grok Search Skill', () => {
  describe('API Key Handling', () => {
    it('should use XAI_SEARCH_API_KEY when set', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-search-key';
      delete process.env.XAI_API_KEY;

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results found' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { headers?: Record<string, string> }];
      const headers = mockCall[1]?.headers;
      expect(headers?.Authorization).toContain('test-search-key');
    });

    it('should fall back to XAI_API_KEY when XAI_SEARCH_API_KEY is not set', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      delete process.env.XAI_SEARCH_API_KEY;
      process.env.XAI_API_KEY = 'fallback-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { headers?: Record<string, string> }];
      const headers = mockCall[1]?.headers;
      expect(headers?.Authorization).toContain('fallback-key');
    });

    it('should return error when neither API key is set', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      delete process.env.XAI_SEARCH_API_KEY;
      delete process.env.XAI_API_KEY;

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.success).toBe(false);
      expect(result.error).toContain('XAI_SEARCH_API_KEY');
    });
  });

  describe('Posts Search - Recent', () => {
    it('should search recent posts with query via x_search', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Here are recent posts about xAI...' }] }],
            citations: [{ url: 'https://x.com/user/status/123', title: 'Post about xAI', snippet: 'Content snippet', source: 'x' }],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'xAI' });

      expect(result.success).toBe(true);
      expect(result.action).toBe('searchPosts');
      expect(result.output).toBeDefined();
      expect(result.citations).toBeDefined();
      expect(result.citations).toHaveLength(1);
      expect(result.citations[0].url).toBe('https://x.com/user/status/123');
    });

    it('should include from_date for recent posts (last 7 days)', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const xSearchTool = body.tools[0];
      expect(xSearchTool.type).toBe('x_search');
      expect(xSearchTool.from_date).toBeDefined();
    });

    it('should support allowedHandles parameter', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'AI', allowedHandles: ['elonmusk'] });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const xSearchTool = body.tools[0];
      expect(xSearchTool.allowed_x_handles).toContain('elonmusk');
    });

    it('should support date range filtering', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test', fromDate: '2026-01-01', toDate: '2026-04-20' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const xSearchTool = body.tools[0];
      expect(xSearchTool.from_date).toBe('2026-01-01');
      expect(xSearchTool.to_date).toBe('2026-04-20');
    });

    it('should use grok-4.1-fast model by default', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      expect(body.model).toBe('grok-4.1-fast');
    });

    it('should allow custom model override', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-123',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test', model: 'grok-4.20-reasoning' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      expect(body.model).toBe('grok-4.20-reasoning');
    });
  });

  describe('Posts Search - Full Archive', () => {
    it('should search all posts without date restriction', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-456',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Historical results...' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPostsAll', query: 'historical search' });

      expect(result.success).toBe(true);
      expect(result.action).toBe('searchPostsAll');

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      // searchPostsAll should NOT auto-set from_date for recent 7 days
      const prompt = body.input[0].content;
      expect(prompt).not.toContain('last 7 days');
    });
  });

  describe('Users Search', () => {
    it('should search users via x_search', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-789',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Found users matching query...' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchUsers', query: 'developer' });

      expect(result.success).toBe(true);
      expect(result.action).toBe('searchUsers');

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const prompt = body.input[0].content;
      expect(prompt.toLowerCase()).toContain('user');
    });
  });

  describe('User Timeline', () => {
    it('should get timeline for a specific user via x_search', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-timeline',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Recent posts from user...' }] }],
            citations: [{ url: 'https://x.com/testuser/status/1', title: 'Post', snippet: 'Content', source: 'x' }],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'getUserTimeline', query: '', username: 'testuser' });

      expect(result.success).toBe(true);
      expect(result.action).toBe('getUserTimeline');

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const xSearchTool = body.tools[0];
      expect(xSearchTool.allowed_x_handles).toContain('testuser');
    });

    it('should return error when username is not provided for getUserTimeline', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      const result = await grokSearch({ action: 'getUserTimeline', query: '' });

      expect(result.success).toBe(false);
      expect(result.error).toContain('username');
    });

    it('should strip @ from username when setting allowed_x_handles', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-at',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Timeline...' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'getUserTimeline', query: '', username: '@elonmusk' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      const xSearchTool = body.tools[0];
      expect(xSearchTool.allowed_x_handles).toContain('elonmusk');
      expect(xSearchTool.allowed_x_handles).not.toContain('@elonmusk');
    });
  });

  describe('Error Handling', () => {
    it('should handle API authentication error (401)', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'invalid-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: false,
          status: 401,
          statusText: 'Unauthorized',
          json: () => Promise.resolve({ error: { detail: 'Invalid API key' } })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.success).toBe(false);
      expect(result.error).toContain('Unauthorized');
    });

    it('should handle rate limit exceeded (429)', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: false,
          status: 429,
          statusText: 'Too Many Requests',
          json: () => Promise.resolve({ error: { detail: 'Rate limit exceeded' } })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.success).toBe(false);
      expect(result.error?.toLowerCase()).toContain('rate limit');
    });

    it('should handle network errors', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.reject(new Error('Network error'))
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.success).toBe(false);
      expect(result.error).toContain('Network error');
    });

    it('should handle empty results gracefully', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-empty',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'No results found.' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 50, total_tokens: 100 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'nonexistent' });

      expect(result.success).toBe(true);
      expect(result.citations).toEqual([]);
      expect(result.usage.xSearchCalls).toBe(1);
    });

    it('should handle invalid JSON response', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.reject(new Error('Invalid JSON'))
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.success).toBe(false);
      expect(result.error).toBeDefined();
    });
  });

  describe('Usage Tracking', () => {
    it('should include executionTime in result', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-time',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result).toHaveProperty('executionTime');
      expect(typeof result.executionTime).toBe('number');
      expect(result.executionTime).toBeGreaterThanOrEqual(0);
    });

    it('should track token usage and x_search call count', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-usage',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 2 },
            usage: { input_tokens: 100, output_tokens: 200, total_tokens: 300 }
          })
        } as Response)
      );

      const result = await grokSearch({ action: 'searchPosts', query: 'test' });

      expect(result.usage.inputTokens).toBe(100);
      expect(result.usage.outputTokens).toBe(200);
      expect(result.usage.totalTokens).toBe(300);
      expect(result.usage.xSearchCalls).toBe(2);
    });
  });

  describe('Request Construction', () => {
    it('should send request to xAI Responses API endpoint', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-ep',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string];
      expect(mockCall[0]).toBe('https://api.x.ai/v1/responses');
    });

    it('should use POST method', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-method',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { method?: string }];
      expect(mockCall[1]?.method).toBe('POST');
    });

    it('should include x_search tool in request body', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-tool',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { body?: string }];
      const body = JSON.parse(mockCall[1]?.body as string);
      expect(body.tools).toBeDefined();
      expect(body.tools[0].type).toBe('x_search');
    });

    it('should include Content-Type and Authorization headers', async () => {
      const { grokSearch } = await import('../src/skills/grok-search/index');

      process.env.XAI_SEARCH_API_KEY = 'test-key';

      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: 'resp-headers',
            output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Results' }] }],
            citations: [],
            server_side_tool_usage: { x_search_calls: 1 },
            usage: { input_tokens: 50, output_tokens: 120, total_tokens: 170 }
          })
        } as Response)
      );

      await grokSearch({ action: 'searchPosts', query: 'test' });

      const mockCall = (fetch as jest.Mock).mock.calls[0] as [string, { headers?: Record<string, string> }];
      const headers = mockCall[1]?.headers;
      expect(headers?.['Content-Type']).toBe('application/json');
      expect(headers?.Authorization).toBe('Bearer test-key');
    });
  });
});