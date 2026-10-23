import { Code, LogOut } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { BACKEND_LOGIN_URL, useAuth } from '@/lib/auth'

const REPO_URL = 'https://github.com/sohaildevx/Repolens'

function focusRepositoryInput() {
  const input = document.getElementById('repository-input')
  if (input instanceof HTMLElement) {
    input.scrollIntoView({ behavior: 'smooth', block: 'center' })
    if (input instanceof HTMLInputElement) {
      input.focus({ preventScroll: true })
    }
  }
}

export function Navbar() {
  const { user, loading, signOut } = useAuth()
  const location = useLocation()
  const onLanding = location.pathname === '/'

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link
          to="/"
          className="flex items-center gap-2.5 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          aria-label="RepoLens — home"
        >
          <Code className="size-[18px] text-primary" aria-hidden="true" />
          <span className="text-[15px] font-semibold tracking-tight">
            Repo<span className="text-primary">Lens</span>
          </span>
        </Link>

        <nav aria-label="Primary" className="flex items-center gap-1 sm:gap-2">
          {onLanding && (
            <a
              href="#how-it-works"
              className="hidden rounded-sm px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:inline-block"
            >
              How it works
            </a>
          )}
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="hidden rounded-sm px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:inline-block"
          >
            Documentation
          </a>
          <Separator
            orientation="vertical"
            className="mx-1.5 hidden h-4 bg-border sm:block"
          />

          {!loading && !user && (
            <a
              href={BACKEND_LOGIN_URL}
              className="rounded-sm px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              Sign in
            </a>
          )}
          {!loading && user && (
            <span className="hidden items-center gap-2 sm:flex">
              <span
                className="max-w-[10rem] truncate px-2 py-1.5 font-mono text-[12px] text-muted-foreground"
                title={`Signed in as ${user.username}`}
              >
                {user.username}
              </span>
              <button
                type="button"
                onClick={() => void signOut()}
                aria-label="Sign out"
                className="rounded-sm p-1.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <LogOut className="size-3.5" aria-hidden="true" />
              </button>
            </span>
          )}

          {onLanding ? (
            <Button
              size="sm"
              className="ml-1 h-8 rounded-sm px-3.5 text-[13px]"
              onClick={focusRepositoryInput}
            >
              Get started
            </Button>
          ) : (
            <Button
              size="sm"
              className="ml-1 h-8 rounded-sm px-3.5 text-[13px]"
              asChild
            >
              <Link to="/">Get started</Link>
            </Button>
          )}
        </nav>
      </div>
    </header>
  )
}
