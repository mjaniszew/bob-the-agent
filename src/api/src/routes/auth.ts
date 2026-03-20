/**
 * Authentication routes
 */

import express from 'express';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { config } from '../config';

export const authRouter = express.Router();

// Login endpoint
authRouter.post('/login', async (req, res) => {
  try {
    const { username, password } = req.body;

    if (!username || !password) {
      return res.status(400).json({ error: 'Username and password required' });
    }

    // Validate credentials
    if (username !== config.auth.username) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    const validPassword = await bcrypt.compare(password,
      await bcrypt.hash(config.auth.password, 10));

    // For simplicity in this initial version, direct comparison
    // In production, use bcrypt properly
    if (password !== config.auth.password &&
        !(await bcrypt.compare(password, await bcrypt.hash(config.auth.password, 10)))) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    // Generate JWT
    const token = jwt.sign(
      { username, role: 'admin' },
      config.jwt.secret,
      { expiresIn: config.jwt.expiration }
    );

    res.json({
      token,
      expiresIn: config.jwt.expiration,
      user: { username, role: 'admin' }
    });
  } catch (error) {
    console.error('Login error:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Verify token endpoint
authRouter.get('/verify', (req, res) => {
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'No token provided' });
  }

  const token = authHeader.substring(7);

  try {
    const decoded = jwt.verify(token, config.jwt.secret);
    res.json({ valid: true, user: decoded });
  } catch (error) {
    res.status(401).json({ error: 'Invalid token' });
  }
});

// Logout endpoint (client-side token removal)
authRouter.post('/logout', (req, res) => {
  res.json({ message: 'Logged out successfully' });
});