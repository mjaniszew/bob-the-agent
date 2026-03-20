const path = require('path');

module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  rootDir: path.join(__dirname, '..'),
  roots: ['<rootDir>/tests'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts'
  ],
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'lcov'],
  verbose: true
};