const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  testMatch: 'workspace.spec.cjs',
  fullyParallel: false,
  workers: 1,
  globalTeardown: './tests/browser/teardown.cjs',
  timeout: 30000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:8015', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: 'python scripts/browser_server.py',
    url: 'http://127.0.0.1:8015/api/health',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
