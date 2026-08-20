import { fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DrawTargetPayoff from './DrawTargetPayoff'

// The canvas internally reads the rendered SVG width through a
// ResizeObserver. jsdom doesn't run layout, so the observer never fires and
// `canvasWidth` stays at its default (600px). The tests below target the
// *logic* (what the box reads) rather than pixel-perfect placement, so this
// is fine — strikes and rows still snap the same way regardless of width.

vi.mock('@/api/strategy-synthesis', () => ({
  strategySynthesisApi: {
    synthesize: vi.fn(),
  },
}))

vi.mock('@/utils/toast', () => ({
  showToast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

const STRIKES = [24000, 24050, 24100, 24150, 24200, 24250, 24300, 24350, 24400, 24450, 24500]

function getCanvasContainer(): HTMLElement {
  // The wrapping div is the parent of the SVG and the host of the info
  // box overlay. The component doesn't put a test id on it, so find it
  // through the SVG.
  const svg = document.querySelector('svg') as SVGSVGElement
  return svg.parentElement as HTMLElement
}

function getSvg(): SVGSVGElement {
  return document.querySelector('svg') as SVGSVGElement
}

function firePointerMove(clientX: number, clientY: number) {
  // jsdom reports getBoundingClientRect as {0,0,0,0} for everything by
  // default, so we monkey-patch a non-zero box for the test SVG. Width
  // matches the component's default `canvasWidth` state (600).
  const svg = getSvg()
  const rect = {
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 600,
    bottom: 220,
    width: 600,
    height: 220,
    toJSON: () => ({}),
  }
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue(rect)
  fireEvent.pointerMove(svg, { clientX, clientY })
}

describe('DrawTargetPayoff — spot line + hover/click info box', () => {
  beforeEach(() => {
    // ResizeObserver is not implemented in jsdom; stub it so the
    // canvasWidth effect does not throw.
    ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserver }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof ResizeObserver
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a vertical spot line at the snapped strike when spotPrice is provided', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={24125}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    const lines = getSvg().querySelectorAll('line')
    // Three lines: center (zero) + spot + (later we'll add one) —
    // we just need the spot line to exist and be vertical.
    const spotLine = Array.from(lines).find((l) => l.getAttribute('stroke') === '#db2777')
    expect(spotLine).toBeTruthy()
    // Vertical line: x1 === x2, y1 === 0, y2 === CANVAS_HEIGHT (220).
    expect(spotLine?.getAttribute('x1')).toBe(spotLine?.getAttribute('x2'))
    expect(spotLine?.getAttribute('y1')).toBe('0')
    expect(spotLine?.getAttribute('y2')).toBe('220')
  })

  it('omits the spot line when spotPrice is null', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )
    const lines = getSvg().querySelectorAll('line')
    const spotLine = Array.from(lines).find((l) => l.getAttribute('stroke') === '#db2777')
    expect(spotLine).toBeUndefined()
  })

  it('shows the strike, shape value, and strike-vs-spot delta in the hover box', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={24050}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    // Move the pointer roughly over the 4th strike column (x ≈ 180 of 600)
    // at the middle row (y ≈ 110 of 220).
    firePointerMove(180, 110)

    const box = getCanvasContainer().querySelector(
      ':scope > div.pointer-events-none'
    ) as HTMLElement
    expect(box).toBeTruthy()
    // The box formats strike with Indian-locale grouping; 24,150 is the
    // 4th strike (index 3) which is what x≈180 should snap to.
    const text = box.textContent ?? ''
    expect(text).toMatch(/24,150/)
    // Shape value for the middle row of 15 is 0.00.
    expect(text).toMatch(/0\.00/)
    // Delta from spot (24,150 − 24,050) is +100.
    expect(text).toMatch(/\+100/)
  })

  it('pins the hover box on click and clears it on pointer leave', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={24050}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    firePointerMove(180, 110)
    const svg = getSvg()
    fireEvent.click(svg, { clientX: 180, clientY: 110 })

    // After click + leave, the box should still be there (pinned).
    fireEvent.pointerLeave(svg)
    const box = getCanvasContainer().querySelector(':scope > div.pointer-events-none')
    expect(box).toBeTruthy()

    // Now re-enter and leave cleanly without clicking — should be gone.
    fireEvent.pointerLeave(svg)
    // pointerLeave already happened above; render a fresh path to verify
    // a pure hover-then-leave cycle clears the box.
    firePointerMove(300, 80)
    expect(getCanvasContainer().querySelector(':scope > div.pointer-events-none')).toBeTruthy()
    fireEvent.pointerLeave(svg)
    expect(getCanvasContainer().querySelector(':scope > div.pointer-events-none')).toBeNull()
  })

  it('omits the strike-vs-spot delta from the box when spotPrice is null', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )

    firePointerMove(180, 110)
    const box = getCanvasContainer().querySelector(
      ':scope > div.pointer-events-none'
    ) as HTMLElement
    expect(box).toBeTruthy()
    const text = box.textContent ?? ''
    // Still has strike + shape value …
    expect(text).toMatch(/24,150/)
    expect(text).toMatch(/0\.00/)
    // … but no third `·`-separated segment (strike · shape, no delta).
    // Counting separators is more robust than regex-matching a signed
    // number, because the shape value itself can start with `+` (e.g.
    // `+0.00`) which collides with a signed-delta regex.
    const separatorCount = (text.match(/·/g) ?? []).length
    expect(separatorCount).toBe(1)
  })
})

// Placed-point interaction tests. Kept in a separate `describe` because
// the helpers (svg/rect-mocking, point counting) are slightly different
// from the spot-line tests above and the assertions focus on different
// state transitions. The "click on an existing point" regression in
// particular is its own concern — see git history for the original bug
// (single click on a placed point used to add a duplicate of that point
// because the click event bubbled to the SVG's onClick).
describe('DrawTargetPayoff — placed point click & double-click', () => {
  beforeEach(() => {
    ;(globalThis as unknown as { ResizeObserver: typeof ResizeObserver }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof ResizeObserver
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function attachRect() {
    const svg = getSvg()
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 600,
      bottom: 220,
      width: 600,
      height: 220,
      toJSON: () => ({}),
    })
  }

  function placedPointCount(): number {
    // Green placed points are the only circles with fill="#10b981".
    return Array.from(getSvg().querySelectorAll('circle')).filter(
      (c) => c.getAttribute('fill') === '#10b981'
    ).length
  }

  function clickAt(clientX: number, clientY: number) {
    attachRect()
    const svg = getSvg()
    fireEvent.pointerDown(svg, { clientX, clientY })
    fireEvent.pointerUp(svg, { clientX, clientY })
    fireEvent.click(svg, { clientX, clientY })
  }

  // Click on whatever circle is at the given pixel. In a real browser,
  // a click on a placed point fires native click on the circle first,
  // then bubbles to the SVG. React then dispatches the synthetic click
  // to the bubble path: circle.onClick first (if any), then svg.onClick.
  // Using fireEvent.click on the svg directly *bypasses* the circle's
  // onClick handler entirely — so to faithfully reproduce browser
  // behavior we must click the circle element when one is hit. The
  // helper below checks whether a placed point sits at the click
  // position and dispatches the click on the right element.
  function clickAtOrOnPoint(clientX: number, clientY: number) {
    attachRect()
    const svg = getSvg()
    fireEvent.pointerDown(svg, { clientX, clientY })
    fireEvent.pointerUp(svg, { clientX, clientY })
    const points = Array.from(svg.querySelectorAll('circle')).filter(
      (c) => c.getAttribute('fill') === '#10b981'
    )
    const hit = points.find((c) => {
      const cx = Number(c.getAttribute('cx'))
      const cy = Number(c.getAttribute('cy'))
      return Math.abs(cx - clientX) <= 5 && Math.abs(cy - clientY) <= 5
    })
    if (hit) {
      fireEvent.click(hit, { clientX, clientY })
    } else {
      fireEvent.click(svg, { clientX, clientY })
    }
  }

  function getPlacedPoint(): SVGCircleElement {
    const point = Array.from(getSvg().querySelectorAll('circle')).find(
      (c) => c.getAttribute('fill') === '#10b981'
    ) as SVGCircleElement
    if (!point) throw new Error('no placed point found')
    return point
  }

  it('places exactly one point on a single click of empty canvas', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )
    clickAt(300, 110)
    expect(placedPointCount()).toBe(1)
  })

  it('does NOT add a duplicate on a single click of an existing point (regression)', () => {
    // Regression: a single click on a placed point used to bubble up
    // to the SVG's onClick handler, which added a duplicate of the
    // clicked point. The fix adds an explicit onClick stopPropagation
    // on the placed-point circle so only canvas-background clicks
    // reach the add-point handler.
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )
    clickAt(300, 110)
    expect(placedPointCount()).toBe(1)

    // Click *exactly* on the existing point. With the fix in place,
    // count should stay at 1. (Pre-fix: count would be 2.) Note: this
    // dispatches the click on the circle element itself, not the SVG,
    // so the circle's onClick handler (which calls stopPropagation) is
    // actually invoked — same code path a real browser takes when the
    // user clicks on a placed point.
    clickAtOrOnPoint(300, 110)
    expect(placedPointCount()).toBe(1)
  })

  it('removes a placed point on double-click', () => {
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )
    clickAt(300, 110)
    expect(placedPointCount()).toBe(1)

    const point = getPlacedPoint()
    fireEvent.doubleClick(point, { clientX: 300, clientY: 110 })
    expect(placedPointCount()).toBe(0)
  })

  it('double-click after single click on existing point still ends with one point', () => {
    // Combined flow: place, single-click-existing (must not duplicate),
    // then place a second point, then double-click to remove the first.
    render(
      <DrawTargetPayoff
        underlying="NIFTY"
        exchange="NFO"
        expiry="04AUG26"
        strikes={STRIKES}
        spotPrice={null}
        resolveContract={vi.fn()}
        onAdd={vi.fn()}
      />
    )
    clickAt(300, 110)
    expect(placedPointCount()).toBe(1)

    // Single click on existing point: no duplicate.
    clickAtOrOnPoint(300, 110)
    expect(placedPointCount()).toBe(1)

    // Place a second point at a different position (empty canvas, so
    // clickAt — not clickAtOrOnPoint — is correct here).
    clickAt(120, 60)
    expect(placedPointCount()).toBe(2)

    // Double-click the first point to remove it.
    const firstPoint = Array.from(getSvg().querySelectorAll('circle')).find(
      (c) => c.getAttribute('fill') === '#10b981' && c.getAttribute('cx') !== null
    ) as SVGCircleElement
    fireEvent.doubleClick(firstPoint, { clientX: 300, clientY: 110 })
    expect(placedPointCount()).toBe(1)
  })
})
