/**
 * Public GitHub metadata client (repository overview on the results page).
 *
 * The browser talks to api.github.com DIRECTLY for display-only metadata
 * (stars, forks, open issues, languages). This keeps the backend out of the
 * metadata business entirely: no stored statistics, no extra server quota,
 * no secrets in the frontend (GitHub's API is readable without a token).
 *
 * Honest-data rules:
 * - a metric GitHub reports as 0 renders as 0;
 * - a metric we could not fetch renders as "unavailable" (null), never 0;
 * - rate limiting (60 requests/hour per visitor IP) is detected and
 *   surfaced as its own state, not as missing data.
 */

export interface RepoMetadata {
  fullName: string
  description: string | null
  stars: number
  forks: number
  /** GitHub counts open issues AND pull requests in this number. */
  openIssuesAndPullRequests: number
  defaultBranch: string
  language: string | null
}

export type MetadataResult =
  | { state: 'ok'; metadata: RepoMetadata; languages: Record<string, number> }
  | { state: 'rate-limited' }
  | { state: 'unavailable' }

const GITHUB_HEADERS = {
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
} as const

async function githubGet<T>(path: string): Promise<T | 'rate-limited' | 'error'> {
  let res: Response
  try {
    res = await fetch(`https://api.github.com${path}`, {
      headers: GITHUB_HEADERS,
    })
  } catch {
    return 'error'
  }
  if (res.status === 403 || res.status === 429) {
    // GitHub signals exhaustion with X-RateLimit-Remaining: 0; a 403
    // without it usually means a different restriction -- treat both as
    // rate limiting for display purposes.
    return 'rate-limited'
  }
  if (!res.ok) {
    return 'error' // 404 (private/renamed) or any other failure
  }
  try {
    return (await res.json()) as T
  } catch {
    return 'error'
  }
}

/**
 * Fetch display metadata + language bytes for a repository.
 * Never throws: failures are encoded in the result state.
 */
export async function fetchRepoMetadata(
  owner: string,
  repo: string,
): Promise<MetadataResult> {
  const info = await githubGet<{
    full_name: string
    description: string | null
    stargazers_count: number
    forks_count: number
    open_issues_count: number
    default_branch: string
    language: string | null
  }>(`/repos/${owner}/${repo}`)

  if (info === 'rate-limited') {
    return { state: 'rate-limited' }
  }
  if (info === 'error') {
    return { state: 'unavailable' }
  }

  const languages = await githubGet<Record<string, number>>(
    `/repos/${owner}/${repo}/languages`,
  )

  return {
    state: 'ok',
    metadata: {
      fullName: info.full_name,
      description: info.description,
      stars: info.stargazers_count,
      forks: info.forks_count,
      openIssuesAndPullRequests: info.open_issues_count,
      defaultBranch: info.default_branch,
      language: info.language,
    },
    languages:
      languages === 'error' || languages === 'rate-limited' ? {} : languages,
  }
}

/** Top languages by byte share; empty map means "unknown", not "none". */
export function topLanguages(
  languages: Record<string, number>,
  max = 3,
): { name: string; bytes: number; share: number }[] {
  const total = Object.values(languages).reduce((a, b) => a + b, 0)
  if (total === 0) {
    return []
  }
  return Object.entries(languages)
    .sort(([, a], [, b]) => b - a)
    .slice(0, max)
    .map(([name, bytes]) => ({ name, bytes, share: bytes / total }))
}
