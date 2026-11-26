const { defineConfig } = require('@playwright/test');
const base = require('./playwright.config.cjs');
module.exports = defineConfig({
  ...base,
  testMatch: 'public.spec.cjs',
  projects: [base.projects[0]],
  webServer: { ...base.webServer, command: 'python scripts/browser_server.py --public' },
});
