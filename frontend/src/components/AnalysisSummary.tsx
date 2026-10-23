import { CircleCheck, CircleX } from 'lucide-react'
import type { AnalysisRun, Issue, Severity } from '@/lib/api'
import { SeverityBadge } from '@/components/SeverityBadge'

const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info']

function severityCounts(issues: Issue[]): Map<Severity, number> {
  const counts = new Map<Severity, number>()
  for (const issue of issues) {
    counts.set(issue.severity, (counts.get(issue.severity) ?? 0) + 1)
  }
  return counts
}

/**
 * The dominant block of the results page: analysis status, files
 * analyzed, findings total, severity breakdown. Every number comes from
 * the real backend response; unknown values render as "—", never 0.
 */
export function AnalysisSummary({ run }: { run: AnalysisRun }) {
  const failed = run.status === 'failed'
  const counts = severityCounts(run.issues)
  // The run detail embeds at most 100 issues; a breakdown computed from a
  // truncated list would misrepresent the total, so it is labeled.
  const breakdownIsPartial = run.issues_truncated

  return (
    <section
      aria-label="Analysis summary"
      className="rounded-sm border border-border bg-card"
    >
      <div
        className={
          'flex items-center gap-2.5 border-b px-5 py-3 font-mono text-[12px] tracking-wide uppercase ' +
          (failed
            ? 'border-remove/30 text-remove'
            : 'border-add/30 text-add')
        }
      >
        {failed ? (
          <CircleX className="size-4" aria-hidden="true" />
        ) : (
          <CircleCheck className="size-4" aria-hidden="true" />
        )}
        Analysis {run.status}
      </div>

      <dl className="grid grid-cols-2 divide-border sm:grid-cols-3 sm:divide-x">
        <div className="px-5 py-4">
          <dt className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
            Files analyzed
          </dt>
          <dd className="mt-1 text-2xl font-bold tracking-tight">
            {run.files_analyzed ?? (
              <span className="text-muted-foreground/60" title="Unknown">
                —
              </span>
            )}
          </dd>
        </div>
        <div className="border-t border-border px-5 py-4 sm:border-t-0">
          <dt className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
            Findings
          </dt>
          <dd className="mt-1 text-2xl font-bold tracking-tight">
            {run.issue_count}
          </dd>
        </div>
        <div className="col-span-2 border-t border-border px-5 py-4 sm:col-span-1 sm:border-t-0">
          <dt className="font-mono text-[11px] tracking-[0.16em] text-muted-foreground uppercase">
            By severity
          </dt>
          <dd className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {run.issue_count === 0 ? (
              <span className="text-sm text-muted-foreground">none</span>
            ) : (
              SEVERITY_ORDER.filter((s) => counts.has(s)).map((s) => (
                <span
                  key={s}
                  className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground"
                >
                  <SeverityBadge severity={s} />
                  {counts.get(s)}
                </span>
              ))
            )}
          </dd>
        </div>
      </dl>

      {breakdownIsPartial && (
        <p className="border-t border-border px-5 py-2 font-mono text-[11px] text-muted-foreground">
          Severity breakdown reflects the first{' '}
          {run.issues.length} of {run.issue_count} findings.
        </p>
      )}

      {run.summary && (
        <p className="border-t border-border px-5 py-3 text-[13px] text-muted-foreground">
          {run.summary}
        </p>
      )}
    </section>
  )
}
