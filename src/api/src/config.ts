/**
 * Configuration for the API server
 */

export const config = {
  port: parseInt(process.env.PORT || '3001'),
  agentApiUrl: process.env.AGENT_API_URL || 'http://localhost:18789',
  jwt: {
    secret: process.env.SESSION_SECRET || 'dev-secret-change-in-production',
    expiration: parseInt(process.env.JWT_EXPIRATION || '86400')
  },
  auth: {
    username: process.env.WEB_USERNAME || 'admin',
    password: process.env.WEB_PASSWORD || 'admin'
  },
  database: {
    path: process.env.WEB_DATABASE_PATH || '.app/data/tasks.db'
  },
  cors: {
    origins: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:81234']
  }
};