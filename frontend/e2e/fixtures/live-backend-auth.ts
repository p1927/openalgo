import type { Page } from '@playwright/test'

/**
 * Real (non-mocked) authentication helper for the live-stack trading-surface
 * specs (see e2e/trading-surface.spec.ts).
 *
 * SAFETY: this MUST only ever be pointed at an OpenAlgo instance whose
 * VALID_BROKERS is restricted to `stock_simulator` — the one broker plugin
 * in this fork that is safe-by-construction to drive end-to-end:
 *   - `broker/stock_simulator/api/auth_api.py` login is a no-op (no real
 *     credential exchange, no external OAuth hop).
 *   - `broker/stock_simulator/api/order_api.py:place_order_api` unconditionally
 *     refuses ("Use Analyzer mode") — the only order path is OpenAlgo's own
 *     Analyzer/sandbox paper-fill engine.
 *   - It never holds an INDmoney (or any real broker) token — see
 *     `openalgo/CLAUDE.md` section "`stock_simulator` broker never reaches a
 *     real broker...".
 *
 * These specs create their own throwaway admin user via the one-time /setup
 * wizard (OPENALGO_E2E_USERNAME/PASSWORD env, defaulted below) — never the
 * repo owner's real single-user account. Do not run this suite against a
 * shared dev/release OpenAlgo instance that has a real broker configured;
 * run it against an isolated scratch instance (own DB files, own port) with
 * VALID_BROKERS='stock_simulator' in its .env.
 */

export const E2E_USERNAME = process.env.OPENALGO_E2E_USERNAME || 'e2etester'
export const E2E_PASSWORD = process.env.OPENALGO_E2E_PASSWORD || 'E2eTestPass!2026'
const E2E_EMAIL = process.env.OPENALGO_E2E_EMAIL || 'e2etester@example.invalid'

/**
 * The one deliberate exception to "no mocks" in this suite: `IndMoneyTokenGate`
 * (frontend/src/components/auth/IndMoneyTokenGate.tsx) is an orthogonal
 * recorder-market-data health check, unrelated to OpenAlgo's own broker
 * session/order-placement path under test here. Its GET handler
 * (`blueprints/indmoney_credentials.py:get_indmoney_recorder_token`) makes a
 * *live* outbound call to the real INDmoney API using whatever
 * `INDMONEY_ACCESS_TOKEN` is configured — and `utils/broker_env_sync.py`'s
 * `_env_path()` resolves via `__file__`, so it reloads the REAL
 * `openalgo/.env` on this submodule checkout regardless of which `.env` the
 * test process was started with, defeating attempts to isolate this one key
 * on a scratch instance. Left un-mocked, every authenticated page load in
 * this suite would depend on and exercise a real third-party financial API
 * with a real credential — never something to leave live in an automated
 * test. See the backlog item's Attempts log for the confirmed trace.
 *
 * Stubbing this single diagnostic endpoint does not touch anything on the
 * actual trading surface under test (auth, broker session, orders,
 * positions all stay fully live against the real backend).
 */
async function stubIndMoneyRecorderGate(page: Page): Promise<void> {
  await page.route('**/api/broker/indmoney-recorder-token', async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'success', data: { status: 'not_configured' } }),
    })
  })
}

/**
 * Ensure the throwaway admin user exists (idempotent — the one-time setup
 * wizard silently redirects to /login if a user already exists), then log
 * in with real password auth, then connect the `stock_simulator` broker via
 * its real (no-op) callback. Leaves `page` on /dashboard, fully
 * authenticated against the real backend.
 */
export async function ensureAuthenticated(page: Page): Promise<void> {
  await stubIndMoneyRecorderGate(page)

  // 1. Setup (idempotent no-op if a user already exists on this instance).
  await page.goto('/setup')
  const usernameInput = page.locator('input[name="username"]')
  if (await usernameInput.isVisible({ timeout: 3000 }).catch(() => false)) {
    await usernameInput.fill(E2E_USERNAME)
    await page.locator('input[name="email"]').fill(E2E_EMAIL)
    // Password field(s) — setup form has password + confirm.
    const passwordInputs = page.locator('input[type="password"]')
    const count = await passwordInputs.count()
    for (let i = 0; i < count; i++) {
      await passwordInputs.nth(i).fill(E2E_PASSWORD)
    }
    await page.locator('button[type="submit"]').click()
    await page.waitForLoadState('domcontentloaded')
  }

  // 2. Real password login.
  await page.goto('/login')
  await page.waitForLoadState('domcontentloaded')
  await page.locator('input#username, input[name="username"]').first().fill(E2E_USERNAME)
  await page.locator('input[type="password"]').first().fill(E2E_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForLoadState('domcontentloaded')

  // 3. Broker connect — VALID_BROKERS is restricted to stock_simulator on the
  // scratch instance this suite targets, so the select defaults to it; the
  // "Connect" submit navigates through stock_simulator's no-op callback
  // straight back to /dashboard, with no real external OAuth hop.
  if (page.url().includes('/broker')) {
    const connectButton = page.getByRole('button', { name: /connect/i })
    await connectButton.click()
    await page.waitForURL('**/dashboard', { timeout: 15000 })
  }

  await page.waitForURL('**/dashboard', { timeout: 15000 })
}
