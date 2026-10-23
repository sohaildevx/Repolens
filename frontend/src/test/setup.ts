import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Unmount React trees between tests (Testing Library auto-cleanup needs
// globals enabled; this project imports `vitest` helpers explicitly, so
// cleanup is registered manually).
afterEach(() => {
  cleanup()
})
