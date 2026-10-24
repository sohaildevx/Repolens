import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { TestResult } from '@/lib/api'
import { ValidationResults } from '@/components/ValidationResults'

const results: TestResult[] = [
  {
    id: 1,
    test_name: 'tests',
    status: 'failed',
    attempt: 1,
    output: 'FAILED (test_add) -- AssertionError',
    created_at: 'x',
  },
  {
    id: 2,
    test_name: 'syntax:app.py',
    status: 'passed',
    attempt: 1,
    output: '',
    created_at: 'x',
  },
  {
    id: 3,
    test_name: 'tests',
    status: 'skipped',
    attempt: 2,
    output: 'Sandbox execution not available in this environment.',
    created_at: 'x',
  },
]

describe('ValidationResults', () => {
  it('expands rows carrying output and collapses them again', async () => {
    const user = userEvent.setup()
    render(<ValidationResults results={results} />)

    const failedRow = screen.getByRole('button', {
      name: /tests\s*failed\s*attempt\s*1/i,
    })
    expect(failedRow).toHaveAttribute('aria-expanded', 'false')

    await user.click(failedRow)
    expect(
      screen.getByText('FAILED (test_add) -- AssertionError'),
    ).toBeInTheDocument()
    expect(failedRow).toHaveAttribute('aria-expanded', 'true')

    await user.click(failedRow)
    expect(
      screen.queryByText('FAILED (test_add) -- AssertionError'),
    ).not.toBeInTheDocument()
  })

  it('keeps skipped rows visible as skipped, with their reason expandable', () => {
    render(<ValidationResults results={results} />)
    expect(screen.getAllByText('skipped')).toHaveLength(1)
    expect(screen.getByText('attempt 2')).toBeInTheDocument()
  })

  it('does not expand rows without output', () => {
    render(<ValidationResults results={results} />)
    // The passed syntax row has no output: present but not expandable.
    const passedRow = screen.getByRole('button', {
      name: /syntax:app\.py\s*passed/i,
    })
    expect(passedRow).toBeDisabled()
  })

  it('renders an honest empty state when no evidence exists', () => {
    render(<ValidationResults results={[]} />)
    expect(screen.getByText(/no validation evidence/i)).toBeInTheDocument()
  })
})
