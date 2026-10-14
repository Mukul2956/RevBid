import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    // REST goes through the dev proxy (same origin, so the refresh cookie just works).
    // The WebSocket connects straight to the API (see VITE_WS_URL), exactly as in production.
    proxy: { '/api': { target: process.env.API_ORIGIN ?? 'http://127.0.0.1:8100' } },
  },
  build: {
    sourcemap: true,
    target: 'es2022',
    rolldownOptions: {
      output: {
        // Stable vendor chunks: a deploy that only changes app code leaves these cached in the browser.
        codeSplitting: {
          groups: [
            // `[\\/]` matches either path separator, so chunking is the same on Windows and Linux.
            { name: 'react', test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/ },
            { name: 'data', test: /node_modules[\\/](@tanstack|zod|react-hook-form|@hookform)[\\/]/ },
            { name: 'ui', test: /node_modules[\\/](@radix-ui|lucide-react|sonner|clsx|tailwind-merge)[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: false,
    setupFiles: ['./src/test/setup.ts'],
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
