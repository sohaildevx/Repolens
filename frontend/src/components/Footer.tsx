import { Code } from 'lucide-react'
import { Separator } from '@/components/ui/separator'

const REPO_URL = 'https://github.com/sohaildevx/Repolens'

export function Footer() {
  return (
    <footer className="py-12">
      <div className="mx-auto max-w-6xl px-6">
        <div className="flex flex-col items-start justify-between gap-8 md:flex-row md:items-center">
          <div className="max-w-md">
            <div className="flex items-center gap-2.5">
              <Code className="size-4 text-primary" aria-hidden="true" />
              <span className="text-sm font-semibold tracking-tight">
                Repo<span className="text-primary">Lens</span>
              </span>
            </div>
            <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">
              AI-powered Python code analysis. Find issues, understand their
              causes, and validate proposed fixes in a restricted
              environment.
            </p>
          </div>

          <nav aria-label="Footer" className="flex items-center gap-4">
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded-sm"
            >
              GitHub
            </a>
            <a
              href="#how-it-works"
              className="text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded-sm"
            >
              How it works
            </a>
          </nav>
        </div>

        <Separator className="my-8 bg-border" />

        <p className="font-mono text-[11px] tracking-wide text-muted-foreground/70 uppercase">
          © 2026 RepoLens — built for Python repositories
        </p>
      </div>
    </footer>
  )
}
