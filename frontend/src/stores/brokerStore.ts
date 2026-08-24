import { create } from 'zustand'
import { API_BASE_URL } from '@/api/client'

interface BrokerCapabilities {
  broker_name: string
  broker_type: 'IN_stock' | 'crypto'
  supported_exchanges: string[]
  leverage_config: boolean
}

interface BrokerStore {
  capabilities: BrokerCapabilities | null
  isLoaded: boolean

  fetchCapabilities: () => Promise<void>
  clearCapabilities: () => void
}

export const useBrokerStore = create<BrokerStore>()((set) => ({
  capabilities: null,
  isLoaded: false,

  fetchCapabilities: async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/broker/capabilities`, {
        credentials: 'include',
      })

      if (response.ok) {
        const data = await response.json()
        if (data.status === 'success' && data.data) {
          set({ capabilities: data.data, isLoaded: true })
        }
      }
    } catch {
      // Silently fail — capabilities will be null, pages fall back to showing all exchanges
    }
  },

  clearCapabilities: () => set({ capabilities: null, isLoaded: false }),
}))
