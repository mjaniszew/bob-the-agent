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

// Staleness grace on top of the target timeout before an orphaned .running
// marker (container restart mid-task, killed wrapper) is reported as failed
// rather than running (Task 5 quality review, issue 2).
const STALE_GRACE_MS = 5 * 60 * 1000;
// Delegation logs/markers older than this are swept (best-effort) on each
// background start — bounds growth of the log dir on a 24/7 container (issue 8).
const LOG_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
// task_id format: <target>-<epoch-ms>-<6 hex chars> — validated so a hostile
// task_id can't traverse paths in check_task (issue 4).
const TASK_ID_RE = /^[a-z]+-\d+-[a-f0-9]{6}$/;

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

// Shared by send_task and send_task_background (identical contract).
function requireTargetAndGoal(params: Record<string, unknown>): { target: string; goal: string } {
  const target = String(params.target_agent_id || '');
  const goal = String(params.goal || '');
  if (!target) throw new Error('target_agent_id is required');
  if (!goal) throw new Error('goal is required');
  if (!KNOWN_TARGETS.includes(target)) {
    throw new Error(`unknown target "${target}" — valid targets: ${KNOWN_TARGETS.join(', ')}`);
  }
  return { target, goal };
}

/** Read only the last few KB of the log — never load a whole verbose log (issue 7). */
function readTail(logFile: string, maxChars = 4000): string {
  const size = fs.statSync(logFile).size;
  const readBytes = Math.min(size, maxChars * 4);
  const fd = fs.openSync(logFile, 'r');
  try {
    const buf = Buffer.alloc(readBytes);
    fs.readSync(fd, buf, 0, readBytes, Math.max(0, size - readBytes));
    const text = buf.toString('utf8');
    return text.length > maxChars ? text.slice(-maxChars) : text;
  } finally {
    fs.closeSync(fd);
  }
}

/** Best-effort age-based sweep of the delegation log dir (issue 8). */
function sweepOldLogs(): void {
  try {
    const cutoff = Date.now() - LOG_RETENTION_MS;
    for (const f of fs.readdirSync(logDir())) {
      const p = path.join(logDir(), f);
      try {
        if (fs.statSync(p).mtimeMs < cutoff) fs.rmSync(p, { force: true });
      } catch { /* raced deletion — best-effort */ }
    }
  } catch { /* sweep never blocks task start */ }
}

function checkTask(id: string): { status: string; task_id: string; log_tail: string; exit_code?: number; stale?: boolean } {
  if (!TASK_ID_RE.test(id)) throw new Error(`invalid task_id "${id}"`);
  const logFile = path.join(logDir(), `${id}.log`);
  if (!fs.existsSync(logFile)) throw new Error(`no log for task ${id}`);
  const tail = readTail(logFile);
  const marker = path.join(logDir(), `${id}.running`);
  const running = fs.existsSync(marker);

  if (!running) {
    // The wrapper's final act is appending "EXIT:<rc>" as the last log line, so
    // success and failure are distinguishable once the marker is gone (issue 5).
    // A missing marker with no sentinel means the wrapper died unexpectedly —
    // report finished with unknown outcome rather than polling forever.
    const lines = tail.trimEnd().split('\n');
    const m = (lines[lines.length - 1] || '').match(/^EXIT:(\d+)$/);
    if (m) {
      const rc = Number(m[1]);
      return rc === 0
        ? { status: 'finished', task_id: id, log_tail: tail, exit_code: 0 }
        : { status: 'failed', task_id: id, log_tail: tail, exit_code: rc };
    }
    return { status: 'finished', task_id: id, log_tail: tail };
  }

  // Staleness TTL (issue 2): if the marker outlives the target's timeout +
  // grace, the process behind it is gone (container restarted mid-task) —
  // report failed instead of "running" forever. Plain/legacy marker content
  // (unparseable JSON) is treated as genuinely running.
  try {
    const info = JSON.parse(fs.readFileSync(marker, 'utf8'));
    const target = String(info.target || id.split('-')[0]);
    const limit = (targetTimeouts[target] || 0) + STALE_GRACE_MS;
    const startedAt = Number(info.startedAt || 0);
    if (startedAt && limit && Date.now() - startedAt > limit) {
      return { status: 'failed', task_id: id, log_tail: tail, stale: true };
    }
  } catch { /* plain/legacy marker — assume genuinely running */ }
  return { status: 'running', task_id: id, log_tail: tail };
}

export async function delegateProfile(params: Record<string, unknown>) {
  const action = String(params.action || '');

  if (action === 'send_task') {
    const { target, goal } = requireTargetAndGoal(params);
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
    const { target, goal } = requireTargetAndGoal(params);
    fs.mkdirSync(logDir(), { recursive: true });
    sweepOldLogs();
    const id = `${target}-${Date.now()}-${crypto.randomBytes(3).toString('hex')}`;
    const logFile = path.join(logDir(), `${id}.log`);
    const runningMarker = path.join(logDir(), `${id}.running`);
    const prompt = buildPrompt(goal, params.context as string | undefined,
      params.save_results_to as string | undefined);
    // Running-marker contract: the marker is written before spawn and removed by
    // the wrapper shell when the task exits, so check_task can distinguish
    // 'running' from 'finished' without inspecting the (already reaped) child.
    // The marker carries {target, startedAt} for the staleness TTL, and the
    // wrapper enforces the target's timeout via coreutils `timeout` (issue 3)
    // and appends an "EXIT:$rc" sentinel line (issue 5).
    fs.writeFileSync(runningMarker, JSON.stringify({ target, startedAt: Date.now() }));
    const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
    const wrapped = [
      `timeout ${Math.ceil(targetTimeouts[target] / 1000)} hermes -p ${q(target)} chat --oneshot -q ${q(prompt)}`,
      `rc=$?`,
      `echo "EXIT:$rc" >> ${q(logFile)}`,
      `rm -f ${q(runningMarker)}`,
      `exit $rc`,
    ].join('; ');
    const out = fs.openSync(logFile, 'a');
    const err = fs.openSync(logFile, 'a');
    const child = spawn('bash', ['-c', wrapped], { detached: true, stdio: ['ignore', out, err] });
    // Spawn failures (bash missing, EMFILE, ...) are async and must not orphan
    // the marker or crash skill-runner (issue 6) — clean up best-effort and
    // append an exit sentinel so check_task reports the task as failed.
    child.on('error', (e) => {
      try { fs.rmSync(runningMarker, { force: true }); } catch { /* best-effort */ }
      try { fs.appendFileSync(logFile, `SPAWN-ERROR:${e.message}\nEXIT:127\n`); } catch { /* best-effort */ }
    });
    child.unref();
    fs.closeSync(out); // the child inherited the fd; the parent closes its copy
    fs.closeSync(err);
    return { status: 'started', task_id: id, target, log_file: logFile, pid: child.pid };
  }

  if (action === 'check_task') {
    const id = String(params.task_id || '');
    if (!id) throw new Error('task_id is required');
    return checkTask(id);
  }

  throw new Error(`unknown action "${action}" — valid actions: send_task, send_task_background, check_task`);
}

// Expose the timeout table as a static property on the skill function so
// consumers holding only the callable (e.g. the skills registry re-export)
// can still read the per-target timeout table.
delegateProfile.targetTimeouts = targetTimeouts;

export default delegateProfile;