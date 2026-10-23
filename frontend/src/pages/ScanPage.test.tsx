import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type AnalysisRun } from '@/lib/api'
import { ScanPage } from '@/pages/ScanPage'

const getRun = vi.fn()
const getRunIssues = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    getRun: (...args: unknown[]) => getRun(...args),
    getRunIssues: (...args: unknown[]) => getRunIssues(...args),
  }
})

function baseRun(overrides: Partial<AnalysisRun>): AnalysisRun {
  return {
    id: 1,
    repository: 7,
    repository_name: 'hello',
    repository_github_url: 'https://github.com/a/hello',
    status: 'completed',
    start_time: '2026-10-23T10:00:00Z',
    completion_time: '2026-10-23T10:00:05Z',
    files_analyzed: 12,
    summary: 'Analyzed 12 file(s); 2 finding(s) (high: 1, low: 1).',
    created_at: '2026-10-23T10:00:00Z',
    issues: [],
    issue_count: 0,
    issues_truncated: false,
    ...overrides,
  }
}

const finding = {
  id: 101,
  file_path: 'app.py',
  line_number: 3,
  severity: 'high' as const,
  rule: 'B605',
  source: 'bandit',
  description: 'Start a process with a shell.',
}

function renderScanPage() {
  return render(
    <MemoryRouter initialEntries={['/scan/1']}>
      <Routes>
        <Route path="/scan/:runId" element={<ScanPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ScanPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders real findings grouped by file', async () => {
    getRun.mockResolvedValue(
      baseRun({ issues: [finding], issue_count: 1 }),
    )
    renderScanPage()

    expect(await screen.findByText('app.py')).toBeInTheDocument()
    expect(screen.getByText(finding.description)).toBeInTheDocument()
    // "high" appears in the list row AND in the summary breakdown -- both
    // are correct renders; assert at least one.
    expect(screen.getAllByText('high').length).toBeGreaterThan(0)
    // Summary numbers come from the backend record. The findings figure
    // renders twice on purpose: in its <dd> and beside the severity chip.
    const summary = screen.getByRole('region', { name: 'Analysis summary' })
    expect(within(summary).getByText('12')).toBeInTheDocument() // files
    expect(within(summary).getAllByText('1')).toHaveLength(2) // findings
  })

  it('shows an honest empty state for a clean completed run', async () => {
    getRun.mockResolvedValue(
      baseRun({ summary: 'Analyzed 5 file(s); 0 finding(s) (no issues).' }),
    )
    renderScanPage()

    expect(await screen.findByText('No issues detected')).toBeInTheDocument()
    expect(
      screen.getByText(/does not mean the repository is bug-free/i),
    ).toBeInTheDocument()
  })

  it('distinguishes a failed analysis from a clean run', async () => {
    getRun.mockResolvedValue(
      baseRun({
        status: 'failed',
        files_analyzed: null,
        summary: 'Analyzer service is unavailable.',
      }),
    )
    renderScanPage()

    // "Analysis failed" renders twice on purpose: once in the summary
    // header, once as the alert with the safe backend message.
    const headings = await screen.findAllByText('Analysis failed')
    expect(headings.length).toBeGreaterThan(1)
    expect(screen.getByRole('alert')).toHaveTextContent(
      /Analyzer service is unavailable/,
    )
    expect(screen.queryByText('No issues detected')).not.toBeInTheDocument()
  })

  it('renders "—" for unknown files analyzed, never 0', async () => {
    getRun.mockResolvedValue(baseRun({ files_analyzed: null }))
    renderScanPage()

    await screen.findByText('Analysis completed')
    expect(screen.getByTitle('Unknown')).toBeInTheDocument()
    expect(screen.getByTitle('Unknown')).toHaveTextContent('—')
  })

  it('shows an error state for a run that is not accessible', async () => {
    getRun.mockRejectedValue(new ApiError(404, 'Not found.'))
    renderScanPage()

    expect(
      await screen.findByText(/does not exist or is not publicly accessible/i),
    ).toBeInTheDocument()
  })

  it('loads additional findings from the paginated endpoint when truncated', async () => {
    const many = Array.from({ length: 100 }, (_, i) => ({
      ...finding,
      id: 200 + i,
      file_path: `f${i}.py`,
    }))
    getRun.mockResolvedValue(
      baseRun({ issues: many, issue_count: 150, issues_truncated: true }),
    )
    getRunIssues.mockResolvedValue({
      count: 150,
      next: null,
      previous: null,
      results: [{ ...finding, id: 999, file_path: 'extra.py' }],
    })
    const user = (await import('@testing-library/user-event')).default.setup()
    renderScanPage()

    await screen.findByText('f0.py')
    const more = await screen.findByRole('button', {
      name: /load more findings/i,
    })
    await user.click(more)

    await waitFor(() => {
      expect(screen.getByText('extra.py')).toBeInTheDocument()
    })
    expect(getRunIssues).toHaveBeenCalledWith(1, 6)
  })
})
