import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { RepositoryUrlInput } from '@/components/RepositoryUrlInput'

const createScan = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, createScan: (...args: unknown[]) => createScan(...args) }
})

function renderInput() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<RepositoryUrlInput />} />
        <Route path="/scan/:runId" element={<div data-testid="scan-page" />} />
      </Routes>
    </MemoryRouter>,
  )
}

const scanOk = {
  run: { id: 42, status: 'completed' },
  repository: { id: 7, github_url: 'https://github.com/a/b' },
}

describe('RepositoryUrlInput', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('rejects an invalid URL without calling the backend', async () => {
    const user = userEvent.setup()
    renderInput()

    await user.type(
      screen.getByLabelText(/repository url/i),
      'https://gitlab.com/a/b',
    )
    await user.click(screen.getByRole('button', { name: /scan repository/i }))

    expect(
      await screen.findByText(/only github\.com/i),
    ).toBeInTheDocument()
    expect(createScan).not.toHaveBeenCalled()
  })

  it('submits a valid URL and navigates to the results page', async () => {
    createScan.mockResolvedValue(scanOk)
    const user = userEvent.setup()
    renderInput()

    await user.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/a/b',
    )
    await user.click(screen.getByRole('button', { name: /scan repository/i }))

    await waitFor(() => {
      expect(screen.getByTestId('scan-page')).toBeInTheDocument()
    })
    expect(createScan).toHaveBeenCalledWith('https://github.com/a/b')
  })

  it('shows an honest loading message while the scan runs', async () => {
    let resolveScan: (value: unknown) => void = () => {}
    createScan.mockReturnValue(
      new Promise((resolve) => {
        resolveScan = resolve
      }),
    )
    const user = userEvent.setup()
    renderInput()

    await user.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/a/b',
    )
    await user.click(screen.getByRole('button', { name: /scan repository/i }))

    expect(
      await screen.findByText(/can take\s+a minute/i),
    ).toBeInTheDocument()

    resolveScan(scanOk)
    await waitFor(() => {
      expect(screen.getByTestId('scan-page')).toBeInTheDocument()
    })
  })

  it('prevents duplicate submissions while a scan is in flight', async () => {
    let resolveScan: (value: unknown) => void = () => {}
    createScan.mockReturnValue(
      new Promise((resolve) => {
        resolveScan = resolve
      }),
    )
    const user = userEvent.setup()
    renderInput()

    const input = screen.getByLabelText(/repository url/i)
    await user.type(input, 'https://github.com/a/b')
    const button = screen.getByRole('button', { name: /scan repository/i })
    await user.click(button)

    // The button disables itself; an extra Enter press is swallowed too.
    expect(button).toBeDisabled()
    await user.type(input, '{enter}')
    expect(createScan).toHaveBeenCalledTimes(1)

    resolveScan(scanOk)
    await waitFor(() => {
      expect(screen.getByTestId('scan-page')).toBeInTheDocument()
    })
  })

  it('shows the backend error for an inaccessible repository', async () => {
    createScan.mockRejectedValue(
      new ApiError(404, 'Repository not found or is private.'),
    )
    const user = userEvent.setup()
    renderInput()

    await user.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/a/missing',
    )
    await user.click(screen.getByRole('button', { name: /scan repository/i }))

    expect(
      await screen.findByText(/not found or is private/i),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('scan-page')).not.toBeInTheDocument()
  })

  it('routes a recorded failed run (502) to its results page', async () => {
    createScan.mockRejectedValue(
      new ApiError(
        502,
        'Analyzer is unavailable.',
        { run: { id: 9, status: 'failed' }, repository: {} },
      ),
    )
    const user = userEvent.setup()
    renderInput()

    await user.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/a/b',
    )
    await user.click(screen.getByRole('button', { name: /scan repository/i }))

    await waitFor(() => {
      expect(screen.getByTestId('scan-page')).toBeInTheDocument()
    })
  })
})
