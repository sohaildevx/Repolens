import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, FileCode2 } from 'lucide-react'
import type { Issue } from '@/lib/api'
import { SeverityBadge } from '@/components/SeverityBadge'
import { cn } from 'cn'

interface FindingsListProps {
  runId: number
  issues: Issue[]
  /** Currently selected issue (results page uses inline selection). */
  selectedIssueId?: number | null
  onSelect?: (issue: Issue) => void
}

/**
 * All findings, grouped by file path. Keyboard accessible: every finding
 * is a button, Enter/Space select it, focus stays visible.
 */
export function FindingsList({
  runId,
  issues,
  selectedIssueId,
  onSelect,
}: FindingsListProps) {
  const navigate = useNavigate()

  const groups = useMemo(() => {
    const byFile = new Map<string, Issue[]>()
    for (const issue of issues) {
      const bucket = byFile.get(issue.file_path)
      if (bucket) {
        bucket.push(issue)
      } else {
        byFile.set(issue.file_path, [issue])
      }
    }
    return [...byFile.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [issues])

  function activate(issue: Issue) {
    if (onSelect) {
      onSelect(issue)
    } else {
      // Default: deep-link to the issue page.
      navigate(`/scan/${runId}/issues/${issue.id}`)
    }
  }

  return (
    <section aria-label="Findings" className="space-y-4">
      {groups.map(([filePath, fileIssues]) => (
        <div
          key={filePath}
          className="rounded-sm border border-border bg-card"
        >
          <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <FileCode2
              className="size-3.5 shrink-0 text-primary"
              aria-hidden="true"
            />
            <code className="truncate font-mono text-[12px] text-foreground/90">
              {filePath}
            </code>
            <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground">
              {fileIssues.length}{' '}
              {fileIssues.length === 1 ? 'finding' : 'findings'}
            </span>
          </header>

          <ul className="divide-y divide-border">
            {fileIssues.map((issue) => (
              <li key={issue.id}>
                <button
                  type="button"
                  onClick={() => activate(issue)}
                  aria-current={selectedIssueId === issue.id || undefined}
                  className={cn(
                    'flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-secondary/40',
                    'focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none',
                    selectedIssueId === issue.id && 'bg-secondary/60',
                  )}
                >
                  <span className="pt-0.5 font-mono text-[11px] text-muted-foreground">
                    L{issue.line_number}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] leading-snug text-foreground/90">
                      {issue.description}
                    </span>
                    <span className="mt-1 flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                      <SeverityBadge severity={issue.severity} />
                      <span>{issue.source}</span>
                      {issue.rule && <span>· {issue.rule}</span>}
                    </span>
                  </span>
                  <ChevronRight
                    className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60"
                    aria-hidden="true"
                  />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}
