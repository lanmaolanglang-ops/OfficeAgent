import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { readFileSync } from 'node:fs'

const versionSource = readFileSync(
  new URL('../office_agent/_version.py', import.meta.url),
  'utf8',
)
const versionMatch = versionSource.match(/^__version__\s*=\s*["']([^"']+)["']/m)
if (!versionMatch) {
  throw new Error('Unable to read Office Agent version from office_agent/_version.py')
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(versionMatch[1]),
  },
  clearScreen: false,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    target: 'es2022',
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
