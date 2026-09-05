import { expect, test, type Browser } from '@playwright/test'
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
 *     default per `frontend/vite.config.ts` — override both the backend
 *     port (`OPENALGO_BACKEND_URL` for the Vite dev-server proxy) and this
 *     suite's target (`PLAYWRIGHT_BASE_URL` / `PLAYWRIGHT_WEBSERVER_PORT`
 *     for `playwright.config.ts`) to run against an independently-launched
 *     scratch instance without colliding with a real `trade dev` on 5001)
 *
 * Master-contract + tick-data plumbing (2026-09-05-openalgo-e2e-order-placement-and-deep-chain-coverage):
 * the scratch instance's `.env` builds a REAL master contract (NIFTY/
 * BANKNIFTY/SENSEX index + options, plus whatever equities the recorder has
 * captured) straight off the checked-in HF replay parquet bundle
 * (`NSE_REPLAY_DATA_ROOT` pointed at the main checkout's `data/nse/historic_data`
 * — read-only, no network call) and reads real quotes/option-chain data via
 * `STOCK_SIMULATOR_URL` pointed at the real shared dev `stock_simulator`
 * service, GET-only (`/data/*` — this suite must NEVER call that service's
 * `/control/replay/*` endpoints, which would mutate the shared replay clock
 * other concurrent sessions depend on). See that item's Attempts log for the
 * full setup and the follow-on gap it surfaced (the stock_simulator
 * service's own replay-arm state is not test-isolatable from the same
 * checkout).
 *
 * Authenticates ONCE in `beforeAll` and shares the resulting storage state
 * across every test in this file, rather than logging in per-test — partly
 * for speed, but mainly because the real backend's own
 * `LOGIN_RATE_LIMIT_MIN = "5 per minute"` throttle (a real security control,
 * not a test artifact) trips after only a handful of fresh logins in quick
 * succession.
 *
 * Not yet wired into CI (`ci.yml:115-134` starts only the frontend dev
 * server) — see the backlog item's Plan step 4. Run locally via:
 *   npx playwright test trading-surface.spec.ts
 */

async function loginStorageState(browser: Browser, baseURL: string | undefined) {
  const context = await browser.newContext({ baseURL })
  const page = await context.newPage()
  await ensureAuthenticated(page)
  const storageState = await context.storageState()
  await context.close()
  return storageState
}

test.describe('Trading surface (live sandbox backend)', () => {
  // `storageState` below re-authenticates once per TEST by default, which is
  // fine under a single worker but races under this project's default
  // `fullyParallel` config: multiple workers each open a fresh
  // browser/setup/login/broker-connect sequence against the same freshly
  // created scratch account at the same time, and the real backend's own
  // session/DB writes aren't safe against that (confirmed 2026-09-05: this
  // file's 5 tests pass reliably with `--workers=1` but flake under the
  // default worker count with a `waitForURL('**/dashboard')` timeout mid
  // setup/login). `mode: 'serial'` keeps this file's tests on one worker
  // without needing a separate CI-only `workers: 1` override for the whole
  // suite.
  test.describe.configure({ mode: 'serial' })

  test.use({
    storageState: async ({ browser, baseURL }, use) => {
      await use(await loginStorageState(browser, baseURL))
    },
  })

  test('positions screen loads real backend data', async ({ page }) => {
    await page.goto('/positions')
    await page.waitForLoadState('domcontentloaded')

    // Real backend call — no route mocking. A fresh sandbox account has
    // no open positions, so assert the real empty-state OR a real table,
    // whichever the backend actually returns, rather than asserting one
    // fixed shape (which would only be true for a fresh instance).
    const table = page.locator('table')
    const emptyState = page.getByText(/no (open )?positions/i)
    await expect(table.or(emptyState).first()).toBeVisible({ timeout: 15000 })

    // Real broker/mode chrome from the actual sandbox session — proves this
    // rendered from a genuinely authenticated, broker-connected backend call
    // rather than a static/error shell.
    await expect(page.getByText('stock_simulator')).toBeVisible()

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
    await page.waitForLoadState('domcontentloaded')

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

  test('option chain screen renders a real strike ladder with live prices', async ({ page }) => {
    // Playwright pages are real, focused browser tabs (unlike this suite's
    // own debugging via the Claude Browser MCP pane, which reports
    // document.visibilityState as 'hidden' even when focused) — the app's
    // polling hooks gate on page visibility (`pauseWhenHidden`), so a real
    // Playwright run is the only way this ever fetches at all.
    await page.bringToFront()
    await page.goto('/optionchain')
    await page.waitForLoadState('domcontentloaded')

    await expect(page.getByText(/something went wrong/i)).toHaveCount(0)

    // Real backend call (NIFTY/NFO is the default underlying/exchange) —
    // wait for the real strike ladder to render rather than just the empty
    // loading spinner. A real chain has an ATM-highlighted strike row and
    // numeric CE/PE prices sourced from the scratch instance's real master
    // contract + the shared stock_simulator service's live replay quotes.
    const strikeCell = page.locator('td', { hasText: /^\d{4,6}(\.\d+)?$/ }).first()
    await expect(strikeCell).toBeVisible({ timeout: 20000 })

    // At least one real, positive LTP rendered somewhere in the CE/PE
    // columns — proves actual quote data flowed through, not just empty
    // dashes for every leg.
    const ceOrPePrice = page.locator('td, div').filter({ hasText: /^\d+\.\d{2}$/ })
    await expect(ceOrPePrice.first()).toBeVisible({ timeout: 20000 })
  })

  test('option chain: places a real sandbox BUY order via the CE Buy button', async ({
    page,
  }) => {
    await page.bringToFront()
    await page.goto('/optionchain')
    await page.waitForLoadState('domcontentloaded')

    const strikeCell = page.locator('td', { hasText: /^\d{4,6}(\.\d+)?$/ }).first()
    await expect(strikeCell).toBeVisible({ timeout: 20000 })

    // The per-leg Buy/Sell "B"/"S" buttons only reveal on hover of their
    // containing row (see OptionChainRow's `group-hover:opacity-100`) — find
    // the row that owns the first visible strike cell and hover it, then
    // click its CE "B" (Buy) button.
    const row = strikeCell.locator('xpath=ancestor::tr[1]')
    await row.hover()
    const buyButton = row.getByRole('button', { name: 'B' }).first()
    await expect(buyButton).toBeVisible({ timeout: 5000 })
    await buyButton.click()

    // PlaceOrderDialog opens with a real symbol/quote — assert its header
    // shows the real (non-mocked) quote fetched for this leg before
    // submitting, then place a MARKET BUY order (the default price type)
    // through OpenAlgo's own Analyzer/sandbox paper-fill engine — never a
    // real broker (see openalgo/CLAUDE.md's stock_simulator invariant).
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible({ timeout: 10000 })
    const submitButton = dialog.getByRole('button', { name: /Place BUY Order/i })
    await expect(submitButton).toBeEnabled({ timeout: 10000 })
    await submitButton.click()

    // Dialog closes on a successful fill; a failure would leave it open
    // with a toast error instead.
    await expect(dialog).toBeHidden({ timeout: 15000 })

    // Confirm the real fill actually landed — Orderbook shows the new
    // order, and Positions reflects the resulting open position. Both are
    // real backend reads against the same sandbox account, not assertions
    // on client-side state alone.
    await page.goto('/orderbook')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.getByText('BUY').first()).toBeVisible({ timeout: 15000 })

    await page.goto('/positions')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.locator('table')).toBeVisible({ timeout: 15000 })
  })

  test('strategy builder: resolves a real chain and adds a leg at the live ATM strike', async ({
    page,
  }) => {
    await page.bringToFront()
    await page.goto('/strategybuilder')
    await page.waitForLoadState('domcontentloaded')

    await expect(page.getByText(/something went wrong/i)).toHaveCount(0)

    // StrategyBuilder shows a marketing/empty-state landing page until a
    // strategy exists — start one from a real template rather than driving
    // the draw-mode canvas, so this test exercises the manual leg builder
    // (ManualLegBuilder.tsx) wired to the real, live-resolved option chain.
    const getStarted = page.getByRole('button', { name: /new strategy|get started|start building/i })
    if (await getStarted.first().isVisible({ timeout: 5000 }).catch(() => false)) {
      await getStarted.first().click()
    }

    // "Add Buy" only enables once the manual leg builder has resolved a
    // real contract for the currently-selected underlying/expiry/strike
    // (`canAdd` in ManualLegBuilder.tsx) — i.e. once the same real
    // master-contract + live-quote data the option chain test above
    // exercised has round-tripped through this screen's own chain-resolution
    // path too.
    const addBuyButton = page.getByRole('button', { name: /Add Buy/i })
    await expect(addBuyButton).toBeVisible({ timeout: 20000 })
    await expect(addBuyButton).toBeEnabled({ timeout: 20000 })
    await addBuyButton.click()

    // A leg backed by a real contract shows the real per-lot size (NIFTY's
    // is 65 — see trade_integrations.stock_simulator.master_contract's
    // UNDERLYING_META, the single source of truth this screen's multiplier
    // auto-fill reads from) rather than a placeholder value.
    await expect(page.getByText(/65/).first()).toBeVisible({ timeout: 15000 })
  })
})
