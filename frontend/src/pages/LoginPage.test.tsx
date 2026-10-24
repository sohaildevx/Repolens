import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { LoginPage } from '@/pages/LoginPage'

const login = vi.fn()
const refresh = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    login: (...a: unknown[]) => login(...a),
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

function renderLogin(
  entry = '/login?next=%2Fscan%2F11%2Fissues%2F5',
) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/scan/:runId/issues/:issueId"
          element={<div>issue page</div>}
        />
        <Route path="/" element={<div>landing page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
    setAuth(null)
  })

  it('validates required fields before calling the backend', async () => {
    setAuth(null)
    const user = userEvent.setup()
    renderLogin()

    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Username is required.')).toBeInTheDocument()
    expect(login).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Password is required.')).toBeInTheDocument()
    expect(login).not.toHaveBeenCalled()
  })

  it('signs in and returns to the original issue page', async () => {
    setAuth(null)
    login.mockResolvedValue({ detail: 'ok', user: { id: 1, username: 'alice' } })
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw-1234')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('issue page')).toBeInTheDocument()
    expect(login).toHaveBeenCalledWith('alice', 'pw-1234')
    expect(refresh).toHaveBeenCalled()
  })

  it('shows the backend message when credentials are rejected', async () => {
    setAuth(null)
    login.mockRejectedValue(
      new ApiError(401, 'Invalid username or password.'),
    )
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByText('Invalid username or password.'),
    ).toBeInTheDocument()
    // Still on the sign-in page -- success is never claimed early.
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('surfaces unexpected errors without claiming success', async () => {
    setAuth(null)
    login.mockRejectedValue(new TypeError('fetch failed'))
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw-1234')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByText('Unexpected error -- please try again.'),
    ).toBeInTheDocument()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('blocks repeated submissions while one is in flight', async () => {
    setAuth(null)
    login.mockReturnValue(new Promise(() => {})) // never resolves
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw-1234')
    const submit = screen.getByRole('button', { name: 'Sign in' })
    await user.click(submit)
    await user.click(submit)

    expect(login).toHaveBeenCalledTimes(1)
    expect(submit).toBeDisabled()
  })

  it('toggles password visibility', async () => {
    setAuth(null)
    const user = userEvent.setup()
    renderLogin()

    const field = screen.getByLabelText('Password')
    expect(field).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(field).toHaveAttribute('type', 'text')

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(field).toHaveAttribute('type', 'password')
  })

  it('links to registration while preserving the return destination', () => {
    setAuth(null)
    renderLogin()

    expect(screen.getByRole('link', { name: 'Create one' })).toHaveAttribute(
      'href',
      '/register?next=%2Fscan%2F11%2Fissues%2F5',
    )
  })

  it('offers a continuation instead of a second form when signed in', () => {
    setAuth({ id: 1, username: 'alice' })
    renderLogin()

    expect(screen.getByText('Signed in as alice.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /continue/i })).toHaveAttribute(
      'href',
      '/scan/11/issues/5',
    )
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('falls back to the landing page for unsafe next values', async () => {
    setAuth(null)
    login.mockResolvedValue({ detail: 'ok', user: { id: 1, username: 'alice' } })
    const user = userEvent.setup()
    renderLogin('/login?next=https%3A%2F%2Fevil.example')

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw-1234')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => {
      expect(screen.getByText('landing page')).toBeInTheDocument()
    })
  })
})
