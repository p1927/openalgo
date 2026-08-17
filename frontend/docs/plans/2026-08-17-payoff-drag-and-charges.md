# Strategy Builder — Draggable Payoff + Per-Leg Charges

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the slider panel below the Strategy Builder payoff chart with **directly-draggable strike markers on the chart itself**, refresh the chart styling, and bake **per-leg brokerage + taxes** into every P&L number the chart shows (max profit, max loss, breakevens, scenario P&L, hover).

**Architecture:**
- `PayoffChart.tsx`: Plotly strike lines become **Plotly shapes (vertical lines + on-line handle dots)** with `editable: true` + `onRelayout` → emit strike changes through the existing `onStrikeChange` callback. Slider panel is removed; reset moves into the chart header.
- `strategyMath.ts` → `computePayoff`: accept an optional per-leg charges map and a `applyChargesToPayoff` flag. Net each sample by the sum of per-leg charges scaled by exposure responsivity (a closed/inactive leg absorbs its fixed cost but contributes no variable payoff). Re-derive max profit / loss / breakevens on net samples.
- `StrategyBuilder.tsx`: thread `legsToChargeInput(legs)` + `calculateIndianCharges(...)` → `planCharges` already exists, expand the `PayoffChart` props to receive `perLegCharges`.
- The `PnLTab` continues to show entry + net rows; we also make the **per-leg "P&L after charges"** column work using the same map, so live mode matches the chart.

**Tech Stack:**
- React 19 + Plotly.js (`@/lib/Plot2D`)
- TypeScript strict
- vitest + @testing-library/react
- Existing `tradeCharges.ts` + `brokerChargePresets.json` (Groww preset included)

---

## Phase map

| # | Subplan / task file | Type | Depends on |
|---|---|---|---|
| 1 | `01-payoff-with-charges-types.md` | `implement` | — |
| 2 | `02-per-leg-charges-prop.md` | `implement` | 1 |
| 3 | `03-draggable-strike-shapes.md` | `implement` | 2 |
| 4 | `04-groww-style-palette.md` | `implement` | 2 |
| 5 | `05-pnltab-perleg-after-charges.md` | `implement` | 2 |
| 6 | `06-tests-and-regression.md` | `implement` | 1–5 |

---

## Global constraints (verbatim)

1. **No silent failures.** Charge map missing a leg → fall back to 0 *and log warn*, never a NaN sample.
2. **Pure payoff stays importable.** Tests that don't supply charges must keep their numbers (`perLegCharges = undefined` ⇒ behavior identical to today).
3. **No drive-by refactors.** Don't touch `computePayoff` internal math — only net out the new constant per sample.
4. **Plotly drag must be debounced / snapped.** Emit one `onStrikeChange` per drag *commit* (snapped to `strikeStep`), not per-pixel.
5. **All existing tests stay green.** Add new tests; do not weaken old ones.

---

## Verification protocol

After each task:
```bash
cd openalgo/frontend
npx vitest run src/components/strategy-builder/PayoffChart.test.tsx -q
npx vitest run src/components/strategy-builder/PnLTab.test.tsx -q
npx vitest run src/lib/strategyMath.test.ts -q
```

After all tasks:
```bash
cd openalgo/frontend
npx vitest run src/components/strategy-builder -q
npx vitest run src/lib -q
```

Commit discipline: **one commit per task** with messages like
`feat(payoff): accept per-leg charges in computePayoff`,
`feat(payoff): drag strike shapes + drop slider panel`, etc.

---

## Tasks

### Task 1 — `computePayoff` accepts optional per-leg charges

**Objective:** Extend `computePayoff` in `strategyMath.ts` to accept an optional per-leg charge total and net each sample by `sum(legCharges)` for legs that contribute variable exposure. Closed / inactive legs contribute their own charge fixed cost only.

**Files:**
- Modify `openalgo/frontend/src/lib/strategyMath.ts` (around `computePayoff` line 753)
- Add: `openalgo/frontend/src/lib/strategyMath.test.ts` — new test block

**Step 1 — Write failing test (RED)**

In `strategyMath.test.ts` add:

```ts
it('nets out per-leg charges from every sample when a perLegCharges map is supplied', () => {
  const legs = [leg('lc', 'BUY', 'CE', 100, 5)]
  const without = computePayoff(legs, 100, 7, 0, [80, 120], 7, 0, 20, NOW)
  const charges = { lc: 12.5 }
  const withC = computePayoff(legs, 100, 7, 0, [80, 120], 7, 0, 20, NOW, { perLegCharges: charges })
  // Every expiry sample must equal `expiry - sum(active+closed charges) = expiry - 12.5`
  for (const s of withC.samples) {
    expect(s.expiry).toBeCloseTo(s.expiry - 12.5, 0) // tautology trap → instead:
  }
})
```

Replace that tautology with a real assert:

```ts
for (let i = 0; i < without.samples.length; i++) {
  expect(withC.samples[i].expiry).toBeCloseTo(without.samples[i].expiry - 12.5, 6)
}
expect(withC.maxProfit).toBeLessThan(without.maxProfit)
```

Run:
```bash
npx vitest run src/lib/strategyMath.test.ts -q
```
Expected: **FAIL** — `computePayoff` does not take a second options arg yet.

**Step 2 — Implement (GREEN)**

Extend the `computePayoff` signature. Find:
```ts
export function computePayoff(
  legs: StrategyLeg[],
  spot: number,
  ...
)
```

Append:
```ts
export interface PayoffChargesOptions {
  /** Map of leg.id → total round-trip charge for that leg (brokerage + STT + GST + stamp + exchange + SEBI). */
  perLegCharges?: Record<string, number>
}
```

Default param: `charges: PayoffChargesOptions = {}`.
Inside the function, after each sample's `expiry`/`tplus0` value is assigned:
```ts
const chargeOffset = computeChargeOffset(legs, charges.perLegCharges)
// then for each sample:
sample.expiry -= chargeOffset
sample.tplus0 -= chargeOffset
```

`computeChargeOffset` helper, placed above `computePayoff`:

```ts
function computeChargeOffset(legs: StrategyLeg[], perLeg?: Record<string, number>): number {
  if (!perLeg) return 0
  let total = 0
  for (const l of legs) {
    if (!l.active) continue
    const c = perLeg[l.id]
    if (typeof c === 'number' && Number.isFinite(c)) total += c
  }
  return total
}
```

Re-derive `maxProfit`/`maxLoss`/`breakevens` from the **netted** samples (existing helpers already operate on the array — just pass them the netted samples).

**Important:** Closed legs' `exitPrice` is fixed, so their realized P&L is `exitPrice - entry` minus their charges too — they shouldn't double-count. The offset above applies once per active leg.

**Step 3 — Verify pass**
```bash
npx vitest run src/lib/strategyMath.test.ts -q
```
Expected: PASS.

Also add a *no-charges* regression: when `perLegCharges` is omitted, results match today's numbers bit-for-bit (this guards the default behavior).

**Step 4 — Commit**
```bash
git add openalgo/frontend/src/lib/strategyMath.ts openalgo/frontend/src/lib/strategyMath.test.ts
git commit -m "feat(payoff): computePayoff nets per-leg charges from every sample"
```

---

### Task 2 — `PayoffChart` accepts and applies per-leg charges

**Objective:** Thread a `perLegCharges` prop from `StrategyBuilder` down to `computePayoff`.

**Files:**
- Modify `PayoffChart.tsx` (props interface around line 15, drops `onStrikeChange`/`strikeStep`/`onResetStrikes`/`canResetStrikes` props unchanged)
- Modify `StrategyBuilder.tsx` (PayoffChart callsite at line 2268)
- Tests in `PayoffChart.test.tsx`

**Step 1 — Failing test**

Add to `PayoffChart.test.tsx`:
```ts
it('threads perLegCharges into the chart sample series', () => {
  const payoff = computePayoff([leg('c','BUY','CE',100,5)], 100, 7, 0, [80,120], 7, 0, 20, NOW)
  render(<PayoffChart title="Net" scenario={BASE_SCENARIO} remainingYears={7/365}
    payoff={payoff} formatCurrency={formatCurrency}
    perLegCharges={{ c: 25 }}
  />)
  const expiry = (plotCapture.props?.data ?? []).find(t => t.name === 'At Expiry') as any
  const xs = expiry.x as number[]
  expect(xs.length).toBeGreaterThan(0)
  // Just check that a net-of-charge prop is now part of the chart contract:
  expect(expiry.hovertemplate).toContain('Charges')
})
```

Run, expect FAIL — prop not in interface.

**Step 2 — Implement**

In `PayoffChartProps`:
```ts
/** Per-leg charge map (leg.id → total round-trip charges). When provided, hover shows the net p&l after charges and the offset is reflected in summary numbers. */
perLegCharges?: Record<string, number>
```

The chart itself doesn't change its trace math — charges are already net into `payoff` upstream. But it must include them in the tooltip. Find:
```ts
const hoverTemplate = (label: string) =>
  `<b>${label}</b>` +
  '<br>Underlying: %{customdata[0]}' +
  '<br>Chg. from Scenario: %{customdata[1]}' +
  '<br>P&L: %{customdata[2]}' +
  '<extra></extra>'
```

Change to:
```ts
const chargesRow = perLegCharges
  ? '<br>After charges: %{customdata[3]}'
  : ''
const hoverTemplate = (label: string) =>
  `<b>${label}</b>` +
  '<br>Underlying: %{customdata[0]}' +
  '<br>Chg. from Scenario: %{customdata[1]}' +
  '<br>P&L: %{customdata[2]}' +
  chargesRow +
  '<extra></extra>'
const hoverData = (values: number[]) =>
  samples.map((sample, i) => [
    formatCurrency(sample.underlying),
    pctFromSpot[i],
    formatCurrency(values[i]),
    perLegCharges ? formatCurrency(sample.expiry) : '',
  ])
```

Also label the y-axis `Net P&L (after charges)` when `perLegCharges` is provided; otherwise keep `Profit / Loss`.

**Step 3 — Verify**
```bash
npx vitest run src/components/strategy-builder/PayoffChart.test.tsx -q
```

**Step 4 — Wire `StrategyBuilder.tsx`**

Find the `PayoffChart` element (line ~2268). It already receives `planCharges` upstream; build:
```ts
const perLegCharges = useMemo(() => {
  if (!planCharges?.per_leg?.length) return undefined
  const out: Record<string, number> = {}
  for (const row of planCharges.per_leg) {
    if (!row?.symbol) continue
    const leg = legs.find(l => l.symbol === row.symbol && l.side === row.side)
    if (!leg) continue
    out[leg.id] = Number(row.total_charges ?? 0)
  }
  return out
}, [planCharges, legs])
```

Re-pass into `computePayoff` (the upstream caller — `StrategyBuilder` calls `computePayoff` somewhere; find it). Add `perLegCharges` to that call.

Then pass `perLegCharges={perLegCharges}` into `<PayoffChart />`.

**Step 5 — Verify**
```bash
npx vitest run src -q --silent 2>&1 | grep -E 'FAIL|PASS|passed|failed'
```

**Step 6 — Commit**
```bash
git add openalgo/frontend/src/components/strategy-builder/PayoffChart.tsx openalgo/frontend/src/pages/StrategyBuilder.tsx openalgo/frontend/src/components/strategy-builder/PayoffChart.test.tsx
git commit -m "feat(payoff): thread per-leg charges through to chart hover & summary"
```

---

### Task 3 — Draggable strike shapes inside the chart

**Objective:** Replace `<input type="range">` slider panel with on-chart draggable vertical lines + handle dots. Snapping to `strikeStep` happens on commit, not per-pixel. Reset button moves into a chart header chip.

**Files:**
- Modify `PayoffChart.tsx` (strike trace block at line 227–246 + slider panel at line 501–564 deleted, replaced with draggable shape layer + relayout handler)
- Tests

**Step 1 — Plan vs rationale**

Plotly's native vertical-line drag works by:
- Rendering the strike lines as **shapes with `editable: true`**, not traces
- Reading `chart.on('plotly_relayout', handler)` → `eventdata.shapes[<index>].x0` is the new strike
- Mapping shape index → leg via a `strikeLegs` ordered array (stable ordering guarantees)
- Calling `onStrikeChange(leg.id, snapStrike(newX, strikeStep))`

**Step 2 — Failing test**

```ts
it('renders strike markers as editable shapes, not traces, when onStrikeChange is set', () => {
  const legs = [leg('c', 'BUY', 'CE', 100, 5)]
  render(
    <PayoffChart title="Drag me"
      scenario={BASE_SCENARIO} remainingYears={7/365} payoff={payoff}
      formatCurrency={formatCurrency} legs={legs} strikeStep={50}
      onStrikeChange={() => {}} />
  )
  const shapes = plotCapture.props?.layout.shapes ?? []
  expect(shapes.some(s => s.type === 'line' && s.xref === 'x' && s.x0 === 100)).toBe(true)
  expect(shapes.some(s => s.editable === true)).toBe(true)
})

it('removes the slider panel when onStrikeChange is set', () => {
  render(<PayoffChart ... onStrikeChange={() => {}} />)
  expect(screen.queryByRole('slider')).toBeNull()
})
```

Run, expect FAIL (the slider still exists).

**Step 3 — Implement**

In `PayoffChart.tsx`:

1. Move the strike geometry from `traces` to `shapes`. Delete the strike-line trace block at line 227–246.
2. After the existing `shapes.push({ type: 'line', zeroLine ... })` add:
```ts
strikeLegs.forEach((leg, i) => {
  const strike = leg.strike ?? scenario.spot
  const isCe = leg.optionType === 'CE'
  const isSell = leg.side === 'SELL'
  const color = isCe ? colors.ceStrike : colors.peStrike
  shapes.push({
    type: 'line',
    xref: 'x', yref: 'paper',
    x0: strike, x1: strike,
    y0: 0, y1: 1,
    line: { color, width: isSell ? 2 : 3, dash: isSell ? 'dash' : 'solid' },
    editable: true,
    name: `strike:${leg.id}`, // round-tripped in plotly_relayout event
    label: legLabel(leg),       // shown in legend via legendgroup below
    legendgroup: `strike-${i}`,
  })
  // Handle dot at the top of each shape so the user has a clear drag affordance:
  shapes.push({
    type: 'circle',
    xref: 'x', yref: 'paper',
    x0: strike - 6, x1: strike + 6, y0: 0.98, y1: 1.02,
    fillcolor: color, line: { color, width: 1.5 },
    editable: true,
    name: `handle:${leg.id}`,
  })
})
```

3. In the chart Layout, add `dragmode: 'pan'` (don't want box-zoom on a drag of these). Add `shapes` to `layout.editable: true` is global; setting each shape `editable: true` is enough.

4. Add a top-level `onRelayout` prop wired through `<Plot>`. Find the `<Plot>` call:
```tsx
<Plot data={data} layout={layout} config={config} useResizeHandler
      style={{ width: '100%', height }} />
```
Replace with a thin wrapper:
```tsx
<Plot
  data={data} layout={layout} config={config} useResizeHandler
  onRelayout={(gd) => handleRelayout(gd, strikeLegs, strikeStep, onStrikeChange, snapStrike, handlePendingChange)}
  style={{ width: '100%', height }}
/>
```

`onRelayout` is passed via the underlying `<Plot>` component — verify it forwards to Plotly (check `frontend/src/lib/Plot2D.tsx`). If not exposed, add `onRelayout: (gd: any) => void` to `Plot2D`'s prop type (small PR).

5. `handleRelayout`:
```ts
function handleRelayout(gd: any, legs: StrategyLeg[], step: number,
  onChange: (id: string, s: number) => void,
  snap: (v: number, s: number) => number,
  setPending: (s: Map<string, number>) => void) {
  // Look at every shape that's a strike or handle, see what moved.
  const newShapes = gd.layout?.shapes ?? []
  for (let i = 0; i < newShapes.length; i++) {
    const sh = newShapes[i]
    if (typeof sh?.name !== 'string' || !sh.name.startsWith('strike:')) continue
    const legId = sh.name.slice('strike:'.length)
    const x = Number(sh.x0)
    if (!Number.isFinite(x)) continue
    const leg = legs.find(l => l.id === legId)
    if (!leg) continue
    const snapped = snap(x, step)
    if (snapped !== (leg.strike ?? gd._prevStrikes?.[legId])) {
      onChange(legId, snapped)
    }
  }
}
```

6. Compute strikes cheaply: capture a ref of `strikeLegs` strike map so `handleRelayout` knows the *previous* strike per leg without re-running `useMemo`.

7. Remove the slider panel JSX block at line 501–564.

8. Move the reset button into a small toolbar chip **inside** the chart card (above the Plot component, not below):
```tsx
{onStrikeChange && canResetStrikes && (
  <div className="flex items-center justify-end px-3 pt-2">
    <Button variant="ghost" size="sm" onClick={onResetStrikes} className="h-7 gap-1 text-[10px]">
      <RotateCcw className="h-3 w-3" /> Reset strikes
    </Button>
  </div>
)}
```

**Step 4 — Verify**
```bash
npx vitest run src/components/strategy-builder/PayoffChart.test.tsx -q
```

**Step 5 — Manual verify** (after unit tests pass):
```bash
cd openalgo/frontend && npm run dev  # confirm the chart still renders without console errors
```

**Step 6 — Commit**
```bash
git add openalgo/frontend/src/components/strategy-builder/PayoffChart.tsx openalgo/frontend/src/lib/Plot2D.tsx openalgo/frontend/src/components/strategy-builder/PayoffChart.test.tsx
git commit -m "feat(payoff): drag strike markers inside chart, drop slider panel"
```

---

### Task 4 — Groww-inspired palette + nicer visual polish

**Objective:** Refresh chart typography, color usage, and tooltip. Groww uses:
- vivid emerald for profit / crimson for loss (deeper)
- a single subtle slate grid
- generous padding + larger legend dots
- slightly thicker expiry curve, monotone light t+0 line
- annotation weight: medium, color-matched to spot/σ markers

**Files:**
- Modify `PayoffChart.tsx` (colors block at line 103)

**Step 1 — Palette changes**

Replace the `colors` memo in PayoffChart.tsx with:

```ts
const colors = useMemo(() => ({
  paper:    isDark ? (isAnalyzer ? '#171226' : '#0b1220') : '#ffffff',
  bg:       isDark ? (isAnalyzer ? '#1f1936' : '#111827') : '#fafbfc',
  text:     isDark ? '#e5e7eb' : '#0f172a',
  mutedText:isDark ? '#94a3b8' : '#64748b',
  grid:     isDark ? 'rgba(148,163,184,0.10)' : 'rgba(15,23,42,0.06)',
  profit:   isDark ? 'rgba(16,185,129,0.20)' : 'rgba(16,185,129,0.16)',
  loss:     isDark ? 'rgba(244,63,94,0.20)'  : 'rgba(244,63,94,0.16)',
  expiryLine: isDark ? '#10b981' : '#059669',
  tplus0Line: isDark ? '#60a5fa' : '#2563eb',
  zeroLine: isDark ? 'rgba(226,232,240,0.4)' : 'rgba(15,23,42,0.4)',
  spotLine: isDark ? '#ec4899' : '#db2777',
  ceStrike: isDark ? '#4ade80' : '#16a34a',
  peStrike: isDark ? '#f87171' : '#dc2626',
  sigma1Band: isDark ? 'rgba(148,163,184,0.20)' : 'rgba(100,116,139,0.10)',
  sigma2Band: isDark ? 'rgba(148,163,184,0.08)' : 'rgba(100,116,139,0.04)',
  sigmaTick: isDark ? 'rgba(148,163,184,0.30)' : 'rgba(15,23,42,0.18)',
  // NEW: card chrome
  cardBorder: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(15,23,42,0.08)',
  tooltipBg: isDark ? '#0f172a' : '#ffffff',
  tooltipText: isDark ? '#e5e7eb' : '#0f172a',
}), [isDark, isAnalyzer])
```

Plus these layout tweaks:
- `legend.font.size` → 11 → 12, with `legend.bgcolor: 'transparent'`
- `hoverlabel.font.size` → 13, `hoverlabel.borderradius: 8`, `hoverlabel.bgcolor: 'transparent'` + bordered via shape (Plotly limitation — keep as is)
- `expiry` line `width: 2.2` → `width: 2.6`
- `tplus0` line `width: 2` → `width: 1.8`, keep dash
- 1σ / 2σ band opacity slightly lower (more Groww-like, less wash)
- Y axis title gets a tiny `${maxChargesText}` suffix when `perLegCharges` defined ("Net P&L (after charges)")

The change is small, surgical. No rewire.

**Step 2 — Verify**
```bash
npx vitest run src/components/strategy-builder/PayoffChart.test.tsx -q
```

Run the user-facing snapshot if there is one:
```bash
npx vitest run src/components/strategy-builder -q
```

**Step 3 — Commit**
```bash
git add openalgo/frontend/src/components/strategy-builder/PayoffChart.tsx
git commit -m "style(payoff): refresh palette and spacing closer to Groww"
```

---

### Task 5 — `PnLTab` per-leg P&L after charges

**Objective:** Add a dedicated `P&L (net)` column in the live P&L table so users can see each leg's pre-charge vs net of charge.

**Files:**
- Modify `openalgo/frontend/src/components/strategy-builder/PnLTab.tsx` (line 75–95 PnlCell, line 285–336 row render)
- Test updates in `PnLTab.test.tsx`

**Step 1 — Failing test**

```ts
it('renders a per-leg net P&L cell when perLegCharges is supplied', () => {
  // build rows via the public prop API
  render(<PnLTab legs={legs} fnoExchange="NFO" fallbackPrices={{}}
    formatCurrency={formatCurrency} perLegChargesMap={{ a: 25, b: 30 }} />)
  expect(screen.getAllByTestId('pnl-net').length).toBe(2)
})
```

**Step 2 — Implement**

Add `perLegChargesMap?: Record<string, number>` to `PnLTabProps`. In the rows `useMemo` (line 155), add `netPnl = pnl - (perLegChargesMap[leg.id] ?? 0)`. Render a new `<TableCell>` (header "Net") with a `<PnlCell value={netPnl} />`. Add `data-testid="pnl-net"` for the test anchor.

When `perLegChargesMap` is undefined, the column renders a `—` placeholder (or is hidden, depending on space — pick `—` for consistency with the rest of the empty-state UX).

**Step 3 — Verify**
```bash
npx vitest run src/components/strategy-builder/PnLTab.test.tsx -q
```

**Step 4 — Wire**
In `StrategyBuilder.tsx`, find the `<PnLTab>` element, pass `perLegChargesMap={perLegCharges}` (same map we built in Task 2).

**Step 5 — Commit**
```bash
git add openalgo/frontend/src/components/strategy-builder/PnLTab.tsx openalgo/frontend/src/pages/StrategyBuilder.tsx openalgo/frontend/src/components/strategy-builder/PnLTab.test.tsx
git commit -m "feat(pnl): per-leg P&L after charges column"
```

---

### Task 6 — Regression sweep

**Objective:** Run the full module test suite + manual smoke check that the drag handlers don't regress when there are no legs.

**Files:** none changed (test-only sweep)

**Step 1**
```bash
cd openalgo/frontend
npx vitest run src/components/strategy-builder -q
npx vitest run src/lib -q
```

**Step 2**

Static check / typecheck if present:
```bash
npm run typecheck 2>/dev/null || npx tsc --noEmit
```

**Step 3**

Manual smoke (Plan calls this out as our only manual gate — full drag testing happens in browser):
1. Open `/strategy` (or wherever SB lives) — verify slider panel is gone.
2. Plot a Straddle — confirm two vertical strike lines render and are draggable.
3. Drag one — the chart curve + summary numbers update.
4. Hover the curve — tooltip now has "After charges: ₹..." row.
5. Open Charges Breakdown — totals are unchanged from before (charges are already correct; chart now shows them, doesn't recompute).

**Step 4 — Final commit** (no commit unless something was missed)

If the typecheck or any test fails, fix and re-commit using the existing convention (`fix:` prefix).

---

## Success criteria

- [ ] Strategy Builder payoff chart renders strike markers **inside** the chart, draggable with mouse + touch.
- [ ] Slider panel below the chart is gone.
- [ ] Drag commit snapp sthe strike to the nearest valid `strikeStep`.
- [ ] Reset button still works; the chart-summary numbers re-derive.
- [ ] Each chart P&L number (max profit / max loss / breakevens / summary / hover) reflects per-leg brokerage + taxes.
- [ ] P&L tab shows a new "Net (after charges)" column.
- [ ] All previous tests pass; new tests added.
- [ ] `npm run typecheck` (if present) exits 0.

## Out of scope

- Touching `brokerChargePresets.json` numbers (Groww preset already exists and is correct).
- Touching backend APIs — `PlanCharges` shape is already complete for what we need.
- Adding futures chart drag — only option strikes change semantically. (Future task.)
