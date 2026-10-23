import { Loader2 } from 'lucide-react'
import type { RunStatus } from '@/lib/api'

/**
 * Analysis lifecycle state. Labels follow the backend's real status
 * vocabulary (pending / running / completed / failed) -- no fabricated
 * progress percentages or invented pipeline stages.
 */
export function ScanStatus({ status }: { status: RunStatus }) {
  if (status === 'completed' || status === 'failed') {
    return null
  }

  const label =
    status === 'running'
      ? 'Analysis running — static analyzers are examining the snapshot…'
      : status === 'pending'
        ? 'Analysis queued…'
        : `Analysis status: ${status}`

  return (
    <p
      aria-live="polite"
      className="flex items-center gap-2.5 font-mono text-[12px] text-muted-foreground"
    >
      <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden="true" />
      {label}
    </p>
  )
}
