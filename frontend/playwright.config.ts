import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright configuration for E2E testing.
 * See https://playwright.dev/docs/test-configuration
 *
 * PLAYWRIGHT_BASE_URL lets a run point at an independently-launched,
 * already-running frontend dev server (e.g. an isolated scratch OpenAlgo
 * instance's own Vite server on a non-default port) instead of always
 * spinning up `npm run dev` on the real dev port 5173/5001 -- see
 * e2e/fixtures/live-backend-auth.ts and e2e/trading-surface.spec.ts for the
 * suite that needs this, and
 * .claude/backlog/items/2026-09-05-openalgo-e2e-order-placement-and-deep-chain-coverage.md
 * for why. Defaults to today's hardcoded value so normal `npx playwright
 * test` usage against the real dev server is unchanged.
 */
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use */
  reporter: 'html',
  /* Shared settings for all the projects below */
  use: {
    /* Base URL to use in actions like `await page.goto('/')` */
    baseURL: BASE_URL,
    /* Collect trace when retrying the failed test */
    trace: 'on-first-retry',
    /* Screenshot on failure */
    screenshot: 'only-on-failure',
  },

  /* Configure projects for major browsers */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    /* Test against mobile viewports */
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone 12'] },
    },
  ],

  /* Run your local dev server before starting the tests */
  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
})
