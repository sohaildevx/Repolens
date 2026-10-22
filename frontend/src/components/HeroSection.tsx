import { RepositoryInput } from '@/components/RepositoryInput'
import { TechnicalMetadata } from '@/components/TechnicalMetadata'


export function HeroSection() {
  return (
    <section
      id="top"
      className="relative overflow-hidden border-b border-border"
      aria-labelledby="hero-heading"
    >
     
      <div
        className="grid-lines pointer-events-none absolute inset-0 opacity-50"
        aria-hidden="true"
      />
      
      <div
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,var(--background),transparent_18%,transparent_82%,var(--background))]"
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-16 text-center md:pt-28 md:pb-20">
        <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
          AI-powered Python analysis
        </p>

        <h1
          id="hero-heading"
          className="mt-6 text-[clamp(2.5rem,8.5vw,5.75rem)] leading-[0.95] font-bold tracking-tight uppercase"
        >
          Your code has
          <br />
          <span className="text-primary">something to say.</span>
        </h1>

        <p className="mt-6 text-lg text-muted-foreground md:text-xl">
          Find the bugs<span className="mx-2 text-primary/70">·</span>
          Understand the cause<span className="mx-2 text-primary/70">·</span>
          Validate the fix
        </p>

        <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-muted-foreground/90 md:text-base">
          Analyze your Python repository, investigate potential issues, and
          use AI to propose fixes you can validate.
        </p>

        <div className="mt-10 md:mt-12">
          <RepositoryInput />
        </div>

        <div className="mt-10 flex justify-center">
          <TechnicalMetadata
            items={['Python / Static analysis', 'AST · Ruff · Bandit']}
          />
        </div>
      </div>
    </section>
  )
}
