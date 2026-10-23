import { useEffect, useState } from 'react'
import { GitFork, Star } from 'lucide-react'
import { repoParts } from '@/lib/api'
import {
  fetchRepoMetadata,
  topLanguages,
  type RepoMetadata,
} from '@/lib/github'
import { Separator } from '@/components/ui/separator'

type MetaState =
  | { phase: 'loading' }
  | { phase: 'rate-limited' }
  | { phase: 'unavailable' }
  | { phase: 'ok'; metadata: RepoMetadata; languages: Record<string, number> }

interface RepositoryOverviewProps {
  githubUrl: string
  /** Fallback name from the backend record while GitHub data loads. */
  fallbackName: string
  fallbackDescription?: string
}

/**
 * Compact repository header -- visually secondary to the analysis.
 * All statistics are fetched live from GitHub's public API; unavailable
 * metrics render as "unavailable", never as a fabricated 0.
 */
export function RepositoryOverview({
  githubUrl,
  fallbackName,
  fallbackDescription,
}: RepositoryOverviewProps) {
  const [state, setState] = useState<MetaState>({ phase: 'loading' })

  useEffect(() => {
    let cancelled = false
    const { owner, repo } = repoParts(githubUrl)
    fetchRepoMetadata(owner, repo).then((result) => {
      if (cancelled) {
        return
      }
      if (result.state === 'ok') {
        setState({
          phase: 'ok',
          metadata: result.metadata,
          languages: result.languages,
        })
      } else {
        setState({ phase: result.state })
      }
    })
    return () => {
      cancelled = true
    }
  }, [githubUrl])

  const ok = state.phase === 'ok'
  const languages = ok ? topLanguages(state.languages) : []

  return (
    <section
      aria-label="Repository overview"
      className="rounded-sm border border-border bg-card/60"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate font-mono text-[13px] font-medium tracking-tight">
            {ok ? state.metadata.fullName : fallbackName}
          </h2>
          <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-muted-foreground">
            {(ok ? state.metadata.description : fallbackDescription) ||
              'No description provided.'}
          </p>
        </div>

        <div className="flex items-center gap-4 font-mono text-[12px] text-muted-foreground">
          {state.phase === 'loading' && (
            <span className="animate-pulse">loading metadata…</span>
          )}
          {state.phase === 'rate-limited' && (
            <span title="GitHub allows 60 unauthenticated requests per hour per IP">
              metadata unavailable — GitHub rate limit
            </span>
          )}
          {state.phase === 'unavailable' && (
            <span>metadata unavailable</span>
          )}
          {ok && (
            <>
              <span
                className="inline-flex items-center gap-1.5"
                title="Stars"
              >
                <Star className="size-3.5" aria-hidden="true" />
                {state.metadata.stars}
              </span>
              <span className="inline-flex items-center gap-1.5" title="Forks">
                <GitFork className="size-3.5" aria-hidden="true" />
                {state.metadata.forks}
              </span>
              <span title="Open issues (GitHub's count includes pull requests)">
                issues {state.metadata.openIssuesAndPullRequests}
                <span className="text-muted-foreground/60"> (+prs)</span>
              </span>
            </>
          )}
        </div>
      </div>

      {ok && (languages.length > 0 || state.metadata.defaultBranch) && (
        <>
          <Separator className="bg-border" />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-2.5 font-mono text-[11px] tracking-wide text-muted-foreground uppercase">
            <span>branch: {state.metadata.defaultBranch}</span>
            {languages.length > 0 && (
              <>
                <span aria-hidden="true" className="text-primary/60">
                  /
                </span>
                <span>
                  languages:{' '}
                  {languages
                    .map(
                      (l) =>
                        `${l.name} ${Math.round(l.share * 100)}%`,
                    )
                    .join(' · ')}
                </span>
              </>
            )}
          </div>
        </>
      )}
    </section>
  )
}
