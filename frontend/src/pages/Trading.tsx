import { ChevronDown, LayoutGrid } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Navbar } from '@/components/layout/Navbar'
import { ChartPane } from '@/components/trading/ChartPane'
import { DrawingRail } from '@/components/trading/DrawingRail'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { DrawStats, TradingTerminal } from '@/lib/trading/terminal'
import { cn } from '@/lib/utils'
import { useBrokerStore } from '@/stores/brokerStore'

interface SimulatorModeState {
  mode: 'live' | 'replay'
  reason: string
  replay_date: string | null
}

/** LIVE / REPLAY badge for the stock_simulator broker, polling the same
 * status endpoint the Sandbox settings page uses so the trade view never
 * shows unlabeled replay data as if it were live. */
function SimulatorModeBadge() {
  const { capabilities } = useBrokerStore()
  const [state, setState] = useState<SimulatorModeState | null>(null)
  const isSimulator = capabilities?.broker_name === 'stock_simulator'

  useEffect(() => {
    if (!isSimulator) {
      setState(null)
      return
    }
    let alive = true
    const poll = async () => {
      try {
        const res = await fetch('/sandbox/api/simulator/status', { credentials: 'include' })
        const data = await res.json()
        if (!alive) return
        if (data.status === 'success' && data.simulator) {
          setState({
            mode: data.simulator.mode,
            reason: data.simulator.reason,
            replay_date: data.simulator.replay_date ?? null,
          })
        }
      } catch {
        // Leave the last known state on a transient poll failure.
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [isSimulator])

  if (!isSimulator || !state) return null

  if (state.mode === 'live') {
    return (
      <span className="flex items-center gap-1.5 rounded border border-emerald-600/40 bg-emerald-600/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
        LIVE
      </span>
    )
  }

  const title =
    state.reason === 'market_closed_fallback'
      ? 'Market is closed — showing the most recently recorded day'
      : 'Replay explicitly started from Sandbox'

  return (
    <span
      title={title}
      className="flex items-center gap-1.5 rounded border border-amber-600/40 bg-amber-600/10 px-2 py-0.5 text-xs font-medium text-amber-500"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
      REPLAY{state.replay_date ? ` — ${state.replay_date}` : ''}
    </span>
  )
}

const NO_DRAW: DrawStats = {
  count: 0,
  canUndo: false,
  canRedo: false,
  hasSelection: false,
  magnet: false,
  tool: null,
  shortcuts: {},
}

/**
 * Grid layout presets. Each preset is a
 * CSS grid: `areas` names the cells, `cells` maps each pane (in order) to a named
 * area — so a pane can span (e.g. the big left chart in "1 + 2").
 */
interface LayoutPreset {
  id: string
  label: string
  cols: string
  rows: string
  areas: string
  cells: string[]
}

const LAYOUTS: LayoutPreset[] = [
  { id: 'single', label: 'Single', cols: '1fr', rows: '1fr', areas: '"a"', cells: ['a'] },
  {
    id: 'cols2',
    label: '2 columns',
    cols: '1fr 1fr',
    rows: '1fr',
    areas: '"a b"',
    cells: ['a', 'b'],
  },
  {
    id: 'rows2',
    label: '2 rows',
    cols: '1fr',
    rows: '1fr 1fr',
    areas: '"a" "b"',
    cells: ['a', 'b'],
  },
  {
    id: 'oneTwo',
    label: '1 + 2',
    cols: '1.4fr 1fr',
    rows: '1fr 1fr',
    areas: '"a b" "a c"',
    cells: ['a', 'b', 'c'],
  },
  {
    id: 'grid4',
    label: '2 × 2',
    cols: '1fr 1fr',
    rows: '1fr 1fr',
    areas: '"a b" "c d"',
    cells: ['a', 'b', 'c', 'd'],
  },
  {
    id: 'grid6',
    label: '3 × 2',
    cols: '1fr 1fr 1fr',
    rows: '1fr 1fr',
    areas: '"a b c" "d e f"',
    cells: ['a', 'b', 'c', 'd', 'e', 'f'],
  },
  {
    id: 'grid8',
    label: '4 × 2',
    cols: '1fr 1fr 1fr 1fr',
    rows: '1fr 1fr',
    areas: '"a b c d" "e f g h"',
    cells: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'],
  },
]

const LAYOUT_KEY = 'oa-trading-layout'

/** Mini glyph that previews a layout preset (renders the actual grid arrangement). */
function LayoutIcon({ preset, className }: { preset: LayoutPreset; className?: string }) {
  return (
    <span
      className={cn('grid h-4 w-4 gap-px', className)}
      style={{
        gridTemplateColumns: preset.cols,
        gridTemplateRows: preset.rows,
        gridTemplateAreas: preset.areas,
      }}
      aria-hidden="true"
    >
      {preset.cells.map((c) => (
        <span key={c} style={{ gridArea: c }} className="rounded-[1px] bg-current" />
      ))}
    </span>
  )
}

export default function Trading() {
  const { isLoaded: brokerLoaded, fetchCapabilities } = useBrokerStore()
  const [layoutId, setLayoutId] = useState(() => {
    const saved = localStorage.getItem(LAYOUT_KEY)
    return LAYOUTS.some((l) => l.id === saved) ? (saved as string) : 'single'
  })
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [wsUrl, setWsUrl] = useState<string | null>(null)
  const [noApiKey, setNoApiKey] = useState(false)

  /* ── one drawing rail for every pane ─────────────────────────────────── */
  const [tool, setTool] = useState<string | null>(null)
  const [magnet, setMagnet] = useState(false)
  const [showRail, setShowRail] = useState(true)
  const [stats, setStats] = useState<DrawStats>(NO_DRAW)
  // Undo / delete act on the pane you last drew in; arming a tool hits them all,
  // so whichever pane you click next is the one that gets the shape.
  const activeRef = useRef<TradingTerminal | null>(null)

  const focusPane = useCallback((t: TradingTerminal | null) => {
    activeRef.current = t
    if (t) setStats(t.drawStats())
  }, [])
  const railStats: DrawStats = { ...stats, tool, magnet }
  /**
   * Hand a key event to the focused pane; it reports whether a tool claimed it.
   * The chord table ships with the lazily loaded draw tier, so the terminal --
   * not this page and not the rail -- is what can answer.
   */
  const armByShortcut = useCallback((e: KeyboardEvent) => {
    const t = activeRef.current
    if (!t || !t.armByShortcut(e)) return false
    setTool(t.drawStats().tool)
    setStats(t.drawStats())
    return true
  }, [])

  const act = (fn: (t: TradingTerminal) => void) => {
    const t = activeRef.current
    if (!t) return
    fn(t)
    setStats(t.drawStats())
  }

  useEffect(() => {
    localStorage.setItem(LAYOUT_KEY, layoutId)
  }, [layoutId])

  useEffect(() => {
    if (!brokerLoaded) fetchCapabilities()
  }, [brokerLoaded, fetchCapabilities])

  // Fetch the API key + WS URL once; every pane shares them.
  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [keyRes, cfgRes] = await Promise.all([
          fetch('/api/websocket/apikey').then((r) => r.json()),
          fetch('/api/websocket/config').then((r) => r.json()),
        ])
        if (!alive) return
        if (keyRes.status !== 'success') {
          setNoApiKey(true)
          return
        }
        setApiKey(keyRes.api_key)
        setWsUrl(cfgRes.websocket_url || 'ws://127.0.0.1:8765')
      } catch {
        if (alive) setNoApiKey(true)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const layout = LAYOUTS.find((l) => l.id === layoutId) ?? LAYOUTS[0]

  return (
    <>
      <Navbar />
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Layout selector (visual presets) */}
        <div className="flex items-center gap-2 border-b bg-background/95 px-3 py-1.5">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-8 gap-1.5">
                <LayoutGrid className="h-4 w-4" />
                <span className="text-xs font-medium">Layout</span>
                <ChevronDown className="h-3.5 w-3.5 opacity-60" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              <div className="grid grid-cols-4 gap-1 p-1">
                {LAYOUTS.map((l) => (
                  <DropdownMenuItem
                    key={l.id}
                    onSelect={() => setLayoutId(l.id)}
                    title={l.label}
                    className={cn(
                      'flex aspect-square flex-col items-center justify-center gap-1 rounded border',
                      l.id === layoutId
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'text-muted-foreground'
                    )}
                  >
                    <LayoutIcon preset={l} />
                    <span className="text-[9px] font-medium">{l.cells.length}</span>
                  </DropdownMenuItem>
                ))}
              </div>
            </DropdownMenuContent>
          </DropdownMenu>
          <span className="text-xs text-muted-foreground">{layout.label}</span>
          <SimulatorModeBadge />
        </div>

        {/* Rail + grid */}
        <main className="flex min-h-0 flex-1">
          {showRail && apiKey && wsUrl && (
            <DrawingRail
              stats={railStats}
              onPick={(id) => setTool(id)}
              onUndo={() => act((t) => t.undoDraw())}
              onRedo={() => act((t) => t.redoDraw())}
              onRemove={(all) => act((t) => t.removeDrawings(all))}
              onMagnet={(v) => setMagnet(v)}
              onShortcut={armByShortcut}
            />
          )}
          <div className="min-h-0 flex-1">
          {noApiKey ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-muted-foreground">No API key found for charting.</p>
              <a href="/apikey" className="text-sm font-medium text-primary underline">
                Generate an API key
              </a>
            </div>
          ) : apiKey && wsUrl ? (
            <div
              className="grid h-full min-h-0 gap-2 p-2"
              style={{
                gridTemplateColumns: layout.cols,
                gridTemplateRows: layout.rows,
                gridTemplateAreas: layout.areas,
              }}
            >
              {layout.cells.map((cell, i) => (
                <ChartPane
                  key={`p${i}`}
                  paneId={`p${i}`}
                  apiKey={apiKey}
                  wsUrl={wsUrl}
                  style={{ gridArea: cell }}
                  sharedTool={tool}
                  sharedMagnet={magnet}
                  onFocusPane={focusPane}
                  onDrawStats={setStats}
                  onToggleRail={() => setShowRail((v) => !v)}
                  railVisible={showRail}
                />
              ))}
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Loading charting terminal…
            </div>
          )}
          </div>
        </main>
      </div>
    </>
  )
}
