/**
 * Status API routes
 */

import express from 'express';
import { getDatabase } from '../database';
import os from 'os';

export const statusRouter = express.Router();

// Get agent status
statusRouter.get('/', (req, res) => {
  try {
    const db = getDatabase();

    // Get task statistics
    const taskStats = db.prepare(`
      SELECT
        COUNT(*) as total,
        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
        SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
      FROM tasks
    `).get() as any;

    // Get schedule statistics
    const scheduleStats = db.prepare(`
      SELECT
        COUNT(*) as total,
        SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) as enabled
      FROM schedules
    `).get() as any;

    // System information
    const systemInfo = {
      platform: os.platform(),
      arch: os.arch(),
      hostname: os.hostname(),
      uptime: process.uptime(),
      memory: {
        total: os.totalmem(),
        free: os.freemem(),
        used: os.totalmem() - os.freemem()
      },
      cpu: {
        model: os.cpus()[0]?.model || 'unknown',
        cores: os.cpus().length,
        load: os.loadavg()
      }
    };

    res.json({
      status: 'running',
      version: process.env.npm_package_version || '1.0.0',
      timestamp: new Date().toISOString(),
      tasks: {
        total: taskStats.total || 0,
        pending: taskStats.pending || 0,
        running: taskStats.running || 0,
        completed: taskStats.completed || 0,
        failed: taskStats.failed || 0
      },
      schedules: {
        total: scheduleStats.total || 0,
        enabled: scheduleStats.enabled || 0
      },
      system: systemInfo
    });
  } catch (error) {
    console.error('Error getting status:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});