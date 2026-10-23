import { FileCode2 } from 'lucide-react'
import type { Issue } from '@/lib/api'
import { SeverityBadge } from '@/components/SeverityBadge'
import { FixAction } from '@/components/FixAction'

/**
 * One issue in full: location, analyzer, severity, description -- plus the
 * authentication-gated fix action. Everything shown here comes from the
 * backend's issue record; nothing is synthesized client-side.
 */
export function FindingDetails({ issue }: { issue: Issue }) {
  return (
    <section
      aria-label="Issue details"
      className="rounded-sm border border-border bg-card"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <SeverityBadge severity={issue.severity} />
        <span className="inline-flex items-center gap-1.5 font-mono text-[12px] text-muted-foreground">
          <FileCode2 className="size-3.5" aria-hidden="true" />
          <code>{issue.file_path}</code>:{issue.line_number}
        </span>
        <span className="ml-auto font-mono text-[11px] tracking-wide text-muted-foreground uppercase">
          {issue.source}
          {issue.rule ? ` · ${issue.rule}` : ''}
        </span>
      </header>

      <div className="px-5 py-4">
        <h2 className="text-[15px] leading-relaxed font-medium">
          {issue.description}
        </h2>
      </div>

      <div className="border-t border-border px-5 py-4">
        <FixAction issue={issue} />
      </div>
    </section>
  )
}
