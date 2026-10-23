import { Loader2 } from 'lucide-react'
import type { AgentRun } from '@/lib/api'

/**
 * Agent-run outcome. Status, attempt count and raw check tallies are
 * shown exactly as the backend reports them; validation language is
 * deliberately conservative (never "verified" unless checks passed).
 */
export function AgentRunStatus({ run }: { run: AgentRun }) {
  const { passed, failed, error, skipped } = run.checks
  const ranChecks = passed + failed + error
  const validated = ranChecks > 0 && failed === 0 && error === 0
  const unverified = ranChecks === 0

  return (
    <section
      aria-label="Agent run status"
      className="rounded-sm border border-border bg-card/60"
    >
      <div
        className={
          'flex items-center gap-2.5 border-b px-4 py-2.5 font-mono text-[12px] tracking-wide uppercase ' +
          (run.status === 'completed'
            ? 'border-add/30 text-add'
            : run.status === 'failed'
              ? 'border-remove/30 text-remove'
              : 'border-border text-muted-foreground')
        }
      >
        {run.status !== 'completed' && run.status !== 'failed' && (
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        )}
        Agent run {run.status} · attempt {run.repair_attempts}
      </div>

      <div className="px-4 py-3">
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-foreground/90">
          {run.final_summary || 'The agent produced no summary.'}
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>checks: {passed} passed</span>
          {failed > 0 && <span className="text-remove">{failed} failed</span>}
          {error > 0 && <span className="text-remove">{error} errored</span>}
          {skipped > 0 && <span>{skipped} skipped</span>}
        </div>

        <p className="mt-2 font-mono text-[11px] tracking-wide uppercase">
          {validated ? (
            <span className="text-add">
              Validation: passed checks on this run
            </span>
          ) : unverified ? (
            <span className="text-primary">
              Validation: unverified — no checks ran
            </span>
          ) : (
            <span className="text-remove">
              Validation: failing checks present
            </span>
          )}
        </p>
      </div>
    </section>
  )
}
