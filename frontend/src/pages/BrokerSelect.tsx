import { BookOpen, ExternalLink, Info, Loader2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { prepareConnect } from '@/api/brokers'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useBrokers } from '@/hooks/useBrokers'
import { useAuthStore } from '@/stores/authStore'
import type { BrokerDescriptor } from '@/types/broker'

export default function BrokerSelect() {
  const { user } = useAuthStore()
  const { data, isLoading, error: loadError } = useBrokers()
  const [selectedBroker, setSelectedBroker] = useState<string>('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const brokers = data?.brokers ?? []
  const defaultBroker = data?.default_broker ?? ''

  useEffect(() => {
    if (selectedBroker || brokers.length === 0) {
      return
    }
    const preferred =
      defaultBroker && brokers.some((broker) => broker.id === defaultBroker)
        ? defaultBroker
        : (brokers[0]?.id ?? '')
    if (preferred) {
      setSelectedBroker(preferred)
    }
  }, [defaultBroker, brokers, selectedBroker])

  const selectedDescriptor = useMemo(
    () => brokers.find((broker) => broker.id === selectedBroker),
    [brokers, selectedBroker]
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!selectedBroker) {
      setError('Please select a broker')
      return
    }

    setIsSubmitting(true)
    setError(null)

    try {
      const result = await prepareConnect(selectedBroker)
      if (result.status !== 'success' || !result.connect_url) {
        setError(result.message || 'Failed to prepare broker connection')
        setIsSubmitting(false)
        return
      }
      window.location.href = result.connect_url
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { message?: string } } })?.response?.data?.message ||
        'Failed to connect to broker'
      setError(message)
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    )
  }

  const listError =
    error ||
    (loadError ? 'Failed to load broker list from server' : null) ||
    (brokers.length === 0
      ? 'No brokers configured. Check VALID_BROKERS in .env and ensure broker plugins exist.'
      : null)

  return (
    <div className="min-h-screen flex items-center justify-center py-8 px-4">
      <div className="container max-w-6xl">
        <div className="flex flex-col lg:flex-row items-center justify-between gap-8 lg:gap-16">
          <Card className="w-full max-w-md shadow-xl order-1 lg:order-2">
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <img src="/logo.png" alt="OpenAlgo" className="h-20 w-20" />
              </div>
              <CardTitle className="text-2xl">Connect Your Trading Account</CardTitle>
              <CardDescription>
                Welcome, <span className="font-medium">{user?.username}</span>!
              </CardDescription>
            </CardHeader>
            <CardContent>
              {listError && (
                <Alert variant="destructive" className="mb-4">
                  <AlertDescription>{listError}</AlertDescription>
                </Alert>
              )}

              <form onSubmit={handleSubmit} className="space-y-6">
                <div className="space-y-2">
                  <Label htmlFor="broker-select" className="block text-center">
                    Connect with broker
                  </Label>
                  {brokers.length > 0 ? (
                    <Select
                      value={selectedBroker}
                      onValueChange={setSelectedBroker}
                      disabled={isSubmitting}
                    >
                      <SelectTrigger id="broker-select" className="w-full">
                        <SelectValue placeholder="Select a broker" />
                      </SelectTrigger>
                      <SelectContent>
                        {brokers.map((broker: BrokerDescriptor) => (
                          <SelectItem key={broker.id} value={broker.id}>
                            {broker.display_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <p className="text-sm text-muted-foreground text-center">
                      No brokers available for this installation.
                    </p>
                  )}
                </div>

                {selectedDescriptor?.login_notice && (
                  <Alert className="border-amber-500/50 bg-amber-500/10">
                    <Info className="h-4 w-4 text-amber-500" />
                    <AlertDescription className="text-amber-700 dark:text-amber-400">
                      {selectedDescriptor.login_notice}
                    </AlertDescription>
                  </Alert>
                )}

                <Button
                  type="submit"
                  className="w-full"
                  disabled={!selectedBroker || isSubmitting || brokers.length === 0}
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    <>
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Connect Account
                    </>
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>

          <div className="flex-1 max-w-xl text-center lg:text-left order-2 lg:order-1">
            <h1 className="text-4xl lg:text-5xl font-bold mb-6">
              Connect Your <span className="text-primary">Broker</span>
            </h1>
            <p className="text-lg lg:text-xl mb-8 text-muted-foreground">
              Choose any broker enabled on this OpenAlgo instance. Your selection applies to this
              login session; set a persistent default in Profile settings.
            </p>

            <Alert className="mb-6">
              <BookOpen className="h-4 w-4" />
              <AlertTitle>Need Help?</AlertTitle>
              <AlertDescription>Check our documentation for broker setup guides.</AlertDescription>
            </Alert>

            <div className="flex justify-center lg:justify-start gap-4">
              <Button variant="outline" asChild>
                <a href="https://docs.openalgo.in" target="_blank" rel="noopener noreferrer">
                  <BookOpen className="mr-2 h-4 w-4" />
                  Documentation
                </a>
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
