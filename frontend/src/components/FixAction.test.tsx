import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type Issue } from '@/lib/api'
import { FixAction } from '@/components/FixAction'

const createAgentRun = vi.fn()
const getAgentRunChanges = vi.fn()
const getAgentRunTests = vi.fn()
const refresh = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    createAgentRun: (...a: unknown[]) => createAgentRun(...a),
    getAgentRunChanges: (...a: unknown[]) => getAgentRunChanges(...a),
    getAgentRunTests: (...a: unknown[]) => getAgentRunTests(...a),
  }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => useAuthMock(),
}))

let useAuthMock: () => {
  user: { id: number; username: string } | null
  loading: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

function setAuth(user: { id: number; username: string } | null) {
  useAuthMock = () => ({
    user,
    loading: false,
    refresh,
    signOut: async () => {},
  })
}

/** Render as if the visitor is on the issue deep link. */
function renderOnIssuePage() {
  return render(
    <MemoryRouter initialEntries={['/scan/1/issues/5']}>
      <FixAction issue={issue} />
    </MemoryRouter>,
  )
}

const issue: Issue = {
  id: 5,
  file_path: 'app.py',
  line_number: 3,
  severity: 'medium',
  rule: '',
  source: 'ast',
  description: 'Bare except clause.',
}

const agentRun = {
  id: 11,
  analysis_run: 1,
  issue_id: 5,
  status: 'completed' as const,
  verification_status: 'verified' as const,
  tests_ran: true,
  repair_attempts: 1,
  start_commit_sha: 'a'.repeat(40),
  final_summary: 'Fix validated by tests on attempt 1.',
  explanation: 'Catch specific exceptions instead of a bare except.',
  checks: { passed: 1, failed: 0, error: 0, skipped: 2 },
  created_at: 'x',
  updated_at: 'x',
}

describe('FixAction', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows the sign-in gate instead of calling the agent when logged out', async () => {
    setAuth(null)
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    const signIn = await screen.findByRole('link', {
      name: /sign in to continue/i,
    })
    // Same-app route carrying this exact issue page as the return
    // destination -- validated by safeNextPath before rendering.
    expect(signIn).toHaveAttribute(
      'href',
      '/login?next=%2Fscan%2F1%2Fissues%2F5',
    )
    expect(createAgentRun).not.toHaveBeenCalled()
  })

  it('re-checks the session from the gate without auto-running anything', async () => {
    setAuth(null)
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )
    await user.click(
      await screen.findByRole('button', { name: /i signed in -- check/i }),
    )

    expect(refresh).toHaveBeenCalled()
    expect(createAgentRun).not.toHaveBeenCalled()
  })

  it('runs the agent for a signed-in user and renders the results', async () => {
    setAuth({ id: 2, username: 'alice' })
    createAgentRun.mockResolvedValue(agentRun)
    getAgentRunChanges.mockResolvedValue([
      {
        id: 1,
        file_path: 'app.py',
        proposed_diff: '- except:\n+ except Exception:',
        attempt: 1,
        created_at: 'x',
      },
    ])
    getAgentRunTests.mockResolvedValue([
      {
        id: 1,
        test_name: 'test_app.py',
        status: 'passed' as const,
        attempt: 1,
        output: '',
        created_at: 'x',
      },
    ])
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    expect(await screen.findByText(agentRun.final_summary)).toBeInTheDocument()
    expect(screen.getByText(/checks: 1 passed/)).toBeInTheDocument()
    expect(screen.getByText(/2 skipped/)).toBeInTheDocument()
    expect(screen.getByText(/proposed patch/i)).toBeInTheDocument()
    expect(screen.getByText('test_app.py')).toBeInTheDocument()
    // Backend verdict and explanation are rendered from real fields.
    expect(screen.getByText('Verified')).toBeInTheDocument()
    expect(screen.getByText(/repository tests: executed/)).toBeInTheDocument()
    expect(screen.getByText(/what changed and why/i)).toBeInTheDocument()
    expect(screen.getByText(agentRun.explanation)).toBeInTheDocument()
    expect(getAgentRunChanges).toHaveBeenCalledWith(11)
    expect(getAgentRunTests).toHaveBeenCalledWith(11)
  })

  it('shows a loading state while the agent runs, with no fake progress', async () => {
    setAuth({ id: 2, username: 'alice' })
    let resolveRun: (run: unknown) => void = () => {}
    createAgentRun.mockImplementation(
      () =>
        new Promise((res) => {
          resolveRun = res
        }),
    )
    getAgentRunChanges.mockResolvedValue([])
    getAgentRunTests.mockResolvedValue([])
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    // Real progress language, no simulated percentage or early success.
    expect(
      screen.getByText(/snapshot, patch, validation/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    ).toBeDisabled()

    resolveRun(agentRun)
    expect(await screen.findByText(agentRun.final_summary)).toBeInTheDocument()
  })

  it('renders partial verification distinctly from success', async () => {
    setAuth({ id: 2, username: 'alice' })
    createAgentRun.mockResolvedValue({
      ...agentRun,
      verification_status: 'partially_verified' as const,
      tests_ran: false,
      final_summary: 'Patch applied and static checks passed on attempt 1.',
    })
    getAgentRunChanges.mockResolvedValue([])
    getAgentRunTests.mockResolvedValue([])
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    const partial = await screen.findByText('Partially verified')
    expect(partial).toHaveClass('text-primary') // copper, not green
    expect(
      screen.getByText(/repository tests: skipped/i),
    ).toBeInTheDocument()
    expect(screen.queryByText('Verified')).not.toBeInTheDocument()
  })

  it('renders a completed run whose checks failed as execution completed, verification failed', async () => {
    setAuth({ id: 2, username: 'alice' })
    createAgentRun.mockResolvedValue({
      ...agentRun,
      verification_status: 'failed' as const,
      final_summary: 'Validation failed after 2 attempt(s): tests failed',
      checks: { passed: 3, failed: 1, error: 0, skipped: 0 },
    })
    getAgentRunChanges.mockResolvedValue([])
    getAgentRunTests.mockResolvedValue([])
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    expect(await screen.findByText('Failed')).toHaveClass('text-remove')
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText(/1 failed/)).toBeInTheDocument()
  })

  it('surfaces the concurrent-run (409) response honestly', async () => {
    setAuth({ id: 2, username: 'alice' })
    createAgentRun.mockRejectedValue(
      new ApiError(409, 'An agent run is already in progress.'),
    )
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    expect(
      await screen.findByText(/already in progress/i),
    ).toBeInTheDocument()
  })

  it('falls back to the gate if the session expired mid-request', async () => {
    setAuth({ id: 2, username: 'alice' })
    createAgentRun.mockRejectedValue(new ApiError(403, 'Authentication required.'))
    const user = userEvent.setup()
    renderOnIssuePage()

    await user.click(
      screen.getByRole('button', { name: /generate fix with ai/i }),
    )

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: /sign in to continue/i }),
      ).toHaveAttribute('href', '/login?next=%2Fscan%2F1%2Fissues%2F5')
    })
  })
})
