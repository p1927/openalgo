import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, PiggyBank, ShieldAlert, Wallet } from 'lucide-react'
import { useMemo } from 'react'
import { portfolioLedgerApi } from '@/api/portfolio-ledger'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { makeFormatCurrency } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'

export default function PortfolioLedger() {
  const broker = useAuthStore((state) => state.user?.broker)
  const formatCurrency = useMemo(() => makeFormatCurrency(broker), [broker])

  const rollupQuery = useQuery({
    queryKey: ['portfolio-ledger', 'rollup'],
    queryFn: () => portfolioLedgerApi.getRollup(),
    refetchInterval: 15_000,
  })

  const performanceQuery = useQuery({
    queryKey: ['portfolio-ledger', 'performance'],
    queryFn: () => portfolioLedgerApi.getPerformance(),
  })

  const rollup = rollupQuery.data
  const performance = performanceQuery.data

  return (
    <div className="container mx-auto max-w-5xl space-y-6 p-4">
      <div>
        <h1 className="text-2xl font-semibold">Portfolio Ledger</h1>
        <p className="text-muted-foreground text-sm">
          Capital currently at risk, profit already banked, and what could safely be withdrawn right
          now — sandbox mode only.
        </p>
      </div>

      {rollupQuery.isLoading && <SummarySkeleton />}

      {rollupQuery.isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Failed to load the portfolio rollup</AlertTitle>
          <AlertDescription>
            {rollupQuery.error instanceof Error ? rollupQuery.error.message : 'Unknown error'}
          </AlertDescription>
        </Alert>
      )}

      {rollupQuery.isSuccess && rollup?.status === 'error' && (
        <Alert variant="warning">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>No sandbox data yet</AlertTitle>
          <AlertDescription>{rollup.message}</AlertDescription>
        </Alert>
      )}

      {rollupQuery.isSuccess && rollup?.status === 'success' && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <SummaryCard
              icon={<ShieldAlert className="h-4 w-4 text-amber-600" />}
              title="Capital at Risk"
              description="Max-risk across every currently-open, risk-profiled strategy"
              value={formatCurrency(rollup.capital_at_risk ?? 0)}
            />
            <SummaryCard
              icon={<PiggyBank className="h-4 w-4 text-emerald-600" />}
              title="Banked P&L"
              description="Realized P&L, all-time"
              value={formatCurrency(rollup.banked_pnl ?? 0)}
            />
            <SummaryCard
              icon={<Wallet className="h-4 w-4 text-blue-600" />}
              title="Safe to Withdraw"
              description="Banked profit minus capital currently at risk"
              value={formatCurrency(rollup.safe_to_withdraw ?? 0)}
              emphasis={(rollup.safe_to_withdraw ?? 0) < 0 ? 'negative' : 'positive'}
            />
          </div>

          {rollup.capital && (
            <Card>
              <CardHeader>
                <CardTitle>Capital Account</CardTitle>
                <CardDescription>Sandbox funds snapshot</CardDescription>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <Field label="Total Capital" value={formatCurrency(rollup.capital.total_capital)} />
                <Field
                  label="Available Balance"
                  value={formatCurrency(rollup.capital.available_balance)}
                />
                <Field label="Used Margin" value={formatCurrency(rollup.capital.used_margin)} />
                <Field
                  label="Realized P&L (all-time)"
                  value={formatCurrency(rollup.capital.realized_pnl)}
                />
                <Field
                  label="Realized P&L (today)"
                  value={formatCurrency(rollup.capital.today_realized_pnl)}
                />
                <Field
                  label="Unrealized P&L"
                  value={formatCurrency(rollup.capital.unrealized_pnl)}
                />
              </CardContent>
            </Card>
          )}

          {rollup.unprofiled_open_strategies && rollup.unprofiled_open_strategies.length > 0 && (
            <Alert variant="warning">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Open strategies with no recorded risk profile</AlertTitle>
              <AlertDescription>
                These are open but excluded from "Capital at Risk" — their max-risk was never
                recorded at entry, so counting them as zero risk would overstate what's safe to
                withdraw: {rollup.unprofiled_open_strategies.join(', ')}
              </AlertDescription>
            </Alert>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Open Strategies</CardTitle>
              <CardDescription>Risk profile recorded at entry</CardDescription>
            </CardHeader>
            <CardContent>
              {rollup.open_strategies && rollup.open_strategies.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Strategy</TableHead>
                      <TableHead className="text-right">Max Risk</TableHead>
                      <TableHead className="text-right">Max Profit</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rollup.open_strategies.map((strategy) => (
                      <TableRow key={strategy.strategy}>
                        <TableCell>{strategy.strategy}</TableCell>
                        <TableCell className="text-right">
                          {formatCurrency(strategy.max_risk)}
                        </TableCell>
                        <TableCell className="text-right">
                          {strategy.max_profit === null
                            ? 'Uncapped'
                            : formatCurrency(strategy.max_profit)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-muted-foreground text-sm">No open, risk-profiled strategies.</p>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Performance</CardTitle>
          <CardDescription>
            Win rate and expectancy over realized-trade-closure events, all-time
          </CardDescription>
        </CardHeader>
        <CardContent>
          {performanceQuery.isLoading && <Skeleton className="h-24 w-full" />}
          {performanceQuery.isSuccess && performance?.status === 'success' && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Field label="Trades" value={String(performance.trade_count ?? 0)} />
              <Field
                label="Win / Loss / Breakeven"
                value={`${performance.win_count ?? 0} / ${performance.loss_count ?? 0} / ${performance.breakeven_count ?? 0}`}
              />
              <Field
                label="Win Rate"
                value={
                  performance.win_rate === null || performance.win_rate === undefined
                    ? '—'
                    : `${(performance.win_rate * 100).toFixed(1)}%`
                }
              />
              <Field
                label="Expectancy"
                value={
                  performance.expectancy === null || performance.expectancy === undefined
                    ? '—'
                    : formatCurrency(performance.expectancy)
                }
              />
              <Field
                label="Average Win"
                value={
                  performance.average_win === null || performance.average_win === undefined
                    ? '—'
                    : formatCurrency(performance.average_win)
                }
              />
              <Field
                label="Average Loss"
                value={
                  performance.average_loss === null || performance.average_loss === undefined
                    ? '—'
                    : formatCurrency(performance.average_loss)
                }
              />
              <Field
                label="Gross Profit / Loss"
                value={`${formatCurrency(performance.gross_profit ?? 0)} / ${formatCurrency(performance.gross_loss ?? 0)}`}
              />
              <Field label="Net P&L" value={formatCurrency(performance.net_pnl ?? 0)} />
            </div>
          )}
          {performanceQuery.isSuccess && performance?.status === 'error' && (
            <p className="text-muted-foreground text-sm">{performance.message}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SummaryCard({
  icon,
  title,
  description,
  value,
  emphasis,
}: {
  icon: React.ReactNode
  title: string
  description: string
  value: string
  emphasis?: 'positive' | 'negative'
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          {icon}
          {title}
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <span
          className={
            emphasis === 'negative'
              ? 'text-2xl font-semibold text-destructive'
              : emphasis === 'positive'
                ? 'text-2xl font-semibold text-emerald-600'
                : 'text-2xl font-semibold'
          }
        >
          {value}
        </span>
      </CardContent>
    </Card>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}

function SummarySkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {['risk', 'banked', 'withdraw'].map((key) => (
        <Card key={key}>
          <CardHeader>
            <Skeleton className="h-4 w-24" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-8 w-32" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
