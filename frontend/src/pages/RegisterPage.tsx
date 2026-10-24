import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { CircleCheck } from 'lucide-react'
import { ApiError, login, register } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { loginPathFor, safeNextPath } from '@/lib/next'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import {
  AuthFieldLabel,
  PasswordField,
} from '@/components/PasswordField'

type FieldName =
  | 'username'
  | 'email'
  | 'password'
  | 'password_confirm'

type FieldErrors = Partial<Record<FieldName, string[]>>

function fieldErrorsFrom(err: unknown): FieldErrors {
  if (!(err instanceof ApiError) || !err.data || typeof err.data !== 'object') {
    return {}
  }
  const out: FieldErrors = {}
  const data = err.data as Record<string, unknown>
  for (const key of Object.keys(data) as FieldName[]) {
    const value = data[key]
    if (Array.isArray(value)) {
      out[key] = value.filter((v): v is string => typeof v === 'string')
    } else if (typeof value === 'string') {
      out[key] = [value]
    }
  }
  return out
}


export function RegisterPage() {
  const [searchParams] = useSearchParams()
  const next = safeNextPath(searchParams.get('next'))
  const navigate = useNavigate()
  const { refresh } = useAuth()

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [localErrors, setLocalErrors] = useState<FieldErrors>({})
  const [serverErrors, setServerErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'submitting' | 'created'>(
    'idle',
  )

  const submitting = phase === 'submitting'

  function validate(): FieldErrors {
    const errors: FieldErrors = {}
    if (!username.trim()) {
      errors.username = ['Username is required.']
    }
    if (!email.trim()) {
      errors.email = ['Email is required.']
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      errors.email = ['Enter a valid email address.']
    }
    if (!password) {
      errors.password = ['Password is required.']
    }
    if (!passwordConfirm) {
      errors.password_confirm = ['Please confirm your password.']
    } else if (password !== passwordConfirm) {
      errors.password_confirm = ['Passwords do not match.']
    }
    return errors
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting || phase === 'created') {
      return // double-submit guard
    }

    const local = validate()
    setLocalErrors(local)
    setServerErrors({})
    setFormError(null)
    if (Object.keys(local).length > 0) {
      return
    }

    setPhase('submitting')
    try {
      await register({ username, email, password, password_confirm: passwordConfirm })
      
      try {
        await login(username, password)
        await refresh()
        navigate(next, { replace: true })
      } catch {
        setPhase('created')
      }
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = fieldErrorsFrom(err)
        if (Object.keys(fields).length > 0) {
          setServerErrors(fields)
        } else {
          setFormError(err.message)
        }
      } else {
        setFormError('Unexpected error -- please try again.')
      }
      setPhase('idle')
    }
  }

  if (phase === 'created') {
    return (
      <div className="mx-auto max-w-md px-6 py-16 md:py-20">
        <p className="flex items-center gap-2 font-mono text-[12px] tracking-wide text-add uppercase">
          <CircleCheck className="size-4" aria-hidden="true" />
          Account created
        </p>
        <h1 className="mt-4 text-3xl font-bold tracking-tight">
          Your account is ready.
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          Sign in to continue {next !== '/' ? 'back to your issue' : 'to RepoLens'}.
        </p>
        <Button size="sm" className="mt-6" asChild>
          <Link to={loginPathFor(next)}>Sign in</Link>
        </Button>
      </div>
    )
  }

  const errorId = 'register-errors'

  return (
    <div className="mx-auto max-w-md px-6 py-16 md:py-20">
      <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
        Account <span className="text-primary/60">/ register</span>
      </p>
      <h1 className="mt-4 text-[clamp(1.75rem,5vw,2.5rem)] leading-[1.05] font-bold tracking-tight">
        Create your RepoLens account.
      </h1>
      <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
        Turn detected issues into proposed fixes, with validation results you
        can inspect.
      </p>

      <form onSubmit={handleSubmit} noValidate className="mt-10 space-y-5">
        <div>
          <AuthFieldLabel htmlFor="register-username">Username</AuthFieldLabel>
          <Input
            id="register-username"
            name="username"
            autoComplete="username"
            spellCheck={false}
            disabled={submitting}
            required
            aria-invalid={
              localErrors.username || serverErrors.username ? true : undefined
            }
            aria-describedby={errorId}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="h-11 rounded-sm bg-card font-mono text-[13px] transition-colors hover:border-muted-foreground/60 focus-visible:border-primary"
            placeholder="Enter Your Username"
          />
        </div>

        <div>
          <AuthFieldLabel htmlFor="register-email">Email</AuthFieldLabel>
          <Input
            id="register-email"
            name="email"
            type="email"
            autoComplete="email"
            spellCheck={false}
            disabled={submitting}
            required
            aria-invalid={
              localErrors.email || serverErrors.email ? true : undefined
            }
            aria-describedby={errorId}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="h-11 rounded-sm bg-card font-mono text-[13px] transition-colors hover:border-muted-foreground/60 focus-visible:border-primary"
            placeholder="Enter Your Email"
          />
        </div>

        <div>
          <AuthFieldLabel htmlFor="register-password">Password</AuthFieldLabel>
          <PasswordField
            id="register-password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            placeholder="••••••••"
            disabled={submitting}
            invalid={!!(localErrors.password || serverErrors.password)}
            describedBy={errorId}
          />
        </div>

        <div>
          <AuthFieldLabel htmlFor="register-password_confirm">
            Confirm password
          </AuthFieldLabel>
          <PasswordField
            id="register-password_confirm"
            value={passwordConfirm}
            onChange={setPasswordConfirm}
            autoComplete="new-password"
            placeholder="••••••••"
            disabled={submitting}
            invalid={
              !!(
                localErrors.password_confirm || serverErrors.password_confirm
              )
            }
            describedBy={errorId}
          />
        </div>

        <div id={errorId} aria-live="polite" className="min-h-[20px] space-y-1">
          {(Object.keys({ ...localErrors, ...serverErrors }) as FieldName[]).map(
            (field) => {
              const messages = serverErrors[field] ?? localErrors[field] ?? []
              return messages.map((message) => (
                <p
                  key={`${field}-${message}`}
                  role="alert"
                  className="font-mono text-[12px] text-destructive"
                >
                  {message}
                </p>
              ))
            },
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
              <Spinner aria-label="Creating account" />
              Creating account…
            </span>
          ) : (
            'Create account'
          )}
        </Button>
      </form>

      <p className="mt-6 text-[13px] text-muted-foreground">
        Already have an account?{' '}
        <Link
          to={loginPathFor(next)}
          className="text-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
        >
          Sign in
        </Link>
      </p>
    </div>
  )
}
