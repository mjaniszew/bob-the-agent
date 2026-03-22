/**
 * Scheduling Skill Implementation
 * Manages scheduled task execution
 */

import { getDatabase } from '../../api/src/database';
import { eventBus } from '../../api/src/websocket';
import { randomUUID } from 'crypto';

interface ScheduleParams {
  mode: 'cron' | 'interval' | 'once';
  schedule: {
    expression?: string;  // For cron mode
    interval?: number;    // For interval mode (seconds)
    datetime?: string;    // For once mode
  };
  task: {
    name: string;
    description?: string;
    skill: string;
    parameters: Record<string, any>;
  };
}

interface ScheduleResult {
  success: boolean;
  scheduleId: string;
  nextExecution?: string;
  message: string;
}

// Create a cron-based schedule
function createCronSchedule(expression: string, task: ScheduleParams['task']): ScheduleResult {
  const db = getDatabase();
  const id = randomUUID();

  // Validate cron expression (basic validation)
  const parts = expression.trim().split(/\s+/);
  if (parts.length < 5 || parts.length > 6) {
    return {
      success: false,
      scheduleId: '',
      message: 'Invalid cron expression. Must have 5 or 6 fields.'
    };
  }

  // Calculate next run time (simplified)
  const nextRun = calculateNextRun(expression);

  try {
    const stmt = db.prepare(`
      INSERT INTO schedules (id, name, description, cron_expression, task_template, enabled)
      VALUES (?, ?, ?, ?, ?, 1)
    `);

    stmt.run(
      id,
      task.name,
      task.description || '',
      expression,
      JSON.stringify(task)
    );

    return {
      success: true,
      scheduleId: id,
      nextExecution: nextRun?.toISOString(),
      message: `Cron schedule created with ID ${id}`
    };
  } catch (error) {
    return {
      success: false,
      scheduleId: '',
      message: `Failed to create schedule: ${error instanceof Error ? error.message : 'Unknown error'}`
    };
  }
}

// Create an interval-based schedule
function createIntervalSchedule(intervalSeconds: number, task: ScheduleParams['task']): ScheduleResult {
  const db = getDatabase();
  const id = randomUUID();

  if (intervalSeconds < 60) {
    return {
      success: false,
      scheduleId: '',
      message: 'Minimum interval is 60 seconds'
    };
  }

  // Convert interval to cron expression
  // For simplicity, we'll store interval-based schedules differently
  // This is a placeholder - in production, you'd use a proper job scheduler

  const nextRun = new Date(Date.now() + intervalSeconds * 1000);

  try {
    // Store as a special interval format in cron_expression field
    const intervalCron = `INTERVAL_${intervalSeconds}`;

    const stmt = db.prepare(`
      INSERT INTO schedules (id, name, description, cron_expression, task_template, enabled)
      VALUES (?, ?, ?, ?, ?, 1)
    `);

    stmt.run(
      id,
      task.name,
      task.description || '',
      intervalCron,
      JSON.stringify({ ...task, interval: intervalSeconds })
    );

    return {
      success: true,
      scheduleId: id,
      nextExecution: nextRun.toISOString(),
      message: `Interval schedule created with ID ${id} (every ${intervalSeconds} seconds)`
    };
  } catch (error) {
    return {
      success: false,
      scheduleId: '',
      message: `Failed to create schedule: ${error instanceof Error ? error.message : 'Unknown error'}`
    };
  }
}

// Create a one-time schedule
function createOnceSchedule(datetime: string, task: ScheduleParams['task']): ScheduleResult {
  const db = getDatabase();
  const id = randomUUID();

  const scheduledDate = new Date(datetime);
  if (isNaN(scheduledDate.getTime())) {
    return {
      success: false,
      scheduleId: '',
      message: 'Invalid datetime format'
    };
  }

  if (scheduledDate <= new Date()) {
    return {
      success: false,
      scheduleId: '',
      message: 'Scheduled time must be in the future'
    };
  }

  try {
    // Store as a special one-time format
    const onceCron = `ONCE_${datetime}`;

    const stmt = db.prepare(`
      INSERT INTO schedules (id, name, description, cron_expression, task_template, enabled)
      VALUES (?, ?, ?, ?, ?, 1)
    `);

    stmt.run(
      id,
      task.name,
      task.description || '',
      onceCron,
      JSON.stringify(task)
    );

    return {
      success: true,
      scheduleId: id,
      nextExecution: scheduledDate.toISOString(),
      message: `One-time schedule created with ID ${id} for ${scheduledDate.toISOString()}`
    };
  } catch (error) {
    return {
      success: false,
      scheduleId: '',
      message: `Failed to create schedule: ${error instanceof Error ? error.message : 'Unknown error'}`
    };
  }
}

// List all schedules
function listSchedules(): any[] {
  const db = getDatabase();
  return db.prepare('SELECT * FROM schedules ORDER BY created_at DESC').all();
}

// Delete a schedule
function deleteSchedule(id: string): { success: boolean; message: string } {
  const db = getDatabase();

  const existing = db.prepare('SELECT id FROM schedules WHERE id = ?').get(id);
  if (!existing) {
    return { success: false, message: 'Schedule not found' };
  }

  try {
    db.prepare('DELETE FROM schedules WHERE id = ?').run(id);
    return { success: true, message: `Schedule ${id} deleted` };
  } catch (error) {
    return {
      success: false,
      message: `Failed to delete schedule: ${error instanceof Error ? error.message : 'Unknown error'}`
    };
  }
}

// Calculate next run time (simplified)
function calculateNextRun(cronExpression: string): Date | null {
  // This is a simplified implementation
  // In production, use a proper cron parser like cron-parser

  const now = new Date();
  now.setMinutes(now.getMinutes() + 1);
  now.setSeconds(0);
  now.setMilliseconds(0);

  return now;
}

// Main function
export function scheduling(params: ScheduleParams): ScheduleResult {
  const { mode, schedule, task } = params;

  switch (mode) {
    case 'cron':
      if (!schedule.expression) {
        return { success: false, scheduleId: '', message: 'Cron expression required for cron mode' };
      }
      return createCronSchedule(schedule.expression, task);

    case 'interval':
      if (!schedule.interval) {
        return { success: false, scheduleId: '', message: 'Interval required for interval mode' };
      }
      return createIntervalSchedule(schedule.interval, task);

    case 'once':
      if (!schedule.datetime) {
        return { success: false, scheduleId: '', message: 'Datetime required for once mode' };
      }
      return createOnceSchedule(schedule.datetime, task);

    default:
      return { success: false, scheduleId: '', message: `Unknown mode: ${mode}` };
  }
}

// Export additional functions
export { listSchedules, deleteSchedule };

export default scheduling;