/**
 * Agent-to-Agent Communication Skill
 *
 * Enables inter-agent messaging via NATS. Agents can send tasks to
 * other agents, check for incoming messages, and send task results back.
 *
 * Uses nats-helper.py Python script for NATS operations.
 */

import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';

const execFileAsync = promisify(execFile);

// Path to the Python NATS helper script
const NATS_HELPER = process.env.NATS_HELPER_PATH ||
  path.join('/app/skills/agent-to-agent/scripts/nats-helper.py');

// Valid actions
type AgentToAgentAction = 'send_task' | 'check_messages' | 'send_result';

// Parameter types
interface SendTaskParams {
  action: 'send_task';
  target_agent_id: string;
  goal: string;
  context?: string;
  toolsets?: string[];
  save_results_to?: string;
}

interface CheckMessagesParams {
  action: 'check_messages';
  timeout?: number;
}

interface SendResultParams {
  action: 'send_result';
  target_agent_id: string;
  original_message_id: string;
  status: 'completed' | 'failed';
  summary?: string;
  result_path?: string;
  error?: string;
  duration?: number;
}

type AgentToAgentParams = SendTaskParams | CheckMessagesParams | SendResultParams;

interface AgentToAgentResult {
  success: boolean;
  error?: string;
  [key: string]: unknown;
}

/**
 * Validate parameters based on the action.
 */
function validateParams(params: AgentToAgentParams): string | null {
  switch (params.action) {
    case 'send_task': {
      if (!params.target_agent_id) {
        return 'Missing required parameter: target_agent_id is required for send_task action';
      }
      if (!params.goal) {
        return 'Missing required parameter: goal is required for send_task action';
      }
      return null;
    }
    case 'check_messages': {
      return null; // No required params for check_messages
    }
    case 'send_result': {
      if (!params.target_agent_id) {
        return 'Missing required parameter: target_agent_id is required for send_result action';
      }
      if (!(params as SendResultParams).original_message_id) {
        return 'Missing required parameter: original_message_id is required for send_result action';
      }
      if (!(params as SendResultParams).status) {
        return 'Missing required parameter: status is required for send_result action';
      }
      return null;
    }
    default:
      return `Invalid action: ${params.action}. Must be 'send_task', 'check_messages', or 'send_result'`;
  }
}

/**
 * Build command line arguments for nats-helper.py based on the action.
 */
function buildArgs(params: AgentToAgentParams): string[] {
  const args: string[] = ['--action', params.action];

  switch (params.action) {
    case 'send_task': {
      args.push('--target', params.target_agent_id);
      args.push('--goal', params.goal);
      if (params.context) {
        args.push('--context', params.context);
      }
      if (params.toolsets && params.toolsets.length > 0) {
        args.push('--toolsets', params.toolsets.join(','));
      }
      if (params.save_results_to) {
        args.push('--save-results-to', params.save_results_to);
      }
      break;
    }
    case 'check_messages': {
      if (params.timeout !== undefined && params.timeout > 0) {
        args.push('--timeout', String(params.timeout));
      }
      break;
    }
    case 'send_result': {
      const resultParams = params as SendResultParams;
      args.push('--target', resultParams.target_agent_id);
      args.push('--original-msg-id', resultParams.original_message_id);
      args.push('--status', resultParams.status);
      if (resultParams.summary) {
        args.push('--summary', resultParams.summary);
      }
      if (resultParams.result_path) {
        args.push('--result-path', resultParams.result_path);
      }
      if (resultParams.error) {
        args.push('--error', resultParams.error);
      }
      if (resultParams.duration !== undefined) {
        args.push('--duration', String(resultParams.duration));
      }
      break;
    }
  }

  return args;
}

/**
 * Agent-to-Agent Communication Skill
 *
 * Sends messages to NATS for inter-agent task delegation,
 * checks for incoming messages, and sends task results.
 */
export async function agentToAgent(params: AgentToAgentParams): Promise<AgentToAgentResult> {
  // Validate action
  const validActions: AgentToAgentAction[] = ['send_task', 'check_messages', 'send_result'];
  if (!validActions.includes(params.action)) {
    return {
      success: false,
      error: `Invalid action: ${params.action}. Must be one of: ${validActions.join(', ')}`,
    };
  }

  // Validate required params
  const validationError = validateParams(params);
  if (validationError) {
    return {
      success: false,
      error: validationError,
    };
  }

  // Build command line arguments
  const args = buildArgs(params);

  try {
    // Execute nats-helper.py
    const { stdout, stderr } = await execFileAsync('python3', [NATS_HELPER, ...args], {
      timeout: 30000, // 30 second timeout
      env: {
        ...process.env,
        // Ensure NATS_URL and AGENT_NAME are available
      },
    });

    // Parse JSON output from the helper
    const result = JSON.parse(stdout.trim());
    return result;
  } catch (error: unknown) {
    // If the error has stdout, try to parse it (helper may have output errors in JSON format)
    const execError = error as { stdout?: string; stderr?: string; message?: string };
    if (execError.stdout) {
      try {
        const result = JSON.parse(execError.stdout.trim());
        return result;
      } catch {
        // Fall through to error handling
      }
    }

    return {
      success: false,
      error: `NATS helper execution failed: ${execError.message || String(error)}`,
      stderr: execError.stderr || undefined,
    };
  }
}

export default agentToAgent;