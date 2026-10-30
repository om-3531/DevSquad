import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: false,
    proxy: {
      '/workflows': 'http://127.0.0.1:8001',
      '/proposals': 'http://127.0.0.1:8001',
      '/agents': 'http://127.0.0.1:8001',
      '/github': 'http://127.0.0.1:8001',
      '/models': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
})
