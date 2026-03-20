/**
 * Tasks API routes
 */

import express from 'express';
import { getDatabase } from '../database';
import { eventBus } from '../websocket';
import { randomUUID } from 'crypto';

export const tasksRouter = express.Router();

// Middleware to verify JWT
function verifyToken(req: express.Request, res: express.Response, next: express.NextFunction) {
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Authentication required' });
  }

  // In production, verify JWT properly
  // For now, just check if token exists
  const token = authHeader.substring(7);
  if (!token) {
    return res.status(401).json({ error: 'Invalid token' });
  }

  next();
}

// List all tasks
tasksRouter.get('/', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const status = req.query.status as string;
    const limit = parseInt(req.query.limit as string) || 100;
    const offset = parseInt(req.query.offset as string) || 0;

    let query = 'SELECT * FROM tasks';
    const params: any[] = [];

    if (status) {
      query += ' WHERE status = ?';
      params.push(status);
    }

    query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const tasks = db.prepare(query).all(...params);
    const total = db.prepare('SELECT COUNT(*) as count FROM tasks').get() as { count: number };

    res.json({
      tasks,
      total: total.count,
      limit,
      offset
    });
  } catch (error) {
    console.error('Error listing tasks:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Get task by ID
tasksRouter.get('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);

    if (!task) {
      return res.status(404).json({ error: 'Task not found' });
    }

    // Get history
    const history = db.prepare('SELECT * FROM task_history WHERE task_id = ? ORDER BY timestamp DESC').all(req.params.id);

    res.json({ ...task, history } as object);
  } catch (error) {
    console.error('Error getting task:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Create new task
tasksRouter.post('/', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const id = randomUUID();
    const {
      name,
      description,
      skill,
      parameters,
      scheduled_for,
      priority,
      notification_enabled,
      notification_type
    } = req.body;

    if (!name) {
      return res.status(400).json({ error: 'Task name is required' });
    }

    const stmt = db.prepare(`
      INSERT INTO tasks (id, name, description, skill, parameters, scheduled_for, priority, notification_enabled, notification_type)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    stmt.run(
      id,
      name,
      description || null,
      skill || null,
      parameters ? JSON.stringify(parameters) : null,
      scheduled_for || null,
      priority || 0,
      notification_enabled ? 1 : 0,
      notification_type || null
    );

    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(id);

    // Emit event
    eventBus.emit('task:created', task);

    res.status(201).json(task);
  } catch (error) {
    console.error('Error creating task:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Run task immediately
tasksRouter.post('/:id/run', verifyToken, async (req, res) => {
  try {
    const db = getDatabase();
    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id) as any;

    if (!task) {
      return res.status(404).json({ error: 'Task not found' });
    }

    // Update status to running
    db.prepare("UPDATE tasks SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?").run(req.params.id);

    // Add to history
    db.prepare('INSERT INTO task_history (task_id, status, message) VALUES (?, ?, ?)').run(req.params.id, 'running', 'Task started');

    // Emit event for task execution
    eventBus.emit('task:run', { taskId: req.params.id, task });

    res.json({ message: 'Task queued for execution', taskId: req.params.id });
  } catch (error) {
    console.error('Error running task:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Update task
tasksRouter.patch('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);

    if (!task) {
      return res.status(404).json({ error: 'Task not found' });
    }

    const allowedFields = ['name', 'description', 'skill', 'parameters', 'scheduled_for', 'priority', 'notification_enabled', 'notification_type'];
    const updates: string[] = [];
    const values: any[] = [];

    for (const [key, value] of Object.entries(req.body)) {
      if (allowedFields.includes(key)) {
        updates.push(`${key} = ?`);
        values.push(key === 'parameters' ? JSON.stringify(value) : value);
      }
    }

    if (updates.length === 0) {
      return res.status(400).json({ error: 'No valid fields to update' });
    }

    updates.push("updated_at = CURRENT_TIMESTAMP");
    values.push(req.params.id);

    db.prepare(`UPDATE tasks SET ${updates.join(', ')} WHERE id = ?`).run(...values);

    const updatedTask = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);
    eventBus.emit('task:updated', updatedTask);

    res.json(updatedTask);
  } catch (error) {
    console.error('Error updating task:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Delete task
tasksRouter.delete('/:id', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id);

    if (!task) {
      return res.status(404).json({ error: 'Task not found' });
    }

    // Delete history first
    db.prepare('DELETE FROM task_history WHERE task_id = ?').run(req.params.id);
    // Delete task
    db.prepare('DELETE FROM tasks WHERE id = ?').run(req.params.id);

    eventBus.emit('task:deleted', { taskId: req.params.id });

    res.json({ message: 'Task deleted', taskId: req.params.id });
  } catch (error) {
    console.error('Error deleting task:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Download task result
tasksRouter.get('/:id/result', verifyToken, (req, res) => {
  try {
    const db = getDatabase();
    const task = db.prepare('SELECT * FROM tasks WHERE id = ?').get(req.params.id) as any;

    if (!task) {
      return res.status(404).json({ error: 'Task not found' });
    }

    if (!task.result) {
      return res.status(404).json({ error: 'No result available' });
    }

    res.json({ result: task.result });
  } catch (error) {
    console.error('Error getting task result:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});