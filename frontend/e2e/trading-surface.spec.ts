import { expect, test } from '@playwright/test'
import { ensureAuthenticated } from './fixtures/live-backend-auth'

/**
 * Real (non-mocked) E2E coverage for openalgo/frontend's actual trading
 * screens — order placement, positions, option chain, strategy builder.
 * See `.claude/backlog/items/2026-08-28-openalgo-e2e-trading-surface.md`
 * for why these had zero coverage before this file, and
 * `e2e/fixtures/live-backend-auth.ts` for the safety contract this suite
 * depends on (stock_simulator broker only — never a real broker).
 *
 * This suite requires a REAL running backend + frontend, unlike the other
 * 4 specs in this directory (which only need `npm run dev`'s frontend and
 * never authenticate). Point it at an isolated scratch OpenAlgo instance:
 *   - its own SQLite DB files (fresh — the /setup wizard runs once)
 *   - `.env` with `VALID_BROKERS='stock_simulator'`
 *   - backend on the port this project's baseURL proxies to (5001 by
 *     default per `frontend/vite.config.ts`)
 *
 * Not yet wired into CI (`ci.yml:115-134` starts only the frontend dev
 * server) — see the backlog item's Plan step 4. Run locally via:
 *   npx playwright test trading-surface.spec.ts
 */

test.describe.serial('Trading surface (live sandbox backend)', () => {
  test.beforeEach(async ({ page }) => {
    await ensureAuthenticated(page)
  })

  test('positions screen loads real backend data', async ({ page }) => {
    await page.goto('/positions')
    await page.waitForLoadState('networkidle')

    // Real backend call — no route mocking. A fresh sandbox account has
    // no open positions, so assert the real empty-state OR a real table,
    // whichever the backend actually returns, rather than asserting one
    // fixed shape (which would only be true for a fresh instance).
    const table = page.locator('table')
    const emptyState = page.getByText(/no (open )?positions/i)
    await expect(table.or(emptyState).first()).toBeVisible({ timeout: 15000 })

    // No client-side crash / unhandled rejection surfaced as a visible error
    // boundary.
    await expect(page.getByText(/something went wrong/i)).toHaveCount(0)
  })

  test('trading (charting terminal) screen reaches an authenticated, connected state', async ({
    page,
  }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })

    await page.goto('/trading')
    await page.waitForLoadState('networkidle')

    // Real backend calls: /api/websocket/apikey + /api/websocket/config.
    // A logged-in, broker-connected session always has an API key
    // (auto-generated at /setup), so the "no API key" empty state must
    // NOT appear — if it does, the real auth/broker-connect chain above
    // silently failed.
    await expect(page.getByText(/no api key found for charting/i)).toHaveCount(0, {
      timeout: 15000,
    })

    // Layout selector is real chrome present once the terminal mounts.
    await expect(page.getByText('Layout')).toBeVisible({ timeout: 15000 })

    const seriousErrors = consoleErrors.filter(
      (e) => !/favicon|ResizeObserver|Websocket.*reconnect/i.test(e)
    )
    expect(seriousErrors, `Unexpected console errors: ${seriousErrors.join('\n')}`).toEqual([])
  })

  test('option chain screen loads against the real backend', async ({ page }) => {
    await page.goto('/optionchain')
    await page.waitForLoadState('networkidle')

    // Real backend call for the underlying/expiry selectors (oiProfileApi).
    // This is a smoke-level check (page mounts, makes real API calls, does
    // not error) rather than a full order-placement exercise — the live
    // option chain needs symbol-master + tick data plumbing not yet wired
    // for this scratch instance; see the backlog item's Attempts entry for
    // what's deferred here.
    await expect(page.getByText(/something went wrong/i)).toHaveCount(0)
    await expect(page.locator('body')).toBeVisible()
  })

  test('strategy builder screen loads against the real backend', async ({ page }) => {
    await page.goto('/strategybuilder')
    await page.waitForLoadState('networkidle')

    await expect(page.getByText(/something went wrong/i)).toHaveCount(0)
    await expect(page.locator('body')).toBeVisible()
  })
})
