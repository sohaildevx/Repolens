

export class ApiError extends Error {
  readonly status: number
  /** Parsed response body, when one existed (e.g. a failed run record). */
  readonly data: unknown

  constructor(status: number, message: string, data: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}


const OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/

/** Validate a GitHub URL and return its canonical form. */
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

  // Authority = everything between "://" and the first "/". Reject userinfo
  // and ANY explicit port (the backend rejects :443 as well).
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

/** Split a validated GitHub URL into (owner, repo). */
export function repoParts(githubUrl: string): { owner: string; repo: string } {
  const canonical = parseGithubRepoUrl(githubUrl)
  const [owner, repo] = canonical.replace('https://github.com/', '').split('/')
  return { owner, repo }
}

/* ------------------------------------------------------------------ *
 * Transport
 * ------------------------------------------------------------------ */

let csrfReady: Promise<void> | null = null

function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name}=([^;]*)`),
  )
  return match ? decodeURIComponent(match[1]) : null
}

/** Fetch the CSRF cookie once per page load; retries after failures. */
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
        csrfReady = null // allow a retry on the next request
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
    // DRF field errors: {"github_url": ["message"]} -- surface the first.
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
    /* non-JSON body (e.g. proxy error page) -- handled below */
  }

  if (!res.ok) {
    throw new ApiError(
      res.status,
      extractDetail(data) ?? `Request failed (${res.status}).`,
      data,
    )
  }
  return data as T
}

/* ------------------------------------------------------------------ *
 * Types + endpoints
 * ------------------------------------------------------------------ */

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed'
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export type TestStatus = 'passed' | 'failed' | 'error' | 'skipped'

export interface Repository {
  id: number
  name: string
  github_url: string
  default_branch: string
  description: string
  created_at: string
  updated_at: string
}

export interface Issue {
  id: number
  file_path: string
  line_number: number
  severity: Severity
  rule: string
  source: string
  description: string
}

export interface AnalysisRun {
  id: number
  repository: number
  repository_name: string
  repository_github_url: string
  status: RunStatus
  start_time: string | null
  completion_time: string | null
  /** Files the analyzer examined; null = unknown (never render a fake 0). */
  files_analyzed: number | null
  summary: string
  created_at: string
  issues: Issue[]
  issue_count: number
  issues_truncated: boolean
}

export interface ScanResponse {
  run: AnalysisRun
  repository: Repository
}

export interface AgentRun {
  id: number
  analysis_run: number
  issue_id: number | null
  status: RunStatus
  repair_attempts: number
  start_commit_sha: string
  final_summary: string
  checks: { passed: number; failed: number; error: number; skipped: number }
  created_at: string
  updated_at: string
}

export interface CodeChange {
  id: number
  file_path: string
  proposed_diff: string
  created_at: string
}

export interface TestResult {
  id: number
  test_name: string
  status: TestStatus
  attempt: number
  output: string
  created_at: string
}

interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

/** Public scan: the backend fetches the repo server-side and analyzes it. */
export function createScan(githubUrl: string): Promise<ScanResponse> {
  const canonical = parseGithubRepoUrl(githubUrl)
  return apiFetch<ScanResponse>('/api/analysis/scans/', {
    method: 'POST',
    body: { github_url: canonical },
  })
}

export function getRun(runId: number): Promise<AnalysisRun> {
  return apiFetch<AnalysisRun>(`/api/analysis/runs/${runId}/`)
}

export function getRunIssues(
  runId: number,
  page = 1,
): Promise<Paginated<Issue>> {
  return apiFetch<Paginated<Issue>>(
    `/api/analysis/runs/${runId}/issues/?page=${page}`,
  )
}

export function getIssue(issueId: number): Promise<Issue> {
  return apiFetch<Issue>(`/api/analysis/issues/${issueId}/`)
}

/** Current user, or null when anonymous (403 is the documented answer). */
export async function getCurrentUser(): Promise<{
  id: number
  username: string
} | null> {
  try {
    return await apiFetch<{ id: number; username: string }>(
      '/api/auth/me/',
    )
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      return null
    }
    throw err
  }
}

export function logout(): Promise<{ detail: string }> {
  return apiFetch<{ detail: string }>('/api/auth/logout/', {
    method: 'POST',
  })
}

export interface AuthUser {
  id: number
  username: string
  email: string
  date_joined: string
  last_login: string | null
}

/**
 * Sign in. The backend opens a session cookie server-side; one generic
 * 401 covers unknown username AND wrong password (no user enumeration).
 */
export function login(
  username: string,
  password: string,
): Promise<{ detail: string; user: AuthUser }> {
  return apiFetch<{ detail: string; user: AuthUser }>('/api/auth/login/', {
    method: 'POST',
    body: { username, password },
  })
}

export interface RegisterInput {
  username: string
  email: string
  password: string
  password_confirm: string
}


export function register(
  input: RegisterInput,
): Promise<{ detail: string; user: AuthUser }> {
  return apiFetch<{ detail: string; user: AuthUser }>('/api/auth/register/', {
    method: 'POST',
    body: input,
  })
}

/** AI fix -- always authenticated; 401/403 means "sign in first". */
export function createAgentRun(issueId: number): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/analysis/issues/${issueId}/agent-run/`, {
    method: 'POST',
  })
}

export function getAgentRunChanges(agentRunId: number): Promise<CodeChange[]> {
  return apiFetch<CodeChange[]>(`/api/agent-runs/${agentRunId}/changes/`)
}

export function getAgentRunTests(agentRunId: number): Promise<TestResult[]> {
  return apiFetch<TestResult[]>(`/api/agent-runs/${agentRunId}/tests/`)
}
