import { TechnicalMetadata } from '@/components/TechnicalMetadata'

type LineKind = 'remove' | 'add'

interface DiffLine {
  n: number
  kind: LineKind
  code: string
}


const DIFF: DiffLine[] = [
  { n: 1, kind: 'remove', code: 'def add_item(item, bucket=[]):' },
  { n: 2, kind: 'remove', code: '    bucket.append(item)' },
  { n: 3, kind: 'remove', code: '    return bucket' },
  { n: 4, kind: 'add', code: 'def add_item(item, bucket=None):' },
  { n: 5, kind: 'add', code: '    if bucket is None:' },
  { n: 6, kind: 'add', code: '        bucket = []' },
  { n: 7, kind: 'add', code: '    bucket.append(item)' },
  { n: 8, kind: 'add', code: '    return bucket' },
]

export function CodeTransformation() {
  return (
    <section
      className="border-b border-border py-20 md:py-28"
      aria-labelledby="transformation-heading"
    >
      <div className="mx-auto grid max-w-6xl grid-cols-1 items-start gap-10 px-6 lg:grid-cols-[1fr_1.15fr] lg:gap-16">
        <div>
          <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
            Output
          </p>
          <h2
            id="transformation-heading"
            className="mt-3 text-3xl font-bold tracking-tight md:text-4xl"
          >
            One finding,
            <br />
            explained and fixed
          </h2>
          <p className="mt-5 max-w-md text-sm leading-relaxed text-muted-foreground">
            Every issue comes with its file, the rule that flagged it, and a
            proposed change you can read before anything is applied. The
            example beside it is Python's shared mutable default argument —
            a concrete defect static analysis genuinely catches.
          </p>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-muted-foreground/80">
            RepoLens targets issues like this one. It does not claim to
            detect every bug, and it shows validation results as they are —
            including when a fix cannot be verified.
          </p>
          <div className="mt-6">
            <TechnicalMetadata items={['Ruff', 'B006', 'Mutable default']} />
          </div>
        </div>

        <figure className="overflow-hidden rounded-sm border border-border bg-card">
          <figcaption className="flex items-center justify-between border-b border-border px-4 py-2.5 font-mono text-[11px] tracking-wide text-muted-foreground uppercase">
            <span>utils.py</span>
            <span className="text-primary/80">B006 · proposed fix</span>
          </figcaption>
          <pre className="overflow-x-auto py-3 font-mono text-[13px] leading-[1.7]">
            <code>
              {DIFF.map((line) => (
                <div
                  key={line.n}
                  className={
                    'flex min-w-max px-2 ' +
                    (line.kind === 'remove'
                      ? 'bg-remove/10 text-remove'
                      : 'bg-add/10 text-add')
                  }
                >
                  <span
                    className="w-8 shrink-0 pr-3 text-right text-muted-foreground/40 select-none"
                    aria-hidden="true"
                  >
                    {line.n}
                  </span>
                  <span className="w-4 shrink-0 select-none" aria-hidden="true">
                    {line.kind === 'remove' ? '−' : '+'}
                  </span>
                  <span className="text-foreground/90">{line.code}</span>
                </div>
              ))}
            </code>
          </pre>
          <div className="border-t border-border px-4 py-3 text-[12px] leading-relaxed text-muted-foreground">
            The default{' '}
            <code className="font-mono text-foreground/90">[]</code> is
            created once at import, so calls share state.{' '}
            <code className="font-mono text-foreground/90">None</code> is
            evaluated per call, restoring the expected behavior.
          </div>
        </figure>
      </div>
    </section>
  )
}
