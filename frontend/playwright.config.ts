import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests run against a *running stack* (API + Postgres + Redis + the Vite dev server or
 * a preview build) that has been seeded with `python -m scripts.seed`. See docs/DEPLOYMENT.md
 * and .github/workflows/ci.yml for how CI boots it.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // scenarios share seeded accounts
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { args: ['--disable-gpu'] },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
