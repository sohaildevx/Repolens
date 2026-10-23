import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RepositoryOverview } from '@/components/RepositoryOverview'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const repoInfo = {
  full_name: 'a/hello',
  description: 'A test repository.',
  stargazers_count: 0, // genuine zero
  forks_count: 7,
  open_issues_count: 2,
  default_branch: 'master',
  language: 'Python',
}

describe('RepositoryOverview', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders fetched metadata, keeping a genuine 0 distinct from missing data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string) => {
        const url = String(input)
        if (url.endsWith('/languages')) {
          return Promise.resolve(jsonResponse({ Python: 900, Makefile: 100 }))
        }
        return Promise.resolve(jsonResponse(repoInfo))
      }),
    )

    render(
      <RepositoryOverview
        githubUrl="https://github.com/a/hello"
        fallbackName="hello"
      />,
    )

    expect(await screen.findByText('a/hello')).toBeInTheDocument()
    // A true zero renders as 0...
    expect(screen.getByTitle('Stars')).toHaveTextContent('0')
    expect(screen.getByTitle('Forks')).toHaveTextContent('7')
    expect(screen.getByText(/Python 90%/)).toBeInTheDocument()
    expect(screen.getByText(/branch: master/)).toBeInTheDocument()
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument()
  })

  it('surfaces GitHub rate limiting as its own state, not as zeros', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response('{"message":"API rate limit exceeded"}', {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    )

    render(
      <RepositoryOverview
        githubUrl="https://github.com/a/hello"
        fallbackName="hello"
      />,
    )

    expect(
      await screen.findByText(/rate limit/i),
    ).toBeInTheDocument()
    // No fabricated stats anywhere.
    expect(screen.queryByTitle('Stars')).not.toBeInTheDocument()
  })

  it('shows the fallback record when GitHub is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('network down'))),
    )

    render(
      <RepositoryOverview
        githubUrl="https://github.com/a/hello"
        fallbackName="hello"
        fallbackDescription="Stored by the backend."
      />,
    )

    expect(await screen.findByText('metadata unavailable')).toBeInTheDocument()
    // The backend-provided name/description keep the header useful.
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.getByText('Stored by the backend.')).toBeInTheDocument()
  })
})
