import { execFile, spawn } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';

const execFileAsync = promisify(execFile);

/** Per-target timeout in ms — mirrors the old agent-to-agent SKILL.md table. */
export const targetTimeouts: Record<string, number> = {
  simple: 15 * 60 * 1000,
  researcher: 60 * 60 * 1000,
  coder: 120 * 60 * 1000,
};

const KNOWN_TARGETS = Object.keys(targetTimeouts);

// Resolved at CALL time, not module scope — so tests can inject
// DELEGATION_LOG_DIR via process.env after import (jest imports this module
// eagerly via src/skills/index.ts before beforeEach runs).
const logDir = () => process.env.DELEGATION_LOG_DIR || '/opt/data/delegation';

function buildPrompt(goal: string, context?: string, saveResultsTo?: string): string {
  let prompt = `You are being delegated a task by the main orchestrator agent.\nGoal: ${goal}`;
  if (context) prompt += `\nContext: ${context}`;
  if (saveResultsTo) prompt += `\nSave any result files to: ${saveResultsTo}`;
  prompt += '\nExecute the task autonomously and return a concise summary of results.';
  return prompt;
}

export async function delegateProfile(params: Record<string, unknown>) {
  const action = String(params.action || '');

  if (action === 'send_task') {
    const target = String(params.target_agent_id || '');
    const goal = String(params.goal || '');
    if (!target) throw new Error('target_agent_id is required');
    if (!goal) throw new Error('goal is required');
    if (!KNOWN_TARGETS.includes(target)) {
      throw new Error(`unknown target "${target}" — valid targets: ${KNOWN_TARGETS.join(', ')}`);
    }
    const prompt = buildPrompt(goal, params.context as string | undefined,
      params.save_results_to as string | undefined);
    const { stdout, stderr } = await execFileAsync(
      'hermes',
      ['-p', target, 'chat', '--oneshot', '-q', prompt],
      { timeout: targetTimeouts[target], maxBuffer: 10 * 1024 * 1024 }
    );
    return { status: 'completed', target, summary: stdout.trim(), stderr: stderr.trim() };
  }

  if (action === 'send_task_background') {
    const target = String(params.target_agent_id || '');
    const goal = String(params.goal || '');
    if (!target) throw new Error('target_agent_id is required');
    if (!goal) throw new Error('goal is required');
    if (!KNOWN_TARGETS.includes(target)) {
      throw new Error(`unknown target "${target}" — valid targets: ${KNOWN_TARGETS.join(', ')}`);
    }
    fs.mkdirSync(logDir(), { recursive: true });
    const id = `${target}-${Date.now()}-${crypto.randomBytes(3).toString('hex')}`;
    const logFile = path.join(logDir(), `${id}.log`);
    const runningMarker = path.join(logDir(), `${id}.running`);
    const prompt = buildPrompt(goal, params.context as string | undefined,
      params.save_results_to as string | undefined);
    // Running-marker contract: the marker is written before spawn and removed by
    // the wrapper shell when the task exits, so check_task can distinguish
    // 'running' from 'finished' without inspecting the (already reaped) child.
    fs.writeFileSync(runningMarker, 'running');
    const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
    const wrapped = [
      `hermes -p ${q(target)} chat --oneshot -q ${q(prompt)}`,
      `rc=$?`,
      `rm -f ${q(runningMarker)}`,
      `exit $rc`,
    ].join('; ');
    const out = fs.openSync(logFile, 'a');
    const err = fs.openSync(logFile, 'a');
    const child = spawn('bash', ['-c', wrapped], { detached: true, stdio: ['ignore', out, err] });
    child.unref();
    fs.closeSync(out); // the child inherited the fd; the parent closes its copy
    fs.closeSync(err);
    return { status: 'started', task_id: id, target, log_file: logFile, pid: child.pid };
  }

  if (action === 'check_task') {
    const id = String(params.task_id || '');
    if (!id) throw new Error('task_id is required');
    const logFile = path.join(logDir(), `${id}.log`);
    if (!fs.existsSync(logFile)) throw new Error(`no log for task ${id}`);
    const content = fs.readFileSync(logFile, 'utf8');
    const running = fs.existsSync(path.join(logDir(), `${id}.running`));
    const tail = content.length > 4000 ? content.slice(-4000) : content;
    return { status: running ? 'running' : 'finished', task_id: id, log_tail: tail };
  }

  throw new Error(`unknown action "${action}" — valid actions: send_task, send_task_background, check_task`);
}

// Expose the timeout table as a static property on the skill function so
// consumers holding only the callable (e.g. the skills registry re-export)
// can still read the per-target timeout table.
delegateProfile.targetTimeouts = targetTimeouts;

export default delegateProfile;