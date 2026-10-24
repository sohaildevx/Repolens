
function hasUnsafeCharCode(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i)
   
    if (code <= 0x20 || code === 0x7f) {
      return true
    }
  }
  return false
}

export function safeNextPath(raw: string | null | undefined): string {
  if (!raw) {
    return '/'
  }
  const value = raw.trim()
  if (
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\') ||
    value.includes('://') ||
    hasUnsafeCharCode(value)
  ) {
    return '/'
  }
  return value
}

/** Build the login URL for a given return destination. */
export function loginPathFor(next: string): string {
  return `/login?next=${encodeURIComponent(safeNextPath(next))}`
}

/** Build the registration URL for a given return destination. */
export function registerPathFor(next: string): string {
  return `/register?next=${encodeURIComponent(safeNextPath(next))}`
}
