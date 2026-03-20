/**
 * WebSocket server setup for real-time updates
 */

import { WebSocketServer, WebSocket } from 'ws';
import { EventEmitter } from 'events';

const eventBus = new EventEmitter();

export function setupWebSocket(wss: WebSocketServer): void {
  wss.on('connection', (ws: WebSocket) => {
    console.log('WebSocket client connected');

    // Send initial status
    ws.send(JSON.stringify({
      type: 'connected',
      timestamp: new Date().toISOString()
    }));

    // Handle incoming messages
    ws.on('message', (data: Buffer) => {
      try {
        const message = JSON.parse(data.toString());
        handleWebSocketMessage(ws, message);
      } catch (error) {
        ws.send(JSON.stringify({
          type: 'error',
          message: 'Invalid message format'
        }));
      }
    });

    ws.on('close', () => {
      console.log('WebSocket client disconnected');
    });

    ws.on('error', (error) => {
      console.error('WebSocket error:', error);
    });
  });

  // Subscribe to task events
  eventBus.on('task:created', broadcastTaskUpdate);
  eventBus.on('task:updated', broadcastTaskUpdate);
  eventBus.on('task:completed', broadcastTaskUpdate);
  eventBus.on('task:failed', broadcastTaskUpdate);
}

function handleWebSocketMessage(ws: WebSocket, message: any): void {
  switch (message.type) {
    case 'subscribe':
      // Client wants to subscribe to specific events
      ws.send(JSON.stringify({
        type: 'subscribed',
        channels: message.channels || ['all']
      }));
      break;

    case 'ping':
      ws.send(JSON.stringify({ type: 'pong' }));
      break;

    default:
      ws.send(JSON.stringify({
        type: 'error',
        message: `Unknown message type: ${message.type}`
      }));
  }
}

function broadcastTaskUpdate(data: any): void {
  // This would be called from the task execution system
  // to broadcast updates to all connected WebSocket clients
  eventBus.emit('broadcast', {
    type: 'task_update',
    data
  });
}

export { eventBus };