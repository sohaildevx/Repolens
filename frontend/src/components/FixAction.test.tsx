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
  repair_attempts: 1,
  start_commit_sha: 'a'.repeat(40),
  final_summary: 'Fix validated by tests on attempt 1.',
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
    expect(getAgentRunChanges).toHaveBeenCalledWith(11)
    expect(getAgentRunTests).toHaveBeenCalledWith(11)
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
