import type { Severity } from '@/lib/api'
import { cn } from 'cn'

const STYLES: Record<Severity, string> = {
  critical: 'border-remove/50 text-remove',
  high: 'border-remove/30 text-remove/90',
  medium: 'border-primary/50 text-primary',
  low: 'border-border text-muted-foreground',
  info: 'border-border text-muted-foreground/80',
}

/**
 * Severity chip. Colors stay inside the RepoLens palette: red family for
 * serious findings, copper for medium, muted gray for low/info.
 */
export function SeverityBadge({
  severity,
  className,
}: {
  severity: Severity
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center rounded-sm border px-1.5 py-0.5 font-mono text-[10px] tracking-[0.14em] uppercase',
        STYLES[severity] ?? STYLES.info,
        className,
      )}
    >
      {severity}
    </span>
  )
}
