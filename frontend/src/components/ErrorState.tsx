import { AlertTriangle } from 'lucide-react'
import type { ReactNode } from 'react'

interface ErrorStateProps {
  title?: string
  /** A backend-provided, already user-safe message (never a stack trace). */
  message: string
  action?: ReactNode
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  action,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="rounded-sm border border-destructive/40 bg-card px-6 py-6"
    >
      <p className="flex items-center gap-2 font-mono text-[12px] tracking-wide text-destructive uppercase">
        <AlertTriangle className="size-3.5" aria-hidden="true" />
        {title}
      </p>
      <p className="mt-3 text-sm leading-relaxed text-foreground/90">
        {message}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
