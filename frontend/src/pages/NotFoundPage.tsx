import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="mx-auto flex max-w-4xl flex-col items-center gap-6 px-6 py-24 text-center">
      <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
        404
      </p>
      <h1 className="text-3xl font-bold tracking-tight">
        This page does not exist.
      </h1>
      <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
        Every link in RepoLens maps to a real route -- scans, findings and
        issues all live under <code className="font-mono">/scan/…</code>.
      </p>
      <Link
        to="/"
        className="rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
      >
        Back to the landing page
      </Link>
    </div>
  )
}
