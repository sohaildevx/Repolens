import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  createScan,
  createAgentRun,
  parseGithubRepoUrl,
  repoParts,
} from '@/lib/api'

describe('parseGithubRepoUrl', () => {
  it('accepts a canonical URL and returns it unchanged', () => {
    expect(parseGithubRepoUrl('https://github.com/sohaildevx/Repolens')).toBe(
      'https://github.com/sohaildevx/Repolens',
    )
  })

  it('strips a trailing slash and a .git suffix', () => {
    expect(parseGithubRepoUrl('https://github.com/a/b/')).toBe(
      'https://github.com/a/b',
    )
    expect(parseGithubRepoUrl('https://github.com/a/b.git')).toBe(
      'https://github.com/a/b',
    )
  })

  it('tolerates surrounding whitespace', () => {
    expect(parseGithubRepoUrl('  https://github.com/a/b  ')).toBe(
      'https://github.com/a/b',
    )
  })

  it.each([
    ['https://gitlab.com/a/b', 'github.com'],
    ['https://www.github.com/a/b', 'github.com'],
    ['https://github.com.evil.example/a/b', 'github.com'],
    ['http://github.com/a/b', 'https://'],
    ['https://user@github.com/a/b', 'userinfo'],
    ['https://github.com:8080/a/b', 'port'],
    ['https://github.com/a', 'repository root'],
    ['https://github.com/a/b/c', 'repository root'],
    ['https://github.com/', 'repository root'],
    ['not a url', 'https://'],
    ['', 'required'],
    ['   ', 'required'],
  ])('rejects %s (%s)', (input, fragment) => {
    expect(() => parseGithubRepoUrl(input)).toThrow(ApiError)
    try {
      parseGithubRepoUrl(input)
    } catch (err) {
      if (err instanceof ApiError) {
        expect(err.message.toLowerCase()).toContain(fragment.toLowerCase())
      }
    }
  })

  it('splits owner and repo for metadata lookups', () => {
    expect(repoParts('https://github.com/sohaildevx/Repolens')).toEqual({
      owner: 'sohaildevx',
      repo: 'Repolens',
    })
  })
})

describe('CSRF-aware transport', () => {
  const fetchMock = vi.fn()

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  function stubFetch(handlers: Record<string, () => Response>) {
    fetchMock.mockImplementation((input: string) => {
      const path = String(input)
      for (const [prefix, handler] of Object.entries(handlers)) {
        if (path.startsWith(prefix)) {
          return Promise.resolve(handler())
        }
      }
      return Promise.resolve(new Response('{}', { status: 404 }))
    })
    vi.stubGlobal('fetch', fetchMock)
  }

  it('bootstraps the CSRF cookie and echoes it as X-CSRFToken on POSTs', async () => {
    stubFetch({
      '/api/auth/csrf/': () => new Response(null, { status: 204 }),
      '/api/analysis/issues/5/agent-run/': () =>
        new Response(
          JSON.stringify({
            id: 1,
            analysis_run: 1,
            issue_id: 5,
            status: 'completed',
            repair_attempts: 1,
            start_commit_sha: 'abc',
            final_summary: 'ok',
            checks: { passed: 1, failed: 0, error: 0, skipped: 0 },
            created_at: 'x',
            updated_at: 'x',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        ),
    })
    // jsdom does not implement document.cookie writes from fetch; set the
    // cookie the way the browser would after the bootstrap response.
    document.cookie = 'csrftoken=test-token-123'

    await createAgentRun(5)

    const post = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).includes('agent-run') &&
        (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(post).toBeDefined()
    const [, init] = post as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers['X-CSRFToken']).toBe('test-token-123')
    expect(init.credentials).toBe('same-origin')
  })

  it('surfaces backend detail messages and keeps the body on errors', async () => {
    stubFetch({
      '/api/auth/csrf/': () => new Response(null, { status: 204 }),
      '/api/analysis/scans/': () =>
        new Response(JSON.stringify({ detail: 'Repository not found or is private.' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
    })

    await expect(createScan('https://github.com/a/b')).rejects.toMatchObject({
      status: 404,
      message: 'Repository not found or is private.',
    })
  })

  it('sends the canonical URL to the scan endpoint', async () => {
    stubFetch({
      '/api/auth/csrf/': () => new Response(null, { status: 204 }),
      '/api/analysis/scans/': () =>
        new Response(JSON.stringify({ run: {}, repository: {} }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
    })

    await createScan('https://github.com/a/b.git/')
    const post = fetchMock.mock.calls.find(
      ([input]) => String(input) === '/api/analysis/scans/',
    ) as [string, RequestInit]
    expect(JSON.parse(String(post[1].body))).toEqual({
      github_url: 'https://github.com/a/b',
    })
  })
})
