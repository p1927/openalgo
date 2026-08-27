import { AlertCircle, Info, Loader2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
    <main className="flex min-h-screen items-center justify-center px-4 py-8">
      <div className="w-full max-w-md text-center">
          <Card className="w-full shadow-xl">
            <CardHeader className="text-center">
              <div className="mb-4 flex justify-center"><img src={`${import.meta.env.BASE_URL}logo.png`} alt="OpenAlgo" className="h-20 w-20" /></div>
              <CardTitle className="text-2xl">Refresh access token</CardTitle>
              <CardDescription>Reconnect IndMoney live market data</CardDescription>
            </CardHeader>
            <CardContent><div className="space-y-4 text-left">
              <Alert><Info className="h-4 w-4" /><AlertTitle>Token renewal required</AlertTitle><AlertDescription>Your token expired at the daily rollover. We’ll save, sync, and test the replacement before returning you to the dashboard.</AlertDescription></Alert>
              <div className="space-y-2"><Label htmlFor="indmoney-token">IndMoney access token</Label><Input id="indmoney-token" value={token} type="password" autoComplete="off" placeholder="Paste your access token" onChange={(e) => setToken(e.target.value)} onPaste={(e) => { const value = e.clipboardData.getData('text'); window.setTimeout(() => void applyPaste(value), 0) }} /></div>
              {message ? <Alert variant={message.startsWith('Connection verified') ? 'default' : 'destructive'}><AlertCircle className="h-4 w-4" /><AlertDescription>{message}</AlertDescription></Alert> : null}
              <Button className="w-full" disabled={!token || working} onClick={() => void applyPaste(token)}>{working ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}Verify and continue</Button>
            </div></CardContent>
          </Card>
      </div>
    </main>
  )
}
