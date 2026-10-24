import { Loader2 } from 'lucide-react'
import type {
  AgentRun,
  RunStatus,
  VerificationStatus,
} from '@/lib/api'

/** Execution label: "pending" is the queued state (created, not started). */
const EXEC_LABEL: Record<RunStatus, string> = {
  pending: 'queued',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
}

/**
 * Verification verdict styling, using ONLY the existing palette:
 * green = passed, copper accent = partial/incomplete, red = failed,
 * muted gray = no evidence.
 */
const VERIFICATION: Record<
  VerificationStatus,
  { label: string; detail: string; className: string }
> = {
  verified: {
    label: 'Verified',
    detail: 'every required check ran and passed',
    className: 'text-add',
  },
  partially_verified: {
    label: 'Partially verified',
    detail: 'some checks passed; relevant checks were skipped or incomplete',
    className: 'text-primary',
  },
  failed: {
    label: 'Failed',
    detail: 'one or more required checks failed',
    className: 'text-remove',
  },
  unverified: {
    label: 'Unverified',
    detail: 'no validation evidence to judge this fix',
    className: 'text-muted-foreground',
  },
}

function testsNote(run: AgentRun): string {
  if (run.tests_ran === null) {
    return 'repository tests: not reached'
  }
  if (run.tests_ran) {
    return 'repository tests: executed'
  }
  return 'repository tests: skipped (sandbox unavailable or no tests found)'
}


export function AgentRunStatus({ run }: { run: AgentRun }) {
  const { passed, failed, error, skipped } = run.checks
  const verification =
    VERIFICATION[run.verification_status] ?? VERIFICATION.unverified
  const isExecuting =
    run.status === 'pending' || run.status === 'running'

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
        {isExecuting && (
          <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
        )}
        Agent run {EXEC_LABEL[run.status] ?? run.status} · attempt{' '}
        {run.repair_attempts}
      </div>

      <div className="px-4 py-3">
        <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px]">
          <div className="flex items-center gap-1.5">
            <dt className="text-muted-foreground">Execution:</dt>
            <dd className="uppercase tracking-wide">
              {EXEC_LABEL[run.status] ?? run.status}
            </dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="text-muted-foreground">Verification:</dt>
            <dd
              className={`uppercase tracking-wide ${verification.className}`}
              title={verification.detail}
            >
              {verification.label}
            </dd>
          </div>
        </dl>
        <p
          className={`mt-1 font-mono text-[11px] ${verification.className}`}
        >
          {verification.detail}
        </p>

        <p className="mt-3 text-[13px] leading-relaxed whitespace-pre-wrap text-foreground/90">
          {run.final_summary || 'The agent produced no summary.'}
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>checks: {passed} passed</span>
          {failed > 0 && <span className="text-remove">{failed} failed</span>}
          {error > 0 && <span className="text-remove">{error} errored</span>}
          {skipped > 0 && <span>{skipped} skipped</span>}
          <span>·</span>
          <span>{testsNote(run)}</span>
        </div>
      </div>
    </section>
  )
}
