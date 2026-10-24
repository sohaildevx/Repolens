import { useState } from 'react'
import { Check, Copy, FileDiff } from 'lucide-react'
import type { CodeChange } from '@/lib/api'

/**
 * Copy the proposed diff to the clipboard. Progressive enhancement: the
 * button hides itself when the clipboard API is unavailable.
 */
function CopyPatchButton({ diff }: { diff: string }) {
  const [copied, setCopied] = useState(false)

  if (typeof navigator === 'undefined' || !navigator.clipboard) {
    return null
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(diff)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard denied -- stay silent; the diff is still selectable.
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      className="inline-flex items-center gap-1 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
    >
      {copied ? (
        <Check className="size-3 text-add" aria-hidden="true" />
      ) : (
        <Copy className="size-3" aria-hidden="true" />
      )}
      {copied ? 'copied' : 'copy patch'}
    </button>
  )
}

/**
 * The agent's explanation ("what changed and why", plus expected
 * behavior) followed by the proposed code changes, one block per modified
 * file. When a run kept multiple attempts, each block is tagged with the
 * attempt that produced it. These are PROPOSALS produced by the AI agent
 * -- the original GitHub repository is never modified. Diff text renders
 * verbatim from the backend.
 */
export function FixProposal({
  changes,
  explanation,
}: {
  changes: CodeChange[]
  explanation?: string
}) {
  if (changes.length === 0 && !explanation) {
    return null
  }

  return (
    <section aria-label="Proposed changes" className="space-y-3">
      {explanation && (
        <div className="rounded-sm border border-border bg-card/60 px-4 py-3">
          <h3 className="font-mono text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
            What changed and why
          </h3>
          <p className="mt-1.5 text-[13px] leading-relaxed whitespace-pre-wrap text-foreground/90">
            {explanation}
          </p>
        </div>
      )}

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
            <span className="ml-auto flex items-center gap-3">
              <CopyPatchButton diff={change.proposed_diff} />
              <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
                proposed patch · attempt {change.attempt}
              </span>
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
