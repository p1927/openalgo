import { AlertCircle, Loader2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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

  const check = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/broker/indmoney-recorder-token`, { credentials: 'include' })
      if (response.ok) setHealth((await response.json()).data)
    } catch {
      // An unavailable check must never lock a user out of OpenAlgo.
    }
  }, [])

  useEffect(() => {
    void check()
    const id = window.setInterval(() => void check(), 5 * 60_000)
    return () => window.clearInterval(id)
  }, [check])

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
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-8 text-foreground">
      <Card className="w-full max-w-lg border-border shadow-sm">
        <CardHeader className="space-y-3 border-b border-border bg-muted/40">
          <div className="flex items-center gap-3">
            <div className="rounded-md bg-primary/10 p-2 text-primary"><ShieldCheck className="h-5 w-5" /></div>
            <div><p className="text-xs font-medium text-muted-foreground">OpenAlgo · Recorder access</p><CardTitle>Renew IndMoney access token</CardTitle></div>
          </div>
          <CardDescription>Your token has expired or was revoked. Paste a new token to restore live market recording.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 pt-6">
          <Input value={token} type="password" autoComplete="off" className="h-10" placeholder="Paste access token" onChange={(e) => setToken(e.target.value)} onPaste={(e) => { const value = e.clipboardData.getData('text'); window.setTimeout(() => void applyPaste(value), 0) }} />
          <Button className="w-full" disabled={!token || working} onClick={() => void applyPaste(token)}>
            {working ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Verify and continue
          </Button>
          {message ? <p className="flex gap-2 rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-primary" />{message}</p> : null}
        </CardContent>
      </Card>
    </main>
  )
}
