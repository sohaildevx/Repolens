import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description: ReactNode
}

/** Honest empty state -- never dressed up as success. */
export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="rounded-sm border border-border bg-card px-6 py-10 text-center">
      <p className="font-mono text-[12px] tracking-[0.18em] text-muted-foreground uppercase">
        Empty result
      </p>
      <h3 className="mt-3 text-lg font-semibold tracking-tight">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>
    </div>
  )
}
