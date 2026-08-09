import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Reads the release version from the CHANGELOG's newest entry (`## X.Y.Z ...`) so the built
// bundle can report its own baked version (see src/version.ts) without duplicating it in
// package.json. 'unknown' when the file is missing or unparsable (e.g. before CHANGELOG.md
// exists in the repo, or a shallow build context that doesn't include it).
function changelogVersion(): string {
  try {
    const text = readFileSync(fileURLToPath(new URL('../CHANGELOG.md', import.meta.url)), 'utf-8')
    const match = text.match(/^## (\d+\.\d+\.\d+) /m)
    return match ? match[1] : 'unknown'
  } catch {
    return 'unknown'
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(changelogVersion()),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['tests/setup.ts'],
    globals: true,
  },
})
