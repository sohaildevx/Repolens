import { ScanSearch, PenLine, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface Step {
  number: string
  title: string
  description: string
  icon: LucideIcon
}

const STEPS: Step[] = [
  {
    number: '01',
    title: 'Analyze',
    description:
      'Inspect Python code using AST, Ruff, and Bandit to identify potential issues.',
    icon: ScanSearch,
  },
  {
    number: '02',
    title: 'Repair',
    description:
      'Use NVIDIA Nemotron to explain findings and propose targeted fixes.',
    icon: PenLine,
  },
  {
    number: '03',
    title: 'Verify',
    description:
      'Apply proposed changes in a restricted environment and inspect actual validation results.',
    icon: ShieldCheck,
  },
]


export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="border-b border-border py-20 md:py-28"
      aria-labelledby="how-it-works-heading"
    >
      <div className="mx-auto max-w-6xl px-6">
        <p className="font-mono text-[11px] tracking-[0.22em] text-muted-foreground uppercase">
          Process
        </p>
        <h2
          id="how-it-works-heading"
          className="mt-3 text-3xl font-bold tracking-tight md:text-4xl"
        >
          How it works
        </h2>

        <ol className="mt-12 grid grid-cols-1 gap-y-10 md:grid-cols-3 md:gap-y-0">
          {STEPS.map((step, index) => {
            const Icon = step.icon
            return (
              <li
                key={step.number}
                className={
                  'md:px-8 ' +
                  (index === 0 ? 'md:pl-0 ' : 'md:border-l md:border-border ') +
                  (index === STEPS.length - 1 ? 'md:pr-0' : '')
                }
              >
                <div className="flex items-baseline gap-4">
                  <span
                    className="font-mono text-4xl font-bold text-primary/35 select-none md:text-5xl"
                    aria-hidden="true"
                  >
                    {step.number}
                  </span>
                  <h3 className="flex items-center gap-2 font-mono text-[13px] tracking-[0.18em] text-foreground uppercase">
                    <Icon
                      className="size-3.5 text-muted-foreground"
                      aria-hidden="true"
                    />
                    {step.title}
                  </h3>
                </div>
                <p className="mt-4 max-w-xs text-sm leading-relaxed text-muted-foreground">
                  {step.description}
                </p>
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}
