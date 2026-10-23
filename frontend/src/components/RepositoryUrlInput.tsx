import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Loader2 } from 'lucide-react'
import { ApiError, createScan, parseGithubRepoUrl } from '@/lib/api'
import type { ScanResponse } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={className}
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  )
}

/**
 * The focal element of the landing page: paste a public GitHub URL, the
 * backend fetches and analyzes the repository server-side. No account is
 * needed to scan; AI fixes later require sign-in (stated below the input).
 */
export function RepositoryUrlInput() {
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return // double-submit guard: one scan per press
    }

    // Client-side validation is convenience only; the backend re-validates.
    try {
      parseGithubRepoUrl(value)
      setLocalError(null)
    } catch (err) {
      setLocalError(
        err instanceof ApiError ? err.message : 'Invalid repository URL.',
      )
      return
    }

    setSubmitting(true)
    setSubmitError(null)
    try {
      const result: ScanResponse = await createScan(value)
      navigate(`/scan/${result.run.id}`)
    } catch (err) {
      if (err instanceof ApiError) {
        // 502 after a snapshot succeeded means the analyzer failed BUT the
        // run was recorded as failed -- send the visitor to that record
        // instead of a bare error message.
        const run = (err.data as ScanResponse | null)?.run
        if (err.status === 502 && run) {
          navigate(`/scan/${run.id}`)
          return
        }
        setSubmitError(err.message)
      } else {
        setSubmitError('Unexpected error — please try again.')
      }
      setSubmitting(false)
    }
  }

  const statusId = 'repository-status'

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="mx-auto w-full max-w-2xl"
      aria-label="Repository scan"
    >
      <Label
        htmlFor="repository-input"
        className="mb-3 block font-mono text-[11px] tracking-[0.18em] text-muted-foreground uppercase"
      >
        Repository URL
      </Label>

      <div className="relative">
        <GithubMark className="pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          id="repository-input"
          type="url"
          name="repository"
          autoComplete="url"
          spellCheck={false}
          disabled={submitting}
          required
          aria-invalid={localError ? true : undefined}
          aria-describedby={statusId}
          placeholder="Paste a public GitHub repository URL..."
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            if (localError) {
              setLocalError(null)
            }
          }}
          className={
            'h-14 rounded-sm bg-card pr-16 pl-12 font-mono text-[13px] transition-colors md:h-16 md:text-sm ' +
            (localError
              ? 'border-destructive focus-visible:border-destructive'
              : 'hover:border-muted-foreground/60 focus-visible:border-primary')
          }
        />
        <Button
          type="submit"
          size="icon"
          disabled={submitting || value.trim().length === 0}
          aria-label="Scan repository"
          className="absolute top-1/2 right-2 size-9 -translate-y-1/2 rounded-sm md:size-10"
        >
          {submitting ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <ArrowRight className="size-4" aria-hidden="true" />
          )}
        </Button>
      </div>

      <div id={statusId} className="mt-3 min-h-[20px] text-left">
        {localError && (
          <div>
            <p role="alert" className="font-mono text-[12px] text-destructive">
              {localError}
            </p>
            <p className="mt-1 text-[12px] text-muted-foreground/80">
              Example:{' '}
              <span className="font-mono">
                https://github.com/owner/repository
              </span>
            </p>
          </div>
        )}

        {!localError && submitting && (
          <p
            aria-live="polite"
            className="font-mono text-[12px] text-muted-foreground"
          >
            Fetching the repository and running the analyzers — this can take
            a minute…
          </p>
        )}

        {!localError && submitError && (
          <p role="alert" className="font-mono text-[12px] text-destructive">
            {submitError}
          </p>
        )}

        {!localError && !submitting && !submitError && (
          <p className="text-[12px] text-muted-foreground">
            No account needed to scan and read findings.{' '}
            <span className="text-muted-foreground/70">
              AI fix generation asks you to sign in first.
            </span>
          </p>
        )}
      </div>
    </form>
  )
}
