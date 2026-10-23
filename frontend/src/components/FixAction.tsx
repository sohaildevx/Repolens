import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, Loader2, ShieldCheck, Wand2 } from 'lucide-react'
import {
  ApiError,
  createAgentRun,
  getAgentRunChanges,
  getAgentRunTests,
  type AgentRun,
  type CodeChange,
  type Issue,
  type TestResult,
} from '@/lib/api'
import { BACKEND_LOGIN_URL, useAuth } from '@/lib/auth'
import { AgentRunStatus } from '@/components/AgentRunStatus'
import { FixProposal } from '@/components/FixProposal'
import { ValidationResults } from '@/components/ValidationResults'

type Phase =
  | { kind: 'idle' }
  | { kind: 'gate' } // not signed in: fixes are authenticated-only
  | { kind: 'running' }
  | {
      kind: 'done'
      run: AgentRun
      changes: CodeChange[]
      tests: TestResult[]
    }
  | { kind: 'error'; message: string }

/**
 * The protected action of the product: generate an AI fix for an issue.
 *
 * - Hiding the button is never the security boundary: the backend
 *   requires authentication for agent runs regardless. This component
 *   only makes the rule visible and navigable.
 * - Logged-out visitors get a gate that links to the backend's existing
 *   sign-in route (a new tab, so this page -- and with it the deep link
 *   back to this exact issue -- stays open). Nothing runs automatically
 *   after sign-in; the visitor presses the button themselves.
 * - The agent execution is synchronous and can take a while; the UI says
 *   so honestly instead of inventing progress.
 */
export function FixAction({ issue }: { issue: Issue }) {
  const { user, loading, refresh } = useAuth()
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })

  // While gated, re-check the session when the visitor returns to this
  // tab (e.g. after signing in on the other tab). Never auto-starts a run.
  useEffect(() => {
    if (phase.kind !== 'gate') {
      return
    }
    const onFocus = () => {
      void refresh()
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [phase.kind, refresh])

  const runAgent = useCallback(async () => {
    setPhase({ kind: 'running' })
    try {
      const run = await createAgentRun(issue.id)
      // The create response carries the run record; changes and tests
      // come from their own endpoints. allSettled: a failure to fetch
      // supporting records must not erase the run that did happen.
      const [changes, tests] = await Promise.allSettled([
        getAgentRunChanges(run.id),
        getAgentRunTests(run.id),
      ])
      setPhase({
        kind: 'done',
        run,
        changes:
          changes.status === 'fulfilled' ? changes.value : ([] as CodeChange[]),
        tests: tests.status === 'fulfilled' ? tests.value : ([] as TestResult[]),
      })
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401 || err.status === 403) {
          setPhase({ kind: 'gate' })
          return
        }
        if (err.status === 409) {
          setPhase({
            kind: 'error',
            message:
              'Another agent run for this issue is already in progress. Wait for it to finish, then reload.',
          })
          return
        }
        setPhase({ kind: 'error', message: err.message })
        return
      }
      setPhase({ kind: 'error', message: 'Unexpected error while starting the agent.' })
    }
  }, [issue.id])

  function handleClick() {
    if (!user) {
      setPhase({ kind: 'gate' })
      return
    }
    void runAgent()
  }

  const button = (
    <button
      type="button"
      onClick={handleClick}
      disabled={loading || phase.kind === 'running'}
      className="inline-flex items-center gap-2 rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
    >
      {phase.kind === 'running' ? (
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
      ) : (
        <Wand2 className="size-4" aria-hidden="true" />
      )}
      Generate fix with AI
    </button>
  )

  return (
    <div className="space-y-4">
      {phase.kind === 'gate' ? (
        <div className="rounded-sm border border-primary/40 bg-secondary/30 px-4 py-4">
          <p className="flex items-center gap-2 font-mono text-[12px] tracking-wide text-primary uppercase">
            <ShieldCheck className="size-3.5" aria-hidden="true" />
            Sign-in required
          </p>
          <p className="mt-2 text-[13px] leading-relaxed text-foreground/90">
            AI fixes run on an authenticated account. The sign-in page opens
            in a new tab; this page stays open, so you can return here and
            start the fix yourself -- nothing runs without you pressing the
            button.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <a
              href={BACKEND_LOGIN_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-sm bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
            >
              Open sign-in
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
            <button
              type="button"
              onClick={() => void refresh()}
              className="font-mono text-[12px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
            >
              I signed in -- check
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          {button}
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            Runs the autonomous repair agent on a sandboxed copy. Proposed
            changes are never pushed to GitHub.
          </p>
        </div>
      )}

      {phase.kind === 'running' && (
        <p
          aria-live="polite"
          className="flex items-center gap-2 font-mono text-[12px] text-muted-foreground"
        >
          <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden="true" />
          The agent is working -- snapshot, patch, validation. This can take
          a minute or two.
        </p>
      )}

      {phase.kind === 'error' && (
        <p
          role="alert"
          className="rounded-sm border border-destructive/40 bg-card px-4 py-3 text-[13px] text-foreground/90"
        >
          {phase.message}
        </p>
      )}

      {phase.kind === 'done' && (
        <div className="space-y-4" aria-live="polite">
          <AgentRunStatus run={phase.run} />
          <FixProposal changes={phase.changes} />
          <ValidationResults results={phase.tests} />
        </div>
      )}
    </div>
  )
}
