import { Code } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'


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
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
     
        <a
          href="#top"
          className="flex items-center gap-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-sm"
          aria-label="RepoLens — back to top"
        >
          <Code className="size-[18px] text-primary" aria-hidden="true" />
          <span className="text-[15px] font-semibold tracking-tight">
            Repo<span className="text-primary">Lens</span>
          </span>
        </a>

        <nav aria-label="Primary" className="flex items-center gap-1 sm:gap-2">
          <a
            href="#how-it-works"
            className="hidden px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded-sm sm:inline-block"
          >
            How it works
          </a>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="hidden px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded-sm sm:inline-block"
          >
            Documentation
          </a>
          <Separator
            orientation="vertical"
            className="mx-1.5 hidden h-4 bg-border sm:block"
          />
          
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                aria-disabled="true"
                className="hidden cursor-default px-3 py-2 text-[13px] text-muted-foreground/50 sm:inline-block"
              >
                Sign in
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              Sign-in ships with the application shell in a later phase
            </TooltipContent>
          </Tooltip>
          <Button
            size="sm"
            className="ml-1 h-8 rounded-sm px-3.5 text-[13px]"
            onClick={focusRepositoryInput}
          >
            Get started
          </Button>
        </nav>
      </div>
    </header>
  )
}
