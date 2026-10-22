

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}


const OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/

export function parseGithubRepoUrl(input: string): string {
  const url = (input ?? '').trim()
  if (!url) {
    throw new ApiError(400, 'Repository URL is required.')
  }

  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    throw new ApiError(400, 'URL must use https:// scheme.')
  }

  if (parsed.protocol !== 'https:') {
    throw new ApiError(400, 'URL must use https:// scheme.')
  }

  const authority = url.split('://')[1]?.split('/')[0] ?? ''
  if (authority.includes('@')) {
    throw new ApiError(400, 'URL must not contain userinfo (user@host).')
  }
  if (authority.includes(':')) {
    throw new ApiError(400, 'URL must not specify a port.')
  }
  if (parsed.hostname !== 'github.com') {
    throw new ApiError(400, 'Only github.com repositories are supported.')
  }

  const path = parsed.pathname.replace(/\/+$/, '')
  const parts = path.replace(/^\/+/, '').split('/')
  if (parts.length !== 2 || parts.some((p) => p.length === 0)) {
    throw new ApiError(
      400,
      'URL must be a repository root: https://github.com/owner/repo',
    )
  }

  const [owner, rawRepo] = parts
  const repo = rawRepo.endsWith('.git') ? rawRepo.slice(0, -4) : rawRepo
  if (!OWNER_RE.test(owner)) {
    throw new ApiError(400, `Invalid GitHub owner name: "${owner}"`)
  }
  if (!REPO_RE.test(repo)) {
    throw new ApiError(400, `Invalid GitHub repository name: "${repo}"`)
  }

  return `https://github.com/${owner}/${repo}`
}



let csrfReady: Promise<void> | null = null

function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name}=([^;]*)`),
  )
  return match ? decodeURIComponent(match[1]) : null
}


async function ensureCsrf(): Promise<void> {
  if (!csrfReady) {
    csrfReady = fetch('/api/auth/csrf/', { credentials: 'same-origin' })
      .then((res) => {
        if (!res.ok) {
          throw new ApiError(
            res.status,
            'Could not prepare the request (CSRF bootstrap failed).',
          )
        }
      })
      .catch((err) => {
        csrfReady = null 
        throw err
      })
  }
  return csrfReady
}

function extractDetail(data: unknown): string | null {
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>
    if (typeof record.detail === 'string') {
      return record.detail
    }
    
    for (const value of Object.values(record)) {
      if (Array.isArray(value) && typeof value[0] === 'string') {
        return value[0]
      }
      if (typeof value === 'string') {
        return value
      }
    }
  }
  return null
}

async function apiFetch<T>(
  path: string,
  init?: { method?: 'GET' | 'POST' | 'DELETE'; body?: unknown },
): Promise<T> {
  const method = init?.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (init?.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (method !== 'GET') {
    await ensureCsrf()
    const token = readCookie('csrftoken')
    if (token) {
      headers['X-CSRFToken'] = token
    }
  }

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body:
        init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    })
  } catch {
    throw new ApiError(
      0,
      'Network error — is the Django backend running on port 8000?',
    )
  }

  if (res.status === 204) {
    return undefined as T
  }

  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    /* non-JSON body (e.g. proxy error page) — handled below */
  }

  if (!res.ok) {
    throw new ApiError(
      res.status,
      extractDetail(data) ?? `Request failed (${res.status}).`,
    )
  }
  return data as T
}


export interface Repository {
  id: number
  name: string
  github_url: string
  default_branch: string | null
  description: string | null
  created_at: string
  updated_at: string
}

export interface AnalysisRun {
  id: number
  repository: number
  repository_name: string
  status: string
  start_time: string | null
  completion_time: string | null
  summary: string | null
  created_at: string
  issue_count: number
  issues_truncated: boolean
}

interface Paginated<T> {
  results?: T[]
}

export function createRepository(githubUrl: string): Promise<Repository> {
  return apiFetch<Repository>('/api/repositories/', {
    method: 'POST',
    body: { github_url: githubUrl },
  })
}

export async function listRepositories(): Promise<Repository[]> {
  const data = await apiFetch<Paginated<Repository> | Repository[]>(
    '/api/repositories/?page_size=100',
  )
  return Array.isArray(data) ? data : (data.results ?? [])
}

export function createAnalysisRun(repositoryId: number): Promise<AnalysisRun> {
  return apiFetch<AnalysisRun>('/api/analysis/runs/', {
    method: 'POST',
    body: { repository: repositoryId },
  })
}

export async function startAnalysis(githubUrl: string): Promise<AnalysisRun> {
  const canonical = parseGithubRepoUrl(githubUrl)

  let repo: Repository
  try {
    repo = await createRepository(canonical)
  } catch (err) {
    const duplicate =
      err instanceof ApiError &&
      err.status === 400 &&
      /already saved/i.test(err.message)
    if (!duplicate) {
      throw err
    }
    const existing = await listRepositories()
    const found = existing.find((r) => r.github_url === canonical)
    if (!found) {
      throw err 
    }
    repo = found
  }

  return createAnalysisRun(repo.id)
}
