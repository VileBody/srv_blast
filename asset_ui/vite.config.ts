import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/admin/assets/',
  server: {
    proxy: {
      '/admin/assets/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/admin\/assets/, ''),
      },
    },
  },
})
