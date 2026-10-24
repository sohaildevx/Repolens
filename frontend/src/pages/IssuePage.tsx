import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Loader2 } from 'lucide-react'
import {
  ApiError,
  getIssue,
  getRun,
  type AnalysisRun,
  type Issue,
} from '@/lib/api'
import { ErrorState } from '@/components/ErrorState'
import { FindingDetails } from '@/components/FindingDetails'
import { SeverityBadge } from '@/components/SeverityBadge'


export function IssuePage() {
  const { runId, issueId } = useParams()
  const runIdNum = Number(runId)
  const issueIdNum = Number(issueId)

  const [run, setRun] = useState<AnalysisRun | null>(null)
  const [issue, setIssue] = useState<Issue | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (
      !Number.isInteger(runIdNum) ||
      runIdNum < 1 ||
      !Number.isInteger(issueIdNum) ||
      issueIdNum < 1
    ) {
      setError('Invalid issue link.')
      return
    }
    let cancelled = false
    // Both fetches are independent; failures collapse into one honest
    // error state without leaking backend internals.
    Promise.allSettled([getIssue(issueIdNum), getRun(runIdNum)]).then(
      ([issueResult, runResult]) => {
        if (cancelled) {
          return
        }
        if (issueResult.status === 'rejected') {
          const err = issueResult.reason
          setError(
            err instanceof ApiError
              ? err.status === 404
                ? 'This issue does not exist or is not publicly accessible.'
                : err.message
              : 'Could not load the issue.',
          )
          return
        }
        setIssue(issueResult.value)
        if (runResult.status === 'fulfilled') {
          setRun(runResult.value)
        }
      },
    )
    return () => {
      cancelled = true
    }
  }, [runIdNum, issueIdNum])

  const backLink = run ? (
    <Link
      to={`/scan/${run.id}`}
      className="inline-flex items-center gap-1.5 rounded-sm font-mono text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      Back to all findings
    </Link>
  ) : (
    <Link
      to="/"
      className="inline-flex items-center gap-1.5 rounded-sm font-mono text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      Home
    </Link>
  )

  if (error) {
    return (
      <div className="mx-auto max-w-4xl space-y-6 px-6 py-16">
        {backLink}
        <ErrorState title="Cannot load this issue" message={error} />
      </div>
    )
  }

  if (!issue) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-16">
        <p className="flex items-center gap-2 font-mono text-[12px] text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden="true" />
          Loading issue…
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {backLink}
        <span className="inline-flex items-center gap-2 font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
          <SeverityBadge severity={issue.severity} />
          issue #{issue.id}
        </span>
      </div>

      {run && (
        <p className="font-mono text-[12px] text-muted-foreground">
          {run.repository_name}
          <span className="mx-2 text-primary/60">/</span>
          analysis run #{run.id}
        </p>
      )}

      <FindingDetails issue={issue} />
    </div>
  )
}
