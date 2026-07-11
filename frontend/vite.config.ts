import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const srcPath = decodeURIComponent(
  new URL('./src', import.meta.url).pathname,
).replace(/^\/([A-Za-z]:)/, '$1')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': srcPath,
    },
  },
  server: {
    host: true,
    port: 5173,
  },
  preview: {
    host: true,
    port: 5174,
  },
})
