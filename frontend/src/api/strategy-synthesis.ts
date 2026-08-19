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
  risk_score: number
  /** P(profit at expiry), 0..1 — 0.5 when the backend had no live spot/IV to estimate from. */
  win_probability: number
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
