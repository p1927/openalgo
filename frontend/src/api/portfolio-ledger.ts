import { webClient } from './client'

export interface CapitalAccount {
  total_capital: number
  available_balance: number
  used_margin: number
  realized_pnl: number
  today_realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
}

export interface CapitalAccountResponse extends Partial<CapitalAccount> {
  status: 'success' | 'error'
  message?: string
}

export interface OpenStrategyRiskProfile {
  strategy: string
  max_risk: number
  max_profit: number | null
}

export interface PortfolioRollup {
  capital: CapitalAccount
  capital_at_risk: number
  banked_pnl: number
  safe_to_withdraw: number
  open_strategies: OpenStrategyRiskProfile[]
  unprofiled_open_strategies: string[]
}

export interface PortfolioRollupResponse extends Partial<PortfolioRollup> {
  status: 'success' | 'error'
  message?: string
}

export interface StrategyPerformance {
  trade_count: number
  win_count: number
  loss_count: number
  breakeven_count: number
  win_rate: number | null
  expectancy: number | null
  average_win: number | null
  average_loss: number | null
  gross_profit: number
  gross_loss: number
  net_pnl: number
}

export interface StrategyPerformanceResponse extends Partial<StrategyPerformance> {
  status: 'success' | 'error'
  message?: string
}

// Let 4xx responses resolve instead of throw — the backend returns structured
// `{status: "error", message: "..."}` bodies for user-fixable states (no
// sandbox funds row yet, strategy book unavailable) — see strategy-chart.ts.
const allow4xx = { validateStatus: (s: number) => s < 500 }

export const portfolioLedgerApi = {
  getCapitalAccount: async (): Promise<CapitalAccountResponse> => {
    const response = await webClient.get<CapitalAccountResponse>(
      '/portfolio-ledger/api/capital-account',
      allow4xx
    )
    return response.data
  },

  getRollup: async (): Promise<PortfolioRollupResponse> => {
    const response = await webClient.get<PortfolioRollupResponse>(
      '/portfolio-ledger/api/rollup',
      allow4xx
    )
    return response.data
  },

  getPerformance: async (strategy?: string): Promise<StrategyPerformanceResponse> => {
    const response = await webClient.get<StrategyPerformanceResponse>(
      '/portfolio-ledger/api/performance',
      { ...allow4xx, params: strategy ? { strategy } : undefined }
    )
    return response.data
  },
}
