import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { RegisterPage } from '@/pages/RegisterPage'

const register = vi.fn()
const login = vi.fn()
const refresh = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    register: (...a: unknown[]) => register(...a),
    login: (...a: unknown[]) => login(...a),
  }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    refresh,
    signOut: async () => {},
  }),
}))

function renderRegister(
  entry = '/register?next=%2Fscan%2F11%2Fissues%2F5',
) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/scan/:runId/issues/:issueId"
          element={<div>issue page</div>}
        />
        <Route path="/login" element={<div>login page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Username'), 'alice')
  await user.type(screen.getByLabelText('Email'), 'alice@example.com')
  await user.type(screen.getByLabelText('Password'), 'long-enough-pw')
  await user.type(screen.getByLabelText('Confirm password'), 'long-enough-pw')
}

const submit = () =>
  screen.getByRole('button', { name: 'Create account' })

describe('RegisterPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('validates required fields before calling the backend', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.click(submit())

    expect(await screen.findByText('Username is required.')).toBeInTheDocument()
    expect(screen.getByText('Email is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(
      screen.getByText('Please confirm your password.'),
    ).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })

  it('validates the email format and the password confirmation', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Email'), 'not-an-email')
    await user.type(screen.getByLabelText('Password'), 'long-enough-pw')
    await user.type(screen.getByLabelText('Confirm password'), 'different-pw')
    await user.click(submit())

    expect(
      await screen.findByText('Enter a valid email address.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })

  it('shows Django field errors such as a duplicate username', async () => {
    register.mockRejectedValue(
      new ApiError(400, 'Validation error.', {
        username: ['A user with that username already exists.'],
      }),
    )
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)
    await user.click(submit())

    expect(
      await screen.findByText('A user with that username already exists.'),
    ).toBeInTheDocument()
    // Still on the form; no success is claimed.
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
  })

  it('shows all password-rule messages returned by Django', async () => {
    register.mockRejectedValue(
      new ApiError(400, 'Validation error.', {
        password: [
          'This password is too short. It must contain at least 8 characters.',
          'This password is too common.',
        ],
      }),
    )
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)
    await user.click(submit())

    expect(
      await screen.findByText(
        'This password is too short. It must contain at least 8 characters.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('This password is too common.')).toBeInTheDocument()
  })

  it('creates the account, signs in, and returns to the original issue', async () => {
    register.mockResolvedValue({
      detail: 'Account created.',
      user: { id: 1, username: 'alice' },
    })
    login.mockResolvedValue({
      detail: 'ok',
      user: { id: 1, username: 'alice' },
    })
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)
    await user.click(submit())

    expect(await screen.findByText('issue page')).toBeInTheDocument()
    expect(register).toHaveBeenCalledWith({
      username: 'alice',
      email: 'alice@example.com',
      password: 'long-enough-pw',
      password_confirm: 'long-enough-pw',
    })
    // Auto sign-in goes through the existing login endpoint.
    expect(login).toHaveBeenCalledWith('alice', 'long-enough-pw')
    expect(refresh).toHaveBeenCalled()
  })

  it('shows the honest success state if auto sign-in fails', async () => {
    register.mockResolvedValue({
      detail: 'Account created.',
      user: { id: 1, username: 'alice' },
    })
    login.mockRejectedValue(new ApiError(401, 'Invalid username or password.'))
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)
    await user.click(submit())

    expect(await screen.findByText('Your account is ready.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/login?next=%2Fscan%2F11%2Fissues%2F5',
    )
    expect(refresh).not.toHaveBeenCalled()
  })

  it('links to sign-in while preserving the return destination', () => {
    renderRegister()

    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/login?next=%2Fscan%2F11%2Fissues%2F5',
    )
  })

  it('blocks repeated submissions while one is in flight', async () => {
    register.mockReturnValue(new Promise(() => {})) // never resolves
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)

    const button = submit()
    await user.click(button)
    await user.click(button)

    expect(register).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
  })

  it('does not claim success when the server returns an unexpected error', async () => {
    register.mockRejectedValue(new TypeError('fetch failed'))
    const user = userEvent.setup()
    renderRegister()
    await fillForm(user)
    await user.click(submit())

    await waitFor(() => {
      expect(
        screen.getByText('Unexpected error -- please try again.'),
      ).toBeInTheDocument()
    })
    expect(login).not.toHaveBeenCalled()
  })
})
