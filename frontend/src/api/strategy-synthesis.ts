import { webClient } from './client'

export interface SynthesisTargetPoint {
  price: number
  pnl: number
}

export interface SynthesisRequest {
  underlying: string
  exchange: string
  expiry_date: string
  target_points: [number, number][]
  max_legs: number
  lot_size?: number
  /** Desired max profit in rupees, per lot. Optional — adds a rupee-closeness scoring axis. */
  target_max_profit?: number
  /** Desired max loss in rupees, per lot (a positive number — "don't lose more than this"). */
  target_max_loss?: number
}

export interface SynthesizedLegResult {
  strike: number
  option_type: 'CE' | 'PE'
  side: 'BUY' | 'SELL'
  premium: number
  qty: number
  /** Broker-tradeable symbol for this strike/type, when the option chain had one. */
  symbol: string | null
}

export interface SynthesisResult {
  score: number
  shape_score: number
  /**
   * Absolute max profit normalized to [0, 1] against an adaptive
   * benchmark derived from the option chain. 1.0 means unlimited upside
   * (naked long call) or max profit above the benchmark; 0.0 means
   * zero/negative or no realized max profit on the sampled price grid.
   */
  profit_score: number
  /**
   * Absolute max loss normalized to [0, 1] (higher = better, i.e. smaller
   * loss). 0.0 means unlimited downside (naked short call); 1.0 means
   * can't lose (fully hedged or the payoff never goes negative).
   */
  loss_score: number
  /**
   * Additive subtractor applied to the base score for leg count: a
   * 1-leg combo has 0, a 2-leg has `leg_count_penalty`, a 4-leg has
   * 3 * `leg_count_penalty`. Captures the per-leg fee cost (brokerage
   * + STT + GST + exchange) on India options.
   */
  leg_count_penalty: number
  /** P(profit at expiry), 0..1 — 0.5 when the backend had no live spot/IV to estimate from. */
  win_probability: number
  /** Closeness to the rupee max profit/loss target, 0..1 — null when no rupee target was set. */
  rupee_score: number | null
  /** null means unlimited (a net-long-calls combo with unbounded upside). */
  max_profit: number | null
  /** null means unlimited (a net-short-calls combo with unbounded loss). */
  max_loss: number | null
  breakevens: number[]
  legs: SynthesizedLegResult[]
}

export interface SynthesisData {
  underlying_ltp: number
  results: SynthesisResult[]
}

export interface SynthesisResponse {
  status: 'success' | 'error'
  message?: string
  data?: SynthesisData
}

// Let 4xx responses resolve instead of throw — the backend returns structured
// `{status: "error", message: "..."}` bodies for user-fixable states (no
// tradable strikes, bad max_legs, etc). Throwing swallows those and the
// caller falls back to a generic message.
const allow4xx = { validateStatus: (s: number) => s < 500 }

export const strategySynthesisApi = {
  synthesize: async (params: SynthesisRequest): Promise<SynthesisResponse> => {
    const response = await webClient.post<SynthesisResponse>(
      '/strategybuilder/api/synthesize',
      params,
      allow4xx
    )
    return response.data
  },
}
