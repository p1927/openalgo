import { webClient } from '@/api/client'
import type {
  BrokersListResponse,
  PrepareConnectResponse,
} from '@/types/broker'

function parseBrokersListResponse(payload: unknown): BrokersListResponse {
  if (!payload || typeof payload !== 'object') {
    throw new Error(
      'Broker list API returned an invalid response. Restart OpenAlgo to load the latest backend.'
    )
  }
  const data = payload as Partial<BrokersListResponse>
  if (data.status !== 'success' || !Array.isArray(data.brokers)) {
    const message =
      typeof (payload as { message?: string }).message === 'string'
        ? (payload as { message: string }).message
        : 'Broker list API returned an unexpected shape. Restart OpenAlgo and refresh.'
    throw new Error(message)
  }
  return data as BrokersListResponse
}

export async function fetchBrokers(): Promise<BrokersListResponse> {
  const response = await webClient.get<BrokersListResponse>('/auth/brokers')
  return parseBrokersListResponse(response.data)
}

export async function prepareConnect(brokerId: string): Promise<PrepareConnectResponse> {
  const response = await webClient.post<PrepareConnectResponse>(
    '/auth/broker/prepare-connect',
    { broker: brokerId }
  )
  return response.data
}
