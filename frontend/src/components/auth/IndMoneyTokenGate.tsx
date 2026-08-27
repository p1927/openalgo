import { AlertCircle, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { API_BASE_URL, fetchCSRFToken } from '@/api/client'

type Health = { status?: string; checked_at?: string }

/** Full-screen repair gate shown only for a confirmed direct recorder 401/403. */
export function IndMoneyTokenGate({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [token, setToken] = useState('')
  const [working, setWorking] = useState(false)
  const [message, setMessage] = useState('')
  const expired = health?.status === 'expired_or_revoked'

  const check = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/broker/indmoney-recorder-token`, { credentials: 'include' })
      if (response.ok) setHealth((await response.json()).data)
    } catch {
      // An unavailable check must never lock a user out of OpenAlgo.
    }
  }

  useEffect(() => {
    void check()
    const id = window.setInterval(() => void check(), 5 * 60_000)
    return () => window.clearInterval(id)
  }, [])

  const applyPaste = async (value: string) => {
    const pasted = value.trim()
    if (!pasted || working) return
    setToken(pasted)
    setWorking(true)
    setMessage('Validating and applying the new access token…')
    try {
      const csrf = await fetchCSRFToken()
      const response = await fetch(`${API_BASE_URL}/api/broker/indmoney-recorder-token`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ token: pasted }),
      })
      const data = await response.json()
      if (response.ok && data.status === 'success') {
        setToken('')
        setHealth(data.data)
        setMessage('Connection verified. Opening your dashboard…')
      } else setMessage(data.message || 'The token could not be verified. Paste a fresh token and try again.')
    } catch {
      setMessage('Could not reach OpenAlgo. Please try again.')
    } finally {
      setWorking(false)
    }
  }

  if (!expired) return <>{children}</>
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_#fef3c7,_#fffaf0_45%,_#f4f8f5)] px-6 py-12 text-stone-900">
      <section className="mx-auto max-w-xl border-l-4 border-amber-600 bg-white/90 p-8 shadow-[0_24px_70px_-36px_rgba(120,53,15,.5)]">
        <p className="mb-8 text-xs font-semibold tracking-[.22em] text-amber-800">OPENALGO · RECORDER ACCESS</p>
        <ShieldCheck className="mb-5 h-9 w-9 text-amber-700" />
        <h1 className="text-3xl font-semibold tracking-tight">Your IndMoney access token needs renewal.</h1>
        <p className="mt-4 leading-7 text-stone-600">Paste today’s token. OpenAlgo will validate it, save it to the Trade root configuration, apply it to the live recorder, and return you here only when the connection is working.</p>
        <Input value={token} type="password" autoComplete="off" className="mt-8 h-12 font-mono" placeholder="Paste access token" onChange={(e) => setToken(e.target.value)} onPaste={(e) => { const value = e.clipboardData.getData('text'); window.setTimeout(() => void applyPaste(value), 0) }} />
        <Button className="mt-4" disabled={!token || working} onClick={() => void applyPaste(token)}>
          {working ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Verify and continue
        </Button>
        {message ? <p className="mt-5 flex gap-2 text-sm text-stone-600"><AlertCircle className="h-4 w-4 shrink-0" />{message}</p> : null}
      </section>
    </main>
  )
}
