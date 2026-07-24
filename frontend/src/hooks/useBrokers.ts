import { useQuery } from '@tanstack/react-query'
import { fetchBrokers } from '@/api/brokers'

export function useBrokers() {
  return useQuery({
    queryKey: ['auth', 'brokers'],
    queryFn: fetchBrokers,
    staleTime: 60_000,
  })
}
