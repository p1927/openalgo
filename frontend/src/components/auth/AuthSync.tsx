import { useEffect, useState } from 'react'
import { API_BASE_URL } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'
import { useSessionStore } from '@/stores/sessionStore'
import { useThemeStore } from '@/stores/themeStore'

interface AuthSyncProps {
  children: React.ReactNode
}

/**
 * AuthSync component that synchronizes Flask session with Zustand store.
 * This ensures the React app knows about authentication state from OAuth callbacks.
 * Also syncs app mode (live/analyzer) from the backend.
 */
export function AuthSync({ children }: AuthSyncProps) {
  const [isChecking, setIsChecking] = useState(true)
  const [syncError, setSyncError] = useState(false)
  const { setUser, setApiKey, logout } = useAuthStore()
  const { fetchCapabilities, clearCapabilities } = useBrokerStore()
  const { setActiveSessionCount } = useSessionStore()
  const { syncAppMode } = useThemeStore()

  useEffect(() => {
    const syncSession = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/session-status`, {
          credentials: 'include',
        })

        if (response.ok) {
          const data = await response.json()

          if (data.status === 'success' && data.logged_in && data.broker) {
            // Flask session is authenticated with broker - sync to Zustand
            setUser({
              username: data.user,
              broker: data.broker,
              isLoggedIn: true,
              loginTime: new Date().toISOString(),
            })
            // Store the API key for trading API calls
            if (data.api_key) {
              setApiKey(data.api_key)
            }
            // Fetch broker capabilities (exchanges, type, features)
            await fetchCapabilities()
            // Also sync app mode from backend
            await syncAppMode()
            // Sync active session count
            if (data.active_sessions !== undefined) {
              setActiveSessionCount(data.active_sessions)
            }
          } else if (data.status === 'success' && data.authenticated && !data.logged_in) {
            // User is logged in but hasn't connected broker yet
            setUser({
              username: data.user,
              broker: null,
              isLoggedIn: false,
              loginTime: null,
            })
            clearCapabilities()
          } else {
            // Not authenticated or status is not success - clear Zustand store
            logout()
            clearCapabilities()
          }
        } else {
          // Any non-OK response (401, 500, etc.) - clear Zustand store
          logout()
          clearCapabilities()
        }
      } catch (_error) {
        // The session-status check itself failed (network error, backend
        // mid-restart during a broker switch, etc). Rendering children
        // against stale persisted auth/broker state here is what produces
        // a silent blank screen - clear the stale state instead and show
        // a retry so the user gets a signal rather than nothing.
        logout()
        clearCapabilities()
        setSyncError(true)
      } finally {
        setIsChecking(false)
      }
    }

    syncSession()
  }, [setUser, setApiKey, logout, fetchCapabilities, clearCapabilities, syncAppMode, setActiveSessionCount])

  // Show nothing while checking - prevents flash of wrong content
  if (isChecking) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  if (syncError) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-sm text-muted-foreground">
            Could not verify your session. Please log in again.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-sm underline text-primary"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
