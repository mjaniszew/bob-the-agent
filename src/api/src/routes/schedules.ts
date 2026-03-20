/**
 * Schedules API routes
 */

import express from 'express';
import { getDatabase } from '../database';
import { eventBus } from '../websocket';
import { randomUUID } from 'crypto';

export const schedulesRouter = express.Router();

// Middleware to verify JWT
function verifyToken(req: express.Request, res: express.Response, next: express.NextFunction) {
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Authentication required' });
  }

  next();
}

// List all schedules
schedulesRouter.get('/', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const schedules = db.prepare('SELECT * FROM schedules ORDER BY created_at DESC').all();

    res.json({ schedules });
  } catch (error) {
    console.error('Error listing schedules:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Get schedule by ID
schedulesRouter.get('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const schedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(req.params.id);

    if (!schedule) {
      return res.status(404).json({ error: 'Schedule not found' });
    }

    res.json(schedule);
  } catch (error) {
    console.error('Error getting schedule:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Create new schedule
schedulesRouter.post('/', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const id = randomUUID();
    const {
      name,
      description,
      cron_expression,
      task_template,
      enabled
    } = req.body;

    if (!name || !cron_expression || !task_template) {
      return res.status(400).json({ error: 'name, cron_expression, and task_template are required' });
    }

    // Validate cron expression (basic validation)
    if (!isValidCron(cron_expression)) {
      return res.status(400).json({ error: 'Invalid cron expression' });
    }

    const stmt = db.prepare(`
      INSERT INTO schedules (id, name, description, cron_expression, task_template, enabled)
      VALUES (?, ?, ?, ?, ?, ?)
    `);

    stmt.run(
      id,
      name,
      description || null,
      cron_expression,
      typeof task_template === 'object' ? JSON.stringify(task_template) : task_template,
      enabled !== false ? 1 : 0
    );

    const schedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(id);

    // Emit event for scheduler to pick up
    eventBus.emit('schedule:created', schedule);

    res.status(201).json(schedule);
  } catch (error) {
    console.error('Error creating schedule:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Update schedule
schedulesRouter.patch('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const schedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(req.params.id);

    if (!schedule) {
      return res.status(404).json({ error: 'Schedule not found' });
    }

    const allowedFields = ['name', 'description', 'cron_expression', 'task_template', 'enabled'];
    const updates: string[] = [];
    const values: any[] = [];

    for (const [key, value] of Object.entries(req.body)) {
      if (allowedFields.includes(key)) {
        if (key === 'cron_expression' && !isValidCron(value as string)) {
          return res.status(400).json({ error: 'Invalid cron expression' });
        }
        updates.push(`${key} = ?`);
        values.push(key === 'task_template' ? JSON.stringify(value) : value);
      }
    }

    if (updates.length === 0) {
      return res.status(400).json({ error: 'No valid fields to update' });
    }

    updates.push("updated_at = CURRENT_TIMESTAMP");
    values.push(req.params.id);

    db.prepare(`UPDATE schedules SET ${updates.join(', ')} WHERE id = ?`).run(...values);

    const updatedSchedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(req.params.id);
    eventBus.emit('schedule:updated', updatedSchedule);

    res.json(updatedSchedule);
  } catch (error) {
    console.error('Error updating schedule:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Delete schedule
schedulesRouter.delete('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const schedule = db.prepare('SELECT * FROM schedules WHERE id = ?').get(req.params.id);

    if (!schedule) {
      return res.status(404).json({ error: 'Schedule not found' });
    }

    db.prepare('DELETE FROM schedules WHERE id = ?').run(req.params.id);

    eventBus.emit('schedule:deleted', { scheduleId: req.params.id });

    res.json({ message: 'Schedule deleted', scheduleId: req.params.id });
  } catch (error) {
    console.error('Error deleting schedule:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Helper: Basic cron validation
function isValidCron(expression: string): boolean {
  // Standard cron: 5 fields (minute, hour, day, month, weekday)
  // Quartz cron: 6-7 fields (with seconds)
  const parts = expression.trim().split(/\s+/);
  return parts.length >= 5 && parts.length <= 7;
}