import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Loader2 } from 'lucide-react'
import {
  ApiError,
  getRun,
  getRunIssues,
  type AnalysisRun,
  type Issue,
} from '@/lib/api'
import { Spinner } from '@/components/ui/spinner'
import { AnalysisSummary } from '@/components/AnalysisSummary'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { FindingsList } from '@/components/FindingsList'
import { RepositoryOverview } from '@/components/RepositoryOverview'
import { ScanStatus } from '@/components/ScanStatus'

/**
 * The results page. Analysis is the dominant content; the repository
 * overview is a compact header above it. All data is the real backend
 * response -- findings, counts, statuses.
 */
export function ScanPage() {
  const { runId } = useParams()
  const id = Number(runId)

  const [run, setRun] = useState<AnalysisRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Extra findings fetched from the paginated endpoint when the run's
  // embedded issue list was truncated (>100 findings).
  const [extraIssues, setExtraIssues] = useState<Issue[]>([])
  const [nextPage, setNextPage] = useState<number | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    if (!Number.isInteger(id) || id < 1) {
      setError('Invalid analysis run id.')
      return
    }
    let cancelled = false
    setRun(null)
    setError(null)
    setExtraIssues([])
    setNextPage(null)
    getRun(id)
      .then((r) => {
        if (cancelled) {
          return
        }
        setRun(r)
        // The run detail embeds the first 100 issues (pages 1-5 of 20);
        // deeper pages load on demand.
        if (r.issues_truncated && r.issues.length > 0) {
          setNextPage(Math.floor(r.issues.length / 20) + 1)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.status === 404
                ? 'This analysis run does not exist or is not publicly accessible.'
                : err.message
              : 'Could not load the analysis run.',
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [id])

  // The backend runs analysis synchronously, so completed/failed arrives
  // with the scan POST. Polling exists only as a safety net if a run is
  // ever observed mid-flight, and stops on any terminal status.
  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') {
      return
    }
    const timer = window.setInterval(() => {
      getRun(id)
        .then(setRun)
        .catch(() => {
          /* transient polling error: keep the current state */
        })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [run, id])

  const loadMore = useCallback(async () => {
    if (nextPage === null || loadingMore) {
      return
    }
    setLoadingMore(true)
    try {
      const page = await getRunIssues(id, nextPage)
      const known = new Set(
        [...(run?.issues ?? []), ...extraIssues].map((i) => i.id),
      )
      setExtraIssues((prev) => [
        ...prev,
        ...page.results.filter((i) => !known.has(i.id)),
      ])
      setNextPage(page.next ? nextPage + 1 : null)
    } catch {
      /* keep the button available for a retry */
    } finally {
      setLoadingMore(false)
    }
  }, [id, nextPage, loadingMore, run, extraIssues])

  const backLink = (
    <Link
      to="/"
      className="inline-flex items-center gap-1.5 rounded-sm font-mono text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      New scan
    </Link>
  )

  if (error) {
    return (
      <div className="mx-auto max-w-4xl space-y-6 px-6 py-16">
        {backLink}
        <ErrorState title="Cannot load this run" message={error} />
      </div>
    )
  }

  if (!run) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-16">
        <p className="flex items-center gap-2 font-mono text-[12px] text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden="true" />
          Loading analysis run…
        </p>
      </div>
    )
  }

  const issues = [...run.issues, ...extraIssues]
  const allLoaded = !run.issues_truncated || nextPage === null

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-10">
      <div className="flex items-center justify-between">
        {backLink}
        <span className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
          run #{run.id}
        </span>
      </div>

      {/* Compact, secondary repository header */}
      <RepositoryOverview
        githubUrl={run.repository_github_url}
        fallbackName={run.repository_name}
      />

      {/* Dominant: analysis state and numbers */}
      <AnalysisSummary run={run} />
      <ScanStatus status={run.status} />

      {run.status === 'completed' && run.issue_count === 0 && (
        <EmptyState
          title="No issues detected"
          description="The configured analyzers (AST rules, Ruff, Bandit) found nothing in the scanned snapshot. This does not mean the repository is bug-free -- analyzers cover only a subset of possible defects."
        />
      )}

      {run.status === 'failed' && (
        <ErrorState
          title="Analysis failed"
          message={
            run.summary ||
            'The analyzer could not complete for this repository. The failed run is recorded above.'
          }
        />
      )}

      {issues.length > 0 && (
        <>
          <FindingsList runId={run.id} issues={issues} />
          {!allLoaded && (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="inline-flex items-center gap-2 rounded-sm border border-border bg-card px-4 py-2 font-mono text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none disabled:opacity-50"
              >
                {loadingMore && (
                  <Spinner className="size-3.5" aria-label="Loading findings" />
                )}
                Load more findings ({issues.length} of {run.issue_count} shown)
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
