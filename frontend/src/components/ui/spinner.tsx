import type { ComponentProps } from 'react'
import { cn } from 'cn'

export function Spinner({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      data-slot="spinner"
      role="status"
      aria-label="Loading"
      className={cn(
        'inline-block size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent',
        className,
      )}
      {...props}
    />
  )
}
