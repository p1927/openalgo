import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useSimulatorStatus } from './useSimulatorStatus'

const ok = (simulator: object) =>
  ({ ok: true, json: async () => ({ status: 'success', simulator }) }) as Response

describe('useSimulatorStatus', () => {
  afterEach(() => vi.restoreAllMocks())

  it('reports an error instead of keeping a stale status when a poll fails', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(ok({ mode: 'live' }))
      .mockResolvedValue({ ok: false, status: 503, json: async () => ({}) } as Response)
    const { result } = renderHook(() => useSimulatorStatus({ pollMs: 20 }))
    await waitFor(() => expect(result.current.status?.mode).toBe('live'))
    await waitFor(() => expect(result.current.error).toMatch(/503/))
    expect(fetchMock).toHaveBeenCalled()
  })

  it('a non-success body is an error, and a later good poll clears it', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: 'error', message: 'no replay' }),
      } as Response)
      .mockResolvedValue(ok({ mode: 'replay' }))
    const { result } = renderHook(() => useSimulatorStatus({ pollMs: 300 }))
    await waitFor(() => expect(result.current.error).toBe('no replay'))
    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.status?.mode).toBe('replay')
  })
})
