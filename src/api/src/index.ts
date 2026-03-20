/**
 * Mini Agent API Server
 * Provides REST API and WebSocket for web interface
 */

import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import { initDatabase } from './database';
import { scheduler } from './scheduler';
import { tasksRouter } from './routes/tasks';
import { schedulesRouter } from './routes/schedules';
import { statusRouter } from './routes/status';
import { authRouter } from './routes/auth';
import { diagnosticsRouter } from './routes/diagnostics';
import { setupWebSocket } from './websocket';
import { config } from './config';

const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ server });

// Middleware
app.use(helmet());
app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:81234'],
  credentials: true
}));
app.use(express.json());

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'healthy', timestamp: new Date().toISOString() });
});

// API Routes
app.use('/api/auth', authRouter);
app.use('/api/tasks', tasksRouter);
app.use('/api/schedules', schedulesRouter);
app.use('/api/status', statusRouter);
app.use('/api/diagnostics', diagnosticsRouter);

// Error handling
app.use((err: Error, req: express.Request, res: express.Response, next: express.NextFunction) => {
  console.error('Error:', err.message);
  res.status(500).json({ error: 'Internal server error' });
});

// Initialize and start
async function start() {
  try {
    // Initialize database
    await initDatabase();
    console.log('Database initialized');

    // Load scheduled tasks
    scheduler.loadSchedules();
    console.log('Scheduler initialized');

    // Setup WebSocket
    setupWebSocket(wss);
    console.log('WebSocket server ready');

    // Start server
    const port = process.env.PORT ? parseInt(process.env.PORT) : 3001;
    server.listen(port, '0.0.0.0', () => {
      console.log(`API server running on port ${port}`);
    });
  } catch (error) {
    console.error('Failed to start server:', error);
    process.exit(1);
  }
}

start();