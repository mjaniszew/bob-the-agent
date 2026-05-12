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
type AgentToAgentAction = 'send_task' | 'check_messages' | 'send_result' | 'update_status';

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

interface UpdateStatusParams {
  action: 'update_status';
  target_agent_id: string;
  original_message_id: string;
  update_details: string;
  progress_percentage?: number;
}

type AgentToAgentParams = SendTaskParams | CheckMessagesParams | SendResultParams | UpdateStatusParams;

interface AgentToAgentResult {
  success: boolean;
  error?: string;
  [key: string]: unknown;
}

/**
 * Validate parameters based on the action.
 */
function validateParams(params: AgentToAgentParams): string | null {
  const action = params.action;
  if (!action || !['send_task', 'check_messages', 'send_result', 'update_status'].includes(action)) {
    return `Invalid action: ${action}. Must be 'send_task', 'check_messages', 'send_result', or 'update_status'`;
  }

  if (action === 'send_task') {
    const p = params as SendTaskParams;
    if (!p.target_agent_id) {
      return 'Missing required parameter: target_agent_id is required for send_task action';
    }
    if (!p.goal) {
      return 'Missing required parameter: goal is required for send_task action';
    }
    return null;
  }

  if (action === 'check_messages') {
    return null;
  }

  if (action === 'send_result') {
    const p = params as SendResultParams;
    if (!p.target_agent_id) {
      return 'Missing required parameter: target_agent_id is required for send_result action';
    }
    if (!p.original_message_id) {
      return 'Missing required parameter: original_message_id is required for send_result action';
    }
    if (!p.status) {
      return 'Missing required parameter: status is required for send_result action';
    }
    return null;
  }

  if (action === 'update_status') {
    const p = params as UpdateStatusParams;
    if (!p.target_agent_id) {
      return 'Missing required parameter: target_agent_id is required for update_status action';
    }
    if (!p.original_message_id) {
      return 'Missing required parameter: original_message_id is required for update_status action';
    }
    if (!p.update_details) {
      return 'Missing required parameter: update_details is required for update_status action';
    }
    return null;
  }

  return null;
}

/**
 * Build command line arguments for nats-helper.py based on the action.
 */
function buildArgs(params: AgentToAgentParams): string[] {
  const args: string[] = ['--action', params.action];

  switch (params.action) {
    case 'send_task': {
      const p = params as SendTaskParams;
      args.push('--target', p.target_agent_id);
      args.push('--goal', p.goal);
      if (p.context) {
        args.push('--context', p.context);
      }
      if (p.toolsets && p.toolsets.length > 0) {
        args.push('--toolsets', p.toolsets.join(','));
      }
      if (p.save_results_to) {
        args.push('--save-results-to', p.save_results_to);
      }
      break;
    }
    case 'check_messages': {
      const p = params as CheckMessagesParams;
      if (p.timeout !== undefined && p.timeout > 0) {
        args.push('--timeout', String(p.timeout));
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
    case 'update_status': {
      const p = params as UpdateStatusParams;
      args.push('--target', p.target_agent_id);
      args.push('--original-msg-id', p.original_message_id);
      args.push('--update-details', p.update_details);
      if (p.progress_percentage !== undefined) {
        args.push('--progress', String(p.progress_percentage));
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
  const validActions: AgentToAgentAction[] = ['send_task', 'check_messages', 'send_result', 'update_status'];
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