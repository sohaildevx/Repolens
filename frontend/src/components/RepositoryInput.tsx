import { useState, type FormEvent } from 'react'
import { ArrowRight, CheckCircle2, Loader2 } from 'lucide-react'
import { ApiError, parseGithubRepoUrl, startAnalysis } from '@/lib/api'
import type { AnalysisRun } from '@/lib/api'
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

type Phase =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'success'; run: AnalysisRun }
  | { kind: 'error'; message: string }


export function RepositoryInput() {
  const [value, setValue] = useState('')
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [localError, setLocalError] = useState<string | null>(null)

  const submitting = phase.kind === 'submitting'

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return 
    }

    try {
      parseGithubRepoUrl(value)
      setLocalError(null)
    } catch (err) {
      setLocalError(
        err instanceof ApiError ? err.message : 'Invalid repository URL.',
      )
      setPhase({ kind: 'idle' })
      return
    }

    setPhase({ kind: 'submitting' })
    try {
      const run = await startAnalysis(value)
      setPhase({ kind: 'success', run })
    } catch (err) {
      setPhase({
        kind: 'error',
        message:
          err instanceof ApiError
            ? err.message
            : 'Unexpected error — please try again.',
      })
    }
  }

  const statusId = 'repository-status'

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="mx-auto w-full max-w-2xl"
      aria-label="Repository analysis"
    >
      <Label
        htmlFor="repository-input"
        className="mb-3 block font-mono text-[11px] tracking-[0.18em] text-muted-foreground uppercase"
      >
        Repository input
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
          aria-label="Analyze repository"
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

        {!localError && phase.kind === 'submitting' && (
          <p
            aria-live="polite"
            className="font-mono text-[12px] text-muted-foreground"
          >
            Contacting backend — saving repository and starting analysis…
          </p>
        )}

        {!localError && phase.kind === 'error' && (
          <p role="alert" className="font-mono text-[12px] text-destructive">
            {phase.message}
          </p>
        )}

        {!localError && phase.kind === 'success' && (
          <div
            aria-live="polite"
            className="rounded-sm border border-border bg-card px-4 py-3"
          >
            <p className="flex items-center gap-2 font-mono text-[12px] tracking-wide text-add uppercase">
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
              Analysis run created
            </p>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[12px] text-muted-foreground">
              <dt>run</dt>
              <dd className="text-foreground">#{phase.run.id}</dd>
              <dt>repository</dt>
              <dd className="text-foreground">{phase.run.repository_name}</dd>
              <dt>status</dt>
              <dd className="text-foreground">{phase.run.status}</dd>
              <dt>issues</dt>
              <dd className="text-foreground">{phase.run.issue_count}</dd>
            </dl>
            <p className="mt-2 text-[12px] text-muted-foreground">
              The analysis screen that lists these issues ships in a later
              frontend phase — the run is saved and queryable via the API.
            </p>
          </div>
        )}

        {!localError && phase.kind === 'idle' && (
          <p className="text-[12px] text-muted-foreground">
            Accounts are required — the backend authenticates every analysis
            request.{' '}
            <span className="text-muted-foreground/70">
              Sign-in is not yet available in this interface.
            </span>
          </p>
        )}
      </div>
    </form>
  )
}
