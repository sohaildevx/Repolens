import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AgentRun } from '@/lib/api'
import { AgentRunStatus } from '@/components/AgentRunStatus'

const baseRun: AgentRun = {
  id: 11,
  analysis_run: 1,
  issue_id: 5,
  status: 'completed',
  verification_status: 'verified',
  tests_ran: true,
  repair_attempts: 1,
  start_commit_sha: 'a'.repeat(40),
  final_summary: 'Fix validated by tests on attempt 1.',
  explanation: 'Because the bug.',
  checks: { passed: 3, failed: 0, error: 0, skipped: 0 },
  created_at: 'x',
  updated_at: 'x',
}

describe('AgentRunStatus', () => {
  it('shows verified runs in green', () => {
    render(<AgentRunStatus run={baseRun} />)
    const verdict = screen.getByText('Verified')
    expect(verdict).toHaveClass('text-add')
    expect(screen.getByText('completed')).toBeInTheDocument()
  })

  it('shows partially verified runs with the copper treatment and honest note', () => {
    render(
      <AgentRunStatus
        run={{
          ...baseRun,
          verification_status: 'partially_verified',
          tests_ran: false,
          checks: { passed: 2, failed: 0, error: 0, skipped: 1 },
        }}
      />,
    )
    const verdict = screen.getByText('Partially verified')
    expect(verdict).toHaveClass('text-primary') // existing copper accent
    expect(
      screen.getByText(/repository tests: skipped/i),
    ).toBeInTheDocument()
    expect(verdict).not.toHaveClass('text-add')
  })

  it('separates a completed execution from a failed verification', () => {
    render(
      <AgentRunStatus
        run={{
          ...baseRun,
          verification_status: 'failed',
          checks: { passed: 3, failed: 1, error: 0, skipped: 0 },
        }}
      />,
    )
    // Execution completed (the workflow ran) but the FIX failed checks.
    expect(screen.getByText('completed')).toBeInTheDocument()
    const verdict = screen.getByText('Failed')
    expect(verdict).toHaveClass('text-remove')
    expect(
      screen.getByText(/one or more required checks failed/i),
    ).toBeInTheDocument()
  })

  it('shows unverified runs in a neutral muted style', () => {
    render(
      <AgentRunStatus
        run={{
          ...baseRun,
          status: 'failed',
          verification_status: 'unverified',
          tests_ran: null,
          repair_attempts: 0,
          checks: { passed: 0, failed: 0, error: 0, skipped: 0 },
        }}
      />,
    )
    // Execution failed (workflow crashed before validation)...
    expect(screen.getByText('failed')).toBeInTheDocument()
    // ...and there is no evidence to judge any fix.
    const verdict = screen.getByText('Unverified')
    expect(verdict).toHaveClass('text-muted-foreground')
    expect(
      screen.getByText(/repository tests: not reached/),
    ).toBeInTheDocument()
  })

  it('renders the queued state for a pending run', () => {
    render(
      <AgentRunStatus
        run={{
          ...baseRun,
          status: 'pending',
          repair_attempts: 0,
          final_summary: '',
        }}
      />,
    )
    expect(screen.getByText(/agent run queued/i)).toBeInTheDocument()
    expect(
      screen.getByText(/the agent produced no summary/i),
    ).toBeInTheDocument()
  })
})
