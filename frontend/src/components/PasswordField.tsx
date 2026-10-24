import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { Input } from '@/components/ui/input'

interface PasswordFieldProps {
  id: string
  value: string
  onChange: (value: string) => void
  autoComplete: 'current-password' | 'new-password'
  placeholder?: string
  invalid?: boolean
  describedBy?: string
  disabled?: boolean
}

export function PasswordField({
  id,
  value,
  onChange,
  autoComplete,
  placeholder,
  invalid,
  describedBy,
  disabled,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? 'text' : 'password'}
        name={id}
        autoComplete={autoComplete}
        spellCheck={false}
        disabled={disabled}
        required
        placeholder={placeholder}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={
          'h-11 rounded-sm bg-card pr-10 font-mono text-[13px] ' +
          (invalid
            ? 'border-destructive focus-visible:border-destructive'
            : 'hover:border-muted-foreground/60 focus-visible:border-primary')
        }
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? 'Hide password' : 'Show password'}
        aria-pressed={visible}
        disabled={disabled}
        className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm p-1.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
      >
        {visible ? (
          <EyeOff className="size-4" aria-hidden="true" />
        ) : (
          <Eye className="size-4" aria-hidden="true" />
        )}
      </button>
    </div>
  )
}

export function AuthFieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string
  children: string
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-2 block font-mono text-[11px] tracking-[0.18em] text-muted-foreground uppercase"
    >
      {children}
    </label>
  )
}
