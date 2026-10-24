import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleX,
  SkipForward,
} from 'lucide-react'
import type { TestResult, TestStatus } from '@/lib/api'

const ICON: Record<
  TestStatus,
  { icon: typeof CircleCheck; className: string; label: string }
> = {
  passed: { icon: CircleCheck, className: 'text-add', label: 'passed' },
  failed: { icon: CircleX, className: 'text-remove', label: 'failed' },
  error: { icon: CircleX, className: 'text-remove', label: 'error' },
  skipped: { icon: SkipForward, className: 'text-muted-foreground', label: 'skipped' },
}

/**
 * Validation/test evidence recorded by the agent run. Each row shows the
 * exact status the backend stored -- skipped and error rows are shown as
 * themselves, never collapsed into "pass". Rows that carry output
 * (actual test output or sanitized failure details) expand on click.
 */
export function ValidationResults({ results }: { results: TestResult[] }) {
  const [openId, setOpenId] = useState<number | null>(null)

  if (results.length === 0) {
    return (
      <p className="rounded-sm border border-border bg-card/60 px-4 py-3 font-mono text-[11.5px] text-muted-foreground">
        No validation evidence: the agent recorded no test results for this
        run (test execution requires the optional sandbox).
      </p>
    )
  }

  return (
    <section aria-label="Validation results" className="space-y-2">
      {results.map((result) => {
        const { icon: Icon, className, label } =
          ICON[result.status] ?? ICON.skipped
        const hasOutput = result.output.trim().length > 0
        const isOpen = openId === result.id
        return (
          <div
            key={result.id}
            className="rounded-sm border border-border bg-card/60"
          >
            <button
              type="button"
              disabled={!hasOutput}
              aria-expanded={hasOutput ? isOpen : undefined}
              onClick={() => setOpenId(isOpen ? null : result.id)}
              className={
                'flex w-full items-center gap-3 px-4 py-2.5 text-left ' +
                (hasOutput
                  ? 'hover:bg-card focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset focus-visible:outline-none'
                  : 'cursor-default')
              }
            >
              {hasOutput ? (
                isOpen ? (
                  <ChevronDown
                    className="size-3 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                ) : (
                  <ChevronRight
                    className="size-3 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                )
              ) : (
                <span className="size-3 shrink-0" aria-hidden="true" />
              )}
              <Icon className={`size-3.5 shrink-0 ${className}`} aria-hidden="true" />
              <code className="min-w-0 flex-1 truncate font-mono text-[12px]">
                {result.test_name}
              </code>
              <span
                className={`shrink-0 font-mono text-[10px] tracking-[0.14em] uppercase ${className}`}
              >
                {label}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                attempt {result.attempt}
              </span>
            </button>
            {hasOutput && isOpen && (
              <pre className="max-h-64 overflow-auto border-t border-border px-4 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
                {result.output}
              </pre>
            )}
          </div>
        )
      })}
    </section>
  )
}
