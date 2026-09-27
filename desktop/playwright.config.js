// End-to-end tests: drive the real Electron app (npm run e2e).
'use strict'
const { defineConfig } = require('@playwright/test')

module.exports = defineConfig({
  testDir: 'e2e',
  timeout: 180_000, // the first launch starts the Python backend and scans the game library
  workers: 1, // one app at a time: they share the Electron binary and the GSI port
  reporter: [['list']],
  outputDir: 'e2e-results',
})
