import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ['4d57f70ae75d16.lhr.life'],
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/static': 'http://127.0.0.1:8001'
    }
  }
});
