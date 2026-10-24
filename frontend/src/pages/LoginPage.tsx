import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { ApiError, login } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { registerPathFor, safeNextPath } from '@/lib/next'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import {
  AuthFieldLabel,
  PasswordField,
} from '@/components/PasswordField'

/**
 * Sign-in page. The backend opens a Django session cookie (CSRF-aware,
 * same-origin); no credential or token is ever stored client-side.
 * The validated `next` parameter carries the visitor back to the page
 * they came from (e.g. the exact issue) -- nothing runs automatically
 * after login.
 */
export function LoginPage() {
  const [searchParams] = useSearchParams()
  const next = safeNextPath(searchParams.get('next'))
  const navigate = useNavigate()
  const { user, loading: authLoading, refresh } = useAuth()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return // double-submit guard
    }

    if (!username.trim() || !password) {
      setFieldError(
        !username.trim() ? 'Username is required.' : 'Password is required.',
      )
      return
    }
    setFieldError(null)
    setFormError(null)
    setSubmitting(true)
    try {
      await login(username, password)
      await refresh() // pick up the session for Navbar/FixAction
      navigate(next, { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        // 401 = the generic "Invalid username or password." from the
        // backend (no user enumeration). Anything else surfaces as-is.
        setFormError(err.message)
      } else {
        setFormError('Unexpected error -- please try again.')
      }
      setSubmitting(false)
    }
  }

  // Already signed in: offer the continuation instead of a second form.
  if (!authLoading && user) {
    return (
      <div className="mx-auto max-w-md px-6 py-20">
        <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
          Account
        </p>
        <h1 className="mt-4 text-3xl font-bold tracking-tight">
          Signed in as {user.username}.
        </h1>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button size="sm" asChild>
            <Link to={next}>
              Continue
              <ArrowRight className="ml-1.5 size-3.5" aria-hidden="true" />
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-md px-6 py-16 md:py-20">
      <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
        Account <span className="text-primary/60">/ sign in</span>
      </p>
      <h1 className="mt-4 text-[clamp(1.75rem,5vw,2.5rem)] leading-[1.05] font-bold tracking-tight">
        Pick up where your code needs you.
      </h1>
      <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
        Sign in to generate AI-powered fixes and validate them in an isolated
        environment.
      </p>

      <form onSubmit={handleSubmit} noValidate className="mt-10 space-y-5">
        <div>
          <AuthFieldLabel htmlFor="login-username">Username</AuthFieldLabel>
          <Input
            id="login-username"
            name="username"
            autoComplete="username"
            spellCheck={false}
            disabled={submitting}
            required
            aria-invalid={fieldError === 'Username is required.' || undefined}
            aria-describedby={fieldError ? 'login-error' : undefined}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="h-11 rounded-sm bg-card font-mono text-[13px] transition-colors hover:border-muted-foreground/60 focus-visible:border-primary"
            placeholder="Enter Your Username"
          />
        </div>

        <div>
          <AuthFieldLabel htmlFor="login-password">Password</AuthFieldLabel>
          <PasswordField
            id="login-password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            placeholder="••••••••"
            disabled={submitting}
            invalid={fieldError === 'Password is required.'}
            describedBy={fieldError ? 'login-error' : undefined}
          />
        </div>

        <div id="login-error" aria-live="polite" className="min-h-[20px]">
          {fieldError && (
            <p role="alert" className="font-mono text-[12px] text-destructive">
              {fieldError}
            </p>
          )}
          {formError && (
            <p role="alert" className="font-mono text-[12px] text-destructive">
              {formError}
            </p>
          )}
        </div>

        <Button
          type="submit"
          disabled={submitting}
          className="h-11 w-full rounded-sm text-[13px]"
        >
          {submitting ? (
            <span className="inline-flex items-center gap-2">
              <Spinner aria-label="Signing in" />
              Signing in…
            </span>
          ) : (
            'Sign in'
          )}
        </Button>
      </form>

      <p className="mt-6 text-[13px] text-muted-foreground">
        No account yet?{' '}
        <Link
          to={registerPathFor(next)}
          className="text-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
        >
          Create one
        </Link>
      </p>
    </div>
  )
}
