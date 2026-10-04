import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// During `npm run dev` the page is served by Vite and the API by a broker on this address,
// so the browser still sees one origin (the same shape as production, where the broker serves both).
const broker = 'http://127.0.0.1:8765'

export default defineConfig({
  // The broker serves the built files under /ui
  base: '/ui/',
  plugins: [react()],
  build: {
    // Shipped inside the Python package (see [tool.setuptools.package-data] in pyproject.toml)
    outDir: '../src/ocpp_broker/ui_dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { '/api': broker, '/health': broker, '/openapi.json': broker },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
