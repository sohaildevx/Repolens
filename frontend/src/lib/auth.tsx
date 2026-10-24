import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { getCurrentUser, logout } from '@/lib/api'

interface AuthState {
  user: { id: number; username: string } | null
  loading: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  refresh: async () => {},
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthState['user']>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setUser(await getCurrentUser())
    } catch {
      setUser(null)
    }
  }, [])

  const signOut = useCallback(async () => {
    try {
      await logout()
    } catch {
      /* already signed out or network hiccup -- state is refreshed below */
    }
    await refresh()
  }, [refresh])

  useEffect(() => {
    let cancelled = false
    getCurrentUser()
      .then((u) => {
        if (!cancelled) {
          setUser(u)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUser(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const value = useMemo(
    () => ({ user, loading, refresh, signOut }),
    [user, loading, refresh, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// oxlint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  return useContext(AuthContext)
}
