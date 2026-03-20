/**
 * Task Scheduler Service
 * Manages scheduled task execution using APScheduler-like functionality
 */

import { getDatabase } from './database';
import { eventBus } from './websocket';
import { randomUUID } from 'crypto';

interface ScheduledTask {
  id: string;
  name: string;
  description?: string;
  cron_expression: string;
  task_template: string;
  enabled: boolean;
  last_run?: Date;
  next_run?: Date;
}

interface ScheduledJob {
  id: string;
  scheduleId: string;
  nextRun: Date;
  timer?: NodeJS.Timeout;
}

// Simple cron parser for common expressions
function parseCron(expression: string): { minute: number[]; hour: number[]; dayOfMonth: number[]; month: number[]; dayOfWeek: number[] } {
  const parts = expression.trim().split(/\s+/);

  if (parts.length < 5 || parts.length > 6) {
    throw new Error('Invalid cron expression. Must have 5 or 6 fields.');
  }

  const parse = (part: string, min: number, max: number): number[] => {
    if (part === '*') {
      return Array.from({ length: max - min + 1 }, (_, i) => i + min);
    }

    if (part.includes('/')) {
      const [base, step] = part.split('/');
      const stepNum = parseInt(step);
      const range = base === '*' ? Array.from({ length: max - min + 1 }, (_, i) => i + min) : parse(base, min, max);
      return range.filter((_, i) => i % stepNum === 0);
    }

    if (part.includes('-')) {
      const [start, end] = part.split('-').map(Number);
      return Array.from({ length: end - start + 1 }, (_, i) => i + start);
    }

    if (part.includes(',')) {
      return part.split(',').map(Number);
    }

    return [parseInt(part)];
  };

  // Handle 5 or 6 field cron (with or without seconds)
  const offset = parts.length === 6 ? 1 : 0;

  return {
    minute: parse(parts[offset], 0, 59),
    hour: parse(parts[offset + 1], 0, 23),
    dayOfMonth: parse(parts[offset + 2], 1, 31),
    month: parse(parts[offset + 3], 1, 12),
    dayOfWeek: parse(parts[offset + 4], 0, 6)
  };
}

// Calculate next run time from cron expression
function getNextRun(cronExpr: string, from: Date = new Date()): Date {
  const parsed = parseCron(cronExpr);
  const next = new Date(from);

  // Round up to next minute
  next.setSeconds(0);
  next.setMilliseconds(0);
  next.setMinutes(next.getMinutes() + 1);

  // Find next matching time
  let iterations = 0;
  const maxIterations = 366 * 24 * 60; // Max 1 year

  while (iterations < maxIterations) {
    const minute = next.getMinutes();
    const hour = next.getHours();
    const dayOfMonth = next.getDate();
    const month = next.getMonth() + 1;
    const dayOfWeek = next.getDay();

    if (
      parsed.minute.includes(minute) &&
      parsed.hour.includes(hour) &&
      parsed.dayOfMonth.includes(dayOfMonth) &&
      parsed.month.includes(month) &&
      parsed.dayOfWeek.includes(dayOfWeek)
    ) {
      return next;
    }

    next.setMinutes(next.getMinutes() + 1);
    iterations++;
  }

  throw new Error('Could not find next run time within reasonable bounds');
}

class TaskScheduler {
  private jobs: Map<string, ScheduledJob> = new Map();
  private checkInterval?: NodeJS.Timeout;

  constructor() {
    // Check for scheduled tasks every minute
    this.checkInterval = setInterval(() => this.checkSchedules(), 60000);
  }

  /**
   * Load all enabled schedules from database and schedule them
   */
  loadSchedules(): void {
    const db = getDatabase();
    const schedules = db.prepare('SELECT * FROM schedules WHERE enabled = 1').all() as ScheduledTask[];

    for (const schedule of schedules) {
      this.scheduleJob(schedule);
    }

    console.log(`Loaded ${schedules.length} scheduled jobs`);
  }

  /**
   * Schedule a new job
   */
  scheduleJob(schedule: ScheduledTask): void {
    // Remove existing job if any
    this.unscheduleJob(schedule.id);

    const nextRun = getNextRun(schedule.cron_expression);
    const job: ScheduledJob = {
      id: randomUUID(),
      scheduleId: schedule.id,
      nextRun
    };

    this.jobs.set(schedule.id, job);
    this.updateNextRun(schedule.id, nextRun);

    console.log(`Scheduled job ${schedule.id} to run at ${nextRun.toISOString()}`);
  }

  /**
   * Unschedule a job
   */
  unscheduleJob(scheduleId: string): void {
    const job = this.jobs.get(scheduleId);
    if (job?.timer) {
      clearTimeout(job.timer);
    }
    this.jobs.delete(scheduleId);
  }

  /**
   * Update next_run in database
   */
  private updateNextRun(scheduleId: string, nextRun: Date): void {
    const db = getDatabase();
    db.prepare('UPDATE schedules SET next_run = ? WHERE id = ?').run(nextRun.toISOString(), scheduleId);
  }

  /**
   * Check and execute due schedules
   */
  private checkSchedules(): void {
    const now = new Date();

    for (const [scheduleId, job] of this.jobs) {
      if (job.nextRun <= now) {
        this.executeSchedule(scheduleId);
      }
    }
  }

  /**
   * Execute a scheduled task
   */
  private async executeSchedule(scheduleId: string): Promise<void> {
    const db = getDatabase();
    const schedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(scheduleId) as ScheduledTask;

    if (!schedule || !schedule.enabled) {
      this.unscheduleJob(scheduleId);
      return;
    }

    console.log(`Executing scheduled task: ${schedule.name}`);

    try {
      // Create task from template
      const taskTemplate = JSON.parse(schedule.task_template);
      const taskId = randomUUID();

      db.prepare(`
        INSERT INTO tasks (id, name, description, skill, parameters, scheduled_for)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        taskId,
        taskTemplate.name || schedule.name,
        taskTemplate.description || schedule.description,
        taskTemplate.skill,
        JSON.stringify(taskTemplate.parameters || {}),
        new Date().toISOString()
      );

      // Add to history
      db.prepare('INSERT INTO task_history (task_id, status, message) VALUES (?, ?, ?)').run(
        taskId,
        'created',
        `Created from schedule ${scheduleId}`
      );

      // Emit event for execution
      eventBus.emit('task:created', { taskId, scheduleId });
      eventBus.emit('task:run', { taskId, fromSchedule: scheduleId });

      // Update last_run
      db.prepare('UPDATE schedules SET last_run = CURRENT_TIMESTAMP WHERE id = ?').run(scheduleId);

      // Schedule next run
      const nextRun = getNextRun(schedule.cron_expression);
      this.updateNextRun(scheduleId, nextRun);

      const job = this.jobs.get(scheduleId);
      if (job) {
        job.nextRun = nextRun;
      }

    } catch (error) {
      console.error(`Error executing schedule ${scheduleId}:`, error);
    }
  }

  /**
   * Shutdown scheduler
   */
  shutdown(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
    }

    for (const [_, job] of this.jobs) {
      if (job.timer) {
        clearTimeout(job.timer);
      }
    }

    this.jobs.clear();
    console.log('Scheduler shutdown complete');
  }
}

// Export singleton instance
export const scheduler = new TaskScheduler();