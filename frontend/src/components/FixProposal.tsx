import { FileDiff } from 'lucide-react'
import type { CodeChange } from '@/lib/api'

/**
 * Proposed code changes, one block per modified file. These are
 * PROPOSALS produced by the AI agent -- the original GitHub repository
 * is never modified. Diff text renders verbatim from the backend.
 */
export function FixProposal({ changes }: { changes: CodeChange[] }) {
  if (changes.length === 0) {
    return null
  }

  return (
    <section aria-label="Proposed changes" className="space-y-3">
      {changes.map((change) => (
        <div
          key={change.id}
          className="rounded-sm border border-border bg-card"
        >
          <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <FileDiff className="size-3.5 text-primary" aria-hidden="true" />
            <code className="truncate font-mono text-[12px]">
              {change.file_path}
            </code>
            <span className="ml-auto font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
              proposed patch
            </span>
          </header>
          <pre className="overflow-x-auto px-4 py-3 font-mono text-[11.5px] leading-relaxed">
            {change.proposed_diff.split('\n').map((line, i) => (
              <div
                key={i}
                className={
                  line.startsWith('+')
                    ? 'text-add'
                    : line.startsWith('-')
                      ? 'text-remove'
                      : 'text-muted-foreground'
                }
              >
                {line || ' '}
              </div>
            ))}
          </pre>
        </div>
      ))}
    </section>
  )
}
