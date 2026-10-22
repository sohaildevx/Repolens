import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // Dev-only proxy: the browser talks to same-origin /api/... and Vite
    // forwards to the Django backend. This keeps session + CSRF cookies
    // same-origin in development, so no CORS changes are needed server-side.
    // Production should serve the built frontend and the API from one origin
    // (reverse proxy), which is the same contract.
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
})
