import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // куски с хэшем в имени — отдельной папкой: nginx кэширует её навсегда, а public/assets
    // (картинки без хэша) — неделю. Раньше они делили /assets/, и вечный кэш был нельзя.
    assetsDir: 'bundle',
    rollupOptions: {
      output: {
        // библиотеки меняются редко — свой кусок переживает деплои в кэше браузера
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query', 'i18next', 'react-i18next', 'zustand']
        }
      }
    }
  },
  server: {
    allowedHosts: ['4d57f70ae75d16.lhr.life'],
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/static': 'http://127.0.0.1:8001'
    }
  }
});
