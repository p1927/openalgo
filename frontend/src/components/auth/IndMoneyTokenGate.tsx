import { AlertCircle, Eye, EyeOff, Info, Loader2, ShieldCheck } from 'lucide-react'
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
  const [showToken, setShowToken] = useState(false)
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
    <main className="flex min-h-screen items-center px-4 py-8">
      <div className="container mx-auto max-w-6xl">
        <div className="flex flex-col items-center justify-between gap-8 lg:flex-row lg:gap-16">
          <Card className="order-1 w-full max-w-xl shadow-xl lg:order-2">
            <CardHeader className="text-center">
              <div className="mb-4 flex justify-center"><img src={`${import.meta.env.BASE_URL}logo.png`} alt="OpenAlgo" className="h-20 w-20" /></div>
              <CardTitle className="text-2xl">Refresh access token</CardTitle>
              <CardDescription>Reconnect IndMoney live market data</CardDescription>
            </CardHeader>
            <CardContent><div className="space-y-4">
              <div className="space-y-2"><Label htmlFor="indmoney-token">IndMoney access token</Label><div className="relative"><Input id="indmoney-token" value={token} type={showToken ? 'text' : 'password'} autoComplete="off" className="pr-10 font-mono text-xs" placeholder="Paste your access token" onChange={(e) => setToken(e.target.value)} onPaste={(e) => { const value = e.clipboardData.getData('text'); window.setTimeout(() => void applyPaste(value), 0) }} /><Button type="button" variant="ghost" size="icon" className="absolute right-0 top-0 h-full px-3 hover:bg-transparent" onClick={() => setShowToken((value) => !value)} aria-label={showToken ? 'Hide access token' : 'Show access token'}>{showToken ? <EyeOff className="h-4 w-4 text-muted-foreground" /> : <Eye className="h-4 w-4 text-muted-foreground" />}</Button></div></div>
              {message ? <Alert variant={message.startsWith('Connection verified') ? 'default' : 'destructive'}><AlertCircle className="h-4 w-4" /><AlertDescription>{message}</AlertDescription></Alert> : null}
              <Button className="w-full" disabled={!token || working} onClick={() => void applyPaste(token)}>{working ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}Verify and continue</Button>
            </div></CardContent>
          </Card>
          <div className="order-2 max-w-xl flex-1 text-center lg:order-1 lg:text-left">
            <h1 className="mb-6 text-4xl font-bold lg:text-5xl">Keep your <span className="text-primary">market data</span> connected.</h1>
            <p className="mb-8 text-lg text-muted-foreground lg:text-xl">Your IndMoney access token expired at the daily rollover. Paste today’s token and OpenAlgo will validate it, apply it to the live recorder, and return you to the dashboard.</p>
            <Alert className="mb-6 text-left"><Info className="h-4 w-4" /><AlertTitle>What happens next?</AlertTitle><AlertDescription>The token is saved to the Trade root configuration, synced to OpenAlgo, and tested against the live INDmoney API before this screen closes.</AlertDescription></Alert>
          </div>
        </div>
      </div>
    </main>
  )
}
