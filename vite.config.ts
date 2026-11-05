import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // (import.meta.url instead of __dirname: works with Vite's new native config loader)
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    // each page is its own file (see src/App.tsx), the shared libraries one more; the biggest is the chart library
    chunkSizeWarningLimit: 700,
    reportCompressedSize: false,          // skips the slow "computing gzip size" step
  },
})
