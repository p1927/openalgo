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
